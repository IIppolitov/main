#!/usr/bin/env python3
"""
Активность разработки в GitHub за период — второй контур к team-load.py.

    github-activity.py                       # вся организация, последние 14 дней
    github-activity.py --days 30
    github-activity.py --from 2026-08-01 --to 2026-08-31
    github-activity.py --who vznaida RusGosuNagib
    github-activity.py --format json
    github-activity.py --commits             # + коммиты, а не только PR
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

**PR — не весь след, и на коротком периоде этого мало.** Разработчик пишет в один
PR несколько дней: за отдельный день у него ноль открытых и ноль влитых, хотя работа
шла. Поэтому для дневного разреза есть `--commits` — поиск коммитов по дате авторства.
На периоде в две недели PR по-прежнему достаточно, и лишний запрос там не нужен.

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
    # Второй аккаунт технического директора — подтверждено им 07.09.2026. Коммитит
    # оттуда больше, чем из itpowbee (31.08–06.09.2026: 35 коммитов против 4, почти
    # все в pbebi_superset_filters), поэтому без этой строки его работа читалась как
    # активность неизвестного логина.
    "dimaulich": ("Улич Дмитрий", "—", "Технический директор"),
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


def commit_repo_of(item):
    """У поиска коммитов репозиторий приходит объектом, а не ссылкой, как у PR."""
    return ((item.get("repository") or {}).get("name")) or "?"


def pr_branch_commits(repo, number):
    """Коммиты ветки открытого PR.

    Нужны потому, что **поиск коммитов индексирует только ветку по умолчанию**:
    работа, которая лежит в неслитой ветке, в нём не находится вообще. Проверено
    07.09.2026 на Знайде — 137 коммитов в поиске всего и ноль за неделю
    31.08–06.09, притом что за ту неделю он открыл семь PR и коммиты в них есть.
    Без этого обхода флаг `--commits` терял ровно тот случай, ради которого
    заводился: разработчика, который несколько дней пишет в один PR.

    Отдаётся первая страница (100 коммитов) — этого хватает: ветка на сотню
    коммитов означает не отчёт о загрузке, а разговор о размере PR.
    """
    try:
        raw = gh(f"repos/{ORG}/{repo}/pulls/{number}/commits?per_page=100")
    except SystemExit:
        raise
    data = json.loads(raw or "[]")
    return data if isinstance(data, list) else []


def collect(start, end, logins, with_commits=False):
    rng = f"{start.isoformat()}..{end.isoformat()}"
    created = search(f"org:{ORG}+type:pr+created:{rng}")
    merged = search(f"org:{ORG}+type:pr+merged:{rng}")
    opened_now = search(f"org:{ORG}+type:pr+state:open")

    people = defaultdict(lambda: {
        "created": [], "merged": [], "open": [], "commits": [],
        "repos": defaultdict(int), "commitRepos": defaultdict(int),
    })
    for item in created:
        login = (item.get("user") or {}).get("login") or "?"
        people[login]["created"].append(item)
        people[login]["repos"][repo_of(item)] += 1
    for item in merged:
        people[(item.get("user") or {}).get("login") or "?"]["merged"].append(item)
    for item in opened_now:
        people[(item.get("user") or {}).get("login") or "?"]["open"].append(item)

    if with_commits:
        # Дата авторства, а не публикации: коммит, сделанный вчера и отправленный
        # сегодня, относится к вчерашнему дню — так и надо для вопроса «чем
        # человек занимался в этот день». Мерж-коммиты считаются тоже.
        seen = set()

        def add_commit(item, repo=None):
            sha = item.get("sha")
            if not sha or sha in seen:
                return
            seen.add(sha)
            if repo:
                item = dict(item, repository={"name": repo})
            login = ((item.get("author") or {}).get("login")
                     or ((item.get("commit") or {}).get("author") or {}).get("name")
                     or "?")
            people[login]["commits"].append(item)
            people[login]["commitRepos"][commit_repo_of(item)] += 1

        for item in search(f"org:{ORG}+author-date:{rng}", kind="commits"):
            add_commit(item)

        # Ветки открытых PR — поиск их не видит (см. pr_branch_commits). Обходим
        # только те PR, которых касались с начала периода: раньше — значит новых
        # коммитов периода в них быть не может, и запрос лишний.
        lo, hi = start.isoformat(), end.isoformat()
        for item in opened_now:
            if (item.get("updated_at") or "")[:10] < lo:
                continue
            repo, number = repo_of(item), item.get("number")
            if not number:
                continue
            for c in pr_branch_commits(repo, number):
                when = (((c.get("commit") or {}).get("author") or {}).get("date") or "")[:10]
                if lo <= when <= hi:
                    add_commit(c, repo=repo)

    if logins:
        people = {k: v for k, v in people.items() if k in logins}
    return people


def pr_age(item):
    created = item.get("created_at")
    if not created:
        return None
    return (datetime.now().date() - datetime.strptime(created[:10], "%Y-%m-%d").date()).days


def print_markdown(people, start, end, out=sys.stdout, with_commits=False):
    w = out.write
    w(f"# Активность в GitHub — {start.isoformat()} — {end.isoformat()}\n\n")
    w(f"Организация `{ORG}`, все репозитории. Снимок на {date.today().isoformat()}.\n\n")
    w("> В организации только разработчики: БА и тестировщиков здесь нет,\n"
      "> их загрузка видна в Трекере (`team-load.py`).\n\n")

    rows = sorted(people.items(), key=lambda kv: -len(kv[1]["merged"]))
    commits_col = " Коммитов |" if with_commits else ""
    w(f"| Кто | Логин | Открыл PR | Влил PR |{commits_col} Висит открытых | Репозитории |\n")
    w("|---|---|---|---|" + ("---|" if with_commits else "") + "---|---|\n")
    for login, d in rows:
        person = GH_TO_PERSON.get(login)
        name = person[0] if person else f"— (нет в справочнике)"
        repos = ", ".join(f"{r} ({n})" for r, n in
                          sorted(d["repos"].items(), key=lambda kv: -kv[1])[:4]) or "—"
        commits = f" {len(d['commits'])} |" if with_commits else ""
        w(f"| {name} | `{login}` | {len(d['created'])} | {len(d['merged'])} |{commits} "
          f"{len(d['open'])} | {repos} |\n")
    w("\n")
    if with_commits:
        w("Столбец «Висит открытых» — состояние на сейчас, а не факт периода:\n"
          "открытый PR мог быть заведён месяц назад.\n\n")

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
        if with_commits:
            if d["commits"]:
                repos = ", ".join(f"{r} ({n})" for r, n in
                                  sorted(d["commitRepos"].items(), key=lambda kv: -kv[1]))
                w(f"**Коммитов за период — {len(d['commits'])}:** {repos}\n\n")
                for item in d["commits"][:15]:
                    msg = ((item.get("commit") or {}).get("message") or "").split("\n")[0]
                    w(f"- `{commit_repo_of(item)}` "
                      f"[{(item.get('sha') or '')[:7]}]({item.get('html_url')}) {msg[:80]}\n")
                if len(d["commits"]) > 15:
                    w(f"- …и ещё {len(d['commits']) - 15}\n")
                w("\n")
            else:
                w("Коммитов за период нет.\n\n")
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
            "commits": [{"repo": commit_repo_of(i), "sha": (i.get("sha") or "")[:7],
                         "message": ((i.get("commit") or {}).get("message")
                                     or "").split("\n")[0],
                         "date": (((i.get("commit") or {}).get("author") or {})
                                  .get("date") or "")[:10],
                         "url": i.get("html_url")} for i in d["commits"]],
            "repos": dict(d["repos"]),
            "commitRepos": dict(d["commitRepos"]),
        } for login, d in sorted(people.items(), key=lambda kv: -len(kv[1]["merged"]))],
    }
    json.dump(payload, out, ensure_ascii=False, indent=2)
    out.write("\n")


def save(people, start, end, with_commits=False):
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    # Отчёт ложится в папку дня: reports/2026-09-03/имя.md. Дата — в имени
    # папки, в имени файла её нет.
    folder = os.path.join(root, "reports", date.today().isoformat())
    os.makedirs(folder, exist_ok=True)
    base = "github-activity"
    md, js = os.path.join(folder, base + ".md"), os.path.join(folder, base + ".json")
    with open(md, "w") as fh:
        print_markdown(people, start, end, out=fh, with_commits=with_commits)
    with open(js, "w") as fh:
        print_json(people, start, end, out=fh)
    print(f"Сохранено:\n  {md}\n  {js}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Активность разработки в GitHub за период")
    ap.add_argument("--who", nargs="+", help="логины GitHub точечно")
    ap.add_argument("--days", type=int, default=14, help="глубина периода (по умолчанию 14)")
    ap.add_argument("--from", dest="date_from", help="начало периода ГГГГ-ММ-ДД")
    ap.add_argument("--to", dest="date_to", help="конец периода ГГГГ-ММ-ДД")
    ap.add_argument("--commits", action="store_true",
                    help="считать и коммиты по дате авторства, а не только PR: "
                         "на периоде в один-два дня PR ещё не появились, а работа шла")
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

    people = collect(start, end, set(args.who) if args.who else None,
                     with_commits=args.commits)
    if not people:
        print("За период активности не найдено.", file=sys.stderr)

    if args.save:
        save(people, start, end, with_commits=args.commits)
    elif args.format == "json":
        print_json(people, start, end)
    else:
        print_markdown(people, start, end, with_commits=args.commits)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
