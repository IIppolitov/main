#!/usr/bin/env python3
"""Предохранители на git-команды, которые нельзя выразить префиксом в permissions.

Пять проверок, все на `PreToolUse` для Bash:

1. `git commit` в защищённую ветку или в detached HEAD — блок. Учитывается смена ветки
   в той же цепочке: `git switch -c CRM-1 && git commit` разрешён, `git switch main &&
   git commit` — нет.
2. `git checkout <путь>` и `git checkout -- <путь>` — блок: это откат рабочего дерева,
   неотличимый префиксом от безобидного `git checkout <ветка>`. Переключение ветки проходит.
3. `git commit`, когда в индексе лежит гитлинк сабмодуля (mode 160000) — блок.
   Указатели модулей в проектах с сабмодулями намеренно не ведут (`ignore = all`
   в .gitmodules), закоммиченный указатель на непушенный коммит ломает
   `git submodule update` у всех.
4. `git reset --hard|--keep|--merge` на любой позиции — блок по той же причине.
5. `git restore` без `--staged` (или с `--worktree`) — блок: это единственная форма,
   которая затирает несохранённую работу. `git restore --staged` индекс трогает, рабочее
   дерево — нет, поэтому она разрешена и служит штатным способом снять лишнее из индекса.

Плюс `-a`/`-A`/`-u` на любой позиции у `commit`/`add` — массовый стейдж втягивает мусор
и указатели сабмодулей.

Почему хук, а не deny-лист: permissions матчатся по префиксу команды и не видят ни текущую
ветку, ни содержимое индекса, ни позицию флага среди аргументов. Deny-лист остаётся первым
рубежом для грубых форм, хук — вторым и точным. Работает и для оркестратора, и для субагентов.

Вход  — JSON на stdin (tool_name, tool_input.command, cwd).
Выход — код 0: пропустить; код 2: заблокировать, текст из stderr уходит агенту.
"""

import json
import os
import re
import shlex
import subprocess
import sys

# Две константы ниже — единственное, что правится при копировании в другой проект.
# PROTECTED — точные имена долгоживущих веток, PROTECTED_PREFIXES — их семейства
# (например, "release/" покрывает release/boehringer-202602 и все будущие).
PROTECTED = {"main", "master", "pre-prod", "prod"}
PROTECTED_PREFIXES = ()

GITLINK_MODE = "160000"

# Глобальные опции git, которые идут ДО подкоманды; часть съедает следующий токен.
GLOBAL_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}

SEGMENT_SEPARATORS = re.compile(r"&&|\|\||;|\n|\|")

# Слова, при отсутствии которых команду можно даже не разбирать.
TRIGGERS = ("commit", "restore", "add", "checkout", "reset")

# Маркер «после этого сегмента ветка неизвестна» — отличается от "" (detached HEAD).
UNKNOWN = object()


def split_segments(command):
    """Режет составную команду на отдельные вызовы, сохраняя порядок."""
    return [seg.strip() for seg in SEGMENT_SEPARATORS.split(command) if seg.strip()]


def parse_git_invocation(tokens):
    """Возвращает (subcommand, args, dir_override) или (None, [], None), если это не git."""
    if not tokens or tokens[0] != "git":
        return None, [], None

    i = 1
    dir_override = None
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("-"):
            return token, tokens[i + 1:], dir_override

        if token in GLOBAL_OPTS_WITH_VALUE:
            if token in ("-C", "--git-dir", "--work-tree") and i + 1 < len(tokens):
                dir_override = tokens[i + 1]
            i += 2
            continue

        for opt in ("--git-dir=", "--work-tree="):
            if token.startswith(opt):
                dir_override = token[len(opt):]
        i += 1

    return None, [], dir_override


def git_output(repo_dir, args):
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    return result.stdout


def current_branch(repo_dir):
    out = git_output(repo_dir, ["branch", "--show-current"])
    return None if out is None else out.strip()


def is_ref(repo_dir, name):
    """Отличает `git checkout <ветка>` от `git checkout <файл>`."""
    return git_output(repo_dir, ["rev-parse", "--verify", "--quiet", name]) is not None


def staged_gitlinks(repo_dir):
    """Пути сабмодулей, чей указатель лежит в индексе под коммит."""
    out = git_output(repo_dir, ["diff", "--cached", "--raw"])
    if not out:
        return []

    paths = []
    for line in out.splitlines():
        if not line.startswith(":"):
            continue
        meta, _, path = line.partition("\t")
        fields = meta[1:].split()
        if len(fields) < 2:
            continue
        src_mode, dst_mode = fields[0], fields[1]
        if GITLINK_MODE in (src_mode, dst_mode) and path:
            paths.append(path.strip())

    return paths


def block(message):
    sys.stderr.write(message)
    sys.exit(2)


def check_bulk_flags(subcommand, args):
    """Ловит -a/-A/-u на ЛЮБОЙ позиции — префиксный deny видит только начало команды.

    `git commit -m "..." -a` и `git add app/Foo.php -A` проходят мимо permissions,
    а втягивают в коммит и мусор, и указатели сабмодулей.
    """
    bulk = {
        "commit": ({"-a", "--all"}, "-a/--all"),
        "add": ({"-A", "--all", "-u", "--update", ":/", "."}, "-A/--all/-u/./:/"),
    }[subcommand]
    flags, human = bulk

    hit = [a for a in args if a in flags]
    # Склеенные короткие флаги: -am, -au и т.п.
    for a in args:
        if re.fullmatch(r"-[a-zA-Z]{2,}", a) and any(f[1:] in a[1:] for f in flags if len(f) == 2):
            hit.append(a)

    if hit:
        block(
            "`git {} {}` запрещён: массовый стейдж втягивает мусор и указатели сабмодулей,\n"
            "которые в этом проекте не ведут.\n"
            "Перечисляй файлы явно: git add <путь> [<путь> ...], затем git commit -m \"...\".\n".format(
                subcommand, human
            )
        )


def switch_target(repo_dir, subcommand, args):
    """На какой ветке окажемся после switch/checkout.

    Возвращает имя ветки, "" для detached HEAD или UNKNOWN, если разобрать не удалось
    (например, `git checkout -- <файл>` — ветку он не меняет вовсе).
    """
    if subcommand == "switch":
        create_flags = {"-c", "-C", "--create", "--force-create", "--orphan"}
    else:
        create_flags = {"-b", "-B", "--orphan"}

    opts_with_value = {"-t", "--track", "--start-point", "--conflict"}

    i = 0
    while i < len(args):
        arg = args[i]

        if arg == "--":
            return UNKNOWN                      # дальше пути, а не ветка

        if arg in create_flags:
            return args[i + 1] if i + 1 < len(args) else UNKNOWN

        for opt in ("--create=", "--orphan="):
            if arg.startswith(opt):
                return arg[len(opt):]

        if arg in ("-d", "--detach"):
            return ""                            # detached HEAD

        if arg in opts_with_value:
            i += 2
            continue

        if arg.startswith("-"):
            i += 1
            continue

        # Первый позиционный аргумент. Для switch это всегда ветка, для checkout —
        # ветка или путь, различаем через rev-parse.
        if subcommand == "switch" or is_ref(repo_dir, arg):
            return arg
        return UNKNOWN

    return UNKNOWN


def check_checkout(repo_dir, subcommand, args):
    """`git checkout` по путям = откат рабочего дерева, только под другим именем.

    Переключение ветки безобидно и разрешено, а `git checkout -- <файл>` затирает
    несохранённую правку ровно так же, как `git restore <файл>`. Префиксом эти две формы
    не различить — они начинаются одинаково, поэтому разбирает их хук.
    """
    if subcommand != "checkout":
        return

    if "--" in args:
        block(
            "`git checkout -- <путь>` запрещён: он затирает рабочее дерево, а незакоммиченные\n"
            "правки не восстановимы ничем — ни объектом в git, ни reflog.\n"
            "Нужно откатить свою же правку — верни файл через Edit/Write.\n"
            "Снять лишнее из индекса можно так: git restore --staged <путь>.\n"
        )

    positional = []
    i = 0
    skip_value = {"-b", "-B", "--orphan", "-t", "--track", "--conflict"}
    while i < len(args):
        arg = args[i]
        if arg in skip_value:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        positional.append(arg)
        i += 1

    if not positional:
        return

    # `git checkout <файл>` — путь, а не ветка; `git checkout <ветка> <файл>` — тоже откат.
    if not is_ref(repo_dir, positional[0]) or len(positional) > 1:
        block(
            "`git checkout <путь>` запрещён: это откат рабочего дерева под другим именем —\n"
            "несохранённые правки он затирает безвозвратно.\n"
            "Переключение ветки (`git checkout <ветка>`, `git switch <ветка>`) разрешено.\n"
            "Нужно откатить свою же правку — верни файл через Edit/Write.\n"
        )


def check_reset(args):
    """`git reset --hard|--keep|--merge` на любой позиции — затирает рабочее дерево.

    `git reset <ref>` без этих флагов трогает только индекс и историю ветки, коммиты
    остаются в reflog — это обратимо и разрешено.
    """
    hard = {"--hard", "--keep", "--merge"}
    hit = [a for a in args if a in hard]
    if hit:
        block(
            "`git reset {}` запрещён: он затирает рабочее дерево, а незакоммиченные правки\n"
            "не восстановимы ничем — ни объектом в git, ни reflog.\n"
            "Сдвинуть ветку, сохранив файлы: git reset --soft <ref> (или без флага).\n"
            "Нужно откатить свою же правку — верни файл через Edit/Write.\n".format(hit[0])
        )


def check_commit(repo_dir, branch):
    if branch == "":
        block(
            "Коммит заблокирован: в {} сейчас detached HEAD.\n"
            "Заведи ветку под задачу (git switch -c <TICKET>) и повтори коммит.\n".format(repo_dir)
        )
    if branch in PROTECTED or branch.startswith(PROTECTED_PREFIXES or ()):
        protected = ", ".join(sorted(PROTECTED) + [p + "*" for p in PROTECTED_PREFIXES])
        block(
            "Коммит в защищённую ветку '{}' запрещён (репозиторий {}).\n"
            "Заведи ветку под задачу: git switch -c <TICKET> — и коммить в неё.\n"
            "Защищённые ветки: {}.\n".format(branch, repo_dir, protected)
        )

    gitlinks = staged_gitlinks(repo_dir)
    if gitlinks:
        block(
            "Коммит заблокирован: в индексе {} лежат указатели сабмодулей:\n  {}\n\n"
            "Указатели модулей в этом проекте не ведут — они намеренно выкидываются из коммитов\n"
            "(ignore = all в .gitmodules), а указатель на непушенный коммит ломает\n"
            "`git submodule update` у всей команды. Сними их из индекса и повтори:\n"
            "  git restore --staged {}\n\n"
            "Правки внутри модуля коммитятся В САМОМ САБМОДУЛЕ (cd modules/<Module>),\n"
            "шага «обновить указатель в основном репо» в этом проекте нет.\n".format(
                repo_dir, "\n  ".join(gitlinks), " ".join(shlex.quote(p) for p in gitlinks)
            )
        )


def check_restore(args):
    if "--worktree" in args or "-W" in args:
        block(
            "`git restore --worktree` запрещён: он затирает несохранённые правки безвозвратно.\n"
            "Нужно откатить свою правку — верни файл через Edit/Write.\n"
            "Снять лишнее из индекса можно так: git restore --staged <путь>.\n"
        )

    if "--staged" not in args and "-S" not in args:
        block(
            "`git restore` без `--staged` запрещён: он затирает рабочее дерево, а незакоммиченные\n"
            "правки не восстановимы ничем — ни объектом в git, ни reflog.\n"
            "Разрешена только работа с индексом: git restore --staged <путь>.\n"
            "Нужно откатить свою же правку — верни файл через Edit/Write.\n"
        )


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    command = (payload.get("tool_input") or {}).get("command") or ""
    if not any(trigger in command for trigger in TRIGGERS):
        sys.exit(0)

    cwd = payload.get("cwd") or os.getcwd()
    # Ветка, на которой окажется репозиторий к моменту коммита в этой же цепочке
    # (`git switch -c CRM-1 && git commit`). Ключ — каталог, значение — имя ветки,
    # "" для detached HEAD. Отсутствие ключа означает «спрашивай у git».
    branch_after = {}

    for segment in split_segments(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            # Неразбираемая цитата — не наше дело, пусть решает permissions.
            continue

        # `cd X` в цепочке меняет каталог для следующих сегментов.
        if tokens and tokens[0] == "cd" and len(tokens) > 1:
            cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(tokens[1])))
            continue

        subcommand, args, dir_override = parse_git_invocation(tokens)
        if subcommand is None:
            continue

        if subcommand == "restore":
            check_restore(args)
            continue

        if subcommand == "add":
            check_bulk_flags("add", args)
            continue

        if subcommand == "reset":
            check_reset(args)
            continue

        if subcommand not in ("commit", "switch", "checkout"):
            continue

        if subcommand == "commit":
            check_bulk_flags("commit", args)

        repo_dir = cwd
        if dir_override:
            repo_dir = os.path.normpath(os.path.join(cwd, os.path.expanduser(dir_override)))
        if not os.path.isdir(repo_dir):
            continue

        if subcommand in ("switch", "checkout"):
            check_checkout(repo_dir, subcommand, args)
            target = switch_target(repo_dir, subcommand, args)
            if target is UNKNOWN:
                branch_after.pop(repo_dir, None)
            else:
                branch_after[repo_dir] = target
            continue

        branch = branch_after.get(repo_dir)
        if branch is None:
            branch = current_branch(repo_dir)
            if branch is None:
                # Не git-репозиторий или git недоступен — пусть git сам скажет об этом.
                continue

        check_commit(repo_dir, branch)

    sys.exit(0)


if __name__ == "__main__":
    main()
