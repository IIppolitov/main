#!/usr/bin/env bash
#
# Подключение к MSSQL под учётной записью ИИ-агента.
#
# Обёртка делает ровно одно: подставляет сервер, логин и пароль. Всё
# остальное — сам sqlcmd, флаги и SQL пишет тот, кто вызывает.
#
#   db-query.sh --profile srv -d crmAdmin -Q "SELECT TOP 5 Id FROM dbo.tPersonConsent"
#   db-query.sh --profile srv -Q "SELECT name FROM sys.databases ORDER BY name"
#   db-query.sh --profile srv -d crmAdmin -i запрос.sql
#   db-query.sh --profiles                              # список клиентов
#   db-query.sh --help                                  # эта справка
#
# Обёртке принадлежат только длинные флаги --profile, --profiles, --help.
# Всё остальное уходит в sqlcmd как есть, включая короткие -h, -W, -y, -Q.
#
# ЗАЧЕМ ОБЁРТКА, ЕСЛИ ЕСТЬ sqlcmd. Чтобы пароль не попадал в контекст агента.
# Собери агент команду сам — ему пришлось бы прочитать пароль и написать его
# в `-P`, то есть отправить во внешний сервис. И так в каждой сессии у каждого
# из четырнадцати человек. Здесь пароль читает шелл из Keychain, модель его
# не видит ни разу.
#
# ЧТО ОБЁРТКА НЕ ДЕЛАЕТ. Не проверяет текст запроса и не ограничивает выборку.
# Единственная гарантия «только чтение» — сама учётная запись: категория
# [AI Agent] это db_datareader и ничего кроме. Держать её такой — обязанность
# того, кто ведёт матрицу доступов, а не этого файла.
# Проверить, что учётки действительно read-only:
#   ../pbe-dba/sql/provisioning/check-ai-agents.sql
#
# ПРОФИЛИ — просто список клиентов, ~/.config/pbe-mssql/, chmod 600.
#
#   common.env      PBE_MSSQL_USER=svc_ai_<фамилия>      — учётка, одна на все контуры
#   <клиент>.env    PBE_MSSQL_SERVER=pbesql03p           — сервер, одна строка
#
# Базу называй флагом sqlcmd `-d <база>`: на сервере клиента их много — своя
# под админку, своя под согласия. Какие есть — спроси у сервера:
#   db-query.sh --profile srv -Q "SELECT name FROM sys.databases ORDER BY name"
#
# Если у клиента база одна и та же всегда, впиши её в профиль строкой
# PBE_MSSQL_DB=<база> — она подставится, когда -d не задан. Заданный -d
# всегда сильнее: профиль задаёт умолчание, а не ограничение.
#
# Профиль клиента может перекрыть учётку своей (PBE_MSSQL_USER) — так живёт
# дата-инженерия, где под витрины заведены отдельные записи.
#
# ПАРОЛЬ ищется по порядку: PBE_MSSQL_PASSWORD в окружении → <клиент>.env →
# common.env → Keychain pbe-mssql-<клиент> → Keychain pbe-mssql.
#
#   security add-generic-password -s pbe-mssql -a "$USER" -w '<пароль>'
#
# --profile обязателен и умолчания не имеет: серверов несколько, они выглядят
# одинаково, и запрос, ушедший не в тот, вернёт чужие цифры — правдоподобные
# и не те.
#
# Каждый вызов пишется в logs/db-query.log (вне git).
#
# Требуется sqlcmd (mssql-tools18):
#   brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release
#   brew install mssql-tools18
#
# Замечание по флагам sqlcmd: -W и -y несовместимы, вызов падает до подключения.
# Нужен неурезанный вывод длинных строк — бери -y 0 и не бери -W.
#
# Коды возврата: 2 — ошибка вызова, 3 — нет профиля или пароля, 4 — нет sqlcmd,
# прочее — код самого sqlcmd.

set -euo pipefail

PROFILE="${PBE_MSSQL_PROFILE:-}"
MODE="run"

# Аргументы для sqlcmd копим в массиве, но разворачиваем везде через
# идиому ${a[@]+"${a[@]}"}. В bash 3.2 — а это ровно тот bash, что стоит
# в macOS по умолчанию, — пустой массив под set -u разворачивается в
# «unbound variable» и роняет скрипт. Проверять надо именно на /bin/bash.
PASSTHRU=()

usage() { sed -n '2,/^set -euo/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)  PROFILE="${2:-}"; shift 2 ;;
    --profiles) MODE="profiles";  shift ;;
    # Только --длинные флаги принадлежат обёртке. Короткие целиком уходят
    # в sqlcmd: -h у него это заголовки, -W, -y, -Q и прочее. Перехватишь
    # -h под справку — и агент не сможет управлять выводом.
    --help)     usage; exit 0 ;;
    --)         shift; PASSTHRU+=("$@"); break ;;
    *)          PASSTHRU+=("$1");  shift ;;
  esac
done

CONF_DIR="${PBE_MSSQL_CONF_DIR:-$HOME/.config/pbe-mssql}"

list_profiles() {
  echo "Каталог профилей: $CONF_DIR"
  if [[ -f "$CONF_DIR/common.env" ]]; then
    echo "  common.env   учётная запись: $(grep -E '^PBE_MSSQL_USER=' "$CONF_DIR/common.env" 2>/dev/null | head -1 | cut -d= -f2- || true)"
  else
    echo "  common.env   нет — учётная запись не задана, см. $0 --help"
  fi
  found=0
  if compgen -G "$CONF_DIR/*.env" > /dev/null; then
    for f in "$CONF_DIR"/*.env; do
      name="$(basename "$f" .env)"
      [[ "$name" == "common" ]] && continue
      # || true обязательно: под set -e grep без совпадения роняет весь скрипт,
      # и список обрывается на первом неполном файле молча.
      srv="$(grep -E '^PBE_MSSQL_SERVER=' "$f" 2>/dev/null | head -1 | cut -d= -f2- || true)"
      printf '  %-14s %s\n' "$name" "${srv:-?}"
      found=1
    done
  fi
  [[ "$found" -eq 0 ]] && echo "  клиентов нет — заведи $CONF_DIR/<клиент>.env со строкой PBE_MSSQL_SERVER="
  return 0
}

if [[ "$MODE" == "profiles" ]]; then
  list_profiles
  exit 0
fi

if [[ -z "$PROFILE" ]]; then
  echo "Не указан клиент: добавь --profile <имя> или задай PBE_MSSQL_PROFILE." >&2
  echo >&2
  list_profiles >&2
  exit 3
fi

# Общая учётка читается первой, профиль клиента её перекрывает.
[[ -f "$CONF_DIR/common.env" ]] && { set -a; . "$CONF_DIR/common.env"; set +a; }

PROFILE_FILE="$CONF_DIR/$PROFILE.env"
if [[ -f "$PROFILE_FILE" ]]; then
  set -a; . "$PROFILE_FILE"; set +a
elif [[ -z "${PBE_MSSQL_SERVER:-}" ]]; then
  cat >&2 <<EOF
Нет профиля клиента «${PROFILE}»: не найден $PROFILE_FILE.

  mkdir -p "$CONF_DIR"

  # один раз на машину — учётная запись
  echo 'PBE_MSSQL_USER=svc_ai_<фамилия>' > "$CONF_DIR/common.env"
  chmod 600 "$CONF_DIR/common.env"
  security add-generic-password -s pbe-mssql -a "\$USER" -w '<пароль>'

  # на каждого клиента — сервер
  echo 'PBE_MSSQL_SERVER=<сервер>' > "$PROFILE_FILE"
  chmod 600 "$PROFILE_FILE"

Учётная запись — категории AI Agent, одна на разработчика, read-only.
Заводит её DBA процедурой spProvisionAiAgent.
EOF
  exit 3
fi

keychain() { security find-generic-password -s "$1" -w 2>/dev/null || true; }
if [[ -z "${PBE_MSSQL_PASSWORD:-}" ]] && command -v security > /dev/null 2>&1; then
  PBE_MSSQL_PASSWORD="$(keychain "pbe-mssql-$PROFILE")"
  [[ -z "$PBE_MSSQL_PASSWORD" ]] && PBE_MSSQL_PASSWORD="$(keychain "pbe-mssql")"
fi

: "${PBE_MSSQL_SERVER:=}"; : "${PBE_MSSQL_USER:=}"; : "${PBE_MSSQL_PASSWORD:=}"

if [[ -z "$PBE_MSSQL_SERVER" || -z "$PBE_MSSQL_USER" || -z "$PBE_MSSQL_PASSWORD" ]]; then
  echo "Профиль «${PROFILE}» неполон: нужны сервер, учётная запись и пароль." >&2
  echo "Сервер — в $PROFILE_FILE. Учётка — там же или в $CONF_DIR/common.env." >&2
  echo "Пароль — в PBE_MSSQL_PASSWORD, в этих же файлах или в Keychain (pbe-mssql-$PROFILE, затем pbe-mssql)." >&2
  exit 3
fi

# База из профиля — только как умолчание. Если -d (или -D) уже есть среди
# аргументов, ничего не подставляем: два -d в одной строке это ребус,
# а не конфигурация.
HAS_DB=0
for a in ${PASSTHRU[@]+"${PASSTHRU[@]}"}; do
  [[ "$a" == "-d" ]] && { HAS_DB=1; break; }
done
if [[ "$HAS_DB" -eq 0 && -n "${PBE_MSSQL_DB:-}" ]]; then
  PASSTHRU=(-d "$PBE_MSSQL_DB" ${PASSTHRU[@]+"${PASSTHRU[@]}"})
fi

SQLCMD=""
for c in sqlcmd /opt/homebrew/bin/sqlcmd /usr/local/bin/sqlcmd \
         /opt/mssql-tools18/bin/sqlcmd /opt/mssql-tools/bin/sqlcmd; do
  if command -v "$c" > /dev/null 2>&1; then SQLCMD="$c"; break; fi
done
if [[ -z "$SQLCMD" ]]; then
  echo "Не найден sqlcmd. macOS: brew install mssql-tools18" >&2
  exit 4
fi

LOG_DIR="${PBE_DE_LOG_DIR:-logs}"
mkdir -p "$LOG_DIR" 2>/dev/null || true
printf '%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$PROFILE" \
  "${PBE_MSSQL_USER}@${PBE_MSSQL_SERVER}" "${PASSTHRU[*]+${PASSTHRU[*]}}" \
  >> "$LOG_DIR/db-query.log" 2>/dev/null || true

# -C: доверять сертификату сервера. mssql-tools18 шифрует соединение и
# проверяет сертификат, на самоподписанном без этого флага — отказ.
exec "$SQLCMD" -S "$PBE_MSSQL_SERVER" -U "$PBE_MSSQL_USER" -P "$PBE_MSSQL_PASSWORD" -C ${PASSTHRU[@]+"${PASSTHRU[@]}"}
