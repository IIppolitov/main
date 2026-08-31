#!/usr/bin/env bash
#
# Публикует ревью-комментарий к Pull Request'у: шапка + замечания на строках,
# одним уведомлением автору.
#
#   pr-review-post.sh 1232 --input review.json
#   pr-review-post.sh https://github.com/powbee/pbeadmin/pull/1232 --input review.json
#   pr-review-post.sh 35 -R powbee/pbeadmin_omni --input review.json --dry-run
#
# Зачем обёртка, если есть `gh api --method POST .../pulls/<N>/reviews`.
# Этот эндпоинт умеет три вещи сразу: оставить комментарий (event=COMMENT),
# заапрувить (APPROVE) и запросить изменения (REQUEST_CHANGES). Правила permissions
# матчатся ПО ПРЕФИКСУ команды, а `event` лежит в теле запроса — значит разрешить
# комментарий и запретить вердикт через settings.json физически нельзя: префикс
# у них общий. Обёртка разрывает этот узел тем, что `event` из входного файла
# не читает вовсе — она проставляет COMMENT сама. Вердикт по PR (approve /
# request-changes), мердж и закрытие остаются за человеком.
#
# Формат --input (JSON):
#
#   {
#     "body": "<шапка ревью: вердикт, что хорошо, эксплуатация, безопасность>",
#     "comments": [
#       {"path": "App/Services/X.php", "line": 458, "start_line": 442,
#        "side": "RIGHT", "start_side": "RIGHT", "body": "<замечание>"}
#     ]
#   }
#
# Поля `event` и `commit_id` в файле не нужны: первое обёртка ставит сама и не
# принимает извне, второе берёт из head PR (переопределяется флагом --commit).
#
# `line` должен попадать в дифф: для нового файла годится любая строка, для
# изменённого — только добавленные или контекстные строки внутри ханка. Промах
# отклоняет ВЕСЬ запрос, а не одно замечание, — поэтому есть --dry-run.
#
# Учётные данные: `gh auth login`, делает человек один раз на машину.
#
# Коды возврата: 2 — ошибка вызова, 3 — нет gh/jq или нет авторизации,
# 4 — репозиторий/PR не определён, 1 — публикация не удалась.

set -uo pipefail

REPO=""
INPUT=""
COMMIT=""
DRY=0
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input)       INPUT="${2:-}"; shift 2 ;;
    --input=*)     INPUT="${1#*=}"; shift ;;
    --commit)      COMMIT="${2:-}"; shift 2 ;;
    --commit=*)    COMMIT="${1#*=}"; shift ;;
    -R|--repo)     REPO="${2:-}"; shift 2 ;;
    -R=*|--repo=*) REPO="${1#*=}"; shift ;;
    --dry-run)     DRY=1; shift ;;
    -h|--help)     sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)            echo "Неизвестный флаг: $1" >&2; exit 2 ;;
    *)             ARGS+=("$1"); shift ;;
  esac
done

[[ ${#ARGS[@]} -eq 0 ]] && { echo "Не указан PR. Пример: pr-review-post.sh 1232 --input review.json" >&2; exit 2; }
[[ -z "$INPUT" ]] && { echo "Не указан --input <файл.json> с полями body и comments." >&2; exit 2; }
[[ -r "$INPUT" ]] || { echo "Файл не читается: $INPUT" >&2; exit 2; }

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

# Репозиторий не задан — берём тот, в чьём каталоге нас запустили. Для проектов
# с сабмодулями это ловушка: из корня основного репозитория gh молча уйдёт в него,
# даже когда речь про PR модуля. Поэтому для модулей -R указывается явно.
[[ -z "$REPO" ]] && REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null)"

[[ -z "$REPO" || -z "$NUM" ]] && { echo "Не определён репозиторий или номер PR. Укажи -R owner/repo." >&2; exit 4; }

jq empty "$INPUT" 2>/dev/null || { echo "$INPUT — не валидный JSON." >&2; exit 2; }

if jq -e 'has("event")' "$INPUT" >/dev/null 2>&1; then
  echo "В $INPUT есть поле event — убери его. Обёртка ставит event=COMMENT сама и" >&2
  echo "не принимает его извне: approve и request-changes ставит человек." >&2
  exit 2
fi

jq -e '(.body // "") != ""' "$INPUT" >/dev/null 2>&1 || {
  echo "В $INPUT пустое поле body: ревью без шапки автор прочитает как набор придирок." >&2
  exit 2
}

# --- head SHA ------------------------------------------------------------------
if [[ -z "$COMMIT" ]]; then
  COMMIT="$(gh api --method GET "repos/$REPO/pulls/$NUM" --jq .head.sha 2>/dev/null)"
  [[ -z "$COMMIT" ]] && { echo "Не удалось прочитать head SHA у $REPO#$NUM." >&2; exit 4; }
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# event проставляется здесь и только здесь.
jq --arg sha "$COMMIT" \
   '{commit_id: $sha, event: "COMMENT", body: .body, comments: (.comments // [])}' \
   "$INPUT" > "$TMP/payload.json"

COUNT="$(jq '.comments | length' "$TMP/payload.json")"
echo "→ $REPO#$NUM, head $COMMIT, event=COMMENT, замечаний на строках: $COUNT" >&2

if [[ $DRY -eq 1 ]]; then
  echo "--dry-run: тело запроса ниже, ничего не отправлено." >&2
  cat "$TMP/payload.json"
  exit 0
fi

if ! gh api --method POST "repos/$REPO/pulls/$NUM/reviews" --input "$TMP/payload.json" \
     --jq '"Опубликовано: " + .html_url' 2>"$TMP/err"; then
  echo "Публикация не удалась:" >&2
  cat "$TMP/err" >&2
  echo >&2
  echo "Типовая причина — замечание на строке, которой нет в диффе: GitHub отклоняет" >&2
  echo "весь запрос целиком. Сверь line/start_line с ханками: gh pr diff $NUM -R $REPO" >&2
  exit 1
fi

exit 0
