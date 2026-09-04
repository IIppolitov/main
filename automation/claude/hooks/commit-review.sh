#!/usr/bin/env bash
#
# PreToolUse-хук на Bash: коммит читает человек — вместо коммита агент выводит сводку.
#
# Хук спит, пока разработчик не попросит спрашивать про коммит в своём
# .claude/settings.local.json. Подробности и причины — в commit_review.py.
#
# Вход  — JSON на stdin. Выход — код 0: пропустить; код 2: заблокировать.
#
# Нет python3 — выходим молча, в отличие от guard-protected-branch.sh. Тот
# предохранитель, и его отсутствие надо заметить; этот — удобство, и ломать им
# работу на ненастроенной машине нельзя.

set -uo pipefail

command -v python3 >/dev/null 2>&1 || exit 0

exec python3 "$(dirname "$0")/commit_review.py"
