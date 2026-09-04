#!/usr/bin/env bash
#
# Выгружает задачи из Яндекс Трекера в markdown на stdout.
#
#   tracker-issue.sh SUPPORTDEV-1788
#   tracker-issue.sh SUPPORTDEV-1788 CRM-544            # несколько задач за вызов
#   tracker-issue.sh https://tracker.yandex.ru/CRM-544
#   tracker-issue.sh CRM-544 --links                    # + тела связанных задач (1 уровень)
#   tracker-issue.sh CRM-544 --attach-dir /tmp/att --all-attachments
#
# Несколько задач — типовой случай: постановка аналитика + задача на разработку по ней.
# Выгружаются в один поток, разделитель между задачами — строка `---`.
#
# --links выключен по умолчанию намеренно: relates в трекере вешают щедро, к одной постановке
# легко прилипает 5-15 задач из соседних контуров. Связи перечисляются всегда, но их тела
# подтягиваются только по флагу и только на один уровень — задача, подтянутая по связи,
# свои связи только перечисляет.
#
# Токен и ID организации (в порядке приоритета):
#   1. переменные окружения YANDEX_TRACKER_TOKEN / YANDEX_TRACKER_ORG_ID
#   2. macOS Keychain: сервисы yandex-tracker и yandex-tracker-org
#   3. файл ~/.config/yandex-tracker/env (chmod 600)
#
# Токен в репозиторий не коммитится ни при каком раскладе — см. .claude/scripts/README.md
#
# Коды возврата: 2 — ошибка вызова, 3 — нет учётных данных, 1 — не выгружено ни одной задачи.
# Если часть задач выгрузилась, а часть нет — 0, причины в stderr.

set -uo pipefail

API="https://api.tracker.yandex.net/v3"
ATTACH_DIR=""
ALL_ATTACHMENTS=0
FOLLOW_LINKS=0
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --attach-dir)       ATTACH_DIR="${2:-}"; shift 2 ;;
    --all-attachments)  ALL_ATTACHMENTS=1; shift ;;
    --links)            FOLLOW_LINKS=1; shift ;;
    -h|--help)          sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)                 echo "Неизвестный флаг: $1" >&2; exit 2 ;;
    *)                  ARGS+=("$1"); shift ;;
  esac
done

if [[ ${#ARGS[@]} -eq 0 ]]; then
  echo "Не указан ключ задачи. Пример: tracker-issue.sh SUPPORTDEV-1788 [CRM-544 ...] [--links]" >&2
  exit 2
fi

# --- ключ задачи: из URL или как есть -----------------------------------------
# Понимает: KEY-1, ссылку со схемой и без неё, с /подстраницей, ?query и #anchor.
normalize_key() {
  local raw="$1" key
  key="$(printf '%s' "$raw" \
    | sed -E 's#^([a-zA-Z]+://)?[^/]*\.[^/]*/##; s#^(pages/)?##; s#[?#].*$##; s#/.*$##' \
    | tr '[:lower:]' '[:upper:]')"

  if [[ ! "$key" =~ ^[A-Z][A-Z0-9_]*-[0-9]+$ ]]; then
    echo "Не разобрал ключ задачи из '$raw' (получилось '$key'). Ожидаю вид QUEUE-123." >&2
    return 1
  fi

  printf '%s' "$key"
}

# --- учётные данные ------------------------------------------------------------
keychain() { security find-generic-password -s "$1" -w 2>/dev/null || true; }

if [[ -z "${YANDEX_TRACKER_TOKEN:-}" || -z "${YANDEX_TRACKER_ORG_ID:-}" ]]; then
  ENV_FILE="${YANDEX_TRACKER_ENV_FILE:-$HOME/.config/yandex-tracker/env}"
  if [[ -f "$ENV_FILE" ]]; then
    set -a; . "$ENV_FILE"; set +a
  fi
fi
: "${YANDEX_TRACKER_TOKEN:=$(keychain yandex-tracker)}"
: "${YANDEX_TRACKER_ORG_ID:=$(keychain yandex-tracker-org)}"

if [[ -z "$YANDEX_TRACKER_TOKEN" || -z "$YANDEX_TRACKER_ORG_ID" ]]; then
  cat >&2 <<'EOF'
Нет доступа к Трекеру: не найдены YANDEX_TRACKER_TOKEN и/или YANDEX_TRACKER_ORG_ID.

Разовая настройка (macOS Keychain, рекомендуется):
  security add-generic-password -s yandex-tracker     -a "$USER" -w '<OAuth-токен>'
  security add-generic-password -s yandex-tracker-org -a "$USER" -w '<ID организации>'

Токен: https://yandex.ru/support/tracker/ru/concepts/access
ID организации Яндекс 360: https://admin.yandex.ru → главная страница, поле «ID организации».
EOF
  exit 3
fi

# --- HTTP ----------------------------------------------------------------------
# Ключ передаётся параметром, а не берётся из глобальной переменной: при обходе
# нескольких задач глобальная соврала бы в сообщении об ошибке.
api() {
  local url="$1" key="$2" body code
  body="$(curl -sS -w $'\n%{http_code}' \
    -H "Authorization: OAuth ${YANDEX_TRACKER_TOKEN}" \
    -H "X-Org-ID: ${YANDEX_TRACKER_ORG_ID}" \
    -H "Accept: application/json" \
    "$url")" || { echo "Сеть недоступна при запросе $key." >&2; return 1; }
  code="${body##*$'\n'}"
  body="${body%$'\n'*}"

  case "$code" in
    200) printf '%s' "$body"; return 0 ;;
    401) echo "401: токен не принят. Проверь YANDEX_TRACKER_TOKEN (OAuth-токен, не пароль)." >&2 ;;
    403) echo "403: нет прав на $key — или неверный X-Org-ID (${YANDEX_TRACKER_ORG_ID})." >&2 ;;
    404) echo "404: задача $key не найдена." >&2 ;;
    *)   echo "HTTP $code от $url" >&2; printf '%s\n' "$body" | head -c 500 >&2 ;;
  esac
  return 1
}

# --- дедупликация ключей -------------------------------------------------------
# Строка-аккумулятор, а не ассоциативный массив: работает на bash 3.2 (штатный на macOS).
SEEN=" "
is_seen()   { [[ "$SEEN" == *" $1 "* ]]; }
mark_seen() { SEEN="${SEEN}${1} "; }

LINK_QUEUE=()
PRINTED=0
DUMPED=0

# --- выгрузка одной задачи -----------------------------------------------------
# dump_issue <ключ> <подтянута_по_связи 0|1>
dump_issue() {
  local KEY="$1" VIA_LINK="$2"
  local ISSUE COMMENTS ATTACHMENTS

  ISSUE="$(api "${API}/issues/${KEY}?expand=links" "$KEY")" || {
    echo "‼️ $KEY — выгрузить не удалось (причина выше в stderr)."
    return 1
  }
  COMMENTS="$(api "${API}/issues/${KEY}/comments?perPage=100" "$KEY")" || COMMENTS='[]'
  ATTACHMENTS="$(api "${API}/issues/${KEY}/attachments" "$KEY")" || ATTACHMENTS='[]'

  if [[ "$PRINTED" -eq 1 ]]; then
    printf '\n---\n\n'
  fi
  PRINTED=1

  jq -r --arg key "$KEY" --arg via "$VIA_LINK" '
    def d(f): (f.display // f.id // f.key // "—");
    "# \($key): \(.summary // "(без темы)")",
    "",
    (if $via == "1" then "> ⚠️ Подтянута по связи (`--links`). Её собственные связи только перечислены.\n" else empty end),
    "https://tracker.yandex.ru/\($key)",
    "",
    "| | |",
    "|---|---|",
    "| Статус | \(d(.status // {})) |",
    "| Тип | \(d(.type // {})) |",
    "| Приоритет | \(d(.priority // {})) |",
    "| Очередь | \(d(.queue // {})) |",
    "| Автор | \(d(.createdBy // {})) |",
    "| Исполнитель | \(d(.assignee // {})) |",
    "| Создана | \(.createdAt // "—") |",
    "| Обновлена | \(.updatedAt // "—") |",
    (if (.tags // []) | length > 0 then "| Теги | \(.tags | join(", ")) |" else empty end)
  ' <<<"$ISSUE"

  # Кастомные поля очереди — там часто живут версия приложения, платформа, клиент.
  jq -r '
    [ "id","key","self","version","summary","description","status","previousStatus","type",
      "priority","queue","project","sprint","parent","assignee","createdBy","updatedBy",
      "followers","access","createdAt","updatedAt","lastCommentUpdatedAt","statusStartTime",
      "statusType","votes","favorite","tags","aliases","links","commentWithExternalMessageCount",
      "pendingReplyFrom","start","end","deadline","resolution","resolvedAt","resolvedBy" ] as $sys
    | to_entries
    | map(select((.key | IN($sys[]) | not) and .value != null and .value != [] and .value != ""))
    | if length > 0 then
        "\n## Поля задачи\n",
        (.[] | "- **\(.key)**: \(
          if (.value|type) == "object" then (.value.display // .value.id // (.value|tostring))
          elif (.value|type) == "array" then (.value | map(if type=="object" then (.display // .id // tostring) else tostring end) | join(", "))
          else (.value|tostring) end)")
      else empty end
  ' <<<"$ISSUE"

  jq -r '
    if (.description // "") | length > 0
    then "\n## Описание\n", .description
    else "\n## Описание\n\n_(пустое)_" end
  ' <<<"$ISSUE"

  jq -r --arg via "$VIA_LINK" --arg follow "$FOLLOW_LINKS" '
    (.links // []) as $l
    | if ($l | length) > 0 then
        "\n## Связи\n",
        ($l[] | "- \(.type.display // .type.id): \(.object.key // "?") — \(.object.display // "")"),
        (if $via == "1" then "\n_(тела не выгружены: раскрытие связей идёт только на один уровень)_"
         elif $follow == "0" then "\n_(тела не выгружены — добавь `--links`, если нужны)_"
         else empty end)
      else empty end
  ' <<<"$ISSUE"

  jq -r '
    if length > 0 then
      "\n## Комментарии (\(length))\n",
      (.[] | "### \(.createdBy.display // "?") — \(.createdAt // "")\n\n\(.text // "_(без текста)_")\n")
    else "\n## Комментарии\n\n_(нет)_" end
  ' <<<"$COMMENTS"

  # --- вложения ---------------------------------------------------------------
  local ATT_COUNT DIR safe dest id idsafe name mimetype size content
  ATT_COUNT="$(jq -r 'length' <<<"$ATTACHMENTS")"
  if [[ "$ATT_COUNT" -gt 0 ]]; then
    echo
    echo "## Вложения ($ATT_COUNT)"
    echo

    # Подкаталог на задачу: одноимённые screenshot.png из разных задач иначе затрут друг друга.
    # Внутри задачи от коллизии спасает не каталог, а префикс из id вложения — см. ниже.
    if [[ -n "$ATTACH_DIR" ]]; then
      DIR="$ATTACH_DIR/$KEY"
    else
      DIR="${TMPDIR:-/tmp}/tracker-attachments/${KEY}"
    fi
    mkdir -p "$DIR"

    while IFS=$'\t' read -r id name mimetype size content; do
      [[ -n "$name" ]] || continue
      # Имя файла = <id вложения>-<имя>. Внутри одной задачи имена повторяются штатно:
      # каждый вставленный из буфера скриншот Трекер называет `image.png`, и без префикса
      # шесть вложений задачи схлопывались в один файл — на диске оставалось последнее.
      idsafe="$(printf '%s' "$id" | tr -c '[:alnum:]._-' '_')"
      safe="$(printf '%s' "$name" | tr -c '[:alnum:]._-' '_')"
      dest="$DIR/${idsafe}-${safe}"

      if [[ "$ALL_ATTACHMENTS" -eq 1 || "$mimetype" == image/* ]]; then
        if curl -sSf -o "$dest" \
             -H "Authorization: OAuth ${YANDEX_TRACKER_TOKEN}" \
             -H "X-Org-ID: ${YANDEX_TRACKER_ORG_ID}" \
             "$content"; then
          echo "- \`$name\` ($mimetype, $size Б) → скачано: $dest"
        else
          # curl -f при HTTP-ошибке уже создал пустой файл: пустышка в каталоге вложений
          # выглядит как скачанный файл, поэтому убираем.
          rm -f "$dest"
          echo "- \`$name\` ($mimetype, $size Б) — скачать не удалось"
        fi
      else
        echo "- \`$name\` ($mimetype, $size Б) — не скачано (не изображение; \`--all-attachments\` чтобы забрать)"
      fi
    done < <(jq -r '.[] | [(.id // "noid"), .name, (.mimetype // "?"), (.size // 0), .content] | @tsv' <<<"$ATTACHMENTS")
  fi

  # Связи собираем только у явно запрошенных задач — второго уровня нет.
  if [[ "$FOLLOW_LINKS" -eq 1 && "$VIA_LINK" -eq 0 ]]; then
    local lk
    while IFS= read -r lk; do
      [[ -n "$lk" ]] && LINK_QUEUE+=("$lk")
    done < <(jq -r '(.links // [])[] | .object.key // empty' <<<"$ISSUE")
  fi

  DUMPED=$((DUMPED + 1))
  return 0
}

# --- обход ---------------------------------------------------------------------
for arg in "${ARGS[@]}"; do
  KEY="$(normalize_key "$arg")" || continue
  is_seen "$KEY" && continue
  mark_seen "$KEY"
  dump_issue "$KEY" 0 || true
done

if [[ "$FOLLOW_LINKS" -eq 1 && ${#LINK_QUEUE[@]} -gt 0 ]]; then
  for lk in "${LINK_QUEUE[@]}"; do
    KEY="$(normalize_key "$lk")" || continue
    is_seen "$KEY" && continue
    mark_seen "$KEY"
    dump_issue "$KEY" 1 || true
  done
fi

if [[ "$DUMPED" -eq 0 ]]; then
  echo "Не выгружено ни одной задачи." >&2
  exit 1
fi
