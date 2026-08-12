#!/usr/bin/env bash
#
# Выгружает страницы Яндекс Вики в markdown на stdout.
#
#   wiki-page.sh homepage/klienty/valenta/trebovanija/registr
#   wiki-page.sh https://wiki.yandex.ru/homepage/klienty/valenta/trebovanija/registr
#   wiki-page.sh slug-1 slug-2                  # несколько страниц за вызов
#   wiki-page.sh <slug> --raw                   # сырой JSON вместо markdown
#
# Несколько страниц выгружаются в один поток, разделитель — строка `---`.
# Принимает и slug, и ссылку: схема, домен, ?query и #якорь отбрасываются,
# %-кодировка (кириллица в ссылке из браузера) раскодируется.
#
# Токен и ID организации (в порядке приоритета):
#   1. переменные окружения YANDEX_WIKI_TOKEN / YANDEX_WIKI_ORG_ID
#   2. macOS Keychain: сервисы yandex-wiki и yandex-wiki-org
#   3. файл ~/.config/yandex-wiki/env (chmod 600)
#   4. учётные данные Трекера (yandex-tracker / yandex-tracker-org) — организация
#      та же, и если OAuth-приложение выдано со скоупом Вики, отдельный токен не нужен
#
# Токен в репозиторий не коммитится ни при каком раскладе — см. README.md рядом.
#
# Коды возврата: 2 — ошибка вызова, 3 — нет учётных данных, 1 — не выгружено ни одной страницы.
# Если часть страниц выгрузилась, а часть нет — 0, причины в stderr.

set -uo pipefail

API="https://api.wiki.yandex.net/v1"
RAW=0
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --raw)      RAW=1; shift ;;
    -h|--help)  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)         echo "Неизвестный флаг: $1" >&2; exit 2 ;;
    *)          ARGS+=("$1"); shift ;;
  esac
done

if [[ ${#ARGS[@]} -eq 0 ]]; then
  echo "Не указана страница. Пример: wiki-page.sh homepage/klienty/valenta/trebovanija/registr" >&2
  exit 2
fi

# --- slug: из ссылки или как есть ----------------------------------------------
normalize_slug() {
  local raw="$1" s="$1"

  # %-кодировка: ссылка с кириллицей, скопированная из адресной строки.
  case "$s" in
    *%[0-9A-Fa-f][0-9A-Fa-f]*) s="$(printf '%b' "${s//%/\\x}")" ;;
  esac

  s="$(printf '%s' "$s" \
    | sed -E 's#^[a-zA-Z]+://##; s#^[^/]*\.[^/]*/##; s#[?#].*$##; s#^/+##; s#/+$##')"

  if [[ -z "$s" ]]; then
    echo "Не разобрал slug из '$raw'." >&2
    return 1
  fi

  printf '%s' "$s"
}

# --- учётные данные ------------------------------------------------------------
keychain() { security find-generic-password -s "$1" -w 2>/dev/null || true; }

if [[ -z "${YANDEX_WIKI_TOKEN:-}" || -z "${YANDEX_WIKI_ORG_ID:-}" ]]; then
  ENV_FILE="${YANDEX_WIKI_ENV_FILE:-$HOME/.config/yandex-wiki/env}"
  if [[ -f "$ENV_FILE" ]]; then
    set -a; . "$ENV_FILE"; set +a
  fi
fi

# Организация у Вики и Трекера одна, поэтому tracker-креды — законный запасной вариант,
# а не костыль: отдельный токен нужен, только если у OAuth-приложения нет скоупа Вики.
: "${YANDEX_WIKI_TOKEN:=$(keychain yandex-wiki)}"
: "${YANDEX_WIKI_ORG_ID:=$(keychain yandex-wiki-org)}"
: "${YANDEX_WIKI_TOKEN:=${YANDEX_TRACKER_TOKEN:-$(keychain yandex-tracker)}}"
: "${YANDEX_WIKI_ORG_ID:=${YANDEX_TRACKER_ORG_ID:-$(keychain yandex-tracker-org)}}"

if [[ -z "$YANDEX_WIKI_TOKEN" || -z "$YANDEX_WIKI_ORG_ID" ]]; then
  cat >&2 <<'EOF'
Нет доступа к Вики: не найдены YANDEX_WIKI_TOKEN и/или YANDEX_WIKI_ORG_ID.

Разовая настройка (macOS Keychain, рекомендуется):
  security add-generic-password -s yandex-wiki     -a "$USER" -w '<OAuth-токен>'
  security add-generic-password -s yandex-wiki-org -a "$USER" -w '<ID организации>'

Токен: https://yandex.ru/support/wiki/ru/api-ref/access
       Сервисный аккаунт Yandex Cloud не подходит — только пользовательский.
ID организации Яндекс 360: https://admin.yandex.ru → главная, поле «ID организации».
EOF
  exit 3
fi

# --- HTTP ----------------------------------------------------------------------
# Ответ и код возвращаются одной строкой (как в tracker-issue.sh): отдельный вызов
# curl ради кода означал бы второй сетевой запрос и рассинхрон с телом.
api_pages() {
  local slug="$1" fields="$2" body code
  local -a q
  q=(--data-urlencode "slug=${slug}")
  [[ -n "$fields" ]] && q+=(--data-urlencode "fields=${fields}")

  body="$(curl -sS -w $'\n%{http_code}' --get \
    -H "Authorization: OAuth ${YANDEX_WIKI_TOKEN}" \
    -H "X-Org-Id: ${YANDEX_WIKI_ORG_ID}" \
    -H "Accept: application/json" \
    "${q[@]}" \
    "${API}/pages")" || { echo "Сеть недоступна при запросе '$slug'." >&2; return 1; }

  code="${body##*$'\n'}"
  body="${body%$'\n'*}"

  printf '%s\n%s' "$code" "$body"
}

fetch_page() {
  local slug="$1" out code body f

  # Без fields ответ содержит только id/slug/title/page_type — тела страницы в нём нет.
  # Валидные поля (проверено вызовами): content, owner, attributes; неизвестное поле
  # даёт 400 BAD_REQUEST без пояснения, какое именно не подошло. Поэтому идём
  # от полного набора к минимальному: если Яндекс переименует поле, скрипт всё
  # равно вернёт текст, а не упадёт.
  for f in "content,owner,attributes" "content" ""; do
    out="$(api_pages "$slug" "$f")" || return 1
    code="${out%%$'\n'*}"; body="${out#*$'\n'}"
    [[ "$code" != "400" && "$code" != "422" ]] && break
  done

  case "$code" in
    200) printf '%s' "$body"; return 0 ;;
    401) echo "401: токен не принят для '$slug'. Нужен OAuth-токен пользователя со скоупом Вики." >&2 ;;
    403) echo "403: нет прав на '$slug' — или неверный X-Org-Id (${YANDEX_WIKI_ORG_ID})." >&2 ;;
    404) echo "404: страница '$slug' не найдена." >&2 ;;
    *)   echo "HTTP $code при запросе '$slug'" >&2; printf '%s\n' "$body" | head -c 500 >&2 ;;
  esac
  return 1
}

# --- дедупликация --------------------------------------------------------------
SEEN=" "
is_seen()   { [[ "$SEEN" == *" $1 "* ]]; }
mark_seen() { SEEN="${SEEN}${1} "; }

PRINTED=0
DUMPED=0

dump_page() {
  local SLUG="$1" JSON

  JSON="$(fetch_page "$SLUG")" || {
    echo "‼️ $SLUG — выгрузить не удалось (причина выше в stderr)."
    return 1
  }

  if [[ "$PRINTED" -eq 1 ]]; then
    printf '\n---\n\n'
  fi
  PRINTED=1

  if [[ "$RAW" -eq 1 ]]; then
    jq '.' <<<"$JSON"
    DUMPED=$((DUMPED + 1))
    return 0
  fi

  jq -r --arg slug "$SLUG" '
    # Ответ может прийти как объектом страницы, так и обёрткой со списком —
    # берём первый элемент, если это список.
    (if type == "array" then .[0] elif has("results") then (.results[0] // {}) else . end) as $p
    | ($p.attributes // {}) as $a
    | ($p.owner.user // {}) as $u
    | "# \($p.title // $slug)",
      "",
      "https://wiki.yandex.ru/\($slug)",
      "",
      "| | |",
      "|---|---|",
      "| Slug | \($slug) |",
      (if $p.id then "| ID | \($p.id) |" else empty end),
      (if ($u | length) > 0 then "| Владелец | \($u.display_name // $u.username // "—")\(if $u.is_dismissed then " (уволен)" else "" end) |" else empty end),
      (if $a.created_at then "| Создана | \($a.created_at) |" else empty end),
      (if $a.modified_at then "| Изменена | \($a.modified_at) |" else empty end),
      (if $p.page_type then "| Тип | \($p.page_type) |" else empty end),
      (if $a.is_draft == true then "| Статус | ⚠️ черновик |" else empty end),
      (if $a.is_readonly == true then "| Доступ | только чтение |" else empty end),
      "",
      "## Содержимое",
      "",
      (($p.content // "") as $c
       | if ($c | length) > 0 then $c
         else
           "_(текст не пришёл в ответе API)_\n\nПоля верхнего уровня: " +
           ($p | keys | join(", ")) +
           "\n\nЗапусти с `--raw`, чтобы посмотреть ответ целиком."
         end)
  ' <<<"$JSON"

  DUMPED=$((DUMPED + 1))
  return 0
}

# --- обход ---------------------------------------------------------------------
for arg in "${ARGS[@]}"; do
  SLUG="$(normalize_slug "$arg")" || continue
  is_seen "$SLUG" && continue
  mark_seen "$SLUG"
  dump_page "$SLUG" || true
done

if [[ "$DUMPED" -eq 0 ]]; then
  echo "Не выгружено ни одной страницы." >&2
  exit 1
fi
