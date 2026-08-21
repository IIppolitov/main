#!/usr/bin/env bash
#
# Выгружает страницы Яндекс Вики в markdown на stdout.
#
#   wiki-page.sh homepage/klienty/valenta/trebovanija/registr
#   wiki-page.sh https://wiki.yandex.ru/homepage/klienty/valenta/trebovanija/registr
#   wiki-page.sh slug-1 slug-2                  # несколько страниц за вызов
#   wiki-page.sh <slug> --comments              # + комментарии к странице
#   wiki-page.sh <slug> --raw                   # сырой JSON вместо markdown
#
# Несколько страниц выгружаются в один поток, разделитель — строка `---`.
# Комментарии по умолчанию не выгружаются: они нужны редко, а это второй запрос
# на каждую страницу. С `--raw` уходят в поле `comments` того же JSON.
# Принимает и slug, и ссылку: схема, домен, ?query и #якорь отбрасываются,
# %-кодировка (кириллица в ссылке из браузера) раскодируется.
#
# Токен и ID организации (в порядке приоритета):
#   1. переменные окружения YANDEX_WIKI_TOKEN / YANDEX_WIKI_ORG_ID
#   2. файл ~/.config/yandex-wiki/env (chmod 600)
#   3. macOS Keychain: сервисы yandex-wiki и yandex-wiki-org
#   4. учётные данные Трекера — файл ~/.config/yandex-tracker/env, затем Keychain
#      yandex-tracker / yandex-tracker-org: организация та же, и если OAuth-приложение
#      выдано со скоупом Вики, отдельный токен не нужен
#
# Токен в репозиторий не коммитится ни при каком раскладе — см. README.md рядом.
#
# Коды возврата: 2 — ошибка вызова, 3 — нет учётных данных, 1 — не выгружено ни одной страницы.
# Если часть страниц выгрузилась, а часть нет — 0, причины в stderr.

set -uo pipefail

API="https://api.wiki.yandex.net/v1"
RAW=0
WITH_COMMENTS=0
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --raw)       RAW=1; shift ;;
    --comments)  WITH_COMMENTS=1; shift ;;
    # Справка — шапка файла до первой некомментарной строки: не съедет при правках.
    -h|--help)  awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "$0"; exit 0 ;;
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

# Трекерные креды тоже бывают в файле, а не только в Keychain — README предлагает
# ~/.config/yandex-tracker/env как альтернативу. Без этого блока запасной вариант
# ниже видит только переменные окружения и Keychain, и настроенный файлом Трекер
# даёт код 3 «нет учётных данных».
if [[ -z "${YANDEX_WIKI_TOKEN:-}" || -z "${YANDEX_WIKI_ORG_ID:-}" ]]; then
  TRACKER_ENV_FILE="${YANDEX_TRACKER_ENV_FILE:-$HOME/.config/yandex-tracker/env}"
  if [[ -f "$TRACKER_ENV_FILE" ]]; then
    set -a; . "$TRACKER_ENV_FILE"; set +a
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

# --- комментарии ---------------------------------------------------------------
# Комментарии живут отдельной ручкой: /pages/{id}/comments. Ответ страничный —
# идём по next_cursor, пока он не опустеет. Если Яндекс когда-нибудь перестанет
# понимать параметр cursor, курсор вернётся тем же — тогда выходим, а не крутим
# один и тот же ответ вечно.
fetch_comments() {
  local page_id="$1" cursor="" prev_cursor="" all='[]' resp code body guard=0
  local -a q

  while :; do
    q=(--data-urlencode "page_size=100")
    [[ -n "$cursor" ]] && q+=(--data-urlencode "cursor=${cursor}")

    resp="$(curl -sS -w $'\n%{http_code}' --get \
      -H "Authorization: OAuth ${YANDEX_WIKI_TOKEN}" \
      -H "X-Org-Id: ${YANDEX_WIKI_ORG_ID}" \
      -H "Accept: application/json" \
      "${q[@]}" \
      "${API}/pages/${page_id}/comments")" || {
        echo "Сеть недоступна при запросе комментариев страницы ${page_id}." >&2
        return 1
      }

    code="${resp##*$'\n'}"
    body="${resp%$'\n'*}"

    if [[ "$code" != "200" ]]; then
      echo "HTTP $code при запросе комментариев страницы ${page_id}." >&2
      [[ "$all" == "[]" ]] && return 1
      echo "Часть комментариев не выгружена — ниже неполный список." >&2
      break
    fi

    all="$(jq -c --argjson acc "$all" '$acc + (.results // [])' <<<"$body")" || return 1

    prev_cursor="$cursor"
    cursor="$(jq -r '.next_cursor // ""' <<<"$body")"
    guard=$((guard + 1))
    [[ -z "$cursor" || "$cursor" == "null" || "$cursor" == "$prev_cursor" || $guard -ge 50 ]] && break
  done

  printf '%s' "$all"
}

# Комментарий привязан к куску текста (inline_text) и адресуется якорем
# #comment-<id> — с ним ссылка из выгрузки открывает то же место, что и в Вике.
render_comments() {
  local slug="$1" json="$2"

  jq -r --arg slug "$slug" '
    [ .[] | select(.is_deleted != true) ] as $c
    | "## Комментарии (\($c | length))",
      "",
      ( if ($c | length) == 0 then "_(нет)_"
        else
          ( $c[]
            | (.author // {}) as $u
            | "### \($u.display_name // $u.username // "?")\(if $u.is_dismissed then " (уволен)" else "" end) — \(.created_at // "?")",
              "",
              "https://wiki.yandex.ru/\($slug)/#comment-\(.id)",
              "",
              ( if .parent_id then "Ответ на комментарий #\(.parent_id).\n" else empty end),
              ( if (.inline_text // "") != "" then "К тексту: «\(.inline_text)»\n" else empty end),
              ( if .resolve_status == "resolved" then "Статус: решён\n" else empty end),
              (.body // ""),
              ""
          )
        end )
  ' <<<"$json"
}

# --- дедупликация --------------------------------------------------------------
SEEN=" "
is_seen()   { [[ "$SEEN" == *" $1 "* ]]; }
mark_seen() { SEEN="${SEEN}${1} "; }

PRINTED=0
DUMPED=0

dump_page() {
  local SLUG="$1" JSON PAGE_ID COMMENTS

  JSON="$(fetch_page "$SLUG")" || {
    echo "‼️ $SLUG — выгрузить не удалось (причина выше в stderr)."
    return 1
  }

  COMMENTS=""
  if [[ "$WITH_COMMENTS" -eq 1 ]]; then
    PAGE_ID="$(jq -r '
      (if type == "array" then .[0] elif has("results") then (.results[0] // {}) else . end)
      | .id // empty' <<<"$JSON")"
    if [[ -n "$PAGE_ID" ]]; then
      COMMENTS="$(fetch_comments "$PAGE_ID")" || COMMENTS=""
    else
      echo "Не нашёл id страницы '$SLUG' — комментарии не выгружены." >&2
    fi
  fi

  if [[ "$PRINTED" -eq 1 ]]; then
    printf '\n---\n\n'
  fi
  PRINTED=1

  if [[ "$RAW" -eq 1 ]]; then
    if [[ "$WITH_COMMENTS" -eq 1 ]]; then
      jq --argjson c "${COMMENTS:-null}" \
        'if type == "object" then . + {comments: $c} else {page: ., comments: $c} end' <<<"$JSON"
    else
      jq '.' <<<"$JSON"
    fi
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

  if [[ "$WITH_COMMENTS" -eq 1 ]]; then
    printf '\n'
    if [[ -n "$COMMENTS" ]]; then
      render_comments "$SLUG" "$COMMENTS"
    else
      printf '## Комментарии\n\n_(выгрузить не удалось — причина выше в stderr)_\n'
    fi
  fi

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
