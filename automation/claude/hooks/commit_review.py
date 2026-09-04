#!/usr/bin/env python3
"""Первый коммит по каждому состоянию индекса заменяется сводкой для человека.

Хук ничего не спрашивает и ничьих настроек не читает. Он один раз прерывает `git commit`
и отдаёт агенту инструкцию: выведи сводку по тому, что уходит в коммит, и повтори команду.
Повтор с тем же индексом хук пропускает — дальше решают обычные permissions:

* `git commit` в allow — коммит выполняется, сводка просто остаётся на экране;
* `git commit` в ask — появляется окно, и человек отвечает, уже прочитав сводку.

Так оба варианта живут от одного файла и без личных настроек у разработчика.

Зачем это вообще. Само по себе `permissions.ask` останавливает коммит, но при отказе
отдаёт агенту служебное «STOP what you are doing and wait for the user» — агент замирает,
и что он сделал, приходится спрашивать отдельно. Хука на отказ человека в Claude Code нет,
перехватить нечем. Значит, сводка должна появляться ДО окна, а не после отказа: тогда отказ
ничего не стоит — всё нужное уже на экране. Текстом, которым блокирует хук, распоряжаемся
мы сами, он уходит агенту как инструкция, — на этом всё и держится.

Отметка о показанной сводке живёт во временном каталоге и привязана к содержимому индекса
и сессии: следующий коммит с другим набором изменений снова получит сводку.

Вход  — JSON на stdin (tool_name, tool_input.command, cwd, session_id).
Выход — код 0: пропустить; код 2: прервать, текст из stderr уходит агенту.

Разовое отключение на сессию: PBE_AI_COMMIT=1 claude
"""

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

GLOBAL_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
SEGMENT_SEPARATORS = re.compile(r"&&|\|\||;|\n|\|")

MARKER_DIR = os.path.join(tempfile.gettempdir(), "claude-commit-review")
MARKER_TTL = 24 * 3600


def parse_git_invocation(tokens):
    """Возвращает подкоманду git или None, если это не git."""
    if not tokens or tokens[0] != "git":
        return None

    i = 1
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("-"):
            return token
        i += 2 if token in GLOBAL_OPTS_WITH_VALUE else 1

    return None


def commits(command):
    """True, если в команде есть настоящий `git commit`, а не упоминание в строке."""
    for segment in SEGMENT_SEPARATORS.split(command):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if parse_git_invocation(tokens) == "commit" and "--dry-run" not in tokens:
            return True
    return False


def staged_key(cwd, session_id, command):
    """Отпечаток «этот индекс в этой сессии». Меняется вместе с содержимым индекса."""
    # Команда в ключ не идёт: правка сообщения коммита не должна требовать второй
    # сводки — код-то тот же. Если git недоступен, ключ хотя бы разводит команды.
    parts = [session_id or "", command]
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--no-color"],
            cwd=cwd, capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            parts = [session_id or "", os.path.abspath(cwd), result.stdout]
    except (OSError, subprocess.SubprocessError):
        pass

    return hashlib.sha1("\x00".join(parts).encode("utf-8", "replace")).hexdigest()


def seen_before(key):
    """True, если сводку по этому индексу уже показывали. Заодно ставит отметку."""
    try:
        os.makedirs(MARKER_DIR, exist_ok=True)
        now = time.time()
        for name in os.listdir(MARKER_DIR):
            path = os.path.join(MARKER_DIR, name)
            try:
                if now - os.path.getmtime(path) > MARKER_TTL:
                    os.remove(path)
            except OSError:
                pass

        marker = os.path.join(MARKER_DIR, key)
        if os.path.exists(marker):
            return True
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(str(int(now)))
    except OSError:
        # Не смогли записать отметку — считаем, что сводки не было. Хуже показать её
        # дважды, чем пропустить коммит мимо чтения.
        return False

    return False


SUMMARY = """Перед коммитом человек читает, что уходит в историю. Это порядок работы,
а не сбой и не запрет: индекс и рабочее дерево не тронуты, всё подготовленное на месте.

Сделай в этом же ходе два шага, в таком порядке.

Шаг 1 — сводка:
1. Ветка и одна строка: что сделано.
2. Файлы. Сначала `git diff --cached --stat`, затем к каждому файлу строка «что
   изменено и зачем». Пути — от корня репозитория, их открывают в IDE.
3. Что проверено (тесты, линтеры, ручной прогон) и чего проверить не удалось.
4. Команда коммита целиком, с полным сообщением, одним блоком для копирования.

Шаг 2 — повтори ту же команду коммита. Второй раз хук её пропустит, дальше решают
обычные разрешения: где коммит разрешён — он выполнится, где стоит вопрос — человек
ответит на него, уже прочитав сводку. Обходить хук другой командой не нужно.

Если человек откажет в окне подтверждения — это не значит, что что-то не так: он
читает код в панели Git своей IDE (VS Code «Source Control», PhpStorm окно «Commit»)
и коммитит сам командой из сводки. Сводка уже выведена, повторять её не нужно.
"""


def main():
    if os.environ.get("PBE_AI_COMMIT") == "1":
        sys.exit(0)

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    command = (payload.get("tool_input") or {}).get("command") or ""
    if "commit" not in command or not commits(command):
        sys.exit(0)

    cwd = payload.get("cwd") or os.getcwd()
    if seen_before(staged_key(cwd, payload.get("session_id"), command)):
        sys.exit(0)

    sys.stderr.write(SUMMARY)
    sys.exit(2)


if __name__ == "__main__":
    main()
