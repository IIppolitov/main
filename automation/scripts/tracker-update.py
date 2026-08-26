#!/usr/bin/env python3
"""Комментарий, новое описание или правка полей задачи Яндекс Трекера.

Дополняет tracker-create.py: тот заводит задачу, этот правит существующую.
Три режима, по одному за вызов: комментарий, описание, поля.

Текст комментария и описания берётся из markdown-файла целиком — ответы на
вопросы команды, справка, уточнение постановки. Причина та же, что у
tracker-create.py: такой текст надо вычитать до отправки, а в командной
строке это неудобно.

Поля правятся флагами и файла не требуют. Типовой случай — привести дерево
задачи к регламенту по отчёту tracker-lint.py: проставить компонент команды,
назначить исполнителя, перевесить багрепорт на нужную подзадачу «Разработка».

По умолчанию НИЧЕГО НЕ ОТПРАВЛЯЕТ — печатает, что было бы отправлено.
Запись включается флагом --apply.

    ./tracker-update.py CRM-1630 ответ.md --comment            # сухой прогон
    ./tracker-update.py CRM-1630 ответ.md --comment --apply
    ./tracker-update.py CRM-1630 задача.md --description --apply
    ./tracker-update.py CRM-1629 CRM-1630 справка.md --comment --apply
    ./tracker-update.py CRM-1030 ответ.md --comment --attach схема.html --apply

    ./tracker-update.py CRM-1240 CRM-1241 --component Администрирование   # сухой
    ./tracker-update.py CRM-131 --assignee dantonova --apply
    ./tracker-update.py BUGREPORTS-150 --parent CRM-1225 --apply
    ./tracker-update.py CRM-131 --field "Срочность БА=3" --apply

Ключей можно передать несколько — одно и то же уходит в каждую задачу.
Типовой случай: ответ команде плюс та же справка в соседние задачи; для
полей — один компонент на четыре подзадачи разом.

--attach прикладывает файл к комментарию (ключ можно повторить). Файл
грузится как временное вложение и становится постоянным в момент создания
комментария — поэтому при нескольких задачах он загружается заново для
каждой. С --description вложения не работают: описание правится целиком,
прикладывать к нему нечего.

Комментарий по умолчанию тихий (без уведомления подписчиков) — как и
wiki-push.py. Уведомить: --notify.

--description ЗАМЕЩАЕТ описание целиком, прежний текст не сохраняется.
Поэтому перед записью скрипт показывает, сколько символов было и станет.

Правка полей
------------

    --assignee ЛОГИН      исполнитель: логин, почта или «Фамилия Имя»
    --component ИМЯ       компонент команды, флаг можно повторить
    --parent KEY          родитель: задача становится подзадачей KEY
    --field ИМЯ=ЗНАЧЕНИЕ  любое другое поле по русскому названию либо id

Значения проверяются ДО записи по справочникам Трекера: исполнитель ищется
среди сотрудников, компонент — среди компонентов очереди, имя поля — среди
полей организации. Опечатка отсекается на сухом прогоне, а не молча
записывается мимо. Сухой прогон печатает «было → станет» по каждой задаче.

Как и у tracker-tag.py, --apply кладёт в reports/ журнал: правка полей
задним числом иначе неотличима от того, что поля были заполнены всегда.

Коды возврата: 0 — всё прошло; 1 — часть или всё не прошло;
2 — ошибка вызова; 3 — не найдены учётные данные.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

API = "https://api.tracker.yandex.net/v3"
TRACKER_HOST = "https://tracker.yandex.ru"

KEY_RE = re.compile(r"([A-Z][A-Z0-9]*-\d+)")
H1_RE = re.compile(r"^#\s+(.+?)\s*$")


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

def normalize_key(raw: str) -> str | None:
    """Ключ задачи из аргумента: `CRM-544` либо ссылка целиком."""
    m = KEY_RE.search(raw.upper())
    return m.group(1) if m else None


def read_body(path: Path, strip_h1: bool) -> str:
    """Текст файла. Для описания H1 отбрасывается — заголовок задачи
    задаётся полем summary, в теле он был бы вторым."""
    text = path.read_text(encoding="utf-8")
    if not strip_h1:
        return text.strip()

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if H1_RE.match(line):
            return "\n".join(lines[i + 1:]).strip()
    return text.strip()


# --- API -----------------------------------------------------------------------

class Tracker:
    def __init__(self, token: str, org: str):
        self.headers = {
            "Authorization": f"OAuth {token}",
            "X-Org-Id": org,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _send(self, method: str, url: str, data: bytes | None = None,
              ctype: str | None = None, timeout: int = 60) -> tuple[int, object]:
        headers = dict(self.headers)
        if ctype:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw or "{}")
            except json.JSONDecodeError:
                return e.code, {"debug_message": raw[:400]}
        except urllib.error.URLError as e:
            return 0, {"debug_message": f"сеть недоступна: {e.reason}"}

    def _call(self, method: str, url: str,
              payload: dict | None = None) -> tuple[int, object]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        return self._send(method, url, data, "application/json")

    def issue(self, key: str) -> dict | None:
        code, body = self._call("GET", f"{API}/issues/{key}")
        return body if code == 200 and isinstance(body, dict) else None

    def upload_temp(self, path: Path) -> tuple[int, object]:
        """Временное вложение. Живёт до привязки к комментарию —
        поэтому грузится отдельно под каждую задачу."""
        boundary = uuid.uuid4().hex
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = b"".join([
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{path.name}"\r\n'.encode("utf-8"),
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8"),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ])
        q = urllib.parse.urlencode({"filename": path.name})
        return self._send("POST", f"{API}/attachments/?{q}", body,
                          f"multipart/form-data; boundary={boundary}",
                          timeout=180)

    def comment(self, key: str, text: str, notify: bool,
                attachment_ids: list[str] | None = None) -> tuple[int, object]:
        q = urllib.parse.urlencode({"isAddToFollowers": str(notify).lower()})
        payload: dict = {"text": text}
        if attachment_ids:
            payload["attachmentIds"] = attachment_ids
        return self._call("POST", f"{API}/issues/{key}/comments?{q}", payload)

    def set_description(self, key: str, text: str) -> tuple[int, object]:
        return self._call("PATCH", f"{API}/issues/{key}",
                          {"description": text})

    def patch(self, key: str, payload: dict) -> tuple[int, object]:
        return self._call("PATCH", f"{API}/issues/{key}", payload)

    def queue_components(self, queue: str) -> list[dict]:
        code, body = self._call("GET", f"{API}/queues/{queue}/components")
        return body if code == 200 and isinstance(body, list) else []

    def fields_for(self, queue: str) -> list[dict]:
        """Поля, доступные задаче: общие плюс локальные поля её очереди.

        «Срочность БА» и все поля оценки заведены локальными в CRM — в общем
        справочнике /fields их нет, и без этого шага скрипт отвечал бы
        «такого поля в Трекере нет» на поле, которое видно в задаче."""
        if not hasattr(self, "_fields_cache"):
            self._fields_cache: dict[str, list[dict]] = {}
        if queue not in self._fields_cache:
            code, common = self._call("GET", f"{API}/fields")
            common = common if code == 200 and isinstance(common, list) else []
            code, local = self._call("GET", f"{API}/queues/{queue}/localFields")
            local = local if code == 200 and isinstance(local, list) else []
            self._fields_cache[queue] = local + common
        return self._fields_cache[queue]

    def find_user(self, needle: str) -> dict | None:
        """Сотрудник по логину, почте или отображаемому имени.

        Точное совпадение по логину и почте, для имени — без учёта регистра
        и порядка слов: в отчёте линта человек назван «Антонова Диана», а в
        Трекере может стоять «Диана Антонова»."""
        code, body = self._call("GET", f"{API}/users?perPage=1000")
        if code != 200 or not isinstance(body, list):
            return None
        want = needle.strip().lower()
        want_words = set(want.split())
        for u in body:
            if want in {str(u.get("login", "")).lower(),
                        str(u.get("email", "")).lower()}:
                return u
        for u in body:
            display = str(u.get("display", "")).lower()
            if display == want or set(display.split()) == want_words:
                return u
        return None


# --- правка полей --------------------------------------------------------------

def parse_pairs(items: list[str]) -> list[tuple[str, str]] | None:
    """`ИМЯ=ЗНАЧЕНИЕ` → пары. None, если хоть одна пара без знака равенства."""
    out: list[tuple[str, str]] = []
    for raw in items:
        if "=" not in raw:
            print(f"--field ждёт ИМЯ=ЗНАЧЕНИЕ, получено: {raw}", file=sys.stderr)
            return None
        name, value = raw.split("=", 1)
        if not name.strip():
            print(f"--field: пустое имя поля в {raw}", file=sys.stderr)
            return None
        out.append((name.strip(), value.strip()))
    return out


def field_names(field: dict) -> list[str]:
    """Названия поля на всех языках, какие вернул Трекер."""
    name = field.get("name")
    if isinstance(name, dict):
        return [str(v) for v in name.values() if v]
    return [str(name)] if name else []


def resolve_field(fields: list[dict], needle: str) -> dict | None:
    """Поле по русскому названию либо по id/ключу."""
    want = needle.strip().lower()
    for f in fields:
        if want in {str(f.get("id", "")).lower(), str(f.get("key", "")).lower()}:
            return f
    for f in fields:
        if any(n.lower() == want for n in field_names(f)):
            return f
    return None


def coerce(field: dict, value: str):
    """Значение под тип поля. Строкой в числовое поле Трекер не примет."""
    kind = str((field.get("schema") or {}).get("type", "string")).lower()
    try:
        if kind in ("integer", "long", "int"):
            return int(value)
        if kind in ("number", "float", "double"):
            return float(value)
        if kind == "boolean":
            return value.strip().lower() in ("1", "true", "да", "yes")
        if kind == "array":
            return [v.strip() for v in value.split(",") if v.strip()]
    except ValueError:
        return None
    return value


def shown(value) -> str:
    """Текущее значение поля человеку: словарь Трекера — это `display`."""
    if value is None or value == [] or value == "":
        return "—"
    if isinstance(value, dict):
        return str(value.get("display") or value.get("key") or value)
    if isinstance(value, list):
        return ", ".join(shown(v) for v in value)
    return str(value)


def journal(lines: list[str]) -> str:
    """Журнал правки полей — в reports/ (папка вне git), как у tracker-tag.py."""
    repo = Path(__file__).resolve().parents[2]
    out_dir = repo / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    path = out_dir / f"tracker-update-fields-{stamp}.log"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(f"# Правка полей, {stamp}\n")
        fh.write("# Команда: automation/scripts/tracker-update.py "
                 f"{' '.join(sys.argv[1:])}\n")
        for line in lines:
            fh.write(line + "\n")
    return str(path)


def build_field_patch(tracker: "Tracker", key: str, issue: dict,
                      args) -> tuple[dict, list[str]] | None:
    """PATCH-payload и строки «поле: было → станет». None — значение не опознано.

    Справочники спрашиваются у Трекера, а не берутся на веру: компонент
    принадлежит очереди, и «Администрирование» из CRM в BUGREPORTS не то же
    самое имя."""
    payload: dict = {}
    report: list[str] = []

    if args.assignee:
        user = tracker.find_user(args.assignee)
        if user is None:
            print(f"✗ {key}: сотрудник «{args.assignee}» не найден — "
                  f"логин, почта или «Фамилия Имя»", file=sys.stderr)
            return None
        payload["assignee"] = user.get("id") or user.get("login")
        report.append(f"  исполнитель: {shown(issue.get('assignee'))} → "
                      f"{user.get('display')}")

    if args.component:
        queue = (issue.get("queue") or {}).get("key", "")
        available = tracker.queue_components(queue)
        by_name = {str(c.get("name", "")).lower(): c for c in available}
        ids, names = [], []
        for wanted in args.component:
            found = by_name.get(wanted.strip().lower())
            if found is None:
                have = ", ".join(sorted(str(c.get("name")) for c in available)) or "—"
                print(f"✗ {key}: в очереди {queue} нет компонента "
                      f"«{wanted}». Есть: {have}", file=sys.stderr)
                return None
            ids.append(found.get("id"))
            names.append(str(found.get("name")))
        payload["components"] = ids
        report.append(f"  компоненты: {shown(issue.get('components'))} → "
                      f"{', '.join(names)}")

    if args.parent:
        parent = tracker.issue(args.parent)
        if parent is None:
            print(f"✗ {key}: родитель {args.parent} не найден или нет доступа",
                  file=sys.stderr)
            return None
        payload["parent"] = args.parent
        report.append(f"  родитель: {shown(issue.get('parent'))} → "
                      f"{args.parent} ({parent.get('summary', '?')})")

    queue_key = (issue.get("queue") or {}).get("key", "")
    for name, raw in (args.field or []):
        field = resolve_field(tracker.fields_for(queue_key), name)
        if field is None:
            print(f"✗ {key}: поля «{name}» нет ни в общих полях, ни в "
                  f"локальных полях очереди {queue_key}", file=sys.stderr)
            return None
        value = coerce(field, raw)
        if value is None:
            kind = (field.get("schema") or {}).get("type")
            print(f"✗ {key}: «{raw}» не подходит полю «{name}» (тип {kind})",
                  file=sys.stderr)
            return None
        fid = field.get("id")
        payload[fid] = value
        report.append(f"  {name}: {shown(issue.get(fid))} → {shown(value)}")

    return payload, report


# --- CLI -----------------------------------------------------------------------

def run_fields(keys: list[str], args) -> int:
    """Режим правки полей: сухой прогон «было → станет», запись по --apply."""
    tracker = Tracker(*credentials())

    plan: list[tuple[str, dict, dict, list[str]]] = []
    failed = 0
    for key in keys:
        issue = tracker.issue(key)
        if issue is None:
            print(f"✗ {key}: задача не найдена или нет доступа", file=sys.stderr)
            failed += 1
            continue
        built = build_field_patch(tracker, key, issue, args)
        if built is None:
            failed += 1
            continue
        payload, report = built
        if not payload:
            print(f"· {key}: нечего менять")
            continue
        plan.append((key, issue, payload, report))

    if not plan:
        return 1

    print(f"## Правка полей: {len(plan)} задач(и)\n")
    for key, issue, _, report in plan:
        print(f"{key} — {issue.get('summary', '?')}")
        print(f"  {TRACKER_HOST}/{key}")
        for line in report:
            print(line)
        print()

    if not args.apply:
        print("Сухой прогон — ничего не записано. Запись: --apply")
        return 1

    log: list[str] = []
    ok = 0
    for key, issue, payload, report in plan:
        code, resp = tracker.patch(key, payload)
        if code in (200, 201):
            print(f"✓ {key} — поля записаны")
            log.append(f"{key} — {issue.get('summary', '?')}")
            log.extend(report)
            ok += 1
        else:
            msg = resp.get("errorMessages") or resp.get("errors") or resp \
                if isinstance(resp, dict) else resp
            print(f"✗ {key} — HTTP {code}: {msg}", file=sys.stderr)
            log.append(f"{key} — НЕ ЗАПИСАНО, HTTP {code}: {msg}")
            failed += 1

    print(f"\nОбновлено задач: {ok}" + (f", не прошло: {failed}" if failed else ""))
    print(f"Журнал: {journal(log)}")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Комментарий, новое описание или правка полей задачи "
                    "Яндекс Трекера.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("args", nargs="+",
                    help="ключи задач (или ссылки) и markdown-файл с текстом")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--comment", action="store_true",
                      help="добавить комментарий")
    mode.add_argument("--description", action="store_true",
                      help="ЗАМЕСТИТЬ описание задачи целиком")
    ap.add_argument("--attach", action="append", metavar="ФАЙЛ", default=[],
                    help="приложить файл к комментарию (можно несколько раз)")
    ap.add_argument("--assignee", metavar="ЛОГИН",
                    help="исполнитель: логин, почта или «Фамилия Имя»")
    ap.add_argument("--component", action="append", metavar="ИМЯ", default=[],
                    help="компонент команды (можно несколько раз)")
    ap.add_argument("--parent", metavar="KEY",
                    help="родитель: задача становится подзадачей KEY")
    ap.add_argument("--field", action="append", metavar="ИМЯ=ЗНАЧЕНИЕ",
                    default=[],
                    help="любое другое поле по русскому названию либо id")
    ap.add_argument("--apply", action="store_true",
                    help="выполнить запись (по умолчанию — сухой прогон)")
    ap.add_argument("--notify", action="store_true",
                    help="уведомить подписчиков (по умолчанию тихо)")
    args = ap.parse_args()

    fields_mode = bool(args.assignee or args.component or args.parent
                       or args.field)
    if fields_mode and (args.comment or args.description):
        print("Правка полей не совмещается с --comment и --description: "
              "режим один за вызов.", file=sys.stderr)
        return 2
    if not fields_mode and not (args.comment or args.description):
        print("Не указано, что делать: --comment, --description либо флаги "
              "правки полей (--assignee / --component / --parent / --field).",
              file=sys.stderr)
        return 2

    pairs = parse_pairs(args.field)
    if pairs is None:
        return 2
    args.field = pairs

    if args.attach and args.description:
        print("--attach работает только с --comment: описание правится "
              "целиком, прикладывать к нему нечего.", file=sys.stderr)
        return 2

    attachments: list[Path] = []
    for a in args.attach:
        p = Path(a)
        if not p.is_file():
            print(f"Нет файла для вложения: {a}", file=sys.stderr)
            return 2
        attachments.append(p)

    keys: list[str] = []
    document: Path | None = None
    for a in args.args:
        p = Path(a)
        if p.is_file():
            if document is not None:
                print(f"Файлов больше одного: {document} и {p}", file=sys.stderr)
                return 2
            document = p
            continue
        key = normalize_key(a)
        if not key:
            print(f"Не ключ задачи и не файл: {a}", file=sys.stderr)
            return 2
        if key not in keys:
            keys.append(key)

    if not keys:
        print("Не указано ни одной задачи.", file=sys.stderr)
        return 2
    if fields_mode:
        if document is not None:
            print(f"Правке полей файл не нужен: {document}", file=sys.stderr)
            return 2
        return run_fields(keys, args)
    if document is None:
        print("Не указан markdown-файл с текстом.", file=sys.stderr)
        return 2

    body = read_body(document, strip_h1=args.description)
    if not body:
        print(f"✗ {document.name}: пусто — нечего отправлять", file=sys.stderr)
        return 2

    tracker = Tracker(*credentials())
    what = "описание" if args.description else "комментарий"

    plan = []
    failed = 0
    for key in keys:
        issue = tracker.issue(key)
        if issue is None:
            print(f"✗ {key}: задача не найдена или нет доступа", file=sys.stderr)
            failed += 1
            continue
        plan.append((key, issue))

    if not plan:
        return 1

    print(f"{document.name} → {what}: {len(body)} символов, "
          f"{body.count(chr(10)) + 1} строк\n")
    for key, issue in plan:
        print(f"{key} — {issue.get('summary', '?')}")
        print(f"  {TRACKER_HOST}/{key}")
        if args.description:
            was = len(issue.get("description") or "")
            print(f"  описание: {was} → {len(body)} символов "
                  f"(прежний текст НЕ сохраняется)")
        else:
            print(f"  уведомление подписчиков: "
                  f"{'да' if args.notify else 'нет'}")
            for p in attachments:
                print(f"  вложение: {p.name} ({p.stat().st_size} Б)")
    print()

    if not args.apply:
        print(f"СУХОЙ ПРОГОН — ничего не отправлено. Запись: --apply")
        return 1

    ok = 0
    for key, _ in plan:
        if args.description:
            code, resp = tracker.set_description(key, body)
        else:
            att_ids: list[str] = []
            broken = False
            for p in attachments:
                up_code, up_resp = tracker.upload_temp(p)
                att_id = up_resp.get("id") if isinstance(up_resp, dict) else None
                if up_code in (200, 201) and att_id:
                    att_ids.append(str(att_id))
                    print(f"  ↑ {key}: {p.name} загружен (id={att_id})")
                else:
                    print(f"✗ {key} — вложение {p.name}: HTTP {up_code}: "
                          f"{up_resp}", file=sys.stderr)
                    broken = True
            if broken:
                print(f"✗ {key} — комментарий не отправлен: не все вложения "
                      f"загрузились", file=sys.stderr)
                failed += 1
                continue
            code, resp = tracker.comment(key, body, args.notify, att_ids)

        if code in (200, 201):
            print(f"✓ {key} — {what} записано")
            ok += 1
        else:
            msg = resp.get("errorMessages") or resp.get("errors") or resp \
                if isinstance(resp, dict) else resp
            print(f"✗ {key} — HTTP {code}: {msg}", file=sys.stderr)
            failed += 1

    print(f"\nОбновлено задач: {ok}" + (f", не прошло: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
