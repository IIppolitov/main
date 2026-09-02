#!/usr/bin/env python3
"""
Отчёт по задачам проектов (спринтов) Яндекс Трекера: оценка, трудозатраты,
возвраты на доработку, сроки.

    tracker-project-report.py 926 930
    tracker-project-report.py https://tracker.yandex.ru/pages/projects/926/issues
    tracker-project-report.py 926 --format csv > sprint13.csv
    tracker-project-report.py 926 930 --save                # → reports/ (папка вне git)
    tracker-project-report.py 926 930 --changelog CRM-914   # печать истории одной задачи

Возвраты считаются по истории статусов (changelog), а не по счётчику переоткрытий:
переход из «ревью»/«тест» назад в «разработку» = один возврат. Разделение на
«с ревью» и «с теста» — по статусу, ИЗ которого задача уехала назад.

Токен и ID организации берутся так же, как в tracker-issue.sh:
переменные окружения → Keychain (yandex-tracker / yandex-tracker-org)
→ ~/.config/yandex-tracker/env.

Коды возврата: 2 — ошибка вызова, 3 — нет учётных данных, 1 — ничего не выгружено.
"""

import argparse
import concurrent.futures
import csv
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

API = "https://api.tracker.yandex.net/v2"

# Рабочий день в Трекере — 8 часов, неделя — 5 дней. Иначе P1D читался бы как 24 ч
# и «оценка 1 день» превращалась бы в трёхкратное расхождение с фактом.
HOURS_PER_DAY = 8
DAYS_PER_WEEK = 5

# --- карта статусов по стадиям -------------------------------------------------
# Ключи статусов, а не названия: названия в интерфейсе переименовывают.
DEV = {
    "open", "needInfo", "inProgress", "forRevision", "readyfordevelopment",
    "indevelopment", "localReady", "selectedForDev", "backlog", "blocked",
    "devAwaiting", "onpause", "onHold",
    # «Есть дефекты» — подзадача ушла в багрепорты ([этап 11], 4.5.3). Собственного
    # состояния у неё в этот момент нет, но работа идёт на стороне разработки,
    # поэтому стадия та же.
    "therearedefects",
}
REVIEW = {"reviewReady", "inReview"}
# Внутренний QA и внешний тест разделены намеренно: возврат от своего тестировщика
# и возврат от КАМа/клиента — разные по цене и по адресату претензии.
QA = {"readyForTest", "testing", "tested", "onvalidation"}
EXT = {"externalTest", "demoToCustomer", "approvalbytheClient", "resultAcceptance"}
DONE = {"resolved", "closed", "rc", "cancelled", "deploytoprod", "releaseneeded"}

# Куда должна уехать задача, чтобы это считалось возвратом.
#
# «Есть дефекты» здесь наравне с «На доработку»: с 02.09.2026 тестировщик,
# заведя багрепорт, переводит подзадачу именно туда ([этап 11], 4.5.3). Без этого
# ключа возвраты после перехода на новый статус перестали бы считаться вовсе —
# метрика обнулилась бы молча.
BACK_TO = {"inProgress", "forRevision", "open", "needInfo", "indevelopment",
           "readyfordevelopment", "therearedefects"}

# Тестировщики — docs/company/org-structure.md. Нужны, чтобы отличить возврат от
# «QA взял задачу в тест»: по этапу 11 регламента QA должен переводить
# «Можно тестировать» → «Тестируется», но на практике переводит в «В работе».
# Такой переход возвратом не является — считается отдельно, как нарушение статусной модели.
QA_PEOPLE = {"Мария Бардюжина", "Владислав Шуваев"}

STAGE_NAMES = {"dev": DEV, "review": REVIEW, "qa": QA, "ext": EXT, "done": DONE}

# Мнемоники клиентов — docs/regulations/tracker-queues.md. По регламенту мнемоника
# обязана стоять тегом задачи и начинать её название; на практике тег ставят не
# всегда, поэтому отчёт умеет опознать клиента и по началу темы — но помечает
# такой случай знаком «?», чтобы нарушение не растворилось в отчёте.
CLIENTS = {
    "PBE": "PowerBee", "ALCEA": "Алцея", "ALP": "Альпен Фарма", "AVX": "Авексима",
    "BAY": "Байер", "BAYBY": "Байер РБ", "BSN": "Безен", "BRG": "Бинергия",
    "BOE": "Берингер Ингельхайм", "CHS": "Кьези", "MAY": "Майоли Фарма",
    "PRM": "Примафарма", "ROC": "РОШ", "SAL": "Сэлвим", "SRV": "Сервье",
    "VLT": "Валента", "XNS": "Ксантис",
}


def client_of(issue):
    """(мнемоника, откуда). Тег приоритетнее темы: он и есть носитель по регламенту."""
    for tag in issue.get("tags") or []:
        code = tag.strip().upper()
        if code in CLIENTS:
            return code, "tag"
    head = re.match(r"^\s*([A-Za-z]{2,5})\b", issue.get("summary") or "")
    if head and head.group(1).upper() in CLIENTS:
        return head.group(1).upper(), "summary"
    return "", ""


def short_name(display):
    """«Андрей Гаврилов» → «Гаврилов А.»: в таблицу с 5-6 именами полные не влезают."""
    parts = (display or "").split()
    if len(parts) >= 2:
        return f"{parts[1]} {parts[0][0]}."
    return display or "?"


def stage_of(status_key):
    for name, keys in STAGE_NAMES.items():
        if status_key in keys:
            return name
    return "other"


# --- учётные данные -------------------------------------------------------------
def keychain(service):
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def credentials():
    token = os.environ.get("YANDEX_TRACKER_TOKEN", "")
    org = os.environ.get("YANDEX_TRACKER_ORG_ID", "")
    if not token or not org:
        env_file = os.environ.get(
            "YANDEX_TRACKER_ENV_FILE", os.path.expanduser("~/.config/yandex-tracker/env")
        )
        if os.path.isfile(env_file):
            with open(env_file) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
            token = token or os.environ.get("YANDEX_TRACKER_TOKEN", "")
            org = org or os.environ.get("YANDEX_TRACKER_ORG_ID", "")
    token = token or keychain("yandex-tracker")
    org = org or keychain("yandex-tracker-org")
    if not token or not org:
        sys.exit(
            "Нет доступа к Трекеру: не найдены YANDEX_TRACKER_TOKEN и/или "
            "YANDEX_TRACKER_ORG_ID.\nНастройка — automation/scripts/README.md"
        )
    return token, org


TOKEN, ORG = "", ""


def api(path, method="GET", body=None, params=None):
    url = f"{API}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"OAuth {TOKEN}")
    req.add_header("X-Org-ID", ORG)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode()), resp.headers
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        raise SystemExit(f"HTTP {e.code} на {url}\n{detail}")


# --- ISO-8601 duration ----------------------------------------------------------
_DUR = re.compile(
    r"^P(?:(\d+(?:\.\d+)?)W)?(?:(\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$"
)


def to_hours(duration):
    """PT4H / P1DT2H30M → часы. None при пустом или неразобранном значении."""
    if not duration:
        return None
    m = _DUR.match(duration)
    if not m:
        return None
    w, d, h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return (
        w * DAYS_PER_WEEK * HOURS_PER_DAY
        + d * HOURS_PER_DAY
        + h
        + mi / 60
        + s / 3600
    )


def fmt_h(hours):
    if hours is None:
        return ""
    if hours == 0:
        return "0"
    return f"{hours:.2f}".rstrip("0").rstrip(".")


def parse_ts(value):
    if not value:
        return None
    # 2026-08-12T09:32:21.118+0000
    return datetime.strptime(value[:19] + value[-5:], "%Y-%m-%dT%H:%M:%S%z")


# --- выгрузка -------------------------------------------------------------------
def project_info(short_id):
    data, _ = api(f"projects/{short_id}")
    return data


def project_issues(short_id):
    issues, page = [], 1
    while True:
        batch, _ = api(
            "issues/_search",
            method="POST",
            body={"filter": {"project": str(short_id)}},
            params={"perPage": 100, "page": page},
        )
        issues.extend(batch)
        if len(batch) < 100:
            return issues
        page += 1


def changelog(key):
    entries, params = [], {"perPage": 100}
    while True:
        batch, headers = api(f"issues/{key}/changelog", params=params)
        entries.extend(batch)
        link = headers.get("Link", "")
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if not (m and batch):
            return entries
        nxt = urllib.parse.parse_qs(urllib.parse.urlparse(m.group(1)).query)
        params = {"perPage": 100, "id": nxt["id"][0]}


def worklog(key):
    """{ФИО: часы} по записям учёта времени. Сумма сходится с полем spent задачи."""
    records, params = [], {"perPage": 100}
    while True:
        batch, headers = api(f"issues/{key}/worklog", params=params)
        records.extend(batch)
        m = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", ""))
        if not (m and batch):
            break
        nxt = urllib.parse.parse_qs(urllib.parse.urlparse(m.group(1)).query)
        params = {"perPage": 100, "id": nxt["id"][0]}

    by_person = {}
    for r in records:
        hours = to_hours(r.get("duration")) or 0
        if hours <= 0:  # PT0S ставят «для галочки» — в разбивку такие записи не идут
            continue
        who = (r.get("createdBy") or {}).get("display") or "?"
        by_person[who] = by_person.get(who, 0) + hours
    return dict(sorted(by_person.items(), key=lambda kv: -kv[1]))


def bugreport_hours(key):
    """Часы, списанные в багрепорты этой подзадачи.

    С 02.09.2026 исправление дефекта разработчик списывает не в подзадачу
    «Разработка», а в багрепорт под ней ([этап 11], раздел 6). Поэтому сравнивать
    оценку подзадачи с одним лишь её собственным списанием больше нельзя: получится
    ложный недорасход тем больший, чем хуже была сделана доработка.
    """
    try:
        links, _ = api(f"issues/{key}/links")
    except SystemExit:
        return 0.0, 0
    kids = [l["object"]["key"] for l in links
            if (l.get("type") or {}).get("id") == "subtask"
            and l.get("direction") == "outward"
            and str(l["object"]["key"]).startswith("BUGREPORTS")]
    total = 0.0
    for k in kids:
        try:
            issue, _ = api(f"issues/{k}")
        except SystemExit:
            continue
        total += to_hours(issue.get("spent")) or 0
    return total, len(kids)


def status_transitions(entries):
    """[(момент, кто, из_ключа, из_имени, в_ключ, в_имя)] в хронологическом порядке."""
    out = []
    for e in entries:
        for f in e.get("fields") or []:
            if (f.get("field") or {}).get("id") != "status":
                continue
            frm, to = f.get("from") or {}, f.get("to") or {}
            out.append((
                parse_ts(e.get("updatedAt")),
                ((e.get("updatedBy") or {}).get("display") or "?"),
                frm.get("key") or "", frm.get("display") or "—",
                to.get("key") or "", to.get("display") or "—",
            ))
    out.sort(key=lambda t: (t[0] or datetime.min.replace(tzinfo=None)))
    return out


def analyse(issue, entries, spent_by, bugs=(0.0, 0)):
    key = issue["key"]
    trans = status_transitions(entries)

    returns = {"review": [], "qa": [], "ext": [], "done": []}
    pickups = []  # QA взял в тест, переведя в «В работе» вместо «Тестируется»
    first_dev = first_test = last_done = None
    time_in = {}

    for i, (ts, who, fk, fd, tk, td) in enumerate(trans):
        frm_stage, to_stage = stage_of(fk), stage_of(tk)

        if tk in BACK_TO and tk != fk and frm_stage in returns:
            # «На доработку» и «Есть дефекты» — однозначные возвраты, кто бы их
            # ни поставил. Всё остальное, сделанное тестировщиком из тестового
            # статуса, — это он взял задачу к себе, а не вернул разработчику.
            if (tk not in ("forRevision", "therearedefects")
                    and who in QA_PEOPLE and frm_stage in ("qa", "ext")):
                pickups.append((ts, who, fd, td))
            else:
                returns[frm_stage].append((ts, who, fd, td))

        if first_dev is None and tk in {"inProgress", "indevelopment"}:
            first_dev = ts
        if first_test is None and to_stage in ("qa", "ext"):
            first_test = ts
        if to_stage == "done":
            last_done = ts
        elif last_done and to_stage != "done":
            last_done = None  # вернули из терминального — снова в работе

        # время в статусе, из которого только что ушли
        if i > 0 and ts and trans[i - 1][0]:
            prev_key = trans[i - 1][4] or fk
            time_in[prev_key] = time_in.get(prev_key, timedelta()) + (ts - trans[i - 1][0])

    created = parse_ts(issue.get("createdAt"))
    now = datetime.now().astimezone()
    if trans and trans[-1][0]:
        cur_key = trans[-1][4]
        time_in[cur_key] = time_in.get(cur_key, timedelta()) + (now - trans[-1][0])

    def days(delta):
        return round(delta.total_seconds() / 86400, 1) if delta else None

    est_orig = to_hours(issue.get("originalEstimation"))
    est = to_hours(issue.get("estimation"))
    spent = to_hours(issue.get("spent"))
    base = est_orig if est_orig else est
    bug_h, bug_n = bugs
    # Факт задачи = её собственные часы плюс часы её багрепортов: исправление
    # дефекта живёт там (раздел 6 этапа 11).
    fact = (spent or 0) + bug_h if (spent is not None or bug_h) else None
    over = (fact - base) if (fact is not None and base) else None

    dev_time = sum(
        (time_in.get(k, timedelta())
         for k in ("inProgress", "indevelopment", "forRevision", "therearedefects")),
        timedelta(),
    )

    code, source = client_of(issue)

    return {
        "key": key,
        "type": ((issue.get("type") or {}).get("display") or ""),
        "summary": issue.get("summary") or "",
        "status": ((issue.get("status") or {}).get("display") or ""),
        "client": code + ("?" if source == "summary" else ""),
        "client_code": code,
        "client_source": source,
        "spent_by": spent_by,
        "spent_by_str": "; ".join(f"{short_name(w)} {fmt_h(h)}" for w, h in spent_by.items()),
        "assignee": ((issue.get("assignee") or {}).get("display") or ""),
        "parent": ((issue.get("parent") or {}).get("key") or ""),
        "est_orig_h": est_orig,
        "est_h": est,
        "spent_h": spent,
        "bug_h": bug_h or None,
        "bug_n": bug_n or None,
        "fact_h": fact,
        "over_h": over,
        "over_pct": (round(over / base * 100) if (over is not None and base) else None),
        "ret_review": len(returns["review"]),
        "ret_qa": len(returns["qa"]),
        "ret_ext": len(returns["ext"]),
        "ret_reopen": len(returns["done"]),
        "ret_total": sum(len(v) for v in returns.values()),
        "qa_pickups": len(pickups),
        "pickups_detail": pickups,
        "created": created,
        "first_dev": first_dev,
        "first_test": first_test,
        "done": last_done,
        "start": issue.get("start") or "",
        "end": issue.get("end") or "",
        "deadline": issue.get("deadline") or "",
        "lead_days": days((last_done or now) - created) if created else None,
        "cycle_days": days((last_done or now) - first_dev) if first_dev else None,
        "dev_days": days(dev_time),
        "transitions": len(trans),
        "returns_detail": sorted(
            [(st, *r) for st, rs in returns.items() for r in rs],
            key=lambda r: r[1].timestamp() if r[1] else 0,
        ),
        "time_in": {k: days(v) for k, v in sorted(time_in.items(), key=lambda kv: -kv[1])},
    }


COLUMNS = [
    ("key", "Задача"),
    ("client", "Клиент"),
    ("type", "Тип"),
    ("status", "Статус"),
    ("est_orig_h", "Оценка, ч"),
    ("spent_h", "Факт, ч"),
    ("bug_h", "Дефекты, ч"),
    ("spent_by_str", "Кто списал, ч"),
    ("over_h", "Δ, ч"),
    ("over_pct", "Δ, %"),
    ("ret_review", "Возвр. ревью"),
    ("ret_qa", "Возвр. QA"),
    ("ret_ext", "Возвр. внешн."),
    ("ret_reopen", "Переоткр."),
    ("cycle_days", "Цикл, дн"),
    ("lead_days", "Lead, дн"),
    ("dev_days", "В разраб., дн"),
    ("start", "Начало"),
    ("end", "Конец"),
    ("deadline", "Дедлайн"),
    ("summary", "Тема"),
]


def cell(row, field):
    v = row.get(field)
    if v is None:
        return ""
    if field.endswith("_h"):
        return fmt_h(v)
    if field == "over_pct":
        return f"{v:+d}"
    if field in ("summary", "spent_by_str"):
        return v.replace("|", "\\|")
    return str(v)


def print_shared(projects):
    """Задачи, попавшие больше чем в один проект — то есть переехавшие из спринта в спринт."""
    seen = {}
    for info, rows in projects:
        for r in rows:
            seen.setdefault(r["key"], []).append(info["name"])
    shared = {k: v for k, v in seen.items() if len(v) > 1}
    if not shared:
        return
    print(f"\n## В нескольких спринтах сразу ({len(shared)})\n")
    print("Задача числится в двух и более проектах — либо не доделана в срок, "
          "либо не убрана из закрытого спринта.\n")
    for key, names in sorted(shared.items()):
        print(f"- **{key}** — {len(names)} спринта: {'; '.join(names)}")


def print_markdown(projects, args):
    for info, rows in projects:
        print(f"\n## {info['name']} (проект {info['id']})\n")
        print("| " + " | ".join(t for _, t in COLUMNS) + " |")
        print("|" + "---|" * len(COLUMNS))
        for r in rows:
            print("| " + " | ".join(cell(r, f) for f, _ in COLUMNS) + " |")

        spent_all = sum((r["spent_h"] or 0) + (r["bug_h"] or 0) for r in rows)
        # Перерасход считается только по задачам, где есть и оценка, и факт: иначе
        # 30 часов на задаче с пустой оценкой раздували бы отклонение по спринту.
        both = [r for r in rows if r["est_orig_h"] and r["fact_h"] is not None]
        est_b = sum(r["est_orig_h"] for r in both)
        spent_b = sum(r["fact_h"] for r in both)
        no_est = [r for r in rows if not r["est_orig_h"] and (r["fact_h"] or 0) > 0]
        over = [r for r in both if r["over_h"] > 0]
        print(f"\n**Итого по {len(rows)} задачам:** списано {fmt_h(spent_all)} ч.")
        print(f"По {len(both)} задачам, где есть и оценка, и факт: оценка {fmt_h(est_b)} ч, "
              f"факт {fmt_h(spent_b)} ч"
              + (f", отклонение {fmt_h(spent_b - est_b)} ч "
                 f"({round((spent_b / est_b - 1) * 100):+d}%)" if est_b else "")
              + f"; вышли за оценку {len(over)}.")
        if no_est:
            print(f"Без оценки, но с трудозатратами — {len(no_est)} задач "
                  f"на {fmt_h(sum(r['fact_h'] for r in no_est))} ч "
                  f"({', '.join(r['key'] for r in no_est)}).")
        print(f"Возвраты: с ревью {sum(r['ret_review'] for r in rows)}, "
              f"с внутреннего теста {sum(r['ret_qa'] for r in rows)}, "
              f"с внешнего теста {sum(r['ret_ext'] for r in rows)}, "
              f"переоткрытий {sum(r['ret_reopen'] for r in rows)}. "
              f"Без единого возврата — {sum(1 for r in rows if r['ret_total'] == 0)} задач.")

        pickups = sum(r["qa_pickups"] for r in rows)
        if pickups:
            print(f"Отдельно: {pickups} раз QA брал задачу в «В работе» вместо "
                  f"«Тестируется» — это не возврат, а нарушение статусной модели "
                  f"(этап 11 регламента).")

        no_tag = [r for r in rows if r["client_source"] != "tag"]
        if no_tag:
            print(f"Без тега клиента — {len(no_tag)} задач "
                  f"({', '.join(r['key'] for r in no_tag)}); "
                  f"мнемоника в колонке «Клиент» со знаком «?» взята из темы.")

        print("\n**Списано по людям:**\n")
        print("| Кто | Часов | Задач |")
        print("|---|---|---|")
        totals = {}
        for r in rows:
            for who, h in r["spent_by"].items():
                acc = totals.setdefault(who, [0, 0])
                acc[0] += h
                acc[1] += 1
        for who, (h, n) in sorted(totals.items(), key=lambda kv: -kv[1][0]):
            print(f"| {who} | {fmt_h(h)} | {n} |")

        by_client = {}
        for r in rows:
            code = r["client_code"] or "—"
            acc = by_client.setdefault(code, [0, 0])
            acc[0] += (r["spent_h"] or 0) + (r["bug_h"] or 0)
            acc[1] += 1
        print("\n**Списано по клиентам:**\n")
        print("| Клиент | Часов | Задач |")
        print("|---|---|---|")
        for code, (h, n) in sorted(by_client.items(), key=lambda kv: -kv[1][0]):
            label = f"{code} — {CLIENTS[code]}" if code in CLIENTS else "не определён"
            print(f"| {label} | {fmt_h(h)} | {n} |")

        if args.returns:
            print("\n### Возвраты подробно\n")
            labels = {"review": "ревью", "qa": "QA", "ext": "внешний тест",
                      "done": "переоткрытие",
                      "pickup": "_не возврат: QA взял в тест_"}
            for r in rows:
                if not (r["returns_detail"] or r["pickups_detail"]):
                    continue
                print(f"**{r['key']}** — {r['summary'][:80]}")
                events = r["returns_detail"] + [("pickup", *p) for p in r["pickups_detail"]]
                for stage, ts, who, fd, td in sorted(
                    events, key=lambda e: e[1].timestamp() if e[1] else 0
                ):
                    print(f"- {ts:%Y-%m-%d %H:%M} · {labels[stage]} · {who}: {fd} → {td}")
                print()

    if len(projects) > 1:
        print_shared(projects)


def print_csv(projects, out):
    w = csv.writer(out)
    w.writerow(["Проект"] + [t for _, t in COLUMNS])
    for info, rows in projects:
        for r in rows:
            w.writerow([info["name"]] + [cell(r, f) for f, _ in COLUMNS])


def print_changelog(key):
    entries = changelog(key)
    print(f"# История статусов {key}\n")
    print("| Когда | Кто | Из | В |")
    print("|---|---|---|---|")
    for ts, who, _, fd, _, td in status_transitions(entries):
        print(f"| {ts:%Y-%m-%d %H:%M} | {who} | {fd} | {td} |")


def save(projects, args):
    """Пишет .md и .csv в reports/ (папка в .gitignore) или в указанный каталог."""
    # Каталог по умолчанию — от расположения скрипта, а не от cwd: отчёт должен
    # лечь в reports/ репозитория, откуда бы скрипт ни запускали.
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out_dir = args.save or os.path.join(repo, "reports")
    os.makedirs(out_dir, exist_ok=True)

    # В файл разбор возвратов пишется всегда: место дешевле, чем повторный запуск
    # ради одного флага, а именно за этим разбором к отчёту и возвращаются.
    args.returns = True

    ids = "-".join(str(i["id"]) for i, _ in projects)
    stamp = datetime.now().strftime("%Y-%m-%d")
    base = os.path.join(out_dir, f"tracker-projects-{ids}-{stamp}")

    with open(f"{base}.md", "w") as fh:
        stdout, sys.stdout = sys.stdout, fh
        try:
            print(f"# Отчёт по проектам Трекера: {', '.join(i['name'] for i, _ in projects)}\n")
            print(f"Собран {stamp} командой "
                  f"`automation/scripts/tracker-project-report.py {' '.join(args.projects)}`.  ")
            print("Данные живые — на момент сборки; в git не коммитится "
                  "(см. [reports/README.md](README.md)).")
            print_markdown(projects, args)
        finally:
            sys.stdout = stdout

    with open(f"{base}.csv", "w", newline="") as fh:
        print_csv(projects, fh)

    print(f"Сохранено:\n  {base}.md\n  {base}.csv")


def normalize_project(arg):
    m = re.search(r"(\d+)", arg)
    if not m:
        raise SystemExit(f"Не разобрал номер проекта из '{arg}'")
    return m.group(1)


def main():
    global TOKEN, ORG
    p = argparse.ArgumentParser(
        description="Отчёт по задачам проектов Трекера: оценки, трудозатраты, возвраты, сроки.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("projects", nargs="*", help="номера проектов или ссылки на них")
    p.add_argument("--format", choices=["md", "csv", "json"], default="md")
    p.add_argument("--returns", action="store_true",
                   help="приложить подробный разбор каждого возврата")
    p.add_argument("--changelog", metavar="KEY",
                   help="вместо отчёта напечатать историю статусов одной задачи")
    p.add_argument("--jobs", type=int, default=8, help="параллельных запросов (по умолчанию 8)")
    p.add_argument("--save", nargs="?", const="", metavar="DIR",
                   help="сохранить отчёт (.md и .csv) в reports/ или в указанный каталог")
    args = p.parse_args()

    TOKEN, ORG = credentials()

    if args.changelog:
        print_changelog(args.changelog.upper())
        return

    if not args.projects:
        p.error("не указан ни один проект")

    projects = []
    for raw in args.projects:
        pid = normalize_project(raw)
        info = project_info(pid)
        issues = project_issues(pid)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            logs = list(pool.map(lambda i: changelog(i["key"]), issues))
            spent = list(pool.map(lambda i: worklog(i["key"]), issues))
            bugs = list(pool.map(lambda i: bugreport_hours(i["key"]), issues))
        rows = [analyse(i, log, sp, bg)
                for i, log, sp, bg in zip(issues, logs, spent, bugs)]
        rows.sort(key=lambda r: (-(r["ret_total"]), -(r["over_h"] or 0)))
        projects.append((info, rows))

    if args.save is not None:
        save(projects, args)
        return

    if args.format == "csv":
        print_csv(projects, sys.stdout)
    elif args.format == "json":
        print(json.dumps(
            [{"project": i["name"], "issues": rows} for i, rows in projects],
            ensure_ascii=False, indent=2, default=str,
        ))
    else:
        print_markdown(projects, args)


if __name__ == "__main__":
    main()
