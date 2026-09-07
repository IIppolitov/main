#!/usr/bin/env python3
"""
Что люди ответили боту консоли в личку.

    msg-otvety.py                        # новое с прошлого чтения
    msg-otvety.py --since 2026-09-07     # с даты (первый вызов, когда курсора нет)
    msg-otvety.py --login dklochkov      # ответы одного человека
    msg-otvety.py --peek                 # посмотреть, не двигая курсор

Обратная сторона `msg-send.py`: отправили напоминание — здесь видно, что человек
ответил. Читает `GET /api/v1/direct-messages` консоли (CONSOLE-34), а **не** Bot API
Яндекса: очередь апдейтов у бота одна на организацию и `getUpdates(offset)` её
разрушает, поэтому в неё ходит только поллер консоли. Локальный второй потребитель
воровал бы у него хотфиксы и «посмотреть позже».

Курсор хранится в `~/.config/pbe-console/direct-messages.json` — в нём `nextAfterId`
прошлого ответа. Поэтому обычный вызов без аргументов отдаёт ровно то, чего вы ещё не
видели, а пустой ответ значит «нового не появилось», а не «ошибка».

Что помнить про источник:

- **только личные сообщения.** Групповых чатов здесь нет ни при каких параметрах —
  так сделано намеренно;
- **записи живут 14 дней** и потом удаляются насовсем. Это не архив переписки: что не
  прочитано вовремя — потеряно;
- **пустой текст** — человек прислал вложение, картинку или стикер. Факт и время
  ответа достоверны, содержимого консоль не хранит;
- `matched=false` — отправителя не сопоставили с сотрудником: написал кто-то извне
  либо логин в консоли не заполнен. Это не ошибка.

⚠️ **Личная переписка.** Ответы читает владелец токена и только для себя: не
пересказывать их третьим лицам, не переносить текст в документы департамента и не
цитировать в чужих сообщениях. В журнал напоминаний идёт факт «внёс / не внёс»,
а не то, что человек написал.

Токен — `PBE_CONSOLE_API_TOKEN` или связка `pbe-console-api` в Keychain, как у
остальных обращений к консоли.

Коды возврата: 2 — ошибка вызова, 3 — нет токена, 5 — консоль ответила ошибкой.
"""

import argparse
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

_S = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("team_load", os.path.join(_S, "team-load.py"))
tl = importlib.util.module_from_spec(_spec)
_argv, sys.argv = sys.argv, ["team-load.py"]
_spec.loader.exec_module(tl)
sys.argv = _argv

API = "https://console.powbeecrm.com/api/v1"
STATE = Path.home() / ".config" / "pbe-console" / "direct-messages.json"


def get(path, params):
    token = tl.console_token()
    if not token:
        sys.exit("msg-otvety.py: токен консоли не найден: ни PBE_CONSOLE_API_TOKEN, "
                 "ни связка pbe-console-api в Keychain")
    url = f"{API}{path}?" + urllib.parse.urlencode({k: v for k, v in params.items()
                                                    if v not in (None, "")})
    req = urllib.request.Request(url)
    req.add_header("X-Console-Token", token)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:300]
        except Exception:
            pass
        sys.exit(f"msg-otvety.py: консоль ответила HTTP {e.code} {body}")
    except Exception as e:
        sys.exit(f"msg-otvety.py: консоль недоступна: {type(e).__name__}: {e}")


def load_cursor():
    try:
        return json.loads(STATE.read_text(encoding="utf-8")).get("nextAfterId")
    except Exception:
        return None


def save_cursor(value):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"nextAfterId": value,
                                 "readAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                                ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Личные ответы боту консоли")
    ap.add_argument("--since", help="с этого момента: ГГГГ-ММ-ДД или 'ГГГГ-ММ-ДД ЧЧ:ММ:СС'")
    ap.add_argument("--login", help="ответы одного отправителя")
    ap.add_argument("--limit", type=int, default=50, help="до 200 за вызов")
    ap.add_argument("--peek", action="store_true", help="не двигать курсор")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    args = ap.parse_args()

    params = {"limit": min(max(args.limit, 1), 200), "login": args.login}
    if args.since:
        since = args.since if len(args.since) > 10 else f"{args.since} 00:00:00"
        params["since"] = since
    else:
        cursor = load_cursor()
        if cursor is not None:
            params["after_id"] = cursor
        else:
            params["since"] = f"{date.today().isoformat()} 00:00:00"
            print("Курсора ещё нет — читаю с начала сегодняшнего дня "
                  "(--since для другой границы).\n", file=sys.stderr)

    payload = get("/direct-messages", params)
    data = payload.get("data") or []
    meta = payload.get("meta") or {}

    if args.format == "json":
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        if not data:
            print("Новых ответов нет.")
        for m in data:
            mark = "" if m.get("matched") else "  [отправитель не сопоставлен]"
            print(f"\n{m.get('sentAt')}  {m.get('name') or m.get('login')} "
                  f"<{m.get('login')}>{mark}")
            text = (m.get("text") or "").strip()
            print("  " + (text.replace("\n", "\n  ") if text
                          else "«без текста» — вложение, картинка или стикер"))
        print(f"\nОтветов: {meta.get('count', len(data))}"
              f"{', есть ещё — вызовите снова' if meta.get('hasMore') else ''}."
              f" Хранятся {meta.get('retentionDays', '?')} дн.")

    nxt = meta.get("nextAfterId")
    if args.peek:
        print("Курсор не сдвинут (--peek).", file=sys.stderr)
    elif nxt is not None:
        save_cursor(nxt)


if __name__ == "__main__":
    main()
