#!/usr/bin/env python3
"""
Списания рабочего времени в задачи без тегов — проверка дисциплины тегирования.

    tracker-untagged.py                          # текущий месяц, все очереди
    tracker-untagged.py 2026-07                  # конкретный месяц
    tracker-untagged.py --from 2026-08-01 --to 2026-08-14
    tracker-untagged.py --queue CRM DIGITAL      # только эти очереди
    tracker-untagged.py --no-client              # шире: нет тега-мнемоники клиента
    tracker-untagged.py --min 1                  # спрятать мелочь до часа
    tracker-untagged.py --format csv > untagged.csv
    tracker-untagged.py --save                   # → reports/ (папка вне git)

По [регламенту очередей](../../docs/regulations/tracker-queues.md) мнемоника клиента
обязана стоять тегом задачи: по тегу собирается разрез трудозатрат по клиентам.
Задача без тега уносит списанные в неё часы мимо этого разреза — отсюда отчёт.

Считается не «сколько задач без тегов», а «сколько часов списано за период в задачи
без тегов»: важен объём потерянного учёта, а не число нарушений. Задача попадает в
отчёт, если в период есть списание в неё, — независимо от того, когда её завели.

Колонка «Клиент?» — догадка по началу темы, не факт из Трекера. Мнемонику в теме
пишут почти всегда, поэтому в большинстве строк видно, какой тег проставить.

Токен и ID организации — как в tracker-issue.sh: переменные окружения → Keychain
(yandex-tracker / yandex-tracker-org) → ~/.config/yandex-tracker/env.

Коды возврата: 2 — ошибка вызова, 3 — нет учётных данных.
"""

import argparse
import calendar
import csv
import importlib.util
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime

# Общий код с отчётом по спринтам: учётные данные, HTTP, разбор ISO-8601, список
# мнемоник клиентов. Импортом, а не копией: копия мнемоник разошлась бы молча —
# новый клиент появляется в одном файле и не появляется во втором.
# Имя файла с дефисом обычным import не берётся, отсюда importlib.
_SIBLING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker-project-report.py")
_spec = importlib.util.spec_from_file_location("tracker_project_report", _SIBLING)
tpr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tpr)

CLIENTS = tpr.CLIENTS
to_hours = tpr.to_hours
fmt_h = tpr.fmt_h

# Трекер и аналитика в нём живут по Москве; период считаем в той же зоне, иначе
# списания последнего вечера месяца уедут в следующий.
TZ = "+03:00"


def bail(message):
    """Ошибка вызова — код 2, как у соседних скриптов (automation/scripts/README.md)."""
    print(message, file=sys.stderr)
    sys.exit(2)


# --- период ---------------------------------------------------------------------
def month_bounds(year, month):
    return (date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]))


def parse_period(args):
    """(начало, конец, подпись). Позиционный YYYY-MM, пара --from/--to, либо текущий месяц."""
    if args.since or args.until:
        if not (args.since and args.until):
            bail("--from и --to указываются вместе")
        try:
            start = datetime.strptime(args.since, "%Y-%m-%d").date()
            end = datetime.strptime(args.until, "%Y-%m-%d").date()
        except ValueError:
            bail("Даты в --from/--to ожидаются в виде 2026-08-01")
        if end < start:
            bail("--to раньше, чем --from")
        return start, end, f"{start:%Y-%m-%d} … {end:%Y-%m-%d}"

    if args.month:
        m = re.fullmatch(r"(\d{4})-(\d{2})", args.month)
        if not m:
            bail(f"Не разобрал месяц из '{args.month}'. Ожидаю вид 2026-08.")
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            bail(f"Месяца {month} не бывает")
    else:
        today = date.today()
        year, month = today.year, today.month

    start, end = month_bounds(year, month)
    return start, end, f"{year}-{month:02d}"


# --- выгрузка --------------------------------------------------------------------
def worklogs(start, end):
    """Все списания периода по всей организации. Фильтр по дате работы, не по дате внесения."""
    logs, page = [], 1
    body = {"start": {"from": f"{start}T00:00:00{TZ}", "to": f"{end}T23:59:59{TZ}"}}
    while True:
        batch, _ = tpr.api("worklog/_search", method="POST", body=body,
                           params={"perPage": 1000, "page": page})
        logs.extend(batch)
        if len(batch) < 1000:
            return logs
        page += 1


def issues_by_key(keys):
    """Задачи пачками по ключам. Полей у ворклога мало — теги и статус только отсюда."""
    found = {}
    for i in range(0, len(keys), 50):
        chunk = keys[i:i + 50]
        batch, _ = tpr.api("issues/_search", method="POST",
                           body={"query": "Key: " + ", ".join(chunk)},
                           params={"perPage": 100})
        for issue in batch:
            found[issue["key"]] = issue
    return found


# --- разбор ----------------------------------------------------------------------
def client_from_summary(summary):
    """Мнемоника из начала темы — подсказка, какой тег проставить. Не факт из Трекера."""
    head = re.match(r"^\s*([A-Za-z]{2,5})\b", summary or "")
    if head and head.group(1).upper() in CLIENTS:
        return head.group(1).upper()
    return ""


def has_client_tag(issue):
    return any((t or "").strip().upper() in CLIENTS for t in issue.get("tags") or [])


def collect(start, end, queues):
    logs = worklogs(start, end)

    spent = defaultdict(float)          # ключ задачи → часы
    who = defaultdict(lambda: defaultdict(float))
    for w in logs:
        key = w["issue"]["key"]
        if queues and key.rsplit("-", 1)[0] not in queues:
            continue
        hours = to_hours(w.get("duration")) or 0.0
        spent[key] += hours
        who[key][w["createdBy"]["display"]] += hours

    issues = issues_by_key(list(spent))

    rows = []
    for key, hours in spent.items():
        issue = issues.get(key)
        if issue is None:
            # Списание есть, а задача не отдалась: удалена или закрыта правами.
            # Молча ронять нельзя — это тоже неучтённые часы.
            rows.append({
                "key": key, "hours": hours, "queue": key.rsplit("-", 1)[0],
                "summary": "(задача недоступна)", "status": "?", "assignee": "—",
                "tags": [], "client": "", "who": dict(who[key]), "available": False,
            })
            continue
        rows.append({
            "key": key,
            "hours": hours,
            "queue": issue.get("queue", {}).get("key", key.rsplit("-", 1)[0]),
            "summary": issue.get("summary") or "",
            "status": issue.get("status", {}).get("display", "?"),
            "assignee": (issue.get("assignee") or {}).get("display", "—"),
            "tags": issue.get("tags") or [],
            "client": client_from_summary(issue.get("summary")),
            "who": dict(who[key]),
            "available": True,
        })
    rows.sort(key=lambda r: (-r["hours"], r["key"]))
    return rows


def violations(rows, mode):
    """mode='notags' — тегов нет вовсе; 'noclient' — нет тега-мнемоники клиента."""
    if mode == "noclient":
        return [r for r in rows if not any(
            (t or "").strip().upper() in CLIENTS for t in r["tags"])]
    return [r for r in rows if not r["tags"]]


def short_who(who):
    """«Иванов И. 5, Петров П. 2» — кому идти проставлять тег."""
    parts = [f"{tpr.short_name(n)} {fmt_h(h)}" for n, h in
             sorted(who.items(), key=lambda x: -x[1])]
    return ", ".join(parts)


# --- вывод -------------------------------------------------------------------------
def print_markdown(rows, bad, label, args, out=sys.stdout):
    total = sum(r["hours"] for r in rows)
    lost = sum(r["hours"] for r in bad)
    share = (lost / total * 100) if total else 0.0
    no_tags = [r for r in rows if not r["tags"]]
    no_client = [r for r in rows if not any(
        (t or "").strip().upper() in CLIENTS for t in r["tags"])]

    p = lambda s="": print(s, file=out)

    scope = "все очереди" if not args.queue else "очереди: " + ", ".join(sorted(args.queue))
    p(f"Период: **{label}**, {scope}.  ")
    p(f"Списано всего: **{fmt_h(total)} ч** по {len(rows)} задачам.  ")
    p(f"Без тегов: **{fmt_h(sum(r['hours'] for r in no_tags))} ч** "
      f"по {len(no_tags)} задачам.  ")
    p(f"Без мнемоники клиента (тег может быть, но не клиентский): "
      f"**{fmt_h(sum(r['hours'] for r in no_client))} ч** по {len(no_client)} задачам.")
    p()

    if not bad:
        p("Нарушений нет.")
        return

    criterion = "нет тега-мнемоники клиента" if args.no_client else "нет ни одного тега"
    p(f"## Задачи: {criterion} — {fmt_h(lost)} ч, {share:.1f} % периода")
    p()
    if args.min:
        hidden = [r for r in bad if r["hours"] < args.min]
        bad = [r for r in bad if r["hours"] >= args.min]
        if hidden:
            p(f"> Скрыто фильтром `--min`: {len(hidden)} задач(и) меньше "
              f"{fmt_h(args.min)} ч, суммарно {fmt_h(sum(r['hours'] for r in hidden))} ч. "
              "В итогах выше они учтены.")
            p()

    p("| Задача | Ч | Очередь | Теги · клиент по теме | Статус | Исполнитель | Кто списал | Тема |")
    p("|---|---:|---|---|---|---|---|---|")
    for r in bad:
        link = f"[{r['key']}](https://tracker.yandex.ru/{r['key']})"
        # Фактические теги и догадка по теме — рядом и раздельно. Если показывать
        # только догадку, задача с техническим тегом («Angular») выглядит как задача
        # с клиентским, и непонятно, что именно надо править.
        tags = ", ".join(r["tags"])
        guess = f"по теме: {r['client']}" if r["client"] else ""
        client = " · ".join(x for x in (tags, guess) if x) or "—"
        p(f"| {link} | {fmt_h(r['hours'])} | {r['queue']} | {client} | {r['status']} "
          f"| {r['assignee']} | {short_who(r['who'])} | {r['summary'].replace('|', '/')} |")
    p()

    # Очереди — где практика не заведена; люди — с кем разговаривать.
    by_queue_all = defaultdict(float)
    for r in rows:
        by_queue_all[r["queue"]] += r["hours"]
    by_queue = defaultdict(lambda: [0.0, 0])
    for r in bad:
        by_queue[r["queue"]][0] += r["hours"]
        by_queue[r["queue"]][1] += 1

    p("## По очередям")
    p()
    p("| Очередь | Ч без тега | Задач | Доля от списаний очереди |")
    p("|---|---:|---:|---:|")
    for q, (hours, count) in sorted(by_queue.items(), key=lambda x: -x[1][0]):
        pct = hours / by_queue_all[q] * 100 if by_queue_all[q] else 0
        p(f"| {q} | {fmt_h(hours)} | {count} | {pct:.0f} % |")
    p()

    by_person = defaultdict(lambda: [0.0, 0])
    for r in bad:
        for name, hours in r["who"].items():
            by_person[name][0] += hours
            by_person[name][1] += 1

    p("## Кто списывал в задачи без тега")
    p()
    p("| Человек | Ч | Задач |")
    p("|---|---:|---:|")
    for name, (hours, count) in sorted(by_person.items(), key=lambda x: -x[1][0]):
        p(f"| {name} | {fmt_h(hours)} | {count} |")


def print_csv(bad, out=sys.stdout):
    w = csv.writer(out)
    w.writerow(["key", "hours", "queue", "client_guess", "tags", "status",
                "assignee", "who", "summary", "url"])
    for r in bad:
        w.writerow([
            r["key"], f"{r['hours']:.2f}", r["queue"], r["client"], " ".join(r["tags"]),
            r["status"], r["assignee"],
            "; ".join(f"{n}: {h:.2f}" for n, h in sorted(r["who"].items(), key=lambda x: -x[1])),
            r["summary"], f"https://tracker.yandex.ru/{r['key']}",
        ])


def save(rows, bad, label, args):
    """Пишет .md и .csv в reports/ (папка в .gitignore) или в указанный каталог."""
    # Каталог по умолчанию — от расположения скрипта, а не от cwd.
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out_dir = args.save or os.path.join(repo, "reports")
    os.makedirs(out_dir, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d")
    slug = label.replace(" … ", "_").replace(" ", "")
    base = os.path.join(out_dir, f"tracker-untagged-{slug}-{stamp}")

    with open(f"{base}.md", "w") as fh:
        print(f"# Списания в задачи без тегов: {label}\n", file=fh)
        print(f"Собран {stamp} командой `automation/scripts/tracker-untagged.py "
              f"{' '.join(sys.argv[1:])}`.  ", file=fh)
        print("Данные живые — на момент сборки; в git не коммитится "
              "(см. [reports/README.md](README.md)).\n", file=fh)
        print_markdown(rows, bad, label, args, out=fh)

    with open(f"{base}.csv", "w", newline="") as fh:
        print_csv(bad, out=fh)

    print(f"Сохранено:\n  {base}.md\n  {base}.csv")


def main():
    p = argparse.ArgumentParser(
        description="Списания в задачи без тегов: проверка дисциплины тегирования.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("month", nargs="?", metavar="YYYY-MM",
                   help="месяц отчёта; без аргумента — текущий")
    p.add_argument("--from", dest="since", metavar="YYYY-MM-DD",
                   help="начало произвольного периода (вместе с --to)")
    p.add_argument("--to", dest="until", metavar="YYYY-MM-DD", help="конец периода")
    p.add_argument("--queue", nargs="+", metavar="KEY", default=[],
                   help="ограничить очередями, например CRM DIGITAL")
    p.add_argument("--no-client", action="store_true",
                   help="считать нарушением отсутствие тега-мнемоники клиента, "
                        "а не отсутствие тегов вообще")
    p.add_argument("--min", type=float, default=0.0, metavar="Ч",
                   help="не показывать задачи меньше N часов (в итогах учитываются)")
    p.add_argument("--format", choices=["md", "csv", "json"], default="md")
    p.add_argument("--save", nargs="?", const="", metavar="DIR",
                   help="сохранить отчёт (.md и .csv) в reports/ или в указанный каталог")
    args = p.parse_args()

    # credentials() из соседнего модуля выходит с кодом 1; здесь код 3 — «нет
    # учётных данных», как договорено в README для всех скриптов Трекера.
    try:
        tpr.TOKEN, tpr.ORG = tpr.credentials()
    except SystemExit as e:
        print(e, file=sys.stderr)
        sys.exit(3)

    start, end, label = parse_period(args)
    queues = {q.upper() for q in args.queue}
    rows = collect(start, end, queues)
    bad = violations(rows, "noclient" if args.no_client else "notags")

    if args.save is not None:
        save(rows, bad, label, args)
    elif args.format == "csv":
        print_csv(bad)
    elif args.format == "json":
        print(json.dumps({
            "period": label,
            "total_hours": round(sum(r["hours"] for r in rows), 2),
            "untagged_hours": round(sum(r["hours"] for r in bad), 2),
            "issues": [dict(r, hours=round(r["hours"], 2),
                            url=f"https://tracker.yandex.ru/{r['key']}") for r in bad],
        }, ensure_ascii=False, indent=2))
    else:
        print_markdown(rows, bad, label, args)


if __name__ == "__main__":
    main()
