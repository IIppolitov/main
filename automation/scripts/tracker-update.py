#!/usr/bin/env python3
"""Комментарий или новое описание существующей задачи Яндекс Трекера.

Дополняет tracker-create.py: тот заводит задачу, этот дописывает к ней.
Текст берётся из markdown-файла целиком — ответы на вопросы команды,
справка, уточнение постановки. Причина та же, что у tracker-create.py:
такой текст надо вычитать до отправки, а в командной строке это неудобно.

По умолчанию НИЧЕГО НЕ ОТПРАВЛЯЕТ — печатает, что было бы отправлено.
Запись включается флагом --apply.

    ./tracker-update.py CRM-1630 ответ.md --comment            # сухой прогон
    ./tracker-update.py CRM-1630 ответ.md --comment --apply
    ./tracker-update.py CRM-1630 задача.md --description --apply
    ./tracker-update.py CRM-1629 CRM-1630 справка.md --comment --apply
    ./tracker-update.py CRM-1030 ответ.md --comment --attach схема.html --apply

Ключей можно передать несколько — один и тот же текст уходит в каждую
задачу. Типовой случай: ответ команде плюс та же справка в соседние задачи.

--attach прикладывает файл к комментарию (ключ можно повторить). Файл
грузится как временное вложение и становится постоянным в момент создания
комментария — поэтому при нескольких задачах он загружается заново для
каждой. С --description вложения не работают: описание правится целиком,
прикладывать к нему нечего.

Комментарий по умолчанию тихий (без уведомления подписчиков) — как и
wiki-push.py. Уведомить: --notify.

--description ЗАМЕЩАЕТ описание целиком, прежний текст не сохраняется.
Поэтому перед записью скрипт показывает, сколько символов было и станет.

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


# --- CLI -----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Комментарий или новое описание задачи Яндекс Трекера.")
    ap.add_argument("args", nargs="+",
                    help="ключи задач (или ссылки) и markdown-файл с текстом")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--comment", action="store_true",
                      help="добавить комментарий")
    mode.add_argument("--description", action="store_true",
                      help="ЗАМЕСТИТЬ описание задачи целиком")
    ap.add_argument("--attach", action="append", metavar="ФАЙЛ", default=[],
                    help="приложить файл к комментарию (можно несколько раз)")
    ap.add_argument("--apply", action="store_true",
                    help="выполнить запись (по умолчанию — сухой прогон)")
    ap.add_argument("--notify", action="store_true",
                    help="уведомить подписчиков (по умолчанию тихо)")
    args = ap.parse_args()

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

    if document is None:
        print("Не указан markdown-файл с текстом.", file=sys.stderr)
        return 2
    if not keys:
        print("Не указано ни одной задачи.", file=sys.stderr)
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
