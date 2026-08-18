#!/usr/bin/env python3
"""Создание задач в Яндекс Трекере по пресету проекта.

Реквизиты задачи (очередь, тип, компонент, тег, префикс названия) у нас
одинаковы внутри проекта и разные между проектами. Руками их проставляют
через раз — отсюда пресеты: называется проект, остальное подставляется.

Текст берётся из markdown-файла: первый H1 — название задачи, всё, что
после него, — описание. Так же устроен wiki-push.py, и по той же причине:
формулировку задачи надо вычитать до отправки, а в командной строке это
неудобно.

По умолчанию НИЧЕГО НЕ СОЗДАЁТ — печатает, что было бы отправлено.
Запись включается флагом --apply.

    ./tracker-create.py pbeadmin   задача.md                       # сухой прогон
    ./tracker-create.py pbeadmin   задача.md --apply
    ./tracker-create.py pbeapp     задача.md --estimate 4h --apply
    ./tracker-create.py pbeconsole задача.md --assignee iippolitov --apply

Подзадачи заводятся теми же пресетами плюс --parent: родитель задаёт связь,
пресет — очередь, тип и компонент.

    ./tracker-create.py crm-razrabotka-admin  задача.md --parent CRM-1121 --apply
    ./tracker-create.py crm-testirovanie      задача.md --parent CRM-1121 --apply
    ./tracker-create.py crm-reliz             задача.md --parent CRM-1121 --apply

Оценка одна на два поля: Трекер хранит «Оценку» и «Первоначальную оценку»
отдельно, а в отчётах по спринту нужны обе. Не указана — не заполняется
ни одна: пустая оценка это открытый вопрос, а не ноль.

Коды возврата: 0 — создано; 1 — не создано; 2 — ошибка вызова;
3 — не найдены учётные данные.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.tracker.yandex.net/v3"
TRACKER_HOST = "https://tracker.yandex.ru"

# Реквизиты по проектам. Меняются вместе с процессом, а не по ходу задачи:
# правка здесь — это правка того, как заводятся ВСЕ задачи проекта.
PRESETS: dict[str, dict] = {
    "pbeadmin": {
        "queue": "CRM",
        "type": "development",          # Разработка
        "components": ["Администрирование"],
        "tags": ["PBE"],
        "prefix": "PBE",
    },
    "pbeapp": {
        "queue": "CRM",
        "type": "development",          # Разработка
        "components": ["Приложение"],
        "tags": ["PBE"],
        "prefix": "PBE",
    },
    "pbeconsole": {
        "queue": "CONSOLE",
        "type": "improvement",          # Улучшение
        "components": [],
        "tags": [],
        "prefix": None,
    },

    # Подзадачи этапов жизненного цикла задачи CRM. Отличаются от пресетов
    # выше тем, что почти всегда идут с --parent: сами по себе они не живут.
    # Префикса нет: название подзадачи читается в контексте родителя, и
    # мнемонику в него добавляет тот, кто заводит.
    "crm-razrabotka-admin": {
        "queue": "CRM",
        "type": "development",          # Разработка
        "components": ["Администрирование"],
        "tags": [],
        "prefix": None,
    },
    "crm-razrabotka-app": {
        "queue": "CRM",
        "type": "development",          # Разработка
        "components": ["Приложение"],
        "tags": [],
        "prefix": None,
    },
    # Работы дата-инженеров живут в своей очереди, а не в CRM: там же вся
    # остальная работа по данным клиентов. Типов «Разработка» в ANALYTIC нет —
    # только «Задача» и «Ошибка», поэтому тип здесь task, и это не упущение.
    "analytic-razrabotka": {
        "queue": "ANALYTIC",
        "type": "task",                 # Задача
        "components": ["Аналитика данных"],
        "tags": [],
        "prefix": None,
    },
    "crm-testirovanie": {
        "queue": "CRM",
        "type": "testing",              # Тестирование
        "components": [],
        "tags": [],
        "prefix": None,
    },
    "crm-reliz": {
        "queue": "CRM",
        "type": "release",              # Релиз
        "components": [],
        "tags": [],
        "prefix": None,
    },
    "crm-ba": {
        "queue": "CRM",
        "type": "businessanalysis",     # Бизнес-анализ
        "components": ["Бизнес анализ"],
        "tags": [],
        "prefix": None,
    },
    "crm-konsultaciya": {
        "queue": "CRM",
        "type": "consultation",         # Консультация
        "components": [],
        "tags": [],
        "prefix": None,
    },
    "crm-dokumentaciya": {
        "queue": "CRM",
        "type": "documentation",        # Документация
        "components": [],
        "tags": [],
        "prefix": None,
    },
    "crm-dizayn": {
        "queue": "CRM",
        "type": "design",               # Дизайн
        "components": [],
        "tags": [],
        "prefix": None,
    },
}

H1_RE = re.compile(r"^#\s+(.+?)\s*$")
DURATION_RE = re.compile(
    r"^(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$", re.IGNORECASE)


# --- учётные данные ------------------------------------------------------------

def keychain(service: str) -> str:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def credentials() -> tuple[str, str]:
    """Тот же порядок, что в tracker-issue.sh: env → файл → Keychain."""
    token = os.environ.get("YANDEX_TRACKER_TOKEN", "")
    org = os.environ.get("YANDEX_TRACKER_ORG_ID", "")

    env_file = Path(os.environ.get(
        "YANDEX_TRACKER_ENV_FILE", Path.home() / ".config/yandex-tracker/env"))
    if (not token or not org) and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if k.strip() == "YANDEX_TRACKER_TOKEN" and not token:
                token = v
            elif k.strip() == "YANDEX_TRACKER_ORG_ID" and not org:
                org = v

    token = token or keychain("yandex-tracker")
    org = org or keychain("yandex-tracker-org")

    if not token or not org:
        sys.exit(
            "Нет доступа к Трекеру: не найдены YANDEX_TRACKER_TOKEN и/или "
            "YANDEX_TRACKER_ORG_ID.\n\n"
            "Разовая настройка (macOS Keychain):\n"
            "  security add-generic-password -s yandex-tracker     -a \"$USER\" -w '<токен>'\n"
            "  security add-generic-password -s yandex-tracker-org -a \"$USER\" -w '<ID организации>'\n"
        )
    return token, org


# --- разбор входных данных -----------------------------------------------------

def parse_document(path: Path) -> tuple[str, str]:
    """Первый H1 — название задачи, всё после него — описание."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    for i, line in enumerate(lines):
        m = H1_RE.match(line)
        if m:
            body = "\n".join(lines[i + 1:]).strip()
            return m.group(1), body

    raise ValueError("нет заголовка H1 — из чего делать название задачи")


def parse_duration(raw: str) -> str:
    """«4h», «2d», «1d4h», «1w» → ISO-8601 для Трекера.

    Дни и недели отдаём как есть: Трекер считает их по рабочему календарю
    (день = 8 часов, неделя = 5 дней), и P1D в отчёте по спринту читается
    как 8 часов, а не 24.
    """
    m = DURATION_RE.match(raw.strip())
    if not m or not any(m.groups()):
        raise ValueError(
            f"не разобрана оценка «{raw}». Формат: 30m, 4h, 2d, 1w, 1d4h")

    w, d, h, mi = (int(g) if g else 0 for g in m.groups())
    date = f"{w}W" if w else ""
    date += f"{d}D" if d else ""
    time = f"{h}H" if h else ""
    time += f"{mi}M" if mi else ""
    return "P" + date + (f"T{time}" if time else "")


def build_summary(title: str, prefix: str | None) -> str:
    """Мнемоника в начале названия — требование регламента очереди."""
    if not prefix or title.upper().startswith(prefix.upper()):
        return title
    return f"{prefix} {title}"


# --- API -----------------------------------------------------------------------

class Tracker:
    def __init__(self, token: str, org: str):
        self.headers = {
            "Authorization": f"OAuth {token}",
            "X-Org-Id": org,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _call(self, method: str, url: str,
              payload: dict | None = None) -> tuple[int, object]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=self.headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw or "{}")
            except json.JSONDecodeError:
                return e.code, {"debug_message": raw[:400]}
        except urllib.error.URLError as e:
            return 0, {"debug_message": f"сеть недоступна: {e.reason}"}

    def component_ids(self, queue: str, names: list[str]) -> list[int]:
        """Имена компонентов → id. Имена берём как есть из пресета: id
        живут в настройках очереди и меняются мимо репозитория."""
        if not names:
            return []
        code, body = self._call("GET", f"{API}/queues/{queue}/components")
        if code != 200 or not isinstance(body, list):
            raise ValueError(f"не прочитаны компоненты очереди {queue}: HTTP {code}")

        found = {c["name"]: c["id"] for c in body}
        missing = [n for n in names if n not in found]
        if missing:
            raise ValueError(
                f"в очереди {queue} нет компонентов: {', '.join(missing)}. "
                f"Есть: {', '.join(sorted(found))}")
        return [found[n] for n in names]

    def resolve_user(self, login: str) -> str | None:
        """Логин как передали, иначе в нижнем регистре.

        В Трекере логины строчные, а в почте и оргструктуре пишутся как
        придётся (`Iippolitov@powbee.ru`). API регистр не прощает — отвечает
        404, и без этой развилки на нём спотыкаются каждый раз.
        """
        for candidate in (login, login.lower()):
            code, _ = self._call(
                "GET", f"{API}/users/{urllib.parse.quote(candidate)}")
            if code == 200:
                return candidate
        return None

    def issue(self, key: str) -> dict | None:
        """Родитель до создания подзадачи: опечатка в ключе иначе всплывёт
        уже после того, как задача заведена не туда."""
        code, body = self._call("GET", f"{API}/issues/{key}")
        return body if code == 200 and isinstance(body, dict) else None

    def find_by_summary(self, queue: str, summary: str) -> list[str]:
        """Задача с таким же названием в очереди — защита от дубля при
        повторном прогоне: скрипт запускают дважды чаще, чем кажется."""
        query = f'Queue: {queue} AND Summary: "{summary}"'
        code, body = self._call("POST", f"{API}/issues/_search", {"query": query})
        if code != 200 or not isinstance(body, list):
            return []
        return [i["key"] for i in body if i.get("summary") == summary]

    def create(self, payload: dict) -> tuple[int, object]:
        return self._call("POST", f"{API}/issues/", payload)


# --- CLI -----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Создание задач в Яндекс Трекере по пресету проекта.")
    ap.add_argument("preset", choices=sorted(PRESETS),
                    help="проект: реквизиты очереди, типа, компонента и тега")
    ap.add_argument("document", help="markdown-файл: H1 — название, дальше — описание")
    ap.add_argument("--estimate", metavar="4h",
                    help="оценка (30m / 4h / 2d / 1w / 1d4h). "
                         "Заполняет и «Оценку», и «Первоначальную оценку». "
                         "Не указана — оба поля остаются пустыми")
    ap.add_argument("--assignee", metavar="LOGIN",
                    help="исполнитель. Не указан — задача остаётся без исполнителя")
    ap.add_argument("--parent", metavar="CRM-1234",
                    help="родительская задача: создаётся подзадачей. "
                         "Ключ проверяется до создания")
    ap.add_argument("--apply", action="store_true",
                    help="создать задачу (по умолчанию — сухой прогон)")
    ap.add_argument("--force", action="store_true",
                    help="создать, даже если задача с таким названием уже есть")
    args = ap.parse_args()

    preset = PRESETS[args.preset]

    path = Path(args.document)
    if not path.is_file():
        print(f"Файл не найден: {path}", file=sys.stderr)
        return 2

    try:
        title, description = parse_document(path)
        summary = build_summary(title, preset["prefix"])
        estimate = parse_duration(args.estimate) if args.estimate else None
    except ValueError as e:
        print(f"✗ {path.name}: {e}", file=sys.stderr)
        return 2

    if not description:
        print(f"✗ {path.name}: после заголовка пусто — нечего класть в описание",
              file=sys.stderr)
        return 2

    tracker = Tracker(*credentials())

    try:
        components = tracker.component_ids(preset["queue"], preset["components"])
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    parent = None
    if args.parent:
        parent = args.parent.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", parent):
            print(f"✗ «{args.parent}» не похоже на ключ задачи. "
                  f"Формат: CRM-1234", file=sys.stderr)
            return 2
        parent_issue = tracker.issue(parent)
        if not parent_issue:
            print(f"✗ в Трекере нет задачи {parent} — родителя не существует "
                  f"или нет доступа", file=sys.stderr)
            return 1

    assignee = None
    if args.assignee:
        assignee = tracker.resolve_user(args.assignee)
        if not assignee:
            print(f"✗ в Трекере нет пользователя «{args.assignee}». "
                  f"Логин — тот же, что в почте, без домена", file=sys.stderr)
            return 1

    payload: dict = {
        "queue": preset["queue"],
        "summary": summary,
        "description": description,
        "type": preset["type"],
    }
    if components:
        payload["components"] = components
    if preset["tags"]:
        payload["tags"] = preset["tags"]
    if estimate:
        payload["estimation"] = estimate
        payload["originalEstimation"] = estimate
    if assignee:
        payload["assignee"] = assignee
    if parent:
        payload["parent"] = parent

    duplicates = tracker.find_by_summary(preset["queue"], summary)

    print(f"{path.name} → {args.preset}")
    print(f"  очередь:     {preset['queue']}")
    print(f"  тип:         {preset['type']}")
    print(f"  компонент:   {', '.join(preset['components']) or '—'}")
    print(f"  теги:        {', '.join(preset['tags']) or '—'}")
    print(f"  название:    {summary}")
    print(f"  описание:    {len(description)} символов, "
          f"{description.count(chr(10)) + 1} строк")
    print(f"  оценка:      {estimate or '— (не заполняется)'}")
    print(f"  исполнитель: {assignee or '— (не назначается)'}")
    if parent:
        print(f"  родитель:    {parent} — "
              f"{parent_issue.get('summary', '?')}")
    if duplicates:
        print(f"  ⚠️ уже есть с таким названием: {', '.join(duplicates)}")
    print()

    if duplicates and not args.force:
        print("Не создано: задача с таким названием уже есть. "
              "Создать всё равно — флаг --force", file=sys.stderr)
        return 1

    if not args.apply:
        print("СУХОЙ ПРОГОН — задача не создана. Создать: --apply")
        return 1

    code, body = tracker.create(payload)
    if code in (200, 201) and isinstance(body, dict):
        key = body.get("key", "?")
        print(f"✓ создана {key} — {TRACKER_HOST}/{key}")
        return 0

    msg = body.get("errorMessages") or body.get("errors") or body \
        if isinstance(body, dict) else body
    print(f"✗ не создана — HTTP {code}: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
