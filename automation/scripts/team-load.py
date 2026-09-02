#!/usr/bin/env python3
"""
Занятость и загрузка людей департамента — снимок из Трекера.

    team-load.py                             # БА и тестирование, последние 14 дней
    team-load.py --team all                  # весь департамент
    team-load.py --team qa dev-admin
    team-load.py --who vshuvaev mbardyuzhina
    team-load.py --from 2026-08-18 --to 2026-09-01
    team-load.py --days 30
    team-load.py --format json               # машиночитаемо — под команду /pbe-zagruzka
    team-load.py --save                      # → reports/ (папка вне git)

Отвечает на четыре вопроса по каждому человеку: **что делает сейчас**, **что стоит
в очереди на него**, **что он закрыл за период** и **сколько часов он списал против
нормы рабочего времени**. Плюс два вопроса по команде: **что придёт** (объём,
который уже виден в спринтах, но до людей ещё не дошёл) и **что ничьё** (задачи в
рабочих статусах команды без исполнителя из этой команды).

Это снимок фактов из Трекера, а не план. Суждение — «кто перегружен», «кому отдать
следующую задачу», отпуска и болезни — живёт в docs/company/team-load.md; скрипт его
не знает и знать не должен.

**Норма считается по календарю Пн–Пт**, без отпусков: отсутствия ведутся руками в
том же team-load.md. Поэтому «списал 40 из 80» может значить и просадку, и две
недели отпуска — цифра ставит вопрос, а не выносит приговор.

Списание относится к дате работы (`start`), не к дате внесения в Трекер, и период
считается по Москве — как в tracker-untagged.py.

Токен и ID организации — как в tracker-issue.sh: переменные окружения → Keychain
(yandex-tracker / yandex-tracker-org) → ~/.config/yandex-tracker/env.

Коды возврата: 2 — ошибка вызова, 3 — нет учётных данных.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta

# Общий код с отчётом по спринтам: учётные данные, HTTP, разбор ISO-8601, мнемоники
# клиентов. Импортом, а не копией — см. комментарий в tracker-untagged.py.
_SIBLING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker-project-report.py")
_spec = importlib.util.spec_from_file_location("tracker_project_report", _SIBLING)
tpr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tpr)

to_hours = tpr.to_hours
fmt_h = tpr.fmt_h

TZ = "+03:00"

# --- люди ------------------------------------------------------------------------
# Первоисточник состава — docs/company/org-structure.md. Здесь только те, чью
# загрузку смотрим, плюс логины: трекерный (по нему идёт поиск) и гитхабовский
# (по нему собирает github-activity.py). У БА и тестировщиков гитхаба нет — они
# в организацию powbee не входят, и это не пробел в справочнике, а факт.
PEOPLE = [
    # логин Трекера, ФИО, команда, роль, логин GitHub
    ("mponomarev",  "Пономарев Михаил",   "ba",        "БА",             None),
    ("rsemkov",     "Семков Родион",      "ba",        "БА",             None),
    ("mbardyuzhina", "Бардюжина Мария",   "qa",        "Тестирование",   None),
    ("vshuvaev",    "Шуваев Владислав",   "qa",        "Тестирование",   None),
    ("aseleznev",   "Селезнев Алексей",   "dev-admin", "Тимлид админок", "aseleznev-powerbee"),
    ("rvorobyev",   "Воробьев Роман",     "dev-admin", "Разработка",     "RusGosuNagib"),
    ("agavrilov",   "Гаврилов Андрей",    "dev-admin", "Разработка",     "Afterdar"),
    ("dklochkov",   "Клочков Денис",      "dev-admin", "Разработка",     "dusiakus"),
    ("spetrovicheva", "Ипполитова Светлана", "dev-app", "Тимлид прилаги", "spetrovicheva"),
    ("vznaida",     "Знайда Василий",     "dev-app",   "Разработка",     "vznaida"),
    ("pgaranzhina", "Гаранжина Полина",   "release",   "Релиз-менеджер", None),
    ("aperedelsky", "Передельский Алексей", "sec",     "ИБ",             None),
    ("omarkgraf",   "Маркграф Олег",      "data",      "Тимлид данных",  "omarkgraf"),
    ("dkorneev",    "Корнеев Дмитрий",    "data",      "Дата-инженер",   None),
    ("ytirtsov",    "Тырцов Ярослав",     "data",      "Дата-инженер",   None),
    ("iyakovlev",   "Яковлев Игорь",      "data",      "Дата-инженер",   None),
]

TEAMS = {
    "ba":        "Бизнес-анализ",
    "qa":        "Тестирование",
    "dev-admin": "Разработка. Админки",
    "dev-app":   "Разработка. Приложение",
    "release":   "Релиз-менеджмент",
    "sec":       "Информационная безопасность",
    "data":      "Дата-инженерия",
}

# По умолчанию — те две команды, ради которых снимок и заводился: у обеих нет
# руководителя, и планировать их некому. Остальные добираются флагом --team.
DEFAULT_TEAMS = ["ba", "qa"]

# --- статусы ---------------------------------------------------------------------
# Раскладка открытых статусов на три корзины. Тип статуса из API (`statusType`)
# для этого не годится: «Можно тестировать» и «Ревью» помечены как paused, хотя
# для тестировщика и ревьювера это ровно очередь работы, а не пауза.
#
# Новый статус в workflow надо дописать сюда, иначе он попадёт в «прочее» —
# корзину видно в выводе, молча статус не потеряется.
ACTIVE = {  # человек работает прямо сейчас
    "inProgress", "testing", "indevelopment", "inProgressBA", "preparation",
    "onvalidation", "firstSupportLine", "secondSupportLine", "asPlanned",
}
QUEUE = {  # работа ждёт этого человека — очередь на него
    "open", "new", "backlog", "selectedForDev", "readyfordevelopment", "forRevision",
    "readyForTest", "reviewReady", "forBA", "needEstimate", "devAwaiting",
    "releaseneeded", "deploytoprod", "deadline", "newGoal",
}
WAITING = {  # ждёт не его: согласование, клиент, чужой этап
    "needInfo", "onpause", "onHold", "blocked", "cisready", "confirmed",
    "approvaloftheBA", "approvaloftheAssessment", "kamapproval",
    "approvalbytheClient", "resultAcceptance", "needAcceptance",
    "demoToCustomer", "externalTest", "tested", "rc", "documentsPrepared",
    "withRisks", "blockedGoal", "localReady",
}
CLOSED = {"closed", "cancelled", "resolved", "achieved"}

# Очередь у разных ролей разная. Подзадача «Тестирование» в статусе «Готово к
# разработке» назначена на тестировщика ([этап 9](docs/regulations/crm-lifecycle/09-raspredelenie-zadach.md), 4.3.1),
# но взять её он не может: разработка ещё не сдана. Считать такие задачи очередью
# значит получить у тестировщика «42 задачи в очереди» и не увидеть за ними те
# четыре, которые действительно ждут его сегодня.
#
# Поэтому для тестирования очередь сужена, остальное уходит в корзину «в плане».
QUEUE_BY_TEAM = {
    # «Оценка задачи» здесь не случайно: по [этапу 5](docs/regulations/crm-lifecycle/05-ocenka-zadachi.md)
    # поле «Оценка: тестирование» заполняет команда тестирования, то есть подзадача
    # в этом статусе ждёт действия тестировщика, а не разработки. Именно эти задачи
    # и обнаруживаются незаполненными, когда на наборе спринта нечего суммировать.
    "qa": {"readyForTest", "forRevision", "reviewReady", "needEstimate"},
}

# Поля разбивки оценки — локальные поля очереди CRM. Фильтр по ним не строится
# (бэклог, К-29), но **прочитать** значение в теле задачи можно — на этом и стоит
# раздел «Что придёт».
FLD = "697bc5297d4f716731e75dc9--"
EST_TESTING = FLD + "estimationTesting"
EST_BA = FLD + "estimationBa"
EST_DEV = FLD + "estimationDevelopment"
EST_APPROVED = FLD + "approvedEstimation"
URGENCY_BA = FLD + "theUrgencyOfTheBa"

# Нерабочие дни РФ. Список неполный и обновляется руками раз в год — на нём стоит
# только норма часов, поэтому промах в один день не ломает отчёт, а сдвигает
# процент на 5 %. Проверять по производственному календарю при смене года.
HOLIDAYS = {
    "2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07",
    "2026-01-08", "2026-02-23", "2026-03-09", "2026-05-01", "2026-05-11",
    "2026-06-12", "2026-11-04", "2026-12-31",
}
WORKDAY_HOURS = 8

# Сколько дней в одном статусе считается залипанием. Не норматив, а порог для
# флага: задача, простоявшая две недели в «Можно тестировать», уже не в очереди,
# а забыта.
STUCK_DAYS = 14

# Горизонт для раздела «Ничьё». Задача в тестовом статусе, не тронутая полгода, —
# это уборка бэклога, а не планирование недели: она попадёт в разбор хвостов,
# а не в разговор о том, кто чем занят.
ORPHAN_FRESH_DAYS = 90


def bail(message):
    sys.exit(f"team-load.py: {message}")


# --- часы из консоли ------------------------------------------------------------
# Второй контур учёта. Трекер отвечает на вопрос «сколько человек списал», консоль
# добавляет «сколько он вообще был за компьютером» (StaffCop). Разница между двумя
# величинами и есть ответ на вопрос, который по одному Трекеру не решается: низкая
# цифра — это отпуск, работа мимо Трекера или реальная просадка.
#
# Складывать их нельзя, это разные величины: присутствие и списание.
CONSOLE_API = "https://console.powbeecrm.com/api/v1"


def console_token():
    token = os.environ.get("PBE_CONSOLE_API_TOKEN", "")
    if not token:
        try:
            out = subprocess.run(["security", "find-generic-password", "-s",
                                  "pbe-console-api", "-w"],
                                 capture_output=True, text=True, timeout=10)
            token = out.stdout.strip() if out.returncode == 0 else ""
        except Exception:
            token = ""
    return token


def console_get(path, params=None):
    """GET к API консоли. (данные, None) либо (None, причина) — падать нельзя:
    консоль второстепенна, отчёт должен собираться и без неё."""
    token = console_token()
    if not token:
        return None, ("токен консоли не найден: ни PBE_CONSOLE_API_TOKEN, "
                      "ни связка pbe-console-api в Keychain")
    url = CONSOLE_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url)
    req.add_header("X-Console-Token", token)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return None, f"консоль: нет прав на {path}"
        return None, f"консоль: HTTP {e.code} на {path}"
    except Exception as e:
        return None, f"консоль недоступна: {type(e).__name__}"


def release_windows():
    """Окна внутреннего тестирования по релизам приложения.

    Задачи прилаги выпускаются **скоупом**: подзадача «Тестирование» не уезжает на
    тест сама по себе, как в админке, — она ждёт, пока релиз клиента дойдёт до
    стадии «Внутренний QA». Поэтому для прилаги вопрос «когда придёт» решается не
    статусом соседней разработки, а стадией релиза клиента.

    Возвращает по одной строке на активный релиз: где он сейчас, когда по плану
    открывается окно теста и кто на нём стоит.
    """
    payload, err = console_get("/releases", {"limit": 100})
    if payload is None:
        return [], err

    people_by_id = {}
    hours, _ = console_get("/hours", {"period": "week"})
    if hours:
        for row in hours.get("data") or []:
            u = row.get("user") or {}
            people_by_id[u.get("id")] = u.get("name")

    out = []
    for rel in payload.get("data") or []:
        if (rel.get("status") or {}).get("code") != 1:   # только активные
            continue
        card, err = console_get(f"/releases/{rel['id']}")
        if card is None:
            continue
        qa = None
        for st in (card.get("data") or {}).get("stages") or []:
            if (st.get("stage") or {}).get("code") == 3:   # Внутренний QA
                qa = st
                break
        tracker = ((card.get("data") or {}).get("tracker") or {})
        out.append({
            "client": (rel.get("client") or {}).get("mnemonic") or "?",
            "title": rel.get("title") or "",
            "stage": (rel.get("stage") or {}).get("title") or "",
            "planned": rel.get("plannedReleaseDate"),
            "overdue": bool(rel.get("overdue")),
            "qa": qa,
            "qaWho": [people_by_id.get(i) or f"id {i}"
                      for i in ((qa or {}).get("responsibleIds") or [])],
            "tasks": tracker.get("tasksTotal"),
            "estHours": (tracker.get("hours") or {}).get("estHours"),
            "syncedAt": tracker.get("syncedAt"),
            "url": rel.get("trackerUrl"),
        })
    return out, None


def console_hours(start, end):
    """{логин Трекера: часы присутствия и списаний} либо (None, причина).

    Логин в консоли — рабочая почта, в Трекере — её локальная часть; по ней и
    сшиваем. Недоступность консоли не должна ронять отчёт: тогда возвращается
    причина, а норма считается по календарю, как раньше.
    """
    token = console_token()
    if not token:
        return None, ("токен консоли не найден: ни PBE_CONSOLE_API_TOKEN, "
                      "ни связка pbe-console-api в Keychain")
    url = (f"{CONSOLE_API}/hours?"
           + urllib.parse.urlencode({"date_from": start.isoformat(),
                                     "date_to": end.isoformat()}))
    req = urllib.request.Request(url)
    req.add_header("X-Console-Token", token)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        code = ""
        try:
            code = (json.loads(e.read().decode()).get("error") or {}).get("code", "")
        except Exception:
            pass
        if e.code == 403:
            return None, "консоль: нет права user.activity.view — часы недоступны"
        return None, f"консоль: HTTP {e.code} {code}".strip()
    except Exception as e:
        return None, f"консоль недоступна: {type(e).__name__}"

    hours = {}
    for row in payload.get("data") or []:
        login = ((row.get("user") or {}).get("login") or "").split("@")[0].lower()
        if not login:
            continue
        hours[login] = {
            "activity": row.get("activityHours") or 0.0,
            "worklog": row.get("worklogHours") or 0.0,
            "activityDays": row.get("activityDays") or 0,
            "worklogDays": row.get("worklogDays") or 0,
            "bound": bool(row.get("staffcopBound")),
        }
    meta = payload.get("meta") or {}
    return hours, meta.get("scope", {}).get("visibleEmployees")


# --- период ----------------------------------------------------------------------
def parse_period(args):
    today = date.today()
    if args.date_from or args.date_to:
        if not (args.date_from and args.date_to):
            bail("--from и --to задаются вместе")
        try:
            start = datetime.strptime(args.date_from, "%Y-%m-%d").date()
            end = datetime.strptime(args.date_to, "%Y-%m-%d").date()
        except ValueError:
            bail("даты в формате ГГГГ-ММ-ДД")
        if end < start:
            bail("--to раньше --from")
        return start, end
    return today - timedelta(days=args.days - 1), today


def workdays(start, end):
    """Рабочие дни периода: Пн–Пт минус список праздников."""
    days, cur = [], start
    while cur <= end:
        if cur.weekday() < 5 and cur.isoformat() not in HOLIDAYS:
            days.append(cur)
        cur += timedelta(days=1)
    return days


# --- выгрузка --------------------------------------------------------------------
def user_index():
    """uid → логин. В записи учёта времени автор приходит по uid, а не по логину."""
    users, _ = tpr.api("/users", params={"perPage": 500})
    return {str(u.get("uid")): (u.get("login") or "").lower() for u in users}


def open_issues(login):
    """Незакрытые задачи, где человек исполнитель."""
    query = (
        f'Assignee: "{login}" AND Status: !closed AND Status: !cancelled '
        f'AND Status: !resolved "Sort by": Updated DESC'
    )
    issues, page = [], 1
    while True:
        batch, _ = tpr.api("issues/_search", method="POST", body={"query": query},
                           params={"perPage": 100, "page": page})
        issues.extend(batch)
        if len(batch) < 100:
            return issues
        page += 1


def closed_in_period(login, start, end):
    """Задачи человека, закрытые внутри периода."""
    query = (
        f'Assignee: "{login}" AND Status: closed '
        f'AND Updated: >= "{start.isoformat()}" AND Updated: <= "{end.isoformat()}"'
    )
    issues, page = [], 1
    while True:
        batch, _ = tpr.api("issues/_search", method="POST", body={"query": query},
                           params={"perPage": 100, "page": page})
        issues.extend(batch)
        if len(batch) < 100:
            return issues
        page += 1


def worklogs(start, end):
    """Все списания периода по организации: фильтр по дате работы, не по дате внесения."""
    logs, page = [], 1
    body = {"start": {"from": f"{start.isoformat()}T00:00:00{TZ}",
                      "to": f"{end.isoformat()}T23:59:59{TZ}"}}
    while True:
        batch, _ = tpr.api("worklog/_search", method="POST", body=body,
                           params={"perPage": 1000, "page": page})
        logs.extend(batch)
        if len(batch) < 1000:
            return logs
        page += 1


def issues_by_key(keys):
    found = {}
    keys = list(keys)
    for i in range(0, len(keys), 50):
        chunk = keys[i:i + 50]
        batch, _ = tpr.api("issues/_search", method="POST",
                           body={"query": "Key: " + ", ".join(chunk)},
                           params={"perPage": 100})
        for issue in batch:
            found[issue["key"]] = issue
    return found


def sprint_projects():
    """Проекты-спринты с датами, разобранными из названия.

    Поле `start` у проектов Трекера пустое повсеместно, а `end` ставят как попало
    (у половины спринтов админки стоит конец квартала). Единственный надёжный
    источник дат — название по шаблону [этапа 8](docs/regulations/crm-lifecycle/08-nabor-sprintov.md),
    поэтому даты берём из него. Название не по шаблону — проект пропускается.
    """
    values, page = [], 1
    while True:
        data, _ = tpr.api("/entities/project/_search", method="POST", body={"filter": {}},
                          params={"perPage": 100, "page": page, "fields": "summary,start,end"})
        values.extend(data.get("values", []))
        if page >= data.get("pages", 1):
            break
        page += 1

    out = []
    for e in values:
        name = (e.get("fields") or {}).get("summary") or ""
        span = sprint_span(name)
        if span:
            out.append({"id": e.get("shortId"), "name": name, "from": span[0], "to": span[1]})
    return out


def sprint_span(name):
    """Границы спринта из названия. None — если название не по шаблону этапа 8."""
    m = re.search(r"Спринт Админка\s+\d+\s+(\d{2}\.\d{2}\.\d{4})-(\d{2}\.\d{2}\.\d{4})", name)
    if m:
        return (datetime.strptime(m.group(1), "%d.%m.%Y").date(),
                datetime.strptime(m.group(2), "%d.%m.%Y").date())
    m = re.search(r"Спринт Аналитика\s+(\d{2}\.\d{2}\.\d{2})\s*-\s*(\d{2}\.\d{2}\.\d{2})", name)
    if m:
        return (datetime.strptime(m.group(1), "%d.%m.%y").date(),
                datetime.strptime(m.group(2), "%d.%m.%y").date())
    m = re.search(r"Спринт Прилага\s+(\d{2})\s+(\d{4})", name)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))
        return date(year, month, 1), last
    return None


# --- разбор ----------------------------------------------------------------------
def bucket(issue, team=None):
    """active — делает сейчас, queue — ждёт его сегодня, planned — назначено, но
    работа ещё не пришла, waiting — ждёт не его, closed — закрыто."""
    key = (issue.get("status") or {}).get("key") or ""
    if key in ACTIVE:
        return "active"
    if key in QUEUE:
        narrow = QUEUE_BY_TEAM.get(team)
        return "queue" if (narrow is None or key in narrow) else "planned"
    if key in WAITING:
        return "waiting"
    if key in CLOSED:
        return "closed"
    return "other"


def days_in_status(issue):
    ts = issue.get("statusStartTime") or issue.get("updatedAt")
    if not ts:
        return None
    try:
        return (datetime.now(tpr.parse_ts(ts).tzinfo) - tpr.parse_ts(ts)).days
    except Exception:
        return None


def client_of(issue):
    """Мнемоника клиента. Знак «?» — опознана по теме, а не по тегу: по регламенту
    очередей тег обязателен, и его отсутствие само по себе нарушение."""
    code, source = tpr.client_of(issue)
    if not code:
        return ""
    return code if source == "tag" else f"{code}?"


def project_of(issue):
    p = issue.get("project")
    if isinstance(p, dict):
        return p.get("display") or ""
    return ""


def estimate_hours(issue, field):
    return to_hours(issue.get(field))


def collect(people, start, end, console=None):
    """Снимок по каждому человеку + сырьё для командных разделов."""
    uid_login = user_index()
    console = console or {}

    # Часы за период — одним запросом на всех, дальше раскладываем по авторам.
    spent_hours = defaultdict(float)              # логин → часы
    spent_days = defaultdict(set)                 # логин → даты со списаниями
    spent_by_issue = defaultdict(lambda: defaultdict(float))  # логин → ключ → часы
    for w in worklogs(start, end):
        hours = to_hours(w.get("duration")) or 0
        if hours <= 0:
            continue
        login = uid_login.get(str((w.get("createdBy") or {}).get("id")), "")
        if not login:
            continue
        day = (w.get("start") or "")[:10]
        spent_hours[login] += hours
        spent_days[login].add(day)
        spent_by_issue[login][(w.get("issue") or {}).get("key") or "?"] += hours

    norm = len(workdays(start, end)) * WORKDAY_HOURS
    snapshot = []
    for login, name, team, role, gh in people:
        issues = open_issues(login)
        by_bucket = defaultdict(list)
        for i in issues:
            by_bucket[bucket(i, team)].append(i)

        closed = closed_in_period(login, start, end)
        mine = set(i["key"] for i in issues) | set(i["key"] for i in closed)
        foreign = {k: h for k, h in spent_by_issue[login].items() if k not in mine}

        presence = console.get(login)
        snapshot.append({
            "login": login, "name": name, "team": team, "role": role, "github": gh,
            "presence": presence,
            "active": by_bucket["active"], "queue": by_bucket["queue"],
            "planned": by_bucket["planned"], "waiting": by_bucket["waiting"],
            "other": by_bucket["other"],
            "closed": closed,
            "hours": round(spent_hours[login], 1),
            "norm": norm,
            "days_logged": len(spent_days[login]),
            "issues_logged": len(spent_by_issue[login]),
            "foreign": foreign,
        })
    return snapshot, norm, uid_login


def pipeline_qa(today):
    """Что придёт тестированию: объём активных спринтов разработки.

    Подзадачи «Тестирование» в спринт разработки не набираются ([этап 8](docs/regulations/crm-lifecycle/08-nabor-sprintov.md), 4.3),
    поэтому объём теста в самом спринте не лежит. Он лежит в поле «Оценка:
    тестирование» **основной** задачи — её и поднимаем по родителю каждой
    подзадачи спринта. Поле локальное и в язык запросов не входит (бэклог, К-29),
    но читается в теле задачи.

    Незаполненное поле — не ноль, а дыра в оценке: такие задачи считаются
    отдельно и печатаются числом, иначе ёмкость выглядит меньше, чем она есть.
    """
    active = [s for s in sprint_projects()
              if s["from"] <= today <= s["to"] and "Аналитика" not in s["name"]]
    out = []
    for sprint in active:
        issues = tpr.project_issues(sprint["id"])
        parents = {}
        for issue in issues:
            parent = issue.get("parent")
            key = parent.get("key") if isinstance(parent, dict) else None
            parents.setdefault(key or issue["key"], None)
        heads = issues_by_key(list(parents))

        rows, total, done, blind = [], 0.0, 0.0, 0
        for key, issue in heads.items():
            est = estimate_hours(issue, EST_TESTING)
            if not est:
                blind += 1
                continue
            state = bucket(issue)
            total += est
            if state == "closed" or (issue.get("status") or {}).get("key") in {"tested", "rc"}:
                done += est
            rows.append({
                "key": key, "summary": issue.get("summary") or "",
                "status": (issue.get("status") or {}).get("display") or "",
                "client": client_of(issue), "est": est, "bucket": state,
            })
        out.append({"sprint": sprint, "rows": rows, "total": total,
                    "done": done, "blind": blind, "issues": len(issues)})
    return out


# Статусы подзадачи «Разработка», по которым видно, скоро ли работа придёт на тест.
DEV_SOON = {"inReview", "reviewReady", "localReady"}
DEV_NOW = {"inProgress", "indevelopment"}
DEV_DONE = {"closed", "resolved", "cancelled", "rc", "releaseneeded", "deploytoprod"}


def forecast(issues):
    """Когда «в плане» превратится в очередь: по состоянию соседней «Разработки».

    Подзадача «Тестирование» стоит в «Готово к разработке» ровно до тех пор, пока
    разработчик (или тимлид) не переведёт **свою** подзадачу в «Можно тестировать».
    Значит предсказать приход можно только одним способом — посмотреть, что сейчас
    с подзадачей «Разработка» того же родителя.

    Отдельно считается случай «у разработки нет исполнителя»: такая задача не
    придёт на тест никогда, она просто не начата. В плане тестировщика она висит,
    в плане разработки её нет — и это не задержка, а незакрытый [этап 9](docs/regulations/crm-lifecycle/09-raspredelenie-zadach.md).
    """
    parents = {}
    for i in issues:
        p = i.get("parent")
        if isinstance(p, dict):
            parents.setdefault(p["key"], []).append(i["key"])

    sibling_keys, links = set(), {}
    for pkey in parents:
        # Фильтра `Parent:` в языке запросов нет — состав дерева читается только
        # через связи задачи.
        data, _ = tpr.api(f"issues/{pkey}/links")
        kids = [l["object"]["key"] for l in data
                if (l.get("type") or {}).get("id") == "subtask" and l.get("direction") == "outward"]
        links[pkey] = kids
        sibling_keys.update(kids)
    siblings = issues_by_key(sibling_keys)

    out = {}
    for pkey, mykeys in parents.items():
        devs = []
        for k in links.get(pkey, []):
            sib = siblings.get(k)
            if not sib or k in mykeys:
                continue
            is_dev = ("азработк" in (sib.get("summary") or "")
                      or (sib.get("type") or {}).get("key") == "development")
            if is_dev:
                devs.append(sib)
        live = [d for d in devs if (d.get("status") or {}).get("key") not in DEV_DONE]
        for mk in mykeys:
            if not devs:
                out[mk] = ("нет подзадачи «Разработка»", [])
            elif not live:
                out[mk] = ("разработка закрыта — почему не на тесте?", devs)
            elif any((d.get("status") or {}).get("key") in DEV_SOON for d in live):
                out[mk] = ("разработка на ревью — придёт со дня на день", live)
            elif any((d.get("status") or {}).get("key") in DEV_NOW for d in live):
                out[mk] = ("разработка в работе", live)
            elif all(not (d.get("assignee") or {}).get("id") for d in live):
                out[mk] = ("у разработки нет исполнителя — не придёт", live)
            else:
                out[mk] = ("разработка не начата", live)
    return out


def assignment_gaps():
    """Разработчик назначен, тестировщик — нет: рассинхрон [этапа 9](docs/regulations/crm-lifecycle/09-raspredelenie-zadach.md) (4.3.1).

    Обратная сторона правила «нет разработчика — нет и тестировщика». Пока
    тестировщик снимается с нераспределённых задач, кто-то должен ловить обратный
    случай: задачу распределили, разработчика назначили, а тестировщика забыли.
    Иначе работа доедет до «Можно тестировать» и станет ничьей — ровно так и
    накопился раздел «Ничьё».

    Считаются только задачи, где разработка уже на ком-то: подзадача без
    разработчика — это не пробел в назначении, а неначатая работа, и она разбирается
    в другом месте.
    """
    fresh = (date.today() - timedelta(days=ORPHAN_FRESH_DAYS)).isoformat()
    query = ('Queue: CRM AND Type: "Тестирование" AND Assignee: empty() '
             'AND Resolution: empty() '
             f'AND Updated: >= "{fresh}" "Sort by": Updated ASC')
    issues, page = [], 1
    while True:
        batch, _ = tpr.api("issues/_search", method="POST", body={"query": query},
                           params={"perPage": 100, "page": page})
        issues.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    plans = forecast(issues)
    gaps = []
    for issue in issues:
        label, devs = plans.get(issue["key"], ("", []))
        named = [d for d in devs if (d.get("assignee") or {}).get("id")]
        if named:
            gaps.append((issue, named))
    return gaps


def orphan_qa(uid_login):
    """«Ничьё»: задачи в тестовых статусах, где исполнитель — не тестировщик.

    Ровно та дыра, из-за которой работа уезжает мимо плана: задача переведена в
    «Можно тестировать», а числится на менеджере клиента с [этапа 5](docs/regulations/crm-lifecycle/05-ocenka-zadachi.md)
    или на разработчике. Формально она ждёт тестирования, фактически — никого.
    """
    qa_logins = {p[0] for p in PEOPLE if p[2] == "qa"}
    # Очереди, которые обслуживает тестирование: доработки CRM, внутренний контур
    # качества и правки прода ([tracker-queues.md](docs/regulations/tracker-queues.md)).
    # Закрытые клиентские очереди и очереди чужих команд сюда не входят — иначе в
    # отчёт лезут задачи 2025 года из ANALYTIC и ADMINISTRATION, к планированию
    # тестирования отношения не имеющие.
    fresh = (date.today() - timedelta(days=ORPHAN_FRESH_DAYS)).isoformat()
    query = ('Queue: CRM, BUGREPORTS, SUPPORTDEV AND '
             'Status: "Можно тестировать", "Тестируется" AND Resolution: empty() '
             f'AND Updated: >= "{fresh}" "Sort by": Updated ASC')
    issues, page = [], 1
    while True:
        batch, _ = tpr.api("issues/_search", method="POST", body={"query": query},
                           params={"perPage": 100, "page": page})
        issues.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    orphan = []
    for i in issues:
        uid = str((i.get("assignee") or {}).get("id") or "")
        login = uid_login.get(uid, "")
        if login not in qa_logins:
            orphan.append((i, (i.get("assignee") or {}).get("display") or "не назначен"))
    return orphan


# --- вывод -----------------------------------------------------------------------
def issue_line(issue, extra_hours=None):
    key = issue["key"]
    status = (issue.get("status") or {}).get("display") or ""
    days = days_in_status(issue)
    age = f"{days} дн." if days is not None else "—"
    # Поля разбивки стоят на основной задаче, а в таблице чаще подзадача —
    # поэтому сначала своя оценка подзадачи, потом разбивка.
    est = (to_hours(issue.get("estimation")) or to_hours(issue.get("originalEstimation"))
           or estimate_hours(issue, EST_TESTING) or estimate_hours(issue, EST_APPROVED))
    est_s = fmt_h(est) if est else "—"
    flag = " ⚠️" if days is not None and days >= STUCK_DAYS else ""
    proj = project_of(issue) or "—"
    return f"| {key} | {status}{flag} | {age} | {proj} | {est_s} | {(issue.get('summary') or '')[:70]} |"


TABLE_HEAD = ("| Задача | Статус | В статусе | Проект | Оценка | Тема |\n"
              "|---|---|---|---|---|---|")
# Та же таблица плюс колонка «Разработка»: чья подзадача и в каком она статусе.
# Именно она и определяет, когда работа придёт на тест.
TABLE_HEAD_DEV = ("| Задача | Статус | В статусе | Проект | Оценка | Тема | Разработка |\n"
                  "|---|---|---|---|---|---|---|")


def print_markdown(snapshot, norm, start, end, teams, pipeline, orphans, plans=None,
                   console_note=None, releases=None, gaps=None, out=sys.stdout):
    w = out.write
    w(f"# Занятость и загрузка — снимок на {date.today().isoformat()}\n\n")
    w(f"Период: **{start.isoformat()} — {end.isoformat()}** "
      f"({len(workdays(start, end))} рабочих дней, календарная норма {norm:.0f} ч).\n")
    w(f"Команды: {', '.join(TEAMS.get(t, t) for t in teams)}.\n\n")
    if console_note:
        w(f"> **Часы только из Трекера:** {console_note}.\n"
          "> Норма поэтому календарная, без учёта отпусков и болезней — отсутствия\n"
          "> ведутся руками в [team-load.md](../docs/company/team-load.md).\n\n")
    else:
        w("> Присутствие берётся из консоли (StaffCop), списания — из Трекера.\n"
          "> Отпуска и больничные учитывать отдельно не нужно: в присутствии их\n"
          "> просто нет.\n\n")

    for team in teams:
        members = [s for s in snapshot if s["team"] == team]
        if not members:
            continue
        w(f"## {TEAMS.get(team, team)}\n\n")
        has_presence = any(m.get("presence") for m in members)
        if has_presence:
            w("| Человек | Присутствие | Списано | Доля | Дней | В работе | Ждёт его | "
              "В плане | Ждёт не его | Закрыл |\n")
            w("|---|---|---|---|---|---|---|---|---|---|\n")
        else:
            w("| Человек | Списано / норма | Дней со списаниями | В работе | Ждёт его | "
              "В плане | Ждёт не его | Закрыл |\n")
            w("|---|---|---|---|---|---|---|---|\n")
        for s in members:
            p = s.get("presence")
            if has_presence:
                if p and p["activity"]:
                    pres = f"{fmt_h(p['activity'])} ч / {p['activityDays']} дн."
                    share = f"{100 * s['hours'] / p['activity']:.0f} %"
                elif p and p["bound"]:
                    pres, share = "**нет данных**", "—"
                else:
                    pres, share = "не подключён", "—"
                w(f"| {s['name']} | {pres} | {fmt_h(s['hours'])} ч | {share} | "
                  f"{s['days_logged']} | {load_cell(s['active'])} | {load_cell(s['queue'])} | "
                  f"{load_cell(s['planned'])} | {len(s['waiting'])} | {len(s['closed'])} |\n")
            else:
                pct = f"{100 * s['hours'] / s['norm']:.0f} %" if s["norm"] else "—"
                w(f"| {s['name']} | {fmt_h(s['hours'])} / {s['norm']:.0f} ч ({pct}) | "
                  f"{s['days_logged']} | {load_cell(s['active'])} | {load_cell(s['queue'])} | "
                  f"{load_cell(s['planned'])} | {len(s['waiting'])} | {len(s['closed'])} |\n")
        w("\n")
        if has_presence:
            w("**Присутствие** — время за компьютером по StaffCop, **списано** — часы\n"
              "в Трекере. Это разные величины, складывать их нельзя; «доля» — сколько\n"
              "присутствия дошло до Трекера. Присутствие уже учитывает отпуска и\n"
              "выходные, поэтому сверять с календарной нормой (%s ч) незачем.\n\n" % f"{norm:.0f}")
        w("Формат ячеек загрузки — «задач / часов оценки»; `+N?` — столько задач без\n"
          "оценки, их объём в сумму не вошёл.\n\n")

        for s in members:
            w(f"### {s['name']} · `{s['login']}` · {s['role']}\n\n")

            if s["active"]:
                w(f"**Делает сейчас — {len(s['active'])}**\n\n{TABLE_HEAD}\n")
                for i in s["active"]:
                    w(issue_line(i) + "\n")
                w("\n")
            else:
                w("**Делает сейчас** — ни одной задачи в рабочем статусе.\n\n")

            if s["queue"]:
                w(f"**Ждёт его прямо сейчас — {len(s['queue'])}**\n\n{TABLE_HEAD}\n")
                for i in sorted(s["queue"], key=lambda x: -(days_in_status(x) or 0)):
                    w(issue_line(i) + "\n")
                w("\n")
            elif s["planned"]:
                w("**Ждёт его прямо сейчас** — ничего: вся очередь ещё не дошла до него.\n\n")

            if s["planned"]:
                w(f"**В плане, работа ещё не пришла — {len(s['planned'])}**\n\n")
                w("Назначено на него, но статус не позволяет взять: у тестировщика это\n"
                  "подзадачи «Тестирование», ждущие, пока разработчик или тимлид переведёт\n"
                  "свою подзадачу в «Можно тестировать».\n\n")
                mine_plans = {k: v for k, v in (plans or {}).items()
                              if k in {i["key"] for i in s["planned"]}}
                if mine_plans:
                    groups = defaultdict(list)
                    for i in s["planned"]:
                        groups[mine_plans.get(i["key"], ("прогноз не построен", []))[0]].append(i)
                    order = ["разработка на ревью — придёт со дня на день",
                             "разработка в работе",
                             "разработка закрыта — почему не на тесте?",
                             "разработка не начата",
                             "у разработки нет исполнителя — не придёт",
                             "нет подзадачи «Разработка»"]
                    for label in order + [g for g in groups if g not in order]:
                        rows = groups.get(label)
                        if not rows:
                            continue
                        total, blind = est_sum(rows)
                        w(f"*{label} — {len(rows)} задач, {fmt_h(total)} ч*"
                          + (f" (+{blind} без оценки)" if blind else "") + "\n\n")
                        w(f"{TABLE_HEAD_DEV}\n")
                        for i in sorted(rows, key=lambda x: -(days_in_status(x) or 0)):
                            devs = mine_plans.get(i["key"], ("", []))[1]
                            who = ", ".join(
                                f"{d['key']} {(d.get('status') or {}).get('display')}"
                                f" ({(d.get('assignee') or {}).get('display') or 'без исполнителя'})"
                                for d in devs[:3]) or "—"
                            w(issue_line(i) + f" {who} |\n")
                        w("\n")
                else:
                    w(f"{TABLE_HEAD}\n")
                    for i in sorted(s["planned"], key=lambda x: -(days_in_status(x) or 0))[:25]:
                        w(issue_line(i) + "\n")
                    if len(s["planned"]) > 25:
                        w(f"\n…и ещё {len(s['planned']) - 25}.\n")
                    w("\n")

            if s["waiting"]:
                keys = ", ".join(i["key"] for i in s["waiting"])
                w(f"**Ждёт не его — {len(s['waiting'])}:** {keys}\n\n")

            if s["other"]:
                keys = ", ".join(f"{i['key']} ({(i.get('status') or {}).get('display')})"
                                 for i in s["other"])
                w(f"**Статус не разложен по корзинам — {len(s['other'])}:** {keys}\n"
                  f"> Дописать ключ статуса в ACTIVE / QUEUE / WAITING внутри скрипта.\n\n")

            if s["closed"]:
                keys = ", ".join(i["key"] for i in s["closed"][:20])
                more = f" и ещё {len(s['closed']) - 20}" if len(s["closed"]) > 20 else ""
                w(f"**Закрыл за период — {len(s['closed'])}:** {keys}{more}\n\n")

            if s["foreign"]:
                rows = ", ".join(f"{k} ({fmt_h(v)})" for k, v in
                                 sorted(s["foreign"].items(), key=lambda kv: -kv[1])[:10])
                w(f"**Списывал в чужие задачи:** {rows}\n"
                  f"> Работа идёт мимо назначения — либо помогает, либо задача числится не на том.\n\n")

            flags = red_flags(s, start, end)
            if flags:
                w("**Флаги**\n\n")
                for f in flags:
                    w(f"- {f}\n")
                w("\n")

    if pipeline:
        w("## Что придёт тестированию\n\n")
        w("Объём активных спринтов по полю «Оценка: тестирование» основной задачи.\n"
          "Поле локальное и в фильтры Трекера не попадает — читается из тела задачи.\n\n")
        for p in pipeline:
            s = p["sprint"]
            left = p["total"] - p["done"]
            w(f"### {s['name']} · {s['from'].isoformat()} — {s['to'].isoformat()}\n\n")
            w(f"Подзадач в спринте: {p['issues']}. Основных задач с проставленной\n"
              f"«Оценкой: тестирование» — **{len(p['rows'])}**, без неё — **{p['blind']}**.\n\n")
            w(f"Набрано теста: **{fmt_h(p['total'])}**, из них пройдено {fmt_h(p['done'])}, "
              f"осталось **{fmt_h(left)}**.\n")
            if p["blind"]:
                w(f"Цифра — оценка снизу: по {p['blind']} задачам поле не заполнено, "
                  f"их объём в неё не вошёл.\n")
            w("\n")
            if p["rows"]:
                w("| Задача | Клиент | Статус | Оценка теста | Тема |\n|---|---|---|---|---|\n")
                for r in sorted(p["rows"], key=lambda x: -x["est"]):
                    w(f"| {r['key']} | {r['client'] or '—'} | {r['status']} | "
                      f"{fmt_h(r['est'])} | {r['summary'][:60]} |\n")
                w("\n")

    if releases is not None:
        w("## Релизы приложения: когда открывается окно теста\n\n")
        w("Задачи прилаги выпускаются **скоупом**: подзадача «Тестирование» не уезжает\n"
          "на тест сама, как в админке, — она ждёт, пока релиз клиента дойдёт до стадии\n"
          "«Внутренний QA». Поэтому «когда придёт» для прилаги читается отсюда, а не по\n"
          "статусу соседней разработки.\n\n")
        if not releases:
            w("Активных релизов приложения нет либо консоль их не отдала.\n\n")
        else:
            w("| Клиент | Стадия сейчас | Окно внутреннего QA | Кто на QA | Релиз | Задач | Оценка |\n")
            w("|---|---|---|---|---|---|---|\n")
            for r in releases:
                qa = r["qa"] or {}
                qa_status = (qa.get("status") or {}).get("title") or "—"
                start_p, end_p = qa.get("plannedStart"), qa.get("plannedEnd")
                if start_p and end_p:
                    window = f"{start_p} — {end_p} ({qa_status.lower()})"
                    if qa_status == "Не начата" and end_p < date.today().isoformat():
                        window += " ⚠️ **просрочено**"
                else:
                    window = "⚠️ **даты не заданы**"
                planned = r["planned"] or "⚠️ **не задана**"
                if r["overdue"]:
                    planned += " ⚠️"
                w(f"| {r['client']} | {r['stage']} | {window} | "
                  f"{', '.join(r['qaWho']) or '⚠️ **не назначен**'} | {planned} | "
                  f"{r['tasks'] if r['tasks'] is not None else '—'} | "
                  f"{fmt_h(r['estHours']) + ' ч' if r['estHours'] else '⚠️ **нет оценок**'} |\n")
            w("\n")
            blind = [r["client"] for r in releases if not r["planned"]]
            if blind:
                w(f"**Плановая дата релиза не задана:** {', '.join(blind)}. По этим клиентам\n"
                  f"окно теста не предсказывается вовсе — объём придёт без предупреждения.\n\n")
            no_window = [r["client"] for r in releases
                         if not ((r["qa"] or {}).get("plannedStart")
                                 and (r["qa"] or {}).get("plannedEnd"))]
            if no_window:
                w(f"**Окно внутреннего QA не запланировано:** {', '.join(no_window)}.\n\n")
            synced = {r["syncedAt"] for r in releases if r["syncedAt"]}
            if synced:
                w(f"Состав и часы релизов — снимок синхронизации с Трекером "
                  f"({min(synced)} — {max(synced)}), а не живой запрос.\n\n")

    if gaps:
        w("## Разработчик назначен, тестировщик — нет\n\n")
        w(f"Задач — **{len(gaps)}**. По [этапу 9](../docs/regulations/crm-lifecycle/09-raspredelenie-zadach.md)\n"
          "(4.3.1) тимлид назначает исполнителей подзадач «Разработка» и «Тестирование»\n"
          "одной сессией. Здесь первое сделано, второе — нет: работа доедет до «Можно\n"
          "тестировать» и станет ничьей.\n\n")
        w("| Тестирование | Статус | В статусе | Клиент | Разработка | Тема |\n")
        w("|---|---|---|---|---|---|\n")
        for issue, devs in sorted(gaps, key=lambda g: -(days_in_status(g[0]) or 0)):
            who = ", ".join(f"{d['key']} — {(d.get('assignee') or {}).get('display')}"
                            for d in devs[:2])
            w(f"| {issue['key']} | {(issue.get('status') or {}).get('display')} | "
              f"{days_in_status(issue)} дн. | {client_of(issue) or '—'} | {who} | "
              f"{(issue.get('summary') or '')[:55]} |\n")
        w("\n")

    if orphans:
        w("## Ничьё: ждёт теста, но числится не на тестировщике\n\n")
        w(f"Задач — **{len(orphans)}** (очереди CRM, BUGREPORTS, SUPPORTDEV, тронутые\n"
          f"за последние {ORPHAN_FRESH_DAYS} дней). Формально они в очереди на тест, фактически\n"
          f"исполнитель другой: [этап 9](../docs/regulations/crm-lifecycle/09-raspredelenie-zadach.md)\n"
          f"(4.3.1) требует назначить тестировщика, и без этого шага задача так и\n"
          f"остаётся на менеджере клиента с этапа 5.\n\n")
        w("| Задача | Статус | В статусе | Числится на | Проект | Тема |\n|---|---|---|---|---|---|\n")
        for i, who in orphans:
            days = days_in_status(i)
            w(f"| {i['key']} | {(i.get('status') or {}).get('display')} | "
              f"{days if days is not None else '—'} дн. | {who} | "
              f"{project_of(i) or '—'} | {(i.get('summary') or '')[:60]} |\n")
        w("\n")


def est_sum(issues):
    """Сумма оценок пачки задач и число задач без оценки.

    Считать «в очереди 39 задач» бессмысленно: там и правка опечатки на 15 минут,
    и постановщик задач на 16 часов. Планировать можно только часы — поэтому рядом
    с числом задач всегда идёт объём и признание того, скольких оценок не хватает.
    """
    total, blind = 0.0, 0
    for i in issues:
        h = (to_hours(i.get("estimation")) or to_hours(i.get("originalEstimation"))
             or estimate_hours(i, EST_TESTING))
        if h:
            total += h
        else:
            blind += 1
    return total, blind


def load_cell(issues):
    total, blind = est_sum(issues)
    if not issues:
        return "—"
    mark = f" +{blind}?" if blind else ""
    return f"{len(issues)} / {fmt_h(total)}{mark}"


def red_flags(s, start, end):
    out = []
    p = s.get("presence")
    if p and p["bound"] and not p["activity"] and s["hours"]:
        out.append(f"StaffCop не отдал ни одной минуты за период, хотя учётка привязана, "
                   f"а в Трекер списано {fmt_h(s['hours'])} ч. Значит агент не собирает — "
                   f"второго контура по человеку нет, и судить о его загрузке не по чему.")
    elif p and p["activity"]:
        share = s["hours"] / p["activity"]
        if share < 0.5:
            out.append(f"Присутствие {fmt_h(p['activity'])} ч за {p['activityDays']} дн., "
                       f"списано {fmt_h(s['hours'])} ч — {100 * share:.0f} %. "
                       f"Больше половины работы не доходит до Трекера.")
        if p["activityDays"] < len(workdays(start, end)) - 1:
            out.append(f"Присутствовал {p['activityDays']} дн. из "
                       f"{len(workdays(start, end))} рабочих — отпуск, болезнь или отгулы. "
                       f"Отметить в разделе «Отсутствия».")
    elif s["norm"] and s["hours"] / s["norm"] < 0.5:
        out.append(f"Списано {fmt_h(s['hours'])} из {s['norm']:.0f} ч календарной нормы — "
                   f"меньше половины. Присутствия из консоли нет, поэтому отличить "
                   f"отпуск от просадки нечем.")
    if not s["active"] and s["queue"]:
        out.append(f"Ни одной задачи в работе при очереди из {len(s['queue'])} — "
                   f"либо не переводит статусы, либо занят вне Трекера.")
    if len(s["planned"]) >= 25:
        out.append(f"{len(s['planned'])} задач назначено «в план» — это не очередь "
                   f"сегодняшнего дня, но и не ноль: столько придёт, когда разработка сдаст.")
    if len(s["active"]) > 4:
        out.append(f"{len(s['active'])} задач одновременно в рабочем статусе — "
                   f"параллелизм вместо очереди.")
    stuck = [i for i in s["active"] + s["queue"]
             if (days_in_status(i) or 0) >= STUCK_DAYS]  # план сюда не берём: там ждут не его
    if stuck:
        keys = ", ".join(f"{i['key']} ({days_in_status(i)} дн.)" for i in stuck[:8])
        out.append(f"Залипло в статусе дольше {STUCK_DAYS} дней: {keys}")
    no_log = [i for i in s["active"] if not (i.get("spent"))]
    if no_log:
        out.append(f"В работе без единого списания: "
                   f"{', '.join(i['key'] for i in no_log[:8])}")
    return out


def print_json(snapshot, norm, start, end, pipeline, orphans, plans=None,
               console_note=None, releases=None, gaps=None, out=sys.stdout):
    def slim(i):
        return {
            "key": i["key"], "summary": i.get("summary"),
            "status": (i.get("status") or {}).get("display"),
            "statusKey": (i.get("status") or {}).get("key"),
            "days": days_in_status(i), "project": project_of(i),
            "client": client_of(i),
            "estTesting": estimate_hours(i, EST_TESTING),
            "estApproved": estimate_hours(i, EST_APPROVED),
        }

    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "period": {"from": start.isoformat(), "to": end.isoformat(),
                   "workdays": len(workdays(start, end)), "norm": norm},
        "consoleNote": console_note,
        "releaseWindows": releases,
        "assignmentGaps": [{
            "key": i["key"], "summary": i.get("summary"),
            "status": (i.get("status") or {}).get("display"),
            "days": days_in_status(i), "client": client_of(i),
            "devs": [{"key": d["key"], "assignee": (d.get("assignee") or {}).get("display"),
                      "status": (d.get("status") or {}).get("display")} for d in devs],
        } for i, devs in (gaps or [])],
        "people": [{
            "login": s["login"], "name": s["name"], "team": s["team"], "role": s["role"],
            "hours": s["hours"], "norm": s["norm"], "daysLogged": s["days_logged"],
            "presence": s.get("presence"),
            "active": [slim(i) for i in s["active"]],
            "queue": [slim(i) for i in s["queue"]],
            "planned": [dict(slim(i), forecast=(plans or {}).get(i["key"], ("", []))[0] or None)
                        for i in s["planned"]],
            "waiting": [i["key"] for i in s["waiting"]],
            "closed": [i["key"] for i in s["closed"]],
            "foreign": s["foreign"],
            "flags": red_flags(s, start, end),
        } for s in snapshot],
        "pipeline": [{
            "sprint": p["sprint"]["name"],
            "from": p["sprint"]["from"].isoformat(), "to": p["sprint"]["to"].isoformat(),
            "total": p["total"], "done": p["done"], "blind": p["blind"],
            "rows": p["rows"],
        } for p in pipeline],
        "orphans": [{
            "key": i["key"], "summary": i.get("summary"),
            "status": (i.get("status") or {}).get("display"),
            "days": days_in_status(i), "assignee": who, "project": project_of(i),
        } for i, who in orphans],
    }
    json.dump(payload, out, ensure_ascii=False, indent=2)
    out.write("\n")


def print_csv(snapshot, out=sys.stdout):
    import csv
    wr = csv.writer(out)
    wr.writerow(["Человек", "Логин", "Команда", "Роль", "Присутствие", "Списано", "Доля",
                 "Норма", "Дней со списаниями", "В работе", "Ждёт его", "В плане",
                 "Ждёт не его", "Закрыл"])
    for s in snapshot:
        p = s.get("presence") or {}
        act = p.get("activity")
        share = f"{100 * s['hours'] / act:.0f}" if act else ""
        wr.writerow([s["name"], s["login"], TEAMS.get(s["team"], s["team"]), s["role"],
                     f"{act:.1f}".replace(".", ",") if act else "",
                     f"{s['hours']:.1f}".replace(".", ","), share, f"{s['norm']:.0f}",
                     s["days_logged"], len(s["active"]), len(s["queue"]),
                     len(s["planned"]), len(s["waiting"]), len(s["closed"])])


def save(snapshot, norm, start, end, teams, pipeline, orphans, plans=None,
         console_note=None, releases=None, gaps=None):
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    folder = os.path.join(root, "reports")
    os.makedirs(folder, exist_ok=True)
    base = f"team-load-{'-'.join(teams)}-{date.today().isoformat()}"
    md = os.path.join(folder, base + ".md")
    js = os.path.join(folder, base + ".json")
    with open(md, "w") as fh:
        print_markdown(snapshot, norm, start, end, teams, pipeline, orphans, plans,
                       console_note, releases, gaps, out=fh)
    with open(js, "w") as fh:
        print_json(snapshot, norm, start, end, pipeline, orphans, plans, console_note,
                   releases, gaps, out=fh)
    print(f"Сохранено:\n  {md}\n  {js}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description="Занятость и загрузка людей департамента — снимок из Трекера",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Команды: " + ", ".join(f"{k} ({v})" for k, v in TEAMS.items()) + ", all",
    )
    ap.add_argument("--team", nargs="+", default=None, help="команды; all — все")
    ap.add_argument("--who", nargs="+", default=None, help="логины Трекера точечно")
    ap.add_argument("--days", type=int, default=14, help="глубина периода в днях (по умолчанию 14)")
    ap.add_argument("--from", dest="date_from", help="начало периода ГГГГ-ММ-ДД")
    ap.add_argument("--to", dest="date_to", help="конец периода ГГГГ-ММ-ДД")
    ap.add_argument("--format", choices=["md", "json", "csv"], default="md")
    ap.add_argument("--no-pipeline", action="store_true", help="без раздела «Что придёт»")
    ap.add_argument("--no-console", action="store_true",
                    help="не ходить в консоль за присутствием: только Трекер и "
                         "календарная норма")
    ap.add_argument("--forecast", action="store_true",
                    help="для тестирования: по каждой задаче «в плане» — состояние "
                         "соседней подзадачи «Разработка». Медленно, но отвечает на "
                         "вопрос «что упадёт на этой неделе»")
    ap.add_argument("--save", action="store_true", help="сохранить в reports/ (.md и .json)")
    args = ap.parse_args()

    if args.days < 1:
        bail("--days должен быть положительным")

    tpr.TOKEN, tpr.ORG = tpr.credentials()
    start, end = parse_period(args)

    if args.who:
        wanted = {w.lower() for w in args.who}
        people = [p for p in PEOPLE if p[0] in wanted]
        unknown = wanted - {p[0] for p in people}
        if unknown:
            bail(f"нет в справочнике: {', '.join(sorted(unknown))}. "
                 f"Состав — docs/company/org-structure.md, справочник — PEOPLE в этом скрипте")
        teams = sorted({p[2] for p in people}, key=lambda t: list(TEAMS).index(t))
    else:
        teams = list(TEAMS) if args.team and "all" in args.team else (args.team or DEFAULT_TEAMS)
        unknown = [t for t in teams if t not in TEAMS]
        if unknown:
            bail(f"неизвестная команда: {', '.join(unknown)}. Доступны: {', '.join(TEAMS)}, all")
        people = [p for p in PEOPLE if p[2] in teams]

    if not people:
        bail("некого смотреть")

    if args.no_console:
        console, console_note = {}, "отключено флагом --no-console"
    else:
        console, reason = console_hours(start, end)
        console_note = reason if console is None else None
        console = console or {}
    snapshot, norm, uid_login = collect(people, start, end, console)
    qa_in_scope = "qa" in teams and not args.no_pipeline
    pipeline = pipeline_qa(date.today()) if qa_in_scope else []
    orphans = orphan_qa(uid_login) if qa_in_scope else []
    gaps = assignment_gaps() if qa_in_scope else []

    # Окна теста по релизам прилаги — только когда в охвате тестирование и консоль
    # доступна: без них раздел стал бы пустой рамкой.
    releases = None
    if qa_in_scope and not args.no_console:
        releases, rel_err = release_windows()
        if rel_err:
            releases = []
            console_note = console_note or rel_err

    # Прогноз считается только для тестирования и только по флагу: он стоит одного
    # запроса на каждого родителя, а у одного тестировщика их бывает под полсотни.
    plans = {}
    if args.forecast:
        planned = [i for s_ in snapshot if s_["team"] == "qa" for i in s_["planned"]]
        if planned:
            plans = forecast(planned)

    if args.save:
        save(snapshot, norm, start, end, teams, pipeline, orphans, plans, console_note,
             releases, gaps)
        return
    if args.format == "json":
        print_json(snapshot, norm, start, end, pipeline, orphans, plans, console_note,
                   releases, gaps)
    elif args.format == "csv":
        print_csv(snapshot)
    else:
        print_markdown(snapshot, norm, start, end, teams, pipeline, orphans, plans,
                       console_note, releases, gaps)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
