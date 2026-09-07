#!/usr/bin/env python3
"""
Проверка следа перед напоминанием: его ли это работа и не списаны ли часы раньше.

    trace-check.py --tracker reports/2026-09-07/po-ljudjam-proshlaja-nedelja-tracker.json
    trace-check.py --tracker <снимок> --who iyakovlev dklochkov
    trace-check.py --who iyakovlev --keys ANALYTIC-848 ANALYTIC-668 --from 2026-08-31 --to 2026-09-06

Снимок `team-load.py --touched` показывает, что задача попала в след человека за
период. Он **не** отвечает на два вопроса, без которых напоминание про часы
превращается в обвинение:

1. **Его ли это движение.** «Закрыл» в снимке значит «задача, где он исполнитель,
   закрыта в периоде», а закрыть её мог кто угодно: коллега, руководитель, робот
   Трекера при массовом автозакрытии. Проверено 07.09.2026: у Яковлева из шести
   закрытых задач четыре последним менял не он.
2. **Не списаны ли часы раньше.** Человек работал две недели назад, тогда же списал,
   а статус двинул сейчас. Часов «за этот день» нет и быть не должно — работа
   оплачена в своём периоде. У того же Яковлева так пять задач из шести: часы стоят
   19.08, 25.08, 04.08.

Скрипт по каждой задаче отвечает на оба и выносит вердикт. Просить часы можно
только по задачам с вердиктом «работа без часов».

Ворклоги берутся целиком по задаче (`GET issues/<key>/worklog`), а не поиском за
период: смысл проверки именно в том, чтобы увидеть списание ВНЕ периода.

Коды возврата: 2 — ошибка вызова, 3 — нет учётных данных.
"""

import argparse
import importlib.util
import json
import os
import sys
from datetime import date

_S = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("team_load", os.path.join(_S, "team-load.py"))
tl = importlib.util.module_from_spec(_spec)
_argv, sys.argv = sys.argv, ["team-load.py"]
_spec.loader.exec_module(tl)
sys.argv = _argv

ROBOT_MARKS = ("робот", "robot")


def main():
    ap = argparse.ArgumentParser(description="Чей след и когда списаны часы")
    ap.add_argument("--tracker", help="снимок team-load.py --format json --touched")
    ap.add_argument("--who", nargs="+", help="логины; без них — все из снимка")
    ap.add_argument("--keys", nargs="+", help="ключи задач вручную (с одним --who)")
    ap.add_argument("--from", dest="date_from", help="начало периода ГГГГ-ММ-ДД")
    ap.add_argument("--to", dest="date_to", help="конец периода ГГГГ-ММ-ДД")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    args = ap.parse_args()

    if not args.tracker and not (args.keys and args.who and len(args.who) == 1):
        tl.bail("нужен --tracker либо пара --who <логин> --keys <ключи>")

    people, lo, hi = [], args.date_from, args.date_to
    if args.tracker:
        snap = json.load(open(args.tracker, encoding="utf-8"))
        lo = lo or snap["period"]["from"]
        hi = hi or snap["period"]["to"]
        for p in snap["people"]:
            if args.who and p["login"] not in set(args.who):
                continue
            keys = list(dict.fromkeys(
                list(p["closed"])
                + [i["key"] for i in p["trace"]["created"]]
                + list(p["trace"]["lastTouch"])))
            if keys:
                people.append((p["login"], p["name"], keys,
                               {i["key"] for i in p["trace"]["created"]},
                               set(p["closed"])))
    else:
        people.append((args.who[0], args.who[0], args.keys, set(), set()))
    if not lo or not hi:
        tl.bail("период не задан: --from и --to (или снимок с периодом)")

    tl.tpr.TOKEN, tl.tpr.ORG = tl.tpr.credentials()
    name_of = {p[0]: p[1] for p in tl.PEOPLE}
    # Сверяем по логину, а не по отображаемому имени: Трекер пишет «Игорь Яковлев»,
    # наш справочник — «Яковлев Игорь», и сравнение строк молча даёт «это не он».
    uid_login = tl.user_index()

    def who(entity):
        e = entity or {}
        return uid_login.get(str(e.get("id")), ""), (e.get("display") or "").strip()

    out = []
    for login, name, keys, created, closed in people:
        display = name_of.get(login, name)
        info = tl.issues_by_key(keys)
        rows = []
        for key in keys:
            issue = info.get(key) or {}
            updated_login, updated_by = who(issue.get("updatedBy"))
            updated_at = (issue.get("updatedAt") or "")[:10]
            logs, _ = tl.tpr.api(f"issues/{key}/worklog")
            mine = [(w.get("start", "")[:10], tl.to_hours(w.get("duration")) or 0.0)
                    for w in (logs or []) if who(w.get("createdBy"))[0] == login]
            others = sorted({who(w.get("createdBy"))[1] or "?"
                             for w in (logs or []) if who(w.get("createdBy"))[0] != login})
            in_period = [d for d, h in mine if lo <= d <= hi and h > 0]
            before = sorted(d for d, h in mine if d < lo and h > 0)
            after = sorted(d for d, h in mine if d > hi and h > 0)
            zero_in_period = [d for d, h in mine if lo <= d <= hi and h <= 0]

            his_move = key in created or updated_login == login
            robot = any(m in updated_by.lower() for m in ROBOT_MARKS)

            outside = bool(updated_at) and not (lo <= updated_at <= hi) and key not in created

            if outside and not in_period:
                verdict = f"движение вне периода: {updated_at}"
            elif not his_move:
                verdict = f"чужой след: последним менял {updated_by or '—'}"
                if robot:
                    verdict = f"движение робота ({updated_by})"
            elif in_period:
                verdict = f"часы за период есть: {', '.join(in_period)}"
            elif before:
                verdict = f"часы списаны раньше периода: {before[-1]}"
            elif after:
                verdict = f"часы списаны после периода: {after[0]}"
            elif zero_in_period:
                verdict = f"списание на ноль часов {zero_in_period[0]}"
            elif others and not mine:
                verdict = f"часы списывал не он: {', '.join(others)}"
            else:
                verdict = "работа без часов"

            rows.append({"key": key, "updatedAt": updated_at, "updatedBy": updated_by,
                         "closed": key in closed, "created": key in created,
                         "myWorklogs": [{"date": d, "hours": round(h, 2)} for d, h in mine],
                         "otherWorklogAuthors": others, "verdict": verdict})
        out.append({"login": login, "name": display, "period": {"from": lo, "to": hi},
                    "issues": rows})

    if args.format == "json":
        json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return

    for p in out:
        real = [r for r in p["issues"] if r["verdict"] == "работа без часов"]
        print(f"\n## {p['name']} — задач в следе {len(p['issues'])}, "
              f"без часов и его руками {len(real)}\n")
        print("| Задача | Последним менял | Мои списания | Вердикт |")
        print("|---|---|---|---|")
        for r in p["issues"]:
            wl = ", ".join(f"{w['date']} {w['hours']} ч" for w in r["myWorklogs"]) or "—"
            print(f"| {r['key']} | {r['updatedBy'] or '—'} ({r['updatedAt']}) | {wl} | {r['verdict']} |")


if __name__ == "__main__":
    main()
