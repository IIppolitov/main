#!/usr/bin/env python3
"""
Отправка готового черновика человеку в Яндекс Мессенджер от имени бота.

    msg-send.py --file reports/2026-09-07/soobshhenie-chasy-dklochkov.md   # показать, не отправлять
    msg-send.py --file reports/2026-09-07/soobshhenie-chasy-dklochkov.md --send
    msg-send.py --dir reports/2026-09-07 --send                            # все черновики папки
    msg-send.py --to dklochkov --text "Денис, привет"  --send

**По умолчанию ничего не отправляется.** Без `--send` скрипт печатает, кому и что
уйдёт, и выходит: сообщение человеку — действие необратимое, и подтверждать его
должен человек, а не агент.

Кому: логин берётся из имени файла `soobshhenie-<что угодно>-<логин>.md` либо из
`--to`. **Логин Мессенджера — это полная рабочая почта**, `dklochkov@powbee.ru`:
короткий `dklochkov` Bot API не находит (`user_not_found`, проверено 07.09.2026).
Домен дописывается сам, поэтому в имени файла и в `--to` достаточно логина Трекера —
у всех шестнадцати человек департамента он равен локальной части почты (сверено с
org-structure.md). Другой домен — писать адрес целиком.

**Только личные сообщения.** Отправка в групповой чат не поддерживается намеренно:
разбор дисциплины учёта при коллегах меняет поведение не в ту сторону, а рассылка
в общий чат перестаёт читаться за неделю.

Токен бота: `PBE_MESSENGER_BOT_TOKEN` в окружении либо связка `pbe-messenger-bot`
в Keychain macOS. В вывод, файлы и коммиты токен не попадает.

Отправленное записывается в `reports/<день>/otpravka-log.md` — чтобы второй запуск
не разослал то же самое ещё раз молча.

Коды возврата: 2 — ошибка вызова, 3 — нет токена, 4 — API отказало хотя бы по одному.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

API = "https://botapi.messenger.yandex.net/bot/v1"
LIMIT = 6000
DOMAIN = "powbee.ru"
REPO = Path(__file__).resolve().parents[2]


def roster():
    """Логин → ФИО из PEOPLE в team-load.py: защита от опечатки в имени файла."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "team_load", os.path.join(os.path.dirname(os.path.abspath(__file__)), "team-load.py"))
    mod = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, ["team-load.py"]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    return {p[0]: p[1] for p in mod.PEOPLE}


def bail(message, code=2):
    print(message, file=sys.stderr)
    sys.exit(code)


def token():
    env = os.environ.get("PBE_MESSENGER_BOT_TOKEN", "")
    if env:
        return env.strip()
    try:
        out = subprocess.run(["security", "find-generic-password",
                              "-s", "pbe-messenger-bot", "-w"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    bail("токен бота не найден: ни PBE_MESSENGER_BOT_TOKEN, ни связка "
         "pbe-messenger-bot в Keychain.\n"
         "Положить в Keychain:\n"
         "  security add-generic-password -s pbe-messenger-bot -a bot -w '<токен>'", 3)


def api(path, body):
    req = urllib.request.Request(f"{API}/{path}/", method="POST",
                                 data=json.dumps(body, ensure_ascii=False).encode())
    req.add_header("Authorization", "OAuth " + token())
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:300]
        except Exception:
            pass
        return None, f"HTTP {e.code} {detail}".strip()
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def address(login):
    """Логин Мессенджера — полная почта; короткому логину дописываем домен."""
    return login if "@" in login else f"{login}@{DOMAIN}"


def login_from_name(path):
    m = re.match(r"^soobshhenie-.*?-([a-z0-9_.\-]+)$", Path(path).stem)
    return m.group(1) if m else None


def collect(args):
    """→ [(логин, текст, путь)]"""
    items = []
    if args.text:
        if not args.to:
            bail("--text требует --to")
        items.append((args.to, args.text, "—"))
    for f in args.file or []:
        p = Path(f)
        if not p.is_file():
            bail(f"нет файла: {f}")
        who = args.to or login_from_name(p)
        if not who:
            bail(f"из имени {p.name} логин не вычитывается — задай --to")
        items.append((who, p.read_text(encoding="utf-8").strip(), str(p)))
    if args.dir:
        d = Path(args.dir)
        if not d.is_dir():
            bail(f"нет папки: {args.dir}")
        for p in sorted(d.glob("soobshhenie-*.md")):
            who = login_from_name(p)
            if not who:
                print(f"пропускаю {p.name}: логин из имени не вычитывается", file=sys.stderr)
                continue
            items.append((who, p.read_text(encoding="utf-8").strip(), str(p)))
    return items


def already_sent(log_path):
    if not log_path.is_file():
        return set()
    return {line.split("|")[1].strip()
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("| 20")}


def main():
    ap = argparse.ArgumentParser(description="Отправить черновик в Мессенджер от бота")
    ap.add_argument("--file", nargs="+", help="файлы черновиков")
    ap.add_argument("--dir", help="папка: все soobshhenie-*.md из неё")
    ap.add_argument("--to", help="логин получателя (перекрывает имя файла)")
    ap.add_argument("--text", help="текст напрямую, без файла")
    ap.add_argument("--send", action="store_true", help="действительно отправить")
    ap.add_argument("--any-login", action="store_true",
                    help="разрешить логин вне справочника PEOPLE")
    ap.add_argument("--preview", action="store_true",
                    help="не гасить превью ссылок (по умолчанию гасим)")
    ap.add_argument("--again", action="store_true",
                    help="отправить даже если по журналу дня уже отправлено")
    args = ap.parse_args()

    items = collect(args)
    if not items:
        bail("нечего отправлять: задай --file, --dir или --text")

    today = date.today().isoformat()
    log_path = REPO / "reports" / today / "otpravka-log.md"
    sent_today = set() if args.again else already_sent(log_path)

    known = roster()
    plan = []
    for who, text, src in items:
        note = ""
        if who.split("@")[0] not in known and not args.any_login:
            note = ("логина нет в справочнике PEOPLE — проверь, кому шлёшь "
                    "(--any-login, если это кто-то вне департамента)")
        if len(text) > LIMIT:
            note = f"СЛИШКОМ ДЛИННОЕ: {len(text)} симв. при пределе {LIMIT}"
        elif who in sent_today:
            note = "уже отправлено сегодня — пропущу (--again чтобы всё равно)"
        plan.append((who, text, src, note))

    print(f"Кому уйдёт ({sum(1 for p in plan if not p[3])} из {len(plan)}):\n")
    for who, text, src, note in plan:
        first = text.splitlines()[0][:70] if text else ""
        print(f"  {address(who):24} {known.get(who.split('@')[0], '?'):22} "
              f"{len(text):5} симв.  {first}")
        if src != "—":
            print(f"  {'':16} из {src}")
        if note:
            print(f"  {'':16} → {note}")
    if not args.send:
        print("\nЭто предпросмотр. Отправить — тот же вызов с --send.")
        return

    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.is_file():
        log_path.write_text("# Отправленные сообщения\n\n"
                            "| Когда | Кому | message_id | Источник |\n|---|---|---|---|\n",
                            encoding="utf-8")

    failed = 0
    with log_path.open("a", encoding="utf-8") as log:
        for who, text, src, note in plan:
            if note:
                continue
            data, err = api("messages/sendText", {
                "login": address(who), "text": text,
                # Превью ссылки на Трекер бесполезно — внешний бот видит страницу входа,
                # а карточка занимает пол-экрана (docs/company/message-style.md).
                "disable_web_page_preview": not args.preview,
            })
            if err or not (data or {}).get("ok"):
                failed += 1
                print(f"  {who:16} НЕ ОТПРАВЛЕНО: {err or data}", file=sys.stderr)
                continue
            mid = data.get("message_id")
            print(f"  {who:16} отправлено, message_id {mid}")
            log.write(f"| {datetime.now():%Y-%m-%d %H:%M} | {who} | {mid} | {src} |\n")
            time.sleep(0.3)

    print(f"\nЖурнал: {log_path}")
    if failed:
        sys.exit(4)


if __name__ == "__main__":
    main()
