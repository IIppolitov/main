#!/usr/bin/env python3
"""
Активность разработки в GitHub за период — второй контур к team-load.py.

    github-activity.py                       # вся организация, последние 14 дней
    github-activity.py --days 30
    github-activity.py --from 2026-08-01 --to 2026-08-31
    github-activity.py --who vznaida RusGosuNagib
    github-activity.py --format json
    github-activity.py --save                # → reports/<день>/ (папка вне git)

Отвечает на вопрос «чем занята разработка», который для планирования тестирования
важнее собственных цифр тестировщиков: объём теста задаёт не тестировщик, а темп
сдачи разработки. Пик открытых PR на этой неделе — это очередь на тест на следующей.

**В GitHub есть только разработчики.** БА и тестировщики в организацию `powbee` не
входят, поэтому их активности здесь нет и быть не может: по ним смотрим Трекер
(`team-load.py`). Это ограничение источника, а не пробел в отчёте.

Считается по организации целиком, а не по трём продуктовым репозиториям: модули
админки (`pbeadmin_omni`, `pbeadmin_id360` и ещё десяток) — отдельные репозитории,
и работа в них к продукту относится ровно так же.

Нужен установленный и авторизованный `gh` (`gh auth status`), скоуп `repo`.

Коды возврата: 2 — ошибка вызова, 3 — нет доступа к GitHub.
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

ORG = "powbee"

# Поиск GitHub ограничен 30 запросами в минуту: на превышении gh не падает,
# а ждёт снятия лимита. Поэтому таймаут щедрый — иначе отчёт обрывается на
# ровном месте посреди пагинации.
GH_TIMEOUT = 300

# Соответствие логинов GitHub людям — берётся из справочника team-load.py, чтобы
# состав команд жил в одном месте. Логины, которых нет в справочнике, печатаются
# как есть: это либо внешний контрибьютор, либо новый человек, которого забыли
# добавить, — и то и другое надо видеть, а не прятать.
_SIBLING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "team-load.py")
_spec = importlib.util.spec_from_file_location("team_load", _SIBLING)
tl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tl)

GH_TO_PERSON = {p[4]: (p[1], p[2], p[3]) for p in tl.PEOPLE if p[4]}

# Люди, которые пишут код, но в справочник команд team-load.py не входят: их
# загрузку мы не планируем, а активность в отчёте видеть надо — иначе половина
# PR по продукту выглядит как работа неизвестного логина.
GH_TO_PERSON.update({
    "IIppolitov": ("Ипполитов Иван", "—", "Директор департамента ИТ"),
    "itpowbee": ("Улич Дмитрий", "—", "Технический директор"),
})
TEAMS = tl.TEAMS


def bail(message, code=2):
    sys.exit(f"github-activity.py: {message}")


def gh(path, jq=None):
    cmd = ["gh", "api", path]
    if jq:
        cmd += ["--jq", jq]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=GH_TIMEOUT)
    except FileNotFoundError:
        sys.exit("github-activity.py: не найден gh. Установка — https://cli.github.com")
    except subprocess.TimeoutExpired:
        bail(f"GitHub не ответил за {GH_TIMEOUT} с: {path}.\n"
             f"Поиск GitHub ограничен 30 запросами в минуту — gh ждёт снятия лимита.\n"
             f"Повторить через минуту либо сузить период.")
    if out.returncode != 0:
        err = (out.stderr or "").strip()
        if "authentication" in err.lower() or "gh auth login" in err:
            sys.exit(f"github-activity.py: нет доступа к GitHub. `gh auth login`\n{err}")
        bail(f"gh api {path}\n{err}")
    return out.stdout


def search(query, kind="issues"):
    """Поиск с пагинацией. Потолок GitHub — 1000 результатов на запрос."""
    items, page = [], 1
    while page <= 10:
        raw = gh(f"search/{kind}?q={query}&per_page=100&page={page}")
        data = json.loads(raw or "{}")
        batch = data.get("items", [])
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def repo_of(item):
    url = item.get("repository_url") or ""
    return url.rsplit("/", 1)[-1] if url else "?"


def collect(start, end, logins):
    rng = f"{start.isoformat()}..{end.isoformat()}"
    created = search(f"org:{ORG}+type:pr+created:{rng}")
    merged = search(f"org:{ORG}+type:pr+merged:{rng}")
    opened_now = search(f"org:{ORG}+type:pr+state:open")

    people = defaultdict(lambda: {
        "created": [], "merged": [], "open": [], "repos": defaultdict(int),
    })
    for item in created:
        login = (item.get("user") or {}).get("login") or "?"
        people[login]["created"].append(item)
        people[login]["repos"][repo_of(item)] += 1
    for item in merged:
        people[(item.get("user") or {}).get("login") or "?"]["merged"].append(item)
    for item in opened_now:
        people[(item.get("user") or {}).get("login") or "?"]["open"].append(item)

    if logins:
        people = {k: v for k, v in people.items() if k in logins}
    return people


def pr_age(item):
    created = item.get("created_at")
    if not created:
        return None
    return (datetime.now().date() - datetime.strptime(created[:10], "%Y-%m-%d").date()).days


def print_markdown(people, start, end, out=sys.stdout):
    w = out.write
    w(f"# Активность в GitHub — {start.isoformat()} — {end.isoformat()}\n\n")
    w(f"Организация `{ORG}`, все репозитории. Снимок на {date.today().isoformat()}.\n\n")
    w("> В организации только разработчики: БА и тестировщиков здесь нет,\n"
      "> их загрузка видна в Трекере (`team-load.py`).\n\n")

    rows = sorted(people.items(), key=lambda kv: -len(kv[1]["merged"]))
    w("| Кто | Логин | Открыл PR | Влил PR | Висит открытых | Репозитории |\n")
    w("|---|---|---|---|---|---|\n")
    for login, d in rows:
        person = GH_TO_PERSON.get(login)
        name = person[0] if person else f"— (нет в справочнике)"
        repos = ", ".join(f"{r} ({n})" for r, n in
                          sorted(d["repos"].items(), key=lambda kv: -kv[1])[:4]) or "—"
        w(f"| {name} | `{login}` | {len(d['created'])} | {len(d['merged'])} | "
          f"{len(d['open'])} | {repos} |\n")
    w("\n")

    stale = []
    for login, d in rows:
        for item in d["open"]:
            age = pr_age(item)
            if age is not None and age >= 14:
                stale.append((age, login, item))
    if stale:
        w("## Открытые PR старше двух недель\n\n")
        w("Незакрытый PR — это работа, которая сделана, но не сдана: в Трекере\n"
          "задача уже может числиться готовой, а на тест она не выйдет.\n\n")
        w("| Возраст | Автор | Репозиторий | PR | Тема |\n|---|---|---|---|---|\n")
        for age, login, item in sorted(stale, key=lambda x: -x[0]):
            person = GH_TO_PERSON.get(login)
            w(f"| {age} дн. | {person[0] if person else login} | {repo_of(item)} | "
              f"[#{item.get('number')}]({item.get('html_url')}) | "
              f"{(item.get('title') or '')[:60]} |\n")
        w("\n")

    for login, d in rows:
        person = GH_TO_PERSON.get(login)
        title = f"{person[0]} · `{login}`" if person else f"`{login}` — вне справочника команд"
        w(f"## {title}\n\n")
        if d["merged"]:
            w(f"**Влил за период — {len(d['merged'])}**\n\n")
            w("| Репозиторий | PR | Тема |\n|---|---|---|\n")
            for item in d["merged"][:30]:
                w(f"| {repo_of(item)} | [#{item.get('number')}]({item.get('html_url')}) | "
                  f"{(item.get('title') or '')[:70]} |\n")
            if len(d["merged"]) > 30:
                w(f"\n…и ещё {len(d['merged']) - 30}.\n")
            w("\n")
        else:
            w("За период не влито ни одного PR.\n\n")
        if d["open"]:
            keys = ", ".join(f"{repo_of(i)}#{i.get('number')} ({pr_age(i)} дн.)"
                             for i in sorted(d["open"], key=lambda x: -(pr_age(x) or 0))[:15])
            w(f"**Висит открытых — {len(d['open'])}:** {keys}\n\n")


def print_json(people, start, end, out=sys.stdout):
    def slim(item):
        return {"repo": repo_of(item), "number": item.get("number"),
                "title": item.get("title"), "url": item.get("html_url"),
                "createdAt": item.get("created_at"), "age": pr_age(item)}

    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "org": ORG,
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "people": [{
            "login": login,
            "name": (GH_TO_PERSON.get(login) or ("", "", ""))[0] or None,
            "team": (GH_TO_PERSON.get(login) or ("", "", ""))[1] or None,
            "created": [slim(i) for i in d["created"]],
            "merged": [slim(i) for i in d["merged"]],
            "open": [slim(i) for i in d["open"]],
            "repos": dict(d["repos"]),
        } for login, d in sorted(people.items(), key=lambda kv: -len(kv[1]["merged"]))],
    }
    json.dump(payload, out, ensure_ascii=False, indent=2)
    out.write("\n")


def save(people, start, end):
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    # Отчёт ложится в папку дня: reports/2026-09-03/имя.md. Дата — в имени
    # папки, в имени файла её нет.
    folder = os.path.join(root, "reports", date.today().isoformat())
    os.makedirs(folder, exist_ok=True)
    base = "github-activity"
    md, js = os.path.join(folder, base + ".md"), os.path.join(folder, base + ".json")
    with open(md, "w") as fh:
        print_markdown(people, start, end, out=fh)
    with open(js, "w") as fh:
        print_json(people, start, end, out=fh)
    print(f"Сохранено:\n  {md}\n  {js}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Активность разработки в GitHub за период")
    ap.add_argument("--who", nargs="+", help="логины GitHub точечно")
    ap.add_argument("--days", type=int, default=14, help="глубина периода (по умолчанию 14)")
    ap.add_argument("--from", dest="date_from", help="начало периода ГГГГ-ММ-ДД")
    ap.add_argument("--to", dest="date_to", help="конец периода ГГГГ-ММ-ДД")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--save", action="store_true", help="сохранить в reports/<день>/")
    args = ap.parse_args()

    today = date.today()
    if args.date_from or args.date_to:
        if not (args.date_from and args.date_to):
            bail("--from и --to задаются вместе")
        try:
            start = datetime.strptime(args.date_from, "%Y-%m-%d").date()
            end = datetime.strptime(args.date_to, "%Y-%m-%d").date()
        except ValueError:
            bail("даты в формате ГГГГ-ММ-ДД")
    else:
        if args.days < 1:
            bail("--days должен быть положительным")
        start, end = today - timedelta(days=args.days - 1), today

    people = collect(start, end, set(args.who) if args.who else None)
    if not people:
        print("За период активности не найдено.", file=sys.stderr)

    if args.save:
        save(people, start, end)
    elif args.format == "json":
        print_json(people, start, end)
    else:
        print_markdown(people, start, end)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
