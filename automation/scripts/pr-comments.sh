#!/usr/bin/env bash
#
# Выгружает ВСЕ комментарии Pull Request'а в markdown на stdout.
#
#   pr-comments.sh 35 -R powbee/pbeadmin_content_factory
#   pr-comments.sh https://github.com/powbee/pbeadmin_content_factory/pull/35
#   pr-comments.sh powbee/pbeadmin#1232
#   pr-comments.sh 35 -R powbee/pbeadmin_content_factory --raw     # сырой JSON
#
# Зачем скрипт, если есть `gh pr view --comments`: комментарии PR отдают ТРИ РАЗНЫХ
# эндпоинта, и `gh pr view --comments` показывает только один из них — ленту обсуждения.
# Инлайновые замечания на строках кода, то есть содержательная часть любого ревью,
# в его вывод не попадают вовсе, и это молчаливая потеря: команда отработала успешно,
# просто половины данных в ней нет. Отдельная грабля — пагинация: без --paginate
# видно только первые 30 записей, и обрыв тоже ничем не обозначен.
#
#   лента обсуждения       GET /issues/<N>/comments
#   тела ревью             GET /pulls/<N>/reviews
#   замечания на строках   GET /pulls/<N>/comments      ← то, что теряется
#
# Инлайновые замечания идут первыми и собраны в треды: ответы (in_reply_to_id)
# подклеены под свой корневой комментарий, а не свалены плоским списком.
#
# Учётные данные: `gh auth login`, делает человек один раз на машину.
#
# Коды возврата: 2 — ошибка вызова, 3 — нет gh или нет авторизации,
# 4 — репозиторий/PR не определён, 1 — не выгружено ничего.

set -uo pipefail

RAW=0
REPO=""
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --raw)      RAW=1; shift ;;
    -R|--repo)  REPO="${2:-}"; shift 2 ;;
    -R=*|--repo=*) REPO="${1#*=}"; shift ;;
    -h|--help)  sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)         echo "Неизвестный флаг: $1" >&2; exit 2 ;;
    *)          ARGS+=("$1"); shift ;;
  esac
done

if [[ ${#ARGS[@]} -eq 0 ]]; then
  echo "Не указан PR. Пример: pr-comments.sh 35 -R powbee/pbeadmin_content_factory" >&2
  exit 2
fi

command -v gh >/dev/null 2>&1 || {
  echo "Нет gh CLI. Поставь: brew install gh, затем gh auth login (делает человек)." >&2
  exit 3
}
command -v jq >/dev/null 2>&1 || { echo "Нет jq." >&2; exit 3; }

gh auth status >/dev/null 2>&1 || {
  echo "gh не авторизован. Выполни: gh auth login (делает человек, агенту это запрещено)." >&2
  exit 3
}

# --- разбор ссылки на PR -------------------------------------------------------
REF="${ARGS[0]}"
NUM=""

case "$REF" in
  *github.com/*/pull/*)
    NUM="$(printf '%s' "$REF" | sed -E 's#.*/pull/([0-9]+).*#\1#')"
    [[ -z "$REPO" ]] && REPO="$(printf '%s' "$REF" | sed -E 's#^[a-zA-Z]+://##; s#^[^/]*/##; s#/pull/.*##')"
    ;;
  */*\#[0-9]*)
    NUM="${REF##*#}"
    [[ -z "$REPO" ]] && REPO="${REF%%#*}"
    ;;
  [0-9]*)
    NUM="$REF"
    ;;
  *)
    echo "Не разобрал '$REF'. Ожидается номер, owner/repo#N или ссылка на PR." >&2
    exit 2
    ;;
esac

# Репозиторий не задан — берём тот, в чьём каталоге нас запустили.
# Это важный случай для проектов с сабмодулями: из корня основного репозитория
# gh молча уходит в него, даже когда речь про PR модуля.
if [[ -z "$REPO" ]]; then
  REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null)"
fi

if [[ -z "$REPO" || -z "$NUM" ]]; then
  echo "Не определён репозиторий или номер PR. Укажи -R owner/repo." >&2
  exit 4
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fetch() {  # fetch <путь> <файл>; пустой массив при ошибке
  gh api --method GET --paginate "repos/$REPO/$1" --jq '.[]' 2>"$TMP/err" \
    | jq -s '.' > "$2" || echo '[]' > "$2"
  [[ -s "$2" ]] || echo '[]' > "$2"
}

gh api --method GET "repos/$REPO/pulls/$NUM" > "$TMP/pr.json" 2>"$TMP/err" || {
  echo "Не удалось прочитать PR $REPO#$NUM:" >&2
  cat "$TMP/err" >&2
  exit 1
}

fetch "pulls/$NUM/comments" "$TMP/inline.json"
fetch "pulls/$NUM/reviews"  "$TMP/reviews.json"
fetch "issues/$NUM/comments" "$TMP/issue.json"

if [[ $RAW -eq 1 ]]; then
  jq -n \
    --slurpfile pr "$TMP/pr.json" \
    --slurpfile inline "$TMP/inline.json" \
    --slurpfile reviews "$TMP/reviews.json" \
    --slurpfile issue "$TMP/issue.json" \
    '{pr: $pr[0], inline: $inline[0], reviews: $reviews[0], issue_comments: $issue[0]}'
  exit 0
fi

# --- markdown ------------------------------------------------------------------
{
jq -r --arg repo "$REPO" '
  "# PR \($repo)#\(.number) — \(.title)\n",
  "| | |\n|---|---|",
  "| Автор | \(.user.login) |",
  "| Состояние | \(.state)\(if .draft then " (draft)" else "" end)\(if .merged then ", смерджен" else "" end) |",
  "| Ветки | \(.base.ref) ← \(.head.ref) |",
  "| head SHA | `\(.head.sha)` |",
  "| Объём | \(.changed_files) файлов, +\(.additions) −\(.deletions) |",
  ""
' "$TMP/pr.json"

TOTAL_INLINE=$(jq 'length' "$TMP/inline.json")
echo "## Инлайновые замечания на строках ($TOTAL_INLINE)"
echo
if [[ "$TOTAL_INLINE" == "0" ]]; then
  echo "_нет_"
  echo
else
  jq -r '
    # корень треда: сам комментарий либо тот, кому он отвечает
    (map(select(.in_reply_to_id == null)) | sort_by(.path, (.line // .original_line // 0))) as $roots
    | (map(select(.in_reply_to_id != null))) as $replies
    | $roots[]
    | . as $r
    | (
        "### \($r.path):\(if $r.start_line then "\($r.start_line)-\($r.line)" else "\($r.line // $r.original_line // "?")" end)",
        "**\($r.user.login)**, \($r.created_at[0:10])\(if $r.line == null then " · _замечание на устаревшей строке (outdated)_" else "" end)",
        "",
        $r.body,
        ""
      ),
      (
        [$replies[] | select(.in_reply_to_id == $r.id)] | sort_by(.created_at) | .[]
        | ("> ↳ **\(.user.login)**, \(.created_at[0:10]):", "> \(.body | gsub("\n"; "\n> "))", "")
      )
  ' "$TMP/inline.json"
fi

TOTAL_REVIEWS=$(jq '[.[] | select((.body // "") != "" or .state != "COMMENTED")] | length' "$TMP/reviews.json")
echo "## Ревью ($TOTAL_REVIEWS)"
echo
if [[ "$TOTAL_REVIEWS" == "0" ]]; then
  echo "_нет_"
  echo
else
  jq -r '
    sort_by(.submitted_at)[]
    | select((.body // "") != "" or .state != "COMMENTED")
    | ("### \(.user.login) — \(.state), \((.submitted_at // "")[0:10])",
       "",
       (if (.body // "") == "" then "_без текста_" else .body end),
       "")
  ' "$TMP/reviews.json"
fi

TOTAL_ISSUE=$(jq 'length' "$TMP/issue.json")
echo "## Обсуждение PR ($TOTAL_ISSUE)"
echo
if [[ "$TOTAL_ISSUE" == "0" ]]; then
  echo "_нет_"
else
  jq -r '
    sort_by(.created_at)[]
    | ("### \(.user.login), \(.created_at[0:10])", "", .body, "")
  ' "$TMP/issue.json"
fi
} 2>/dev/null

exit 0
