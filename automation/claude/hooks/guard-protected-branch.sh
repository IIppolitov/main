#!/usr/bin/env bash
#
# PreToolUse-хук на Bash: не даёт закоммитить в защищённую ветку или в detached HEAD.
#
# Зачем именно хук, а не deny-лист: permissions в .claude/settings.json матчатся по префиксу
# команды и текущую ветку не видят. «Не коммить в main» — условие о состоянии репозитория,
# проверить его может только исполняемый код. Хук работает и для тебя, и для всех субагентов.
#
# Вход  — JSON на stdin (tool_name, tool_input.command, cwd).
# Выход — код 0: пропустить; код 2: заблокировать, текст из stderr уходит агенту.
#
# Хук НЕ подменяет остальные правила: запрет push/reset/clean/checkout живёт в deny-листе,
# а смысловые правила (ветка под тикет, не бампать указатели сабмодулей) — в CLAUDE.md.

set -uo pipefail

GUARD="$(dirname "$0")/guard_protected_branch.py"

# Нет python3 (типовой случай — Git Bash под Windows): fail-closed узко. Пропускаем всё,
# кроме коммита, — иначе одна ненастроенная машина обесточивает предохранитель молча.
if ! command -v python3 >/dev/null 2>&1; then
  PAYLOAD="$(cat)"
  if printf '%s' "$PAYLOAD" | grep -q 'git commit'; then
    cat >&2 <<'MSG'
Коммит заблокирован: на этой машине нет python3, и хук защиты веток не может проверить,
куда идёт коммит. Поставь python3 (macOS: он есть из коробки; Windows: WSL2 или
python.org) и повтори. Проверить: python3 --version.
MSG
    exit 2
  fi
  exit 0
fi

exec python3 "$GUARD"
