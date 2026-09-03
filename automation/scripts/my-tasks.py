#!/usr/bin/env python3
"""
Что висит на мне — снимок личной очереди из Трекера.

    my-tasks.py                                  # всё, что на мне сейчас
    my-tasks.py --release admin-09               # + разрез «в релизе / вне релиза»
    my-tasks.py --projects 931 932 1031          # свой набор проектов вместо пресета
    my-tasks.py --no-improvements                # без блока улучшений
    my-tasks.py --pending-days 0                 # + забытые флаги «ждут ответа» старше 90 дней
    my-tasks.py --who aseleznev                  # чужая очередь (в пределах своих прав)
    my-tasks.py --format json                    # машиночитаемо — под команду /pbe-my-tasks
    my-tasks.py --save                           # .md и .json в reports/ (папка вне git)

Отвечает на один вопрос: **что ждёт меня и в каком порядке за это браться**. Задачи
разложены по тому, какого действия от меня ждут: ревью, ответ, оценка, разработка.
Внутри «ответа» отдельно аналитика и поддержка — это разные контуры и разные люди
на том конце.

Три источника «на мне», а не один:

- **исполнитель** — очевидная часть;
- **поле «Ревьювер»** — исполнитель другой, но ревью числится за мной;
- **«Ожидается ответ от»** — задача уехала к менеджеру или в поддержку, но стоит
  из-за моего ответа. Такие в списке «мои задачи» не видны вообще, а именно они
  чаще всего и держат чужую работу.

Сравнение с прошлым снимком — не украшение: состав очереди за день перетряхивается
целиком (03.09.2026 из семи задач на ревью ушли пять и пришли восемь других), и без
дельты снимок читается как «всё то же самое».

Токен и ID организации — как в tracker-issue.sh: переменные окружения → Keychain
(yandex-tracker / yandex-tracker-org) → ~/.config/yandex-tracker/env.

Коды возврата: 2 — ошибка вызова, 3 — нет учётных данных.
"""

import argparse
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone

# Общий код с отчётом по спринтам: учётные данные, HTTP, разбор ISO-8601. Импортом,
# а не копией — как в team-load.py.
_SIBLING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker-project-report.py")
_spec = importlib.util.spec_from_file_location("tracker_project_report", _SIBLING)
tpr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tpr)

TRACKER = "https://tracker.yandex.ru"

# --- статусы ---------------------------------------------------------------------
# Раскладка по тому, какого действия ждут от человека. Тип статуса из API
# (`statusType`) для этого не годится: «Готов к ревью» помечен как paused, хотя для
# ревьювера это работа, которая ждёт его сегодня.
#
# Новый статус workflow надо дописать сюда, иначе он попадёт в «Прочее» — корзина
# печатается, молча статус не потеряется.
REVIEW = {"reviewReady"}
ANSWER = {"needInfo"}                      # «Требуется информация» — ждут моего ответа
ESTIMATE = {"needEstimate"}                # «Оценка задачи»
DEV = {                                    # работа на мне: писать код
    "open", "new", "backlog", "selectedForDev", "readyfordevelopment", "inProgress",
    "indevelopment", "forRevision", "preparation", "devAwaiting",
}
# «Есть дефекты» в эту корзину не входит намеренно: по [этапу 11], 4.5.3 работа идёт
# в багрепорте, а подзадача ждёт его закрытия. Она уходит в «Прочее» — иначе очередь
# дня разбавляется задачами, по которым делать нечего, пока не закрыт дефект.
CLOSED = {"closed", "cancelled", "resolved", "achieved"}

# --- контуры ---------------------------------------------------------------------
# Очередь говорит, кто на том конце ждёт ответа: дата-инженер, поддержка или
# разработка. Смешивать их в одном списке нельзя — это три разных разговора.
CONTOURS = [
    ("analytics", "Аналитика",  {"ANALYTIC", "SUPPORTANALYTIC"}),
    ("support",   "Поддержка",  {"SUPPORT", "SUPPORTDEV", "SUPPORTINFO"}),
]
CONTOUR_DEFAULT = ("product", "Разработка и продукт")

# --- пресеты релизов -------------------------------------------------------------
# Связь «релиз → его проекты» нигде в Трекере не записана: у релиза админки два
# спринта плюс проект «Вне спринта», и вывести это из дат нельзя — спринт 17
# заканчивается до 15.09, но едет уже в следующий релиз. Первоисточник — решение
# релиз-менеджера, зафиксированное в docs/company/team-load.md; здесь пресет под
# частый вызов. Новый релиз — дописать строку.
RELEASES = {
    "admin-09": {
        "title": "релиз админки 09 (15.09.2026)",
        "projects": ["Спринт Админка 15", "Спринт Админка 16", "Релиз Админка 09. Вне спринта"],
    },
}

MSK = timezone.utc  # даты Трекера приходят в UTC; для «сколько дней» смещение не важно


def api(path, method="GET", body=None, params=None):
    return tpr.api(path, method=method, body=body, params=params)


def search(query, per_page=100):
    """Поиск с пагинацией. Сортировка по ключу — иначе страницы разъезжаются."""
    out, page = [], 1
    while True:
        data, _ = api("issues/_search", "POST", {"query": query}, {"page": page, "perPage": per_page})
        out.extend(data)
        if len(data) < per_page:
            return out
        page += 1
        if page > 50:  # 5000 задач — заведомо ошибка в запросе, а не очередь человека
            return out


def project_issue_keys(project_id):
    keys, page = [], 1
    while True:
        batch, _ = api("issues/_search", "POST", {"filter": {"project": str(project_id)}},
                       {"perPage": 100, "page": page})
        keys.extend(i["key"] for i in batch)
        if len(batch) < 100:
            return keys
        page += 1


def resolve_projects(names_or_ids):
    """Принимает id проекта или часть названия. Возвращает [(id, название)]."""
    resolved, unknown = [], []
    catalog = None
    for item in names_or_ids:
        if str(item).isdigit():
            info = tpr.project_info(item)
            fields = info.get("fields", info)
            resolved.append((str(item), fields.get("summary", str(item))))
            continue
        if catalog is None:
            data, _ = api("entities/project/_search", "POST", {"filter": {}},
                          {"perPage": 300, "fields": "summary"})
            catalog = data.get("values", data)
        needle = str(item).lower()
        hits = [(str(p.get("shortId")), p.get("fields", {}).get("summary", ""))
                for p in catalog
                if needle in (p.get("fields", {}).get("summary", "") or "").lower()]
        if hits:
            resolved.extend(hits)
        else:
            unknown.append(item)
    return resolved, unknown


def field_value(issue, suffix):
    """Локальные поля приходят с префиксом организации: <id>--theReviewer."""
    for key, value in issue.items():
        if key.endswith("--" + suffix):
            return value
    return None


def people(value):
    if not value:
        return []
    if isinstance(value, dict):
        value = [value]
    return [p.get("display", "") for p in value if isinstance(p, dict)]


def short_project(title):
    """Названия проектов длинные: в таблице нужен различающий кусок, а не всё."""
    m = re.match(r"Спринт (Админка|Прилага|Аналитика)\s*(\S+)", title)
    if m:
        return f"Спринт {m.group(2)}"
    m = re.match(r"Релиз Админка (\d+)", title)
    if m:
        return f"Релиз {m.group(1)}, вне спринта"
    return title if len(title) <= 32 else title[:29] + "…"


def days_since(ts):
    if not ts:
        return None
    moment = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).days


def collect(login, uid, release_projects, pending_days):
    """Три источника «на мне» → один список задач без дублей."""
    seen, rows, stale_pending = {}, [], 0

    def add(issue, source):
        key = issue["key"]
        if key in seen:
            seen[key]["sources"].add(source)
            return
        seen[key] = {"issue": issue, "sources": {source}}

    for issue in search(f'Assignee: {login} AND Resolution: empty() "Sort by": key asc'):
        if issue["status"]["key"] not in CLOSED:
            add(issue, "assignee")

    # Поле «Ревьювер» язык запросов не видит (К-29): берём все задачи на ревью и
    # фильтруем по полю на нашей стороне. Их десятки, не тысячи.
    for issue in search('Status: "Готов к ревью" "Sort by": key asc'):
        if issue["status"]["key"] not in CLOSED and _is_reviewer(issue, uid):
            add(issue, "reviewer")

    # «Ожидается ответ от» без границы по дате даёт хвост в полсотни задач 2025 года,
    # где флаг повесили и забыли: очередью дня это не является. Отсечённое считаем и
    # печатаем числом — молча терять его нельзя, оно поднимается флагом.
    for issue in search(f'"Pending Reply From": {login} AND Resolution: empty() "Sort by": key asc'):
        if issue["status"]["key"] in CLOSED:
            continue
        idle = days_since(issue.get("updatedAt"))
        if pending_days and idle is not None and idle > pending_days:
            stale_pending += 1
            continue
        add(issue, "pending")

    for key, item in seen.items():
        rows.append(describe(item["issue"], item["sources"], uid, release_projects))
    return rows, stale_pending


def _has_person(value, uid):
    """Поле с одним человеком или списком: есть ли там я."""
    if not value:
        return False
    if isinstance(value, dict):
        value = [value]
    return any(str(p.get("id")) == str(uid) or str(p.get("passportUid")) == str(uid)
               for p in value if isinstance(p, dict))


def _is_reviewer(issue, uid):
    return _has_person(field_value(issue, "theReviewer"), uid)


def describe(issue, sources, uid, release_projects):
    assignee = issue.get("assignee") or {}
    status_key = issue["status"]["key"]
    queue = issue["queue"]["key"]
    parent = (issue.get("parent") or {}).get("key", "")
    sprints = sorted(set(release_projects.get(issue["key"], []) + release_projects.get(parent, [])))
    contour = CONTOUR_DEFAULT[0]
    for code, _title, queues in CONTOURS:
        if queue in queues:
            contour = code
            break
    waiting = people(issue.get("pendingReplyFrom"))
    pending_on_me = _has_person(issue.get("pendingReplyFrom"), uid)
    return {
        "key": issue["key"],
        "url": f"{TRACKER}/{issue['key']}",
        "summary": issue["summary"],
        "queue": queue,
        "type": issue["type"]["display"],
        "status": issue["status"]["display"],
        "statusKey": status_key,
        "daysInStatus": days_since(issue.get("statusStartTime")),
        "updatedAt": issue.get("updatedAt", "")[:10],
        "assignee": assignee.get("display", ""),
        "mine": str(assignee.get("id")) == str(uid),
        "reviewer": people(field_value(issue, "theReviewer")),
        "waitingFor": waiting,
        "pendingOnMe": pending_on_me,
        "project": (issue.get("project") or {}).get("display", ""),
        "sprints": sprints,
        "inRelease": bool(sprints),
        "parent": parent,
        "contour": contour,
        "sources": sorted(sources),
        "improvement": issue["type"]["display"] == "Улучшение",
        "estimation": tpr.to_hours(issue.get("estimation")),
    }


def group_of(row):
    """Чего от меня ждут. Порядок групп и есть порядок разбора."""
    if row["statusKey"] in REVIEW:
        return "review"
    if row["statusKey"] in ANSWER or (row["pendingOnMe"] and not row["mine"]):
        return "answer"
    if row["statusKey"] in ESTIMATE:
        return "estimate"
    if row["statusKey"] in DEV:
        return "dev"
    return "other"


GROUP_TITLES = [
    ("review",   "Ждут моего ревью"),
    ("answer",   "Ждут моего ответа"),
    ("dev",      "Работа на мне"),
    ("estimate", "Ждут оценки"),
    ("other",    "Прочее на мне"),
]


def sort_key(row):
    # Внутри группы: сперва то, что в релизе, затем по времени в статусе — залипшее
    # сверху. Дни могут быть None у задач без statusStartTime.
    return (not row["inRelease"], -(row["daysInStatus"] or 0))


def render(rows, args, release, delta, stale_pending):
    out = []
    today = datetime.now().strftime("%d.%m.%Y %H:%M")
    out.append(f"# Что на мне — снимок {today}")
    out.append("")
    improvements = [r for r in rows if r["improvement"]]
    main = [r for r in rows if not r["improvement"]]
    as_assignee = sum(1 for r in rows if r["mine"])
    as_reviewer = sum(1 for r in rows if "reviewer" in r["sources"] and not r["mine"])
    as_pending = sum(1 for r in rows if not r["mine"] and "reviewer" not in r["sources"])
    out.append(f"Исполнитель — {as_assignee}, ревью за мной в чужих задачах — {as_reviewer}, "
               f"ждут моего ответа в чужих задачах — {as_pending}. "
               f"Из всего этого улучшений — {len(improvements)}.")
    if release:
        in_release = sum(1 for r in main if r["inRelease"])
        out.append("")
        out.append(f"В охвате «{release['title']}» — задач: {in_release}.")
    if stale_pending:
        out.append("")
        out.append(f"Ещё {stale_pending} задач с флагом «ждут моего ответа» не трогали дольше "
                   f"{args.pending_days} дней — это забытые флаги, а не очередь; "
                   f"поднять их: `--pending-days 0`.")
    out.append("")

    if delta:
        out.append("## Изменилось с прошлого снимка")
        out.append("")
        out.append(f"Прошлый снимок: {delta['prev_date']}.")
        out.append("")
        for title, items in (("Ушло с меня", delta["gone"]), ("Пришло", delta["new"]),
                             ("Сменили статус", delta["moved"])):
            if items:
                out.append(f"- **{title}:** " + " · ".join(items))
        if not (delta["gone"] or delta["new"] or delta["moved"]):
            out.append("- Состав и статусы не изменились.")
        out.append("")

    for code, title in GROUP_TITLES:
        group = sorted([r for r in main if group_of(r) == code], key=sort_key)
        if not group:
            continue
        out.append(f"## {title} — {len(group)}")
        out.append("")
        if code == "answer":
            for contour_code, contour_title in [(c[0], c[1]) for c in CONTOURS] + [CONTOUR_DEFAULT]:
                part = [r for r in group if r["contour"] == contour_code]
                if not part:
                    continue
                out.append(f"### {contour_title}")
                out.append("")
                out.extend(table(part, release))
                out.append("")
        else:
            out.extend(table(group, release))
            out.append("")

    if improvements and not args.no_improvements:
        out.append(f"## Улучшения на мне — {len(improvements)}")
        out.append("")
        out.append("Отдельным блоком: в очередь дня не идут, но с меня не сняты.")
        out.append("")
        out.extend(table(sorted(improvements, key=sort_key), release))
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def table(rows, release):
    head = "| Задача | Статус | В статусе | Где | Тема |"
    sep = "|---|---|---|---|---|"
    lines = [head, sep]
    for r in rows:
        marks = []
        if not r["mine"]:
            marks.append(f"исп. {r['assignee'] or 'не назначен'}")
        if r["pendingOnMe"] and len(r["waitingFor"]) > 1:
            marks.append("ждут не только меня")
        if "reviewer" in r["sources"] and not r["mine"]:
            marks.append("я ревьювер")
        status = r["status"] + (" · " + ", ".join(marks) if marks else "")
        days = f"{r['daysInStatus']} дн" if r["daysInStatus"] is not None else "—"
        where = ", ".join(short_project(x) for x in r["sprints"]) if r["sprints"] \
            else (short_project(r["project"]) if r["project"] else "—")
        summary = r["summary"].replace("|", "\\|")
        if len(summary) > 80:
            summary = summary[:77] + "…"
        lines.append(f"| [{r['key']}]({r['url']}) | {status} | {days} | {where} | {summary} |")
    return lines


def compare(rows, previous):
    """Дельта к прошлому снимку: что ушло, что пришло, что сменило статус."""
    if not previous:
        return None
    prev_rows = {r["key"]: r for r in previous.get("tasks", [])}
    now_rows = {r["key"]: r for r in rows}
    gone = [f"{k} ({prev_rows[k]['status']})" for k in sorted(prev_rows.keys() - now_rows.keys())]
    new = [f"{k} ({now_rows[k]['status']})" for k in sorted(now_rows.keys() - prev_rows.keys())]
    moved = [f"{k}: {prev_rows[k]['status']} → {now_rows[k]['status']}"
             for k in sorted(prev_rows.keys() & now_rows.keys())
             if prev_rows[k]["status"] != now_rows[k]["status"]]
    return {"prev_date": previous.get("takenAt", "неизвестно"), "gone": gone, "new": new, "moved": moved}


def previous_snapshot(reports_dir):
    if not os.path.isdir(reports_dir):
        return None
    files = sorted(f for f in os.listdir(reports_dir)
                   if re.match(r"^moi-zadachi-\d{4}-\d{2}-\d{2}.*\.json$", f))
    if not files:
        return None
    with open(os.path.join(reports_dir, files[-1]), encoding="utf-8") as fh:
        return json.load(fh)


def main():
    parser = argparse.ArgumentParser(description="Что висит на мне — снимок личной очереди из Трекера")
    parser.add_argument("--who", help="логин в Трекере; по умолчанию владелец токена")
    parser.add_argument("--release", choices=sorted(RELEASES), help="пресет релиза для разреза «в релизе / вне»")
    parser.add_argument("--projects", nargs="+", metavar="ID|ЧАСТЬ_НАЗВАНИЯ",
                        help="свой набор проектов вместо пресета")
    parser.add_argument("--no-improvements", action="store_true", help="без блока улучшений")
    parser.add_argument("--pending-days", type=int, default=90, metavar="N",
                        help="флаг «ждут моего ответа» учитывать, если задачу трогали за N дней "
                             "(по умолчанию 90; 0 — брать все)")
    parser.add_argument("--format", choices=["md", "json"], default="md")
    parser.add_argument("--save", action="store_true", help="положить .md и .json в reports/")
    args = parser.parse_args()

    tpr.TOKEN, tpr.ORG = tpr.credentials()

    me, _ = api("myself")
    login = args.who or me.get("login")
    uid = me.get("uid") if not args.who else None
    if args.who:
        found, _ = api("users/" + args.who)
        uid = found.get("uid", found.get("id"))

    release = None
    projects = []
    if args.projects:
        release = {"title": "заданный набор проектов", "projects": args.projects}
    elif args.release:
        release = RELEASES[args.release]
    if release:
        projects, unknown = resolve_projects(release["projects"])
        if args.projects and projects:
            # Пресета нет — назовём охват тем, что реально нашлось, а не «набором»
            release["title"] = ", ".join(short_project(title) for _pid, title in projects)
        if unknown:
            print(f"Проекты не найдены: {', '.join(map(str, unknown))}", file=sys.stderr)
        if not projects:
            sys.exit(2)

    release_projects = {}
    for pid, title in projects:
        for key in project_issue_keys(pid):
            release_projects.setdefault(key, []).append(title)

    rows, stale_pending = collect(login, uid, release_projects, args.pending_days)

    # reports/ лежит в корне рабочего пространства, рядом с automation/
    reports_dir = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "reports"))
    delta = compare(rows, previous_snapshot(reports_dir))

    payload = {
        "takenAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "who": login,
        "release": release["title"] if release else None,
        "tasks": rows,
        "delta": delta,
        "stalePending": stale_pending,
    }

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(rows, args, release, delta, stale_pending))

    if args.save:
        stamp = datetime.now().strftime("%Y-%m-%d")
        os.makedirs(reports_dir, exist_ok=True)
        base = os.path.join(reports_dir, f"moi-zadachi-{stamp}")
        with open(base + ".md", "w", encoding="utf-8") as fh:
            fh.write(render(rows, args, release, delta, stale_pending))
        with open(base + ".json", "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"\nСохранено: {base}.md и {base}.json", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
