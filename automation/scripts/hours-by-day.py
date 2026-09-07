#!/usr/bin/env python3
"""
Часы и присутствие **по дням** — разрез, которого нет в team-load.py.

    hours-by-day.py --from 2026-08-31 --to 2026-09-06
    hours-by-day.py --days 7 --team dev-admin data
    hours-by-day.py --from 2026-08-31 --to 2026-09-06 --who dklochkov --format json

team-load.py отдаёт суммы за период и число дней со списаниями, но не сами даты.
Для напоминания про часы нужны именно даты: «добери за неделю» без дней люди
закрывают ровными восьмёрками по памяти, и учёт от этого становится хуже. Отсюда
отдельный скрипт: по каждому человеку и каждому дню — **сколько списано в Трекер**
и **сколько присутствия отдала консоль**.

Обе величины берутся из тех же источников, что в team-load.py, и тем же способом:
списания — `worklog/_search` по дате работы (`start`), присутствие — `GET /hours`
консоли, вызванный по одному дню за раз (агрегат за период даты не сохраняет).
Поэтому запросов к консоли столько, сколько дней в периоде.

Присутствие — только для отчёта руководителю. **В сообщение человеку оно не идёт
никогда:** цифра слежения за компьютером в личном сообщении превращает напоминание
в надзор.

Коды возврата: 2 — ошибка вызова, 3 — нет учётных данных.
"""

import argparse
import importlib.util
import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

_SIBLING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "team-load.py")
_spec = importlib.util.spec_from_file_location("team_load", _SIBLING)
tl = importlib.util.module_from_spec(_spec)
_argv, sys.argv = sys.argv, ["team-load.py"]      # у соседа argparse на импорте не срабатывает
_spec.loader.exec_module(tl)
sys.argv = _argv


def parse_args():
    p = argparse.ArgumentParser(description="Часы и присутствие по дням")
    p.add_argument("--team", nargs="+", default=["all"], help="команды; all — все")
    p.add_argument("--who", nargs="+", help="логины Трекера точечно")
    p.add_argument("--days", type=int, help="последние N дней")
    p.add_argument("--from", dest="date_from", help="начало периода ГГГГ-ММ-ДД")
    p.add_argument("--to", dest="date_to", help="конец периода ГГГГ-ММ-ДД")
    p.add_argument("--format", choices=["md", "json"], default="md")
    a = p.parse_args()
    if (a.date_from or a.date_to) and not (a.date_from and a.date_to):
        tl.bail("--from и --to задаются вместе")
    if a.date_from:
        start = date.fromisoformat(a.date_from)
        end = date.fromisoformat(a.date_to)
    else:
        end = date.today()
        start = end - timedelta(days=(a.days or 7) - 1)
    if start > end:
        tl.bail("начало периода позже конца")
    return a, start, end


def main():
    args, start, end = parse_args()
    tl.tpr.TOKEN, tl.tpr.ORG = tl.tpr.credentials()

    if args.who:
        people = [p for p in tl.PEOPLE if p[0] in set(args.who)]
    elif "all" in args.team:
        people = list(tl.PEOPLE)
    else:
        people = [p for p in tl.PEOPLE if p[2] in set(args.team)]
    if not people:
        tl.bail("никто не выбран: проверь --who и --team")

    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    work = set(tl.workdays(start, end))

    uid_login = tl.user_index()
    spent = defaultdict(lambda: defaultdict(float))
    for w in tl.worklogs(start, end):
        hours = tl.to_hours(w.get("duration")) or 0
        login = uid_login.get(str((w.get("createdBy") or {}).get("id")), "")
        if not login:
            continue
        # списание на ноль часов оставляем видимым: это «день формально закрыт, а часов нет»
        spent[login][(w.get("start") or "")[:10]] += hours

    presence, notes = defaultdict(dict), []
    for day in days:
        hrs, note = tl.console_hours(day, day)
        if hrs is None:
            notes.append(f"{day.isoformat()}: {note}")
            continue
        for login, v in hrs.items():
            presence[login][day.isoformat()] = v["activity"]

    out = {"period": {"from": start.isoformat(), "to": end.isoformat(),
                      "workdays": len(work)},
           "consoleNotes": notes,
           "people": []}
    for login, name, team, role, _gh in people:
        out["people"].append({
            "login": login, "name": name, "team": team, "role": role,
            "days": [{"date": d.isoformat(),
                      "workday": d in work,
                      "hours": round(spent[login].get(d.isoformat(), 0.0), 2)
                               if d.isoformat() in spent[login] else None,
                      "presence": presence.get(login, {}).get(d.isoformat())}
                     for d in days],
        })

    if args.format == "json":
        json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return

    def cell(d):
        h = "—" if d["hours"] is None else tl.fmt_h(d["hours"])
        a = "—" if d["presence"] is None else tl.fmt_h(d["presence"])
        return f"{a} / {h}"

    print(f"# Часы и присутствие по дням: {start.isoformat()} — {end.isoformat()}")
    print(f"\nРабочих дней в периоде: {len(work)}. В клетке — **присутствие / списано**;"
          " «—» в часах значит, что списаний за день нет вовсе.\n")
    head = "| Кто | " + " | ".join(
        d.strftime("%d.%m") + ("" if d in work else " (вых.)") for d in days) + " |"
    print(head)
    print("|---" * (len(days) + 1) + "|")
    for p in out["people"]:
        print(f"| {p['name']} | " + " | ".join(cell(d) for d in p["days"]) + " |")
    if notes:
        print("\nКонсоль не ответила по дням: " + "; ".join(notes))


if __name__ == "__main__":
    main()
