#!/usr/bin/env bash
#
# Заводит профили клиентов для db-query.sh — ~/.config/pbe-mssql/<мнемоника>.env.
#
#   db-profiles-init.sh                       # создать/проверить все профили
#   db-profiles-init.sh --dry-run             # показать, что сделает, не трогая файлы
#   db-profiles-init.sh --user svc_ai_ivanov  # заодно завести common.env с учёткой
#   db-profiles-init.sh --force               # переписать сервер там, где он разошёлся
#   db-profiles-init.sh --list                # таблица «профиль — сервер» из скрипта
#
# ИМЯ ПРОФИЛЯ — МНЕМОНИКА КЛИЕНТА (vlt, srv, bay), а не его название. Та же,
# что в тегах Трекера и в консоли, только в нижнем регистре. Профили, заведённые
# по старым именам (valenta.env, servier.env), скрипт не трогает и не удаляет —
# он перечислит их как «вне списка»; лишние снести руками.
#
# ИДЕМПОТЕНТНОСТЬ. Прогонять можно сколько угодно раз: файл с тем же сервером
# не переписывается, только чинится chmod 600. Дублей не будет — на клиента
# один файл, имя файла и есть ключ.
#
# ЧТО ДЕЛАЕТ ПРИ РАСХОЖДЕНИИ. Если в файле стоит другой сервер, скрипт его
# НЕ трогает: пишет предупреждение и завершается кодом 1. Молча переписать
# сервер нельзя — запрос уйдёт не в тот контур и вернёт правдоподобные чужие
# цифры. Согласен с новым значением — прогони с --force.
#
# Прочие строки профиля (PBE_MSSQL_DB, PBE_MSSQL_USER) сохраняются: правится
# ровно строка PBE_MSSQL_SERVER, файл целиком не перезаписывается.
#
# Пароль скрипт не трогает — он в Keychain:
#   security add-generic-password -s pbe-mssql -a "$USER" -w '<пароль>'
#
# Коды возврата: 0 — всё сошлось, 1 — есть расхождения (нужен --force), 2 — ошибка вызова.

set -euo pipefail

# профиль|сервер|комментарий в шапку файла
#
# Профиль клиента называется его МНЕМОНИКОЙ — той же, что в Трекере и консоли
# (docs/regulations/tracker-queues.md, раздел «Клиентская мнемоника и Теги»),
# в нижнем регистре. Так `--profile vlt` в db-query.sh, тег `VLT` в задаче и
# клиент в консоли указывают на одного и того же клиента, а не на трёх разных.
# Тестовый контур — та же мнемоника с суффиксом `-qa`.
PROFILES=(
  "dev|212.8.235.60|дев-среда (общая, не клиент)"
  "vlt|91.107.87.131|Валента, прод"
  "vlt-qa|212.8.235.60|Валента, тест (на сервере дев-среды)"
  "avx|172.30.1.9|Авексима, прод"
  "avx-qa|212.8.235.60|Авексима, тест (на сервере дев-среды)"
  "alcea|172.30.2.12|Алцея, прод"
  "bsn|172.30.2.5|Безен, прод"
  "may|172.30.2.13|Майоли, прод"
  "bay|pbesql02p|Байер, прод"
  "bay-qa|pbesql04t|Байер, QA"
  "boe|172.20.15.10|Берингер, прод"
  "boe-qa|pbesql03t|Берингер, QA"
  "roc|pbesql04p|РОШ, прод"
  "roc-qa|172.20.14.12|РОШ, QA"
  "srv|pbesql03p|Сервье, прод"
  "srv-qa|172.20.14.11|Сервье, QA"
)

DRY=0; FORCE=0; USER_ACCOUNT=""; MODE="run"

usage() { sed -n '2,/^set -euo/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --force)   FORCE=1; shift ;;
    --user)    USER_ACCOUNT="${2:-}"; shift 2 ;;
    --list)    MODE="list"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Неизвестный аргумент: $1 (см. --help)" >&2; exit 2 ;;
  esac
done

CONF_DIR="${PBE_MSSQL_CONF_DIR:-$HOME/.config/pbe-mssql}"

if [[ "$MODE" == "list" ]]; then
  # шапка выравнивается вручную: printf считает байты, а кириллица в UTF-8 двухбайтовая
  printf '%s\n' "ПРОФИЛЬ    СЕРВЕР           ЧТО ЭТО"
  for row in "${PROFILES[@]}"; do
    IFS='|' read -r name server note <<< "$row"
    printf '%-10s %-16s %s\n' "$name" "$server" "$note"
  done
  exit 0
fi

say() { printf '%s\n' "$*"; }
run() { [[ "$DRY" -eq 1 ]] || "$@"; }

# Значение ключа из env-файла: последнее непустое присваивание, как его увидит `.`
value_of() { grep -E "^${2}=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- || true; }

# Правит одну строку в файле, не трогая остальные. Нет строки — дописывает.
set_key() {
  local file="$1" key="$2" val="$3" tmp
  if grep -qE "^${key}=" "$file" 2>/dev/null; then
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$val" '
      $0 ~ "^" k "=" { if (!done) { print k "=" v; done=1 } ; next }
      { print }
      END { if (!done) print k "=" v }
    ' "$file" > "$tmp"
    cat "$tmp" > "$file"      # не mv: сохраняем владельца и права уже созданного файла
    rm -f "$tmp"
  else
    printf '%s=%s\n' "$key" "$val" >> "$file"
  fi
}

say "Каталог профилей: $CONF_DIR${DRY:+}"
[[ "$DRY" -eq 1 ]] && say "(dry-run: файлы не меняются)"
run mkdir -p "$CONF_DIR"
run chmod 700 "$CONF_DIR"

created=0; ok=0; fixed=0; conflicts=0

# --- общая учётка -----------------------------------------------------------
COMMON="$CONF_DIR/common.env"
if [[ -n "$USER_ACCOUNT" ]]; then
  if [[ -f "$COMMON" ]]; then
    cur="$(value_of "$COMMON" PBE_MSSQL_USER)"
    if [[ "$cur" == "$USER_ACCOUNT" ]]; then
      say "  common.env      ok            $USER_ACCOUNT"
    else
      say "  common.env      учётка        $cur -> $USER_ACCOUNT"
      run set_key "$COMMON" PBE_MSSQL_USER "$USER_ACCOUNT"
      fixed=$((fixed+1))
    fi
  else
    say "  common.env      создан        $USER_ACCOUNT"
    [[ "$DRY" -eq 1 ]] || printf '# учётная запись ИИ-агента, одна на все контуры\nPBE_MSSQL_USER=%s\n' "$USER_ACCOUNT" > "$COMMON"
    created=$((created+1))
  fi
  run chmod 600 "$COMMON"
elif [[ ! -f "$COMMON" ]]; then
  say "  common.env      НЕТ           учётка не задана: прогони с --user svc_ai_<фамилия>"
fi

# --- клиенты ----------------------------------------------------------------
for row in "${PROFILES[@]}"; do
  IFS='|' read -r name server note <<< "$row"
  file="$CONF_DIR/$name.env"

  if [[ ! -f "$file" ]]; then
    say "  $(printf '%-10s' "$name")  создан        $server"
    [[ "$DRY" -eq 1 ]] || printf '# %s\nPBE_MSSQL_SERVER=%s\n' "$note" "$server" > "$file"
    run chmod 600 "$file"
    created=$((created+1))
    continue
  fi

  cur="$(value_of "$file" PBE_MSSQL_SERVER)"
  if [[ "$cur" == "$server" ]]; then
    say "  $(printf '%-10s' "$name")  ok            $server"
    ok=$((ok+1))
  elif [[ -z "$cur" ]]; then
    say "  $(printf '%-10s' "$name")  дописан       $server"
    run set_key "$file" PBE_MSSQL_SERVER "$server"
    fixed=$((fixed+1))
  elif [[ "$FORCE" -eq 1 ]]; then
    say "  $(printf '%-10s' "$name")  переписан     $cur -> $server"
    run set_key "$file" PBE_MSSQL_SERVER "$server"
    fixed=$((fixed+1))
  else
    say "  $(printf '%-10s' "$name")  РАСХОЖДЕНИЕ   в файле $cur, в скрипте $server"
    conflicts=$((conflicts+1))
  fi
  run chmod 600 "$file"
done

say ""
say "Итого: создано $created, без изменений $ok, поправлено $fixed, расхождений $conflicts."

# Чужие профили, которых нет в скрипте, — не ошибка, но о них стоит знать.
if compgen -G "$CONF_DIR/*.env" > /dev/null; then
  extra=""
  for f in "$CONF_DIR"/*.env; do
    n="$(basename "$f" .env)"
    [[ "$n" == "common" ]] && continue
    known=0
    for row in "${PROFILES[@]}"; do [[ "${row%%|*}" == "$n" ]] && { known=1; break; }; done
    [[ "$known" -eq 0 ]] && extra="$extra $n"
  done
  if [[ -n "$extra" ]]; then
    say "Профили вне списка (скрипт их не трогает):$extra"
    say "Профили по старым именам клиентов заменены мнемониками — лишние удалить руками."
  fi
fi

if [[ "$conflicts" -gt 0 ]]; then
  say ""
  say "Сервер в файле не совпадает со скриптом. Прав скрипт — прогони с --force;"
  say "прав файл — поправь список PROFILES в $0."
  exit 1
fi
exit 0
