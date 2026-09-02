#!/usr/bin/env python3
"""
Проверка дерева задачи CRM на соответствие регламенту жизненного цикла.

    tracker-lint.py CRM-131
    tracker-lint.py CRM-131 CRM-1508 --errors-only
    tracker-lint.py https://tracker.yandex.ru/CRM-131 --format json
    tracker-lint.py CRM-131 --stage 5          # проверить по этапу вручную
    tracker-lint.py --rules                    # каталог правил, без обращения к Трекеру

Скрипт отвечает на один вопрос: **всё ли в дереве задачи заведено так, как
требует [жизненный цикл задачи CRM](../../docs/regulations/crm-lifecycle/)** —
состав подзадач, типы, компоненты, мнемоника, поля оценки, форма привязки
багрепортов.

Правила привязаны к этапам. Задача на бизнес-анализе не обязана иметь
заполненные поля оценки, и ругать её за это бессмысленно: этап определяется по
статусам дерева (максимально достигнутый), правило применяется, только если
этап достигнут. Ручное переопределение — `--stage`.

Уровни: `error` — прямое нарушение пункта регламента; `warn` — расхождение,
которое регламент не запрещает явно, но которое ломает отчётность или
воспроизводимость. Код возврата: 0 — ошибок нет, 1 — есть ошибки, 2 — ошибка
вызова, 3 — нет учётных данных.

Формат `json` — для UI в [pbeconsole](../../docs/systems/pbeconsole.md): состав
дерева, найденные нарушения и коды правил в машиночитаемом виде.

Токен и ID организации берутся так же, как в tracker-issue.sh:
переменные окружения → Keychain (yandex-tracker / yandex-tracker-org)
→ ~/.config/yandex-tracker/env.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.tracker.yandex.net/v3"
TRACKER_HOST = "https://tracker.yandex.ru"

HOURS_PER_DAY = 8
DAYS_PER_WEEK = 5

# --- справочники ---------------------------------------------------------------

# Мнемоники клиентов — docs/regulations/tracker-queues.md, раздел «Клиентская
# мнемоника и Теги». Список дублируется в tracker-project-report.py; при правке
# менять оба.
CLIENTS = {
    "PBE", "ALCEA", "ALP", "AVX", "BAY", "BAYBY", "BSN", "BRG", "BOE",
    "CHS", "MAY", "PRM", "ROC", "SAL", "SRV", "VLT", "XNS",
}

# Команды разработки = компоненты очереди CRM. «Аналитика данных» командой
# разработки не является: её работы живут в очереди ANALYTIC (П-6).
DEV_TEAMS = {"Администрирование", "Приложение"}

# Типы подзадач, предусмотренные регламентом (этап 3, 4.7; этап 5, 4.3.1).
ALLOWED_SUBTASK_TYPES = {
    "development",       # Разработка
    "testing",           # Тестирование
    "businessanalysis",  # Бизнес-анализ
    "design",            # Дизайн
    "release",           # Релиз
}

# Второй уровень дерева: багрепорт под своей «Разработкой» (этап 11, 4.5.2) и
# консультация под «Бизнес Анализом» (этап 3, 4.5). Больше там ничего не живёт.
ALLOWED_NESTED_TYPES = {"bug", "consultation"}

# Статус → минимальный достигнутый этап жизненного цикла. Ключи статусов, а не
# названия: названия в интерфейсе переименовывают. Статус, которого здесь нет,
# на определение этапа не влияет — при заведении нового его надо дописать.
STATUS_STAGE = {
    "open": 1, "needInfo": 1, "backlog": 1, "onHold": 1, "onpause": 1,
    "forBA": 3, "inProgressBA": 3,
    "approvaloftheBA": 4, "kamapproval": 4,
    "needEstimate": 5,
    "approvaloftheAssessment": 6,
    "selectedForDev": 8, "readyfordevelopment": 9, "confirmed": 9,
    "inProgress": 10, "indevelopment": 10, "localReady": 10,
    "forRevision": 10, "blocked": 10, "devAwaiting": 10,
    "reviewReady": 11, "inReview": 11, "readyForTest": 11,
    "testing": 11, "tested": 11, "onvalidation": 11,
    "externalTest": 12, "approvalbytheClient": 12, "needAcceptance": 12,
    "rc": 13, "releaseneeded": 13, "deploytoprod": 13, "preparation": 13,
    "demoToCustomer": 14, "resultAcceptance": 14,
    # Закрытая задача разбирается отдельно — CLOSED_STAGE_BY_TYPE.
    "resolved": 0, "closed": 0, "cancelled": 0,
}

# Закрытая подзадача говорит о достигнутом этапе ровно то, что её работа
# закончена. Закрытый бизнес-анализ — это этап 4, а не 13: путать их значит
# требовать заполненных полей оценки от задачи, которая до оценки не дошла.
# Отменённая задача не доказывает ничего — она могла быть отменена сразу.
CLOSED_STAGE_BY_TYPE = {
    "businessanalysis": 4,
    "design": 4,
    "development": 13,
    "testing": 13,
    "release": 13,
}

CLOSED_STATUSES = {"resolved", "closed", "cancelled"}

# Семь полей разбивки оценки в основной задаче — этап 5, 4.5.
BREAKDOWN_FIELDS = [
    ("estimationDevelopment", "Оценка: разработка"),
    ("estimationReview", "Оценка: ревью"),
    ("estimationTesting", "Оценка: тестирование"),
    ("estimationRisks", "Оценка: риски"),
    ("estimationDeploy", "Оценка: деплой"),
    ("estimationDesign", "Оценка: дизайн"),
    ("estimationBa", "Оценка: БА"),
]

# Обязательные блоки постановки — этап 3, 4.6. Названия сведены с шаблоном
# CRM-1449 (рабочая копия — ../pbeba/templates/spec.md) решением от 26.08.2026;
# «Критерии приёмки» — прежнее название «Критериев тестирования», принимается как
# синоним ради задач, заведённых до этой даты. Необязательные блоки
# («Системная постановка», «Зависимость от данных админки») не проверяются.
SPEC_BLOCKS = [
    ("Бизнес проблема", (r"бизнес\s*-?\s*проблема", r"цель\s*/\s*проблема")),
    ("Где", (r"^\W*где\W*$",)),
    ("Задача", (r"^\W*задача\W*$", r"^\W*задачи\W*$")),
    ("Детали", (r"^\W*детали\W*$",)),
    ("Бизнес постановка", (r"бизнес\s*-?\s*постановка",)),
    ("Критерии тестирования", (r"критерии\s+(тестирования|при[её]мки)",)),
    ("Затрагиваемые разделы мануала", (r"раздел\w*\s+мануала",)),
]


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
        sys.stderr.write(
            "Нет доступа к Трекеру: не найдены YANDEX_TRACKER_TOKEN и/или "
            "YANDEX_TRACKER_ORG_ID.\nНастройка — automation/scripts/README.md\n"
        )
        sys.exit(3)
    return token, org


TOKEN, ORG = "", ""


def api(path, params=None):
    url = f"{API}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"OAuth {TOKEN}")
    req.add_header("X-Org-ID", ORG)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        raise SystemExit(f"HTTP {e.code} на {url}\n{detail}")


# --- разбор значений ------------------------------------------------------------
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
    return w * DAYS_PER_WEEK * HOURS_PER_DAY + d * HOURS_PER_DAY + h + mi / 60 + s / 3600


def fmt_h(hours):
    if hours is None:
        return "—"
    return f"{hours:.2f}".rstrip("0").rstrip(".") + "ч"


def local_field(issue, key):
    """Локальные поля приходят с префиксом очереди: `697bc...--estimationBa`."""
    for k, v in issue.items():
        if k.endswith("--" + key):
            return v
    return issue.get(key)


def normalize_key(raw):
    key = re.sub(r"^([a-zA-Z]+://)?[^/]*\.[^/]*/", "", raw)
    key = re.sub(r"^(pages/)?", "", key)
    key = re.split(r"[?#/]", key)[0].upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*-[0-9]+", key):
        sys.stderr.write(f"Не разобрал ключ задачи из '{raw}' (получилось '{key}').\n")
        sys.exit(2)
    return key


def mnemonic_of(text):
    m = re.match(r"^\s*([A-Za-z]{2,5})\b", text or "")
    code = m.group(1).upper() if m else ""
    return code if code in CLIENTS else ""


def tag_mnemonic(issue):
    for tag in issue.get("tags") or []:
        if tag.strip().upper() in CLIENTS:
            return tag.strip().upper()
    return ""


def components_of(issue):
    return [c.get("display", "") for c in issue.get("components") or []]


def team_of(issue):
    for c in components_of(issue):
        if c in DEV_TEAMS:
            return c
    return ""


def assignee_of(issue):
    """id исполнителя либо пустая строка. Поле у задачи без исполнителя приходит
    как null, а не отсутствует, — отсюда двойная проверка."""
    return (issue.get("assignee") or {}).get("id") or ""


def assignee_name(issue):
    return (issue.get("assignee") or {}).get("display") or "—"


def url_of(key):
    return f"{TRACKER_HOST}/{key}"


# --- модель дерева ---------------------------------------------------------------
class Tree:
    """Дерево задачи на два уровня.

    Второй уровень появился вместе с багрепортами: по этапу 11 (4.5.2) дефект
    по доработке заводится подзадачей той подзадачи «Разработка», в которой она
    сделана. Правила состава и оценки смотрят только на первый уровень —
    подзадачи «Разработка» и «Тестирование» там и живут; правила по багрепортам
    и закрытию обходят оба (`all_tasks`).
    """

    def __init__(self, root, links, subtasks, nested):
        self.root = root
        self.links = links            # [(rel, direction, key, display)]
        self.subtasks = subtasks      # [issue] — первый уровень
        self.nested = nested          # [issue] — второй уровень
        self.by_type = {}
        for st in subtasks:
            self.by_type.setdefault(st["type"]["key"], []).append(st)

    @property
    def all_tasks(self):
        return self.subtasks + self.nested

    def of_type(self, *keys):
        out = []
        for k in keys:
            out.extend(self.by_type.get(k, []))
        return out

    @property
    def stage(self):
        """Максимально достигнутый этап по статусам дерева.

        Максимум, а не статус основной задачи: основная висит в «Открыт» весь
        жизненный цикл, работа идёт в подзадачах. Этап 14 даёт только закрытая
        основная задача — приёмку клиентом подтверждает она, а не подзадачи.
        """
        stages = [1]
        for issue in [self.root] + self.all_tasks:
            key = issue["status"]["key"]
            if key in CLOSED_STATUSES:
                if key != "cancelled":
                    stages.append(CLOSED_STAGE_BY_TYPE.get(issue["type"]["key"], 1))
            else:
                stages.append(STATUS_STAGE.get(key, 1))
        stage = min(max(stages), 13)
        if self.root["status"]["key"] in {"resolved", "closed"}:
            stage = 14
        return stage


def resolve_root(key):
    """Дерево проверяется от основной задачи. Дали подзадачу — поднимаемся к родителю.

    Ключ подзадачи в разговоре звучит чаще: именно в ней идёт работа. Поднимаемся
    молча только на один уровень — вложенных деревьев регламент не предусматривает.
    """
    for l in api(f"issues/{key}/links"):
        if l["type"]["id"] == "subtask" and l.get("direction") == "inward":
            return l["object"]["key"], key
    return key, ""


def load_tree(key):
    root = api(f"issues/{key}")
    links = api(f"issues/{key}/links")
    rows = [
        (l["type"]["id"], l.get("direction", ""), l["object"]["key"],
         l["object"].get("display", ""))
        for l in links
    ]
    sub_keys = [k for rel, d, k, _ in rows if rel == "subtask" and d == "outward"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        subtasks = list(pool.map(lambda k: api(f"issues/{k}"), sub_keys))

        # Второй уровень. Глубже не идём: регламент двух уровней и не описывает,
        # а бесконечный обход на чужой вложенности стоил бы десятков запросов.
        def children(key):
            return [l["object"]["key"] for l in api(f"issues/{key}/links")
                    if l["type"]["id"] == "subtask" and l.get("direction") == "outward"]

        nested_keys = [k for ks in pool.map(children, sub_keys) for k in ks]
        nested = list(pool.map(lambda k: api(f"issues/{k}"), nested_keys))
    return Tree(root, rows, subtasks, nested)


# --- каталог правил ---------------------------------------------------------------
# Правило = (код, уровень, минимальный этап, формулировка, пункт регламента,
# функция проверки). Функция получает Tree и возвращает список сообщений;
# каждое сообщение — (ключ задачи | "", текст).
RULES = []


def rule(code, level, since, title, ref):
    def deco(fn):
        RULES.append({
            "code": code, "level": level, "since": since,
            "title": title, "ref": ref, "check": fn,
        })
        return fn
    return deco


# --- Н: мнемоника, названия ---------------------------------------------------
@rule("Н-1", "error", 1, "Название основной задачи начинается с мнемоники клиента",
      "tracker-queues.md, «Клиентская мнемоника и Теги»")
def _n1(t):
    if not mnemonic_of(t.root["summary"]):
        return [(t.root["key"], f"название начинается не с мнемоники: «{t.root['summary'][:60]}»")]
    return []


@rule("Н-2", "error", 1, "Мнемоника клиента стоит тегом и совпадает с названием",
      "tracker-queues.md, «Клиентская мнемоника и Теги»")
def _n2(t):
    name, tag = mnemonic_of(t.root["summary"]), tag_mnemonic(t.root)
    if not tag:
        return [(t.root["key"], "нет тега с мнемоникой клиента")]
    if name and name != tag:
        return [(t.root["key"], f"мнемоника в названии ({name}) не совпадает с тегом ({tag})")]
    return []


@rule("Н-3", "error", 1, "Мнемоника стоит тегом на каждой задаче дерева",
      "tracker-queues.md, «Клиентская мнемоника и Теги»")
def _n3(t):
    root_tag = tag_mnemonic(t.root)
    out = []
    for st in t.all_tasks:
        tag = tag_mnemonic(st)
        if not tag:
            out.append((st["key"], "нет тега с мнемоникой клиента"))
        elif root_tag and tag != root_tag:
            out.append((st["key"], f"тег {tag} не совпадает с тегом основной задачи ({root_tag})"))
    return out


@rule("Н-4", "warn", 1, "Название подзадачи начинается с названия основной задачи",
      "03-biznes-analiz.md, 4.7")
def _n4(t):
    head = t.root["summary"].strip()
    out = []
    for st in t.subtasks:
        if st["queue"]["key"] != t.root["queue"]["key"]:
            continue  # чужая очередь живёт по своим правилам названий
        if not st["summary"].strip().startswith(head):
            out.append((st["key"], "название не начинается с названия основной задачи"))
    return out


@rule("Н-5", "warn", 3, "Названия подзадач разработки и тестирования по шаблону "
                        "«…. Разработка. <Команда>»", "03-biznes-analiz.md, 4.7")
def _n5(t):
    out = []
    for st in t.of_type("development"):
        if not re.search(r"\.\s*Разработка\.\s*(Администрирование|Приложение)", st["summary"]):
            out.append((st["key"], "в названии нет блока «Разработка. <Команда>»"))
    for st in t.of_type("testing"):
        if not re.search(r"\.\s*Тестирование\.\s*(Администрирование|Приложение)", st["summary"]):
            out.append((st["key"], "в названии нет блока «Тестирование. <Команда>»"))
    return out


# --- О: основная задача --------------------------------------------------------
@rule("О-1", "error", 1, "Тип основной задачи — «Улучшение»", "01-sozdanie-zadachi.md, 4.3")
def _o1(t):
    if t.root["type"]["key"] != "improvement":
        return [(t.root["key"], f"тип «{t.root['type']['display']}», ожидается «Улучшение»")]
    return []


@rule("О-2", "warn", 1, "Поле «Срочность БА» заполнено значением 1–5",
      "01-sozdanie-zadachi.md, 4.4")
def _o2(t):
    """Предупреждение, а не нарушение: поле рекомендуемое.

    У него есть рабочее умолчание — пустое трактуется как 5, — а обязательный
    атрибут с умолчанием обязательным не является. Ругаться на него ошибкой
    значило бы держать в отчёте нарушение, которое все привыкли пропускать, а
    такие нарушения обесценивают отчёт целиком."""
    v = local_field(t.root, "theUrgencyOfTheBa")
    if v in (None, "", "-"):
        return [(t.root["key"], "поле «Срочность БА» пустое — команда БА разберёт "
                                "задачу как срочность 5, в конце очереди")]
    if str(v) not in {"1", "2", "3", "4", "5"}:
        return [(t.root["key"], f"«Срочность БА» = «{v}», ожидается 1–5")]
    return []


@rule("О-3", "warn", 2, "У основной задачи есть исполнитель (менеджер клиента)",
      "02-validacija.md, 4.3")
def _o3(t):
    if not t.root.get("assignee"):
        return [(t.root["key"], "исполнитель не назначен: с этапа 2 задачу ведёт менеджер клиента")]
    return []


@rule("О-4", "error", 2, "Создана хотя бы одна подзадача «Разработка»",
      "03-biznes-analiz.md, 4.7")
def _o4(t):
    if not t.of_type("development"):
        return [(t.root["key"], "нет ни одной подзадачи «Разработка» — это не задача очереди CRM")]
    return []


@rule("О-5", "error", 2, "Подзадача «Бизнес Анализ» не более одной",
      "05-ocenka-zadachi.md, 4.4")
def _o5(t):
    ba = t.of_type("businessanalysis")
    if len(ba) > 1:
        return [("", "подзадач «Бизнес Анализ» несколько: " + ", ".join(x["key"] for x in ba)
                 + " — у одной основной задачи она всегда одна, круги доработки живут внутри неё")]
    return []


@rule("О-6", "error", 2, "Подзадача «Релиз» не более одной", "05-ocenka-zadachi.md, 4.3.1")
def _o6(t):
    rel = t.of_type("release")
    if len(rel) > 1:
        return [("", "подзадач «Релиз» несколько: " + ", ".join(x["key"] for x in rel))]
    return []


@rule("О-7", "warn", 5, "Вопрос о подзадаче «Релиз» решён", "05-ocenka-zadachi.md, 4.3.1")
def _o7(t):
    if t.of_type("release"):
        return []
    teams = {team_of(st) for st in t.of_type("development") if team_of(st)}
    if len(teams) > 1:
        return [(t.root["key"], "задействовано больше одной команды, а подзадачи «Релиз» нет — "
                                "проверить признаки п. 4.3.1 (порядок выкатки между командами)")]
    return []


# --- П: состав подзадач ---------------------------------------------------------
@rule("П-1", "error", 3, "К каждой задействованной команде есть подзадача «Тестирование»",
      "03-biznes-analiz.md, 4.7")
def _p1(t):
    dev_teams = {team_of(st) for st in t.of_type("development") if team_of(st)}
    qa_teams = {team_of(st) for st in t.of_type("testing") if team_of(st)}
    return [("", f"нет подзадачи «Тестирование» для команды «{team}»")
            for team in sorted(dev_teams - qa_teams)]


@rule("П-2", "error", 3, "Подзадача «Тестирование» — одна на команду",
      "03-biznes-analiz.md, 4.7; 05-ocenka-zadachi.md, 4.3")
def _p2(t):
    seen = {}
    for st in t.of_type("testing"):
        seen.setdefault(team_of(st), []).append(st["key"])
    return [("", f"подзадач «Тестирование» для «{team or 'без компонента'}» несколько: "
                 + ", ".join(keys))
            for team, keys in sorted(seen.items()) if len(keys) > 1]


@rule("П-3", "error", 3, "У подзадачи «Разработка» проставлен компонент команды",
      "03-biznes-analiz.md, 4.7")
def _p3(t):
    return [(st["key"], "не проставлен компонент «Администрирование» / «Приложение» — "
                        "подзадача не относится ни к одной команде")
            for st in t.of_type("development") if not team_of(st)]


@rule("П-4", "error", 3, "У подзадачи «Тестирование» проставлен компонент команды",
      "03-biznes-analiz.md, 4.7")
def _p4(t):
    return [(st["key"], "не проставлен компонент — непонятно, тестирование какой команды")
            for st in t.of_type("testing") if not team_of(st)]


@rule("Р-1", "error", 9, "Есть разработчик — есть и тестировщик",
      "09-raspredelenie-zadach.md, 4.3.1")
def _r1(t):
    """Асимметрия распределения в одну сторону.

    Этап 9 требует, чтобы у подзадачи «Тестирование» был назначен тестировщик, и
    ставит это критерием завершения этапа. **Момент назначения регламентом не
    определён** — сессия распределения проходит на виклике разработки, где
    тестировщиков нет, и это открытый вопрос. Но требование от этого не исчезает:
    распределённая разработка без назначенного тестировщика доедет до «Можно
    тестировать» ни на ком, и задача осядет на менеджере клиента.

    Правило ловит именно результат, а не момент: оно ничего не говорит о том,
    когда назначение должно было случиться.
    """
    out = []
    for test in t.of_type("testing"):
        if assignee_of(test):
            continue
        team = team_of(test)
        devs = [d for d in t.of_type("development")
                if (not team or team_of(d) == team) and assignee_of(d)]
        if devs:
            who = ", ".join(f"{d['key']} → {assignee_name(d)}" for d in devs[:2])
            out.append((test["key"], "нет исполнителя, хотя разработка распределена "
                                     f"({who})"))
    return out


@rule("Р-2", "warn", 9, "Тестировщик не назначается раньше разработчика",
      "09-raspredelenie-zadach.md, 4.3.1")
def _r2(t):
    """Асимметрия в обратную сторону.

    Подзадача «Тестирование» с исполнителем при нераспределённой «Разработке» —
    обещание, выданное авансом: работа не начата, а в плане тестировщика место
    занято. У одного тестировщика таких накапливается больше десятка, и его
    очередь перестаёт читаться.

    Уровень `warn`, а не `error`: сама по себе такая пара регламенту не
    противоречит, ломается от неё планирование, а не процесс.
    """
    out = []
    for test in t.of_type("testing"):
        if not assignee_of(test):
            continue
        team = team_of(test)
        devs = [d for d in t.of_type("development")
                if (not team or team_of(d) == team)
                and (d.get("status") or {}).get("key") not in CLOSED_STATUSES]
        if devs and not any(assignee_of(d) for d in devs):
            keys = ", ".join(d["key"] for d in devs[:3])
            out.append((test["key"], f"тестировщик назначен, а разработка ещё ни на ком "
                                     f"({keys}) — до распределения исполнителя снять"))
    return out


@rule("П-5", "error", 1, "В дереве только предусмотренные регламентом типы подзадач",
      "03-biznes-analiz.md, 4.7; 05-ocenka-zadachi.md, 4.3")
def _p5(t):
    out = []
    for st in t.subtasks:
        if st["queue"]["key"] != "CRM":
            continue  # BUGREPORTS разбирают Б-1…Б-5, прочие чужие очереди — П-6
        if st["type"]["key"] not in ALLOWED_SUBTASK_TYPES:
            out.append((st["key"], f"тип подзадачи «{st['type']['display']}» регламентом "
                                   "не предусмотрен (Разработка, Тестирование, Бизнес-анализ, "
                                   "Дизайн, Релиз)"))
    return out


@rule("П-6", "warn", 1, "Работы дата-инженеров живут в очереди ANALYTIC",
      "tracker-queues.md, «Ответственность за очереди»")
def _p6(t):
    return [(st["key"], "компонент «Аналитика данных», но задача в очереди CRM — "
                        "запросы к дата-инженерам идут в ANALYTIC")
            for st in t.subtasks
            if st["queue"]["key"] == "CRM" and "Аналитика данных" in components_of(st)]


@rule("П-7", "warn", 3, "«Документация» — отдельная задача со связью relates, не подзадача",
      "03-biznes-analiz.md, 4.7.2")
def _p7(t):
    return [(st["key"], "«Документация» заведена подзадачей: она должна быть отдельной "
                        "задачей на раздел мануала со связью relates")
            for st in t.subtasks if st["type"]["key"] == "documentation"]


# --- Э: оценка ------------------------------------------------------------------
@rule("Э-1", "error", 5, "В подзадачах «Разработка» и «Тестирование» заполнены оба поля оценки",
      "05-ocenka-zadachi.md, 4.5–4.6")
def _e1(t):
    out = []
    for st in t.of_type("development", "testing"):
        miss = [n for f, n in (("originalEstimation", "Первоначальная оценка"),
                               ("estimation", "Оценка")) if not st.get(f)]
        if miss:
            out.append((st["key"], "не заполнено: " + ", ".join(f"«{m}»" for m in miss)))
    return out


@rule("Э-2", "error", 5, "«Первоначальная оценка» и «Оценка» заполнены одним значением",
      "05-ocenka-zadachi.md, 4.5")
def _e2(t):
    out = []
    for st in t.of_type("development", "testing"):
        orig, est = to_hours(st.get("originalEstimation")), to_hours(st.get("estimation"))
        spent = to_hours(st.get("spent")) or 0
        if orig is None or est is None or orig == est:
            continue
        # «Оценка» штатно уходит вниз, когда факт превысил план, — это не ошибка
        # заполнения, а индикатор перерасхода.
        if spent > orig:
            continue
        out.append((st["key"], f"«Первоначальная оценка» {fmt_h(orig)} ≠ «Оценка» {fmt_h(est)} "
                               f"при факте {fmt_h(spent)} — поля заполнены разными значениями"))
    return out


@rule("Э-3", "warn", 5, "Оценка подзадачи не нулевая", "05-ocenka-zadachi.md, 4.5")
def _e3(t):
    return [(st["key"], "«Первоначальная оценка» = 0 — это не «оценили в ноль», а «поле не заполнили»")
            for st in t.of_type("development", "testing")
            if to_hours(st.get("originalEstimation")) == 0]


@rule("Э-4", "error", 6, "В основной задаче заполнены все семь полей разбивки оценки",
      "05-ocenka-zadachi.md, 4.5–4.6")
def _e4(t):
    miss = [name for f, name in BREAKDOWN_FIELDS if local_field(t.root, f) in (None, "")]
    if miss:
        return [(t.root["key"], "не заполнены поля разбивки: " + ", ".join(f"«{m}»" for m in miss)
                 + ". Пустое поле читается как «забыли посчитать», ноль — как «затрат не было»")]
    return []


@rule("Э-5", "warn", 6, "«Оценка: разработка» сходится с суммой по подзадачам",
      "05-ocenka-zadachi.md, 4.5")
def _e5(t):
    return _sum_check(t, "estimationDevelopment", "Оценка: разработка", t.of_type("development"))


@rule("Э-6", "warn", 6, "«Оценка: тестирование» сходится с суммой по подзадачам",
      "05-ocenka-zadachi.md, 4.5")
def _e6(t):
    return _sum_check(t, "estimationTesting", "Оценка: тестирование", t.of_type("testing"))


def _sum_check(t, field, name, subtasks):
    field_h = to_hours(local_field(t.root, field))
    if field_h is None:
        return []                      # пустое поле — забота Э-4
    parts = [to_hours(st.get("originalEstimation")) for st in subtasks]
    if any(p is None for p in parts) or not parts:
        return []                      # неполные оценки — забота Э-1
    total = sum(parts)
    if abs(total - field_h) > 0.01:
        return [(t.root["key"], f"«{name}» = {fmt_h(field_h)}, сумма «Первоначальных оценок» "
                                f"подзадач = {fmt_h(total)}")]
    return []


@rule("Э-7", "warn", 6, "Заполнены «Согласованная оценка» и «Дата согласования оценки»",
      "06-soglasovanie-ocenki.md, 4.3; 08-nabor-sprintov.md, 4.5")
def _e7(t):
    miss = [n for f, n in (("approvedEstimation", "Согласованная оценка"),
                           ("dateOfApprovalOfTheEstimation", "Дата согласования оценки"))
            if local_field(t.root, f) in (None, "")]
    if miss:
        return [(t.root["key"], "не заполнено: " + ", ".join(f"«{m}»" for m in miss)
                 + " — без них задача не берётся в спринт")]
    return []


# --- Б: багрепорты --------------------------------------------------------------
@rule("Б-1", "error", 11, "Багрепорт привязан подзадачей, а не связью relates",
      "11-testirovanie-i-revju.md, 4.5.2")
def _b1(t):
    """Связь `relates` ничего не держит: багрепорт не попадает в дерево и не
    мешает закрыть основную задачу с невыправленным дефектом (этап 14, 5)."""
    return [(k, "багрепорт привязан связью relates — по 4.5.2 дефект по доработке "
                "заводится подзадачей своей подзадачи «Разработка» либо, если она не "
                "определяется, подзадачей основной задачи; дефект в функционале вообще — "
                "самостоятельной задачей с привязкой к проекту релиза, без relates")
            for rel, d, k, _ in t.links
            if rel == "relates" and k.startswith("BUGREPORTS-")]


@rule("Б-2", "error", 11, "Задача из BUGREPORTS имеет тип «Ошибка»",
      "11-testirovanie-i-revju.md, 4.5.4")
def _b2(t):
    return [(st["key"], f"тип «{st['type']['display']}», у багрепорта ожидается «Ошибка»")
            for st in t.all_tasks
            if st["queue"]["key"] == "BUGREPORTS" and st["type"]["key"] != "bug"]


@rule("Б-4", "error", 11, "Родитель багрепорта — подзадача «Разработка» либо основная задача",
      "11-testirovanie-i-revju.md, 4.5.2")
def _b4(t):
    """Две законные формы, и обе внутри дерева.

    Опознал тестировщик подзадачу «Разработка» — багрепорт под неё: оттуда
    наследуются команда, компонент и адресат. Не опознал либо завёл список
    замечаний — под основную задачу, на один уровень с «Разработкой» (4.5.2.1).
    Скрипт не отличает одно от другого: определимость подзадачи знает только
    тестировщик. Ловится третье — родитель вне дерева либо сам багрепорт:
    вложенность «багрепорт под багрепортом» регламент не предусматривает."""
    allowed = {st["key"] for st in t.of_type("development")} | {t.root["key"]}
    out = []
    for st in t.all_tasks:
        if st["queue"]["key"] != "BUGREPORTS":
            continue
        parent = (st.get("parent") or {}).get("key", "")
        if parent in allowed:
            continue
        if parent.startswith("BUGREPORTS-"):
            out.append((st["key"], f"родитель — багрепорт {parent}: по 4.5.2 родителем "
                                   f"бывает подзадача «Разработка» либо основная задача, "
                                   f"но не другой багрепорт"))
        elif parent:
            out.append((st["key"], f"родитель — {parent}: это не подзадача «Разработка» "
                                   f"и не основная задача дерева {t.root['key']}"))
    return out


@rule("Б-3", "error", 14, "К закрытию основной задачи багрепорты закрыты",
      "14-demonstracija-zakazchiku.md, 4.3; 5")
def _b3(t):
    if t.root["status"]["key"] not in CLOSED_STATUSES:
        return []
    return [(st["key"], "багрепорт открыт, а основная задача уже закрыта")
            for st in t.all_tasks
            if st["queue"]["key"] == "BUGREPORTS" and st["status"]["key"] not in CLOSED_STATUSES]


@rule("Б-5", "warn", 11, "У багрепорта назначен исполнитель — тимлид команды",
      "11-testirovanie-i-revju.md, 4.5.4")
def _b5(t):
    return [(st["key"], "исполнитель не назначен: по 4.5.4 багрепорт назначается "
                        "на тимлида команды, в чьей зоне дефект")
            for st in t.all_tasks
            if st["queue"]["key"] == "BUGREPORTS"
            and st["status"]["key"] not in CLOSED_STATUSES
            and not st.get("assignee")]


# --- ПС: постановка ---------------------------------------------------------------
@rule("ПС-1", "warn", 4, "В подзадаче «Бизнес Анализ» заполнены блоки постановки",
      "03-biznes-analiz.md, 4.6")
def _ps1(t):
    """Задачи ускоренного порядка не проверяются, и это не упущение.

    Подзадачи «Бизнес Анализ» у них нет ([этап 2](02-validacija.md), 4.3.2),
    поэтому правило на них молчит. Так и задумано: ускоренный порядок покупает
    скорость тем, что оформленной постановки не требует вовсе — единственное
    жёсткое условие там одно, критерии тестирования (4.3.1, условие 2), и его
    проверяет владелец продукта на валидации, а не скрипт. Расширять правило на
    описание основной задачи не нужно.
    """
    out = []
    for st in t.of_type("businessanalysis"):
        text = st.get("description") or ""
        if len(text.strip()) < 200 and re.search(r"https?://", text):
            out.append((st["key"], "описание — ссылка на внешний документ: блоки постановки "
                                   "в задаче не заполнены, проверить состав нечем"))
            continue
        heads = [h.strip() for h in re.findall(r"(?m)^#{1,4}\s*(.+)$", text)]
        low = [re.sub(r"[*_`]", "", h).strip().lower() for h in heads]
        miss = [name for name, pats in SPEC_BLOCKS
                if not any(re.search(p, h) for h in low for p in pats)]
        if miss:
            out.append((st["key"], "нет блоков постановки: " + ", ".join(f"«{m}»" for m in miss)))
    return out


@rule("П-8", "error", 3, "На втором уровне дерева только багрепорты и консультации",
      "11-testirovanie-i-revju.md, 4.5.2; 03-biznes-analiz.md, 4.5")
def _p8(t):
    out = []
    for st in t.nested:
        if st["type"]["key"] not in ALLOWED_NESTED_TYPES:
            out.append((st["key"], f"тип «{st['type']['display']}» на втором уровне дерева "
                                   "регламентом не предусмотрен: там живут только багрепорты "
                                   "и консультации"))
    return out


@rule("П-9", "error", 3, "Родитель консультации — подзадача «Бизнес Анализ»",
      "03-biznes-analiz.md, 4.5")
def _p9(t):
    ba = {st["key"] for st in t.of_type("businessanalysis")}
    return [(st["key"], "консультация заведена не под подзадачей «Бизнес Анализ» — "
                        "по 4.5 родитель именно она, чтобы было видно, к какой задаче "
                        "потребовалась консультация")
            for st in t.all_tasks
            if st["type"]["key"] == "consultation"
            and (st.get("parent") or {}).get("key", "") not in ba]


@rule("ПС-2", "warn", 1, "В описании основной задачи есть блок «Цель / Проблема»",
      "01-sozdanie-zadachi.md, 4.3")
def _ps2(t):
    text = (t.root.get("description") or "").lower()
    if not re.search(r"(бизнес\s*-?\s*проблема|цель\s*/\s*проблема|проблема|цель задачи)", text):
        return [(t.root["key"], "в описании не нашёл блок «Цель / Проблема» — "
                                "постановка должна говорить, что и зачем, а не как")]
    return []


# --- Ф: финал -------------------------------------------------------------------
@rule("Ф-1", "error", 14, "К закрытию основной задачи закрыты все подзадачи",
      "14-demonstracija-zakazchiku.md, 5")
def _f1(t):
    if t.root["status"]["key"] not in CLOSED_STATUSES:
        return []
    return [(st["key"], f"подзадача открыта («{st['status']['display']}»), "
                        "а основная задача закрыта")
            for st in t.all_tasks if st["status"]["key"] not in CLOSED_STATUSES]


# --- прогон ------------------------------------------------------------------------
def run_rules(tree, stage):
    findings = []
    for r in RULES:
        if stage < r["since"]:
            continue
        for key, message in r["check"](tree):
            findings.append({
                "code": r["code"], "level": r["level"], "issue": key,
                "url": url_of(key) if key else "", "message": message,
                "rule": r["title"], "ref": r["ref"],
            })
    order = {"error": 0, "warn": 1}
    findings.sort(key=lambda f: (order[f["level"]], f["code"], f["issue"]))
    return findings


def tree_rows(tree):
    rows = []
    for st in [tree.root] + tree.subtasks + tree.nested:
        nested = st in tree.nested
        rows.append({
            "key": ("└ " if nested else "") + st["key"],
            "role": "основная" if st is tree.root else ("подзадача 2-го уровня" if nested else "подзадача"),
            "parent": (st.get("parent") or {}).get("key", ""),
            "type": st["type"]["display"],
            "status": st["status"]["display"],
            "component": ", ".join(components_of(st)),
            "assignee": (st.get("assignee") or {}).get("display", ""),
            "original_estimation_h": to_hours(st.get("originalEstimation")),
            "estimation_h": to_hours(st.get("estimation")),
            "spent_h": to_hours(st.get("spent")),
            "summary": st["summary"],
        })
    return rows


def render_markdown(tree, stage, findings, rows):
    out = []
    root = tree.root
    out.append(f"# {root['key']}: {root['summary']}")
    out.append("")
    errors = sum(1 for f in findings if f["level"] == "error")
    warns = len(findings) - errors
    out.append(f"{url_of(root['key'])}")
    out.append("")
    out.append(f"Достигнутый этап: **{stage}**. Нарушений: **{errors}**, предупреждений: **{warns}**.")
    out.append("")
    out.append("## Состав дерева")
    out.append("")
    out.append("| Ключ | Тип | Статус | Компонент | Исполнитель | План | Оценка | Факт |")
    out.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        out.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["key"], r["type"], r["status"], r["component"] or "—",
            r["assignee"] or "—", fmt_h(r["original_estimation_h"]),
            fmt_h(r["estimation_h"]), fmt_h(r["spent_h"])))
    out.append("")
    if not findings:
        out.append("## Нарушений нет")
        out.append("")
        return "\n".join(out)
    for level, title in (("error", "Нарушения"), ("warn", "Предупреждения")):
        block = [f for f in findings if f["level"] == level]
        if not block:
            continue
        out.append(f"## {title}")
        out.append("")
        # Группируем по коду: одно и то же правило часто срабатывает на десятке
        # подзадач, и построчный список превращает отчёт в простыню.
        for code in dict.fromkeys(f["code"] for f in block):
            same = [f for f in block if f["code"] == code]
            head = same[0]
            out.append(f"### {code} — {head['rule']}")
            out.append("")
            out.append(f"_{head['ref']}_")
            out.append("")
            for f in same:
                where = f"[{f['issue']}]({f['url']})" if f["issue"] else "дерево"
                out.append(f"- {where} — {f['message']}")
            out.append("")
    return "\n".join(out)


def render_rules():
    lines = ["# Каталог правил", "",
             "| Код | Уровень | С этапа | Правило | Пункт регламента |",
             "|---|---|---|---|---|"]
    for r in sorted(RULES, key=lambda x: (x["code"][0], x["code"])):
        lines.append(f"| {r['code']} | {r['level']} | {r['since']} | {r['title']} | {r['ref']} |")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(
        description="Проверка дерева задачи CRM на соответствие регламенту жизненного цикла.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("issues", nargs="*", help="ключи задач или ссылки")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    p.add_argument("--stage", type=int, help="проверить по указанному этапу, а не по статусам")
    p.add_argument("--errors-only", action="store_true", help="только нарушения, без предупреждений")
    p.add_argument("--rules", action="store_true", help="напечатать каталог правил и выйти")
    args = p.parse_args()

    if args.rules:
        print(render_rules())
        return 0
    if not args.issues:
        p.error("не указан ключ задачи. Пример: tracker-lint.py CRM-131")

    global TOKEN, ORG
    TOKEN, ORG = credentials()

    reports, exit_code = [], 0
    for raw in args.issues:
        key, came_from = resolve_root(normalize_key(raw))
        tree = load_tree(key)
        stage = args.stage or tree.stage
        findings = run_rules(tree, stage)
        if args.errors_only:
            findings = [f for f in findings if f["level"] == "error"]
        rows = tree_rows(tree)
        if any(f["level"] == "error" for f in findings):
            exit_code = 1
        reports.append({
            "issue": key,
            "url": url_of(key),
            "summary": tree.root["summary"],
            "requested": came_from or key,
            "stage": stage,
            "counts": {
                "error": sum(1 for f in findings if f["level"] == "error"),
                "warn": sum(1 for f in findings if f["level"] == "warn"),
            },
            "tree": rows,
            "findings": findings,
        })
        if args.format == "markdown":
            if reports[:-1]:
                print("\n---\n")
            if came_from:
                print(f"> {came_from} — подзадача, проверяю дерево основной задачи {key}.\n")
            print(render_markdown(tree, stage, findings, rows))

    if args.format == "json":
        print(json.dumps(reports if len(reports) > 1 else reports[0],
                         ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
