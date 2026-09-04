# Яндекс Вики через ИИ-агента

> **Живёт только здесь и в Вики не публикуется.** Страницы у него нет намеренно:
> в шапке нет строки с адресом, поэтому публикатор этот документ пропускает.
> Нужен кому-то вовне — отдаём файлом или текстом, а не ссылкой.

| | |
|---|---|
| **Версия** | 1.0 |
| **Дата** | 2026-09-04 |
| **Владелец** | Ипполитов Иван, директор департамента информационных технологий |
| **Статус** | draft |
| **Кому** | всем, кто работает с Вики через ИИ-агента: разработка, бизнес-анализ, дата-инженерия, работа с клиентами |
| **Публикация** | нет, документ внутренний |

Документ самодостаточный: тексты всех скриптов лежат внутри него. Его можно
отдать целиком любому ИИ-агенту — в терминале или в чате — и больше ничего не
прикладывать. Доступа к нашим репозиториям для работы по нему не нужно: хватает
доступа к самой Вики.

Здесь не только готовые рецепты. Работа с Вики через API устроена
неочевидно, и половина документа — про то, где мы уже наступали и почему
сделано именно так. Рецепт без этой половины ломается на второй странице.

**Три режима работы, они очень разные:**

| Режим | Что это | Кто ходит в Вики |
|---|---|---|
| **Агент с руками** | Claude Code (или любой агент с терминалом) на твоей машине | агент сам, через API |
| **Агент с одним инструментом** | Claude Desktop с подключённым MCP-сервером | агент сам читает страницы по ссылке; пишет — человек |
| **Агент без рук** | Claude в браузере, Проект | **человек**; агент только читает присланное, пишет текст и диктует команды |

Первые семь разделов — общие, они нужны в любом из трёх. Работа без терминала
разобрана отдельно в разделе 8, там же — как включить чтение по ссылке. Если ты
работаешь в чате, читать всё равно всё: грабли те же, просто наступает на них
человек.

## 1. Правила, обязательные для агента

Их семь, и они не обсуждаются. Всё остальное в документе — объяснения к ним.

1. **Токен не печатать.** Ни в вывод, ни в файл, ни в тело задачи, ни в чат.
   Если понадобилось проверить доступ — проверяй результатом запроса, а не
   значением переменной.
2. **Перед любой записью — сухой прогон.** Показать человеку, что именно уйдёт
   в Вики и на какой адрес. Запись только после его «да».
3. **Slug не выдумывать.** Адрес страницы берётся из ссылки, которую дал
   человек, или из шапки документа. Нет адреса — спросить, а не подобрать
   похожий: страница создастся не там, где надо, и об этом никто не узнает.
4. **Не писать поверх, не сличив.** Запись в Вики — это замена всего тела
   страницы целиком. Если страницу правили в браузере после того, как мы её
   забрали, правка исчезнет молча.
5. **Что открыто тебе — попадёт в контекст.** Токен персональный, агент видит
   ровно то, что видишь ты. Смотри, что выгружаешь: страницы клиентов, договоры
   и персональные данные в контекст сессии тянуть незачем.
6. **Уведомлять подписчиков — по умолчанию нет.** Пятнадцать страниц подряд с
   уведомлениями — это пятнадцать писем каждому подписчику раздела.
7. **Правку делаем в одном месте.** Если страница уже ведётся в репозитории и
   публикуется оттуда — в браузере её не редактируем. Две точки правки
   расходятся за неделю, и дальше никто не знает, где правда.

## 2. Что API умеет и чего не умеет

Прежде чем строить процесс, надо знать границы. Они жёстче, чем кажется.

**Умеет:** прочитать страницу по slug, прочитать её комментарии, создать
страницу, заменить тело и заголовок существующей.

**Не умеет — и обойти это нельзя:**

| Чего нет | Что это значит на практике |
|---|---|
| Обхода дерева | Одна страница за обращение. «Выгрузи мне весь раздел» — это список slug'ов, собранный руками или из ссылок родительской страницы |
| Ленты изменений | Нельзя спросить «что поменялось в разделе за неделю» и «кто автор правки». Есть только `modified_at` конкретной страницы, которую ты и так уже знаешь |
| Частичной правки | Нельзя дописать абзац. Только заменить тело страницы целиком — со всеми вытекающими (см. правило 4) |
| Скачивания вложений | Файлы и картинки страницы закрыты авторизацией и через API не приходят. В выгрузке останется ссылка |
| Поиска | Найти страницу по словам нельзя. Ищи в браузере, копируй ссылку |

**Работает от имени человека, а не сервиса.** Сервисный аккаунт Yandex Cloud
для API Вики не подходит — нужен пользовательский OAuth-токен. Отсюда два
следствия: агент видит ровно то, что видит владелец токена, и любая запись в
истории версий Вики подписана его именем. «Это агент написал» — не оправдание,
подпись твоя.

## 3. Доступ: токен и ID организации

Нужны два значения:

| Что | Где взять |
|---|---|
| **OAuth-токен** | получаешь сам: [инструкция Яндекса](https://yandex.ru/support/wiki/ru/api-ref/access). Токен персональный, коллеге не передаётся |
| **ID организации** | [admin.yandex.ru](https://admin.yandex.ru) → главная, поле «ID организации». Либо у директора департамента информационных технологий |

**Отдельный токен для Вики обычно не нужен.** Организация у Вики и Трекера
одна, и если у OAuth-приложения есть скоуп Вики, подходят трекерные учётные
данные — скрипты подхватывают их сами. Заводить отдельную пару стоит, только
если понадобилось разделить права.

Скрипты ищут учётные данные в четырёх местах **по порядку**, первое найденное
выигрывает:

1. переменные окружения `YANDEX_WIKI_TOKEN` / `YANDEX_WIKI_ORG_ID`;
2. файл `~/.config/yandex-wiki/env`;
3. Keychain macOS: сервисы `yandex-wiki` и `yandex-wiki-org`;
4. трекерные учётные данные — тем же порядком: переменные
   `YANDEX_TRACKER_*`, файл `~/.config/yandex-tracker/env`, Keychain
   `yandex-tracker` / `yandex-tracker-org`.

**macOS** — Keychain:

```bash
security add-generic-password -s yandex-wiki     -a "$USER" -w '<OAuth-токен>'
security add-generic-password -s yandex-wiki-org -a "$USER" -w '<ID организации>'
```

**Linux / WSL** — команды `security` там нет, работает файл с правами `600`:

```bash
mkdir -p ~/.config/yandex-wiki
printf 'YANDEX_WIKI_TOKEN=<OAuth-токен>\nYANDEX_WIKI_ORG_ID=<ID организации>\n' \
  > ~/.config/yandex-wiki/env
chmod 600 ~/.config/yandex-wiki/env
```

Имена сервисов, путь файла и имена переменных менять нельзя — скрипты ищут
ровно их.

**Проверка доступа** — не печатью токена, а запросом. Открой любую страницу,
которую ты и так видишь в браузере:

```bash
wiki-page.sh <slug> | head -20
```

Вернулся заголовок и текст — доступ есть. Вернулось «Нет доступа к Вики» —
учётные данные не нашлись ни в одном из четырёх мест. Вернулось `401` — токен
есть, но не принят: чаще всего у приложения нет скоупа Вики.

**В репозиторий не коммитится ничего.** Токен живёт в Keychain или в файле вне
репозитория. В документах — плейсхолдер вида `<OAuth-токен>`.

## 4. Как устроен API

Этот раздел нужен, чтобы агент мог работать, даже если скриптов под рукой нет —
или если надо понять, что именно пошло не так внутри скрипта.

База: `https://api.wiki.yandex.net/v1`. Заголовки на каждом запросе:

```
Authorization: OAuth <токен>
X-Org-Id: <ID организации>
Accept: application/json
Content-Type: application/json     # только для записи
```

| Действие | Запрос |
|---|---|
| Прочитать страницу | `GET /pages?slug=<slug>&fields=content,owner,attributes` |
| Комментарии | `GET /pages/{id}/comments?page_size=100&cursor=<курсор>` |
| Создать страницу | `POST /pages` с телом `{slug, title, content, access_policy}` |
| Заменить тело | `POST /pages/{id}?is_silent=true` с телом `{title, content}` |

Четыре вещи, которые в документации не бросаются в глаза, а ломают всё:

**`fields` обязателен.** Без него ответ содержит только `id`, `slug`, `title`,
`page_type` — тела страницы в нём нет. Ошибки не будет: придёт `200` и пустота
там, где ты ждал текст.

**Неизвестное поле в `fields` даёт `400 BAD_REQUEST` — без указания, какое
именно не подошло.** Рабочий набор, проверенный вызовами: `content`, `owner`,
`attributes`. Поэтому `wiki-page.sh` идёт от полного набора к минимальному
(`content,owner,attributes` → `content` → без `fields`): если Яндекс
переименует поле, выгрузка станет беднее, но не упадёт.

**Ответ приходит то объектом, то обёрткой.** Иногда это сама страница, иногда
массив, иногда `{results: [...]}`. Разбирая ответ, бери первый элемент, если
это список, — на одном из трёх вариантов жёсткий разбор сломается.

**Правка — это `POST`, а не `PUT`/`PATCH`,** и она заменяет тело целиком.
`is_silent=true` — без уведомления подписчиков.

### Коды ответа: что они значат на самом деле

| Код | Формально | Что это обычно на самом деле |
|---|---|---|
| `401` | токен не принят | у OAuth-приложения нет скоупа Вики. Токен Трекера сам по себе валиден, но Вики его не пускает |
| `403` на чтении | нет прав | **или неверный `X-Org-Id`.** Проверяй сначала организацию, потом права |
| `403` на создании | нет прав | **родительский раздел закрыт на запись.** У страницы стоит «только чтение» — это видно в шапке выгрузки |
| `404` | страницы нет | slug опечатан, либо страница удалена, либо она в другом разделе |
| `400 BAD_REQUEST` на чтении | неверный запрос | почти всегда — неизвестное поле в `fields` |
| `400 Immediate parent page does not exist` | нет родителя | **следствие предыдущего:** иерархия задаётся slug'ом, а родителя не создали. Публикуй раздел одним прогоном и в порядке имён: родитель до потомков |

## 5. Установка скриптов

Четыре скрипта: читалка, конвертер разметки, публикатор и MCP-сервер для тех,
кто работает не в терминале. Полные тексты — в [разделе 6](#6-тексты-скриптов).
Нужны `bash`, `curl`, `jq`, `python3` — `jq` на macOS ставится через
`brew install jq`.

| Скрипт | Что делает | Кому |
|---|---|---|
| `wiki-page.sh` | читает страницу и комментарии | всем |
| `wiki2md.py` | переводит разметку Вики в чистый markdown | кто забирает страницы себе |
| `wiki-push.py` | публикует markdown обратно в Вики | кто ведёт документы у себя |
| `wiki-mcp.py` | даёт агенту читать Вики по ссылке, без копипаста | кто работает в Claude Desktop |

Положить их можно куда угодно; здесь и дальше подразумевается `~/bin/pbe-wiki/`:

```bash
mkdir -p ~/bin/pbe-wiki
# сохранить в этот каталог wiki-page.sh, wiki2md.py, wiki-push.py, wiki-mcp.py
chmod +x ~/bin/pbe-wiki/*
export PATH="$HOME/bin/pbe-wiki:$PATH"   # строку — в ~/.zshrc или ~/.bashrc
```

Дальше в примерах скрипты вызываются по имени. Если `PATH` не правился —
вызывай по полному пути, `~/bin/pbe-wiki/wiki-page.sh`.

**Одна оговорка про `wiki-push.py`.** Он предполагает, что публикуемые
документы лежат в репозитории и знают свой адрес в Вики (см. раздел 7.4).
Двум другим скриптам никакой репозиторий не нужен.

### Все команды одним списком

```bash
# ЧТЕНИЕ
wiki-page.sh <slug>                        # страница в markdown на stdout
wiki-page.sh https://wiki.yandex.ru/<slug> # ссылку понимает целиком
wiki-page.sh <slug-1> <slug-2>             # несколько за вызов, разделитель — строка ---
wiki-page.sh <slug> --comments             # + раздел «Комментарии» под текстом
wiki-page.sh <slug> --raw                  # сырой JSON вместо markdown
wiki-page.sh --help                        # справка

# ЗАБРАТЬ СТРАНИЦУ СЕБЕ (с переводом разметки Вики в чистый markdown)
wiki-page.sh <slug> --raw | jq -r .content | wiki2md.py > файл.md
wiki2md.py входной.md > выходной.md        # конвертер отдельно, из файла

# ПУБЛИКАЦИЯ
wiki-push.py <каталог|файлы>               # сухой прогон: что и куда уйдёт
wiki-push.py <каталог> --apply             # запись, тихо
wiki-push.py <файл> --apply --notify       # запись с уведомлением подписчиков
wiki-push.py <файл> --apply --create       # создать страницу, если её нет

# ПОЛЕЗНОЕ
wiki-page.sh <slug> --raw | jq -r '.attributes.comments_count'   # есть ли комментарии
wiki-page.sh <slug> --comments --raw | jq -r '.comments[] | .body'

# ЧТЕНИЕ ПО ССЫЛКЕ ПРЯМО ИЗ РАЗГОВОРА (разовая настройка, дальше команд нет)
claude mcp add powbee-wiki -- python3 ~/bin/pbe-wiki/wiki-mcp.py   # Claude Code
# Claude Desktop — блоком в настройках, см. раздел 8.1
```

Коды возврата одинаковые у всех трёх: `0` — получилось (частичные неудачи в
stderr), `1` — не получилось ничего, `2` — ошибка вызова, `3` — нет учётных
данных.

## 6. Тексты скриптов

Ниже — полные тексты, как они есть. Копируются целиком, правки в них по ходу
дела не нужны.

Пути в примерах внутри скриптов (`../../docs/regulations/…`) — из рабочего
пространства, где эти скрипты живут. У себя подставляй свои.

### 6.1. `wiki-page.sh` — прочитать страницу

Выгружает страницу в markdown на stdout: шапка (владелец, даты, тип, признаки
черновика и «только чтение»), затем текст. По флагу `--comments` — комментарии
с автором, датой, якорной ссылкой и фрагментом текста, к которому комментарий
привязан.

```bash
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
```

### 6.2. `wiki2md.py` — перевести разметку Вики в markdown

Нужен, когда страницу не читают, а забирают себе и дальше правят. Переводит
таблицы `#| … |#`, выноски `{% note %}`, подчёркивания, картинки и якорные
сноски. Читает файл аргументом или поток со stdin.

```python
#!/usr/bin/env python3
"""
Преобразует разметку Яндекс Вики в чистый markdown.

    wiki-page.sh <slug> --raw | jq -r .content | wiki2md.py > файл.md
    wiki2md.py входной.md > выходной.md

Что переводится:
  · таблицы `#| || ячейка | ячейка || |#`  → markdown-таблицы
  · блоки `{% note info "Заголовок" %}…{% endnote %}` → цитата с заголовком
  · подчёркивание `++текст++` → просто текст (в markdown подчёркивания нет)
  · картинки `![alt](/path =1648x230)` → абсолютная ссылка на wiki, размер убирается
  · якорные сноски `&[текст](12345)` → просто текст

Первая строка вики-таблицы становится заголовком markdown-таблицы. Если в
исходной таблице заголовка не было, шапку надо поправить руками — определить
это автоматически нельзя.
"""

import re
import sys

WIKI_HOST = "https://wiki.yandex.ru"


def convert_table(block):
    """Вики-таблица → markdown. Ячейки бывают многострочными, поэтому разбор построчный."""
    rows, row, cell = [], [], []

    def flush_cell():
        text = " ".join(l.strip() for l in cell if l.strip())
        row.append(text)
        cell.clear()

    for line in block:
        s = line.strip()
        # Однострочный вариант: || ячейка | ячейка ||
        if s.startswith("||") and s.endswith("||") and len(s) > 4:
            rows.append([c.strip() for c in s[2:-2].split("|")])
            continue
        if s == "||":
            if row or cell:            # закрываем текущую строку
                flush_cell()
                rows.append(row[:])
                row.clear()
            continue
        if s == "|":                   # граница ячейки
            flush_cell()
            continue
        cell.append(line)

    if row or cell:
        flush_cell()
        rows.append(row[:])

    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return []

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    out = ["| " + " | ".join(rows[0]) + " |",
           "|" + "---|" * width]
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    return out


def convert(text):
    lines = text.split("\n")
    out, i = [], 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped == "#|":                       # таблица
            block, i = [], i + 1
            while i < len(lines) and lines[i].strip() != "|#":
                block.append(lines[i])
                i += 1
            i += 1
            out.extend(convert_table(block))
            out.append("")
            continue

        m = re.match(r'\{%\s*note\s+(\w+)(?:\s+"([^"]*)")?\s*%\}', stripped)
        if m:                                      # выноска
            title = m.group(2) or {"info": "Важно", "warning": "Внимание",
                                   "alert": "Внимание"}.get(m.group(1), "Важно")
            body, i = [], i + 1
            while i < len(lines) and not re.match(r'\{%\s*endnote\s*%\}', lines[i].strip()):
                body.append(lines[i])
                i += 1
            i += 1
            out.append(f"> **{title}**")
            out.append(">")
            for b in body:
                out.append(("> " + b.strip()).rstrip() if b.strip() else ">")
            out.append("")
            continue

        out.append(line)
        i += 1

    text = "\n".join(out)

    # Картинка с размером: ![alt](/path =1648x230) → абсолютная ссылка без размера.
    # Файлы на вики закрыты авторизацией, поэтому в markdown это ссылка, а не встроенное изображение.
    text = re.sub(r'!\[([^\]]*)\]\((/[^)\s]+)\s*=\d+x\d+\)',
                  lambda m: f'[изображение: {m.group(1)}]({WIKI_HOST}{m.group(2)})', text)
    text = re.sub(r'!\[([^\]]*)\]\((/[^)\s]+)\)',
                  lambda m: f'[изображение: {m.group(1)}]({WIKI_HOST}{m.group(2)})', text)

    text = re.sub(r'&\[([^\]]*)\]\(\d+\)', r'\1', text)   # якорные сноски
    text = text.replace("++", "")                          # подчёркивание
    # \s съел бы и перевод строки вместе с пустой строкой после выноски — только пробелы и табы
    text = re.sub(r'(?:^>[ \t]*$\n)+', '>\n', text, flags=re.M)  # пустые строки внутри выноски
    text = re.sub(r'\n>[ \t]*\n(?=\n)', '\n', text)        # висящий > в конце выноски
    text = re.sub(r'\n{3,}', '\n\n', text)                 # схлопнуть пустые строки

    return text.strip() + "\n"


if __name__ == "__main__":
    src = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else sys.stdin.read()
    sys.stdout.write(convert(src))
```

### 6.3. `wiki-push.py` — опубликовать обратно

Обратная операция к `wiki-page.sh`. По умолчанию **ничего не пишет** — печатает,
что было бы отправлено. Запись включается флагом `--apply`.

```python
#!/usr/bin/env python3
"""Публикация markdown-документов репозитория в Яндекс Вики.

Обратная операция к wiki-page.sh: тот выгружает страницу сюда, этот
возвращает правку обратно. Целевой slug берётся из шапки самого документа —
той строки «Выгружено из Вики», по которой файл когда-то приехал. Отдельной
карты соответствий нет намеренно: она разошлась бы с файлами молча.

По умолчанию НИЧЕГО НЕ ПИШЕТ — печатает, что было бы отправлено. Запись
включается флагом --apply.

    ./wiki-push.py ../../docs/regulations/crm-lifecycle/            # сухой прогон
    ./wiki-push.py ../../docs/regulations/crm-lifecycle/ --apply    # запись
    ./wiki-push.py ../../docs/regulations/crm-lifecycle/05-*.md --apply --notify

Коды возврата: 0 — всё хорошо; 1 — часть страниц не прошла; 2 — ошибка
вызова; 3 — не найдены учётные данные.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.wiki.yandex.net/v1"
WIKI_HOST = "https://wiki.yandex.ru"

# Где искать документы, у которых есть страница в Вики. Ссылки на всё
# остальное (бэклог, оргструктура, паспорта проектов) в Вики не ведут —
# они разворачиваются в обычный текст, см. rewrite_links.
KNOWN_DIRS = ["docs/regulations", "docs/regulations/crm-lifecycle",
              "docs/regulations/claude-code", "docs/regulations/vpn"]

SLUG_RE = re.compile(r"https://wiki\.yandex\.ru/([^\s)\]]+)")
H1_RE = re.compile(r"^#\s+(.+?)\s*$")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


# --- учётные данные ------------------------------------------------------------

def keychain(service: str) -> str:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def credentials() -> tuple[str, str]:
    """Тот же порядок, что в wiki-page.sh: env → файл → Keychain → трекерные."""
    token = os.environ.get("YANDEX_WIKI_TOKEN", "")
    org = os.environ.get("YANDEX_WIKI_ORG_ID", "")

    env_file = Path(os.environ.get(
        "YANDEX_WIKI_ENV_FILE", Path.home() / ".config/yandex-wiki/env"))
    if (not token or not org) and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if k.strip() == "YANDEX_WIKI_TOKEN" and not token:
                token = v
            elif k.strip() == "YANDEX_WIKI_ORG_ID" and not org:
                org = v

    token = token or keychain("yandex-wiki") or os.environ.get(
        "YANDEX_TRACKER_TOKEN", "") or keychain("yandex-tracker")
    org = org or keychain("yandex-wiki-org") or os.environ.get(
        "YANDEX_TRACKER_ORG_ID", "") or keychain("yandex-tracker-org")

    if not token or not org:
        sys.exit(
            "Нет доступа к Вики: не найдены YANDEX_WIKI_TOKEN и/или "
            "YANDEX_WIKI_ORG_ID.\n\n"
            "Разовая настройка (macOS Keychain):\n"
            "  security add-generic-password -s yandex-wiki     -a \"$USER\" -w '<токен>'\n"
            "  security add-generic-password -s yandex-wiki-org -a \"$USER\" -w '<ID организации>'\n"
        )
    return token, org


# --- разбор документа ----------------------------------------------------------

def target_slug(text: str) -> str | None:
    """Slug страницы — из ссылки «Выгружено из Вики» в шапке документа."""
    head = "\n".join(text.splitlines()[:12])
    m = SLUG_RE.search(head)
    return m.group(1).rstrip("/") if m else None


def page_title(text: str) -> str | None:
    for line in text.splitlines():
        m = H1_RE.match(line)
        if m:
            return m.group(1)
    return None


def strip_repo_header(text: str) -> str:
    """Убирает H1 и служебную выноску «Рабочая копия».

    Заголовок в Вики задаётся полем title, дублировать его в теле не нужно.
    Выноска — заметка о состоянии нашей копии, в первоисточнике она бессмысленна.
    """
    lines = text.splitlines()
    out, i = [], 0

    while i < len(lines) and not H1_RE.match(lines[i]):
        i += 1
    if i < len(lines):
        i += 1                                    # сам H1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith(">"):
        while i < len(lines) and lines[i].lstrip().startswith(">"):
            i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1

    out.extend(lines[i:])
    return "\n".join(out).strip() + "\n"


def build_slug_map(repo_root: Path) -> dict[Path, str]:
    """Карта «файл → slug в Вики» по шапкам документов."""
    mapping: dict[Path, str] = {}
    for d in KNOWN_DIRS:
        for path in sorted((repo_root / d).glob("*.md")):
            slug = target_slug(path.read_text(encoding="utf-8"))
            if slug:
                mapping[path.resolve()] = slug
    return mapping


def rewrite_links(text: str, src: Path, slug_map: dict[Path, str]) -> tuple[str, list[str]]:
    """Относительные ссылки → адреса Вики. Что не имеет страницы — в текст.

    Оставить относительную ссылку нельзя: в Вики она ведёт в никуда. Молча
    выкинуть тоже нельзя — поэтому всё, что развернулось в текст, возвращается
    списком и печатается в отчёте.

    Путь от корня (`/homepage/...`) — уже адрес Вики, а не ссылка на файл
    репозитория, и трогать его нельзя: так записаны картинки
    `![x](/раздел/.files/x.png =600x400)` и вложения `:file[…](/раздел/.files/…)`.
    Развернув их в текст, мы бы вырезали из страницы все иллюстрации.
    """
    dropped: list[str] = []

    def repl(m: re.Match) -> str:
        label, href = m.group(1), m.group(2)
        if href.startswith(("http://", "https://", "mailto:", "#", "/")):
            return m.group(0)

        anchor = ""
        if "#" in href:
            href, anchor = href.split("#", 1)
            anchor = "#" + anchor
        if not href:
            return m.group(0)

        target = (src.parent / href).resolve()
        slug = slug_map.get(target)
        if slug:
            return f"[{label}]({WIKI_HOST}/{slug}{anchor})"

        dropped.append(f"{label} → {href}")
        return label

    return LINK_RE.sub(repl, text), dropped


def prepare(path: Path, repo_root: Path, slug_map: dict[Path, str]) -> dict:
    raw = path.read_text(encoding="utf-8")
    slug, title = target_slug(raw), page_title(raw)
    if not slug:
        raise ValueError("в шапке нет ссылки на страницу Вики — некуда публиковать")
    if not title:
        raise ValueError("нет заголовка H1 — нечего положить в title")

    body, dropped = rewrite_links(strip_repo_header(raw), path, slug_map)
    return {"path": path, "slug": slug, "title": title,
            "content": body, "dropped": dropped}


# --- API -----------------------------------------------------------------------

class Wiki:
    def __init__(self, token: str, org: str):
        self.headers = {
            "Authorization": f"OAuth {token}",
            "X-Org-Id": org,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _call(self, method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw or "{}")
            except json.JSONDecodeError:
                return e.code, {"debug_message": raw[:400]}
        except urllib.error.URLError as e:
            return 0, {"debug_message": f"сеть недоступна: {e.reason}"}

    def page_id(self, slug: str) -> int | None:
        q = urllib.parse.urlencode({"slug": slug})
        code, body = self._call("GET", f"{API}/pages?{q}")
        return body.get("id") if code == 200 else None

    def create(self, slug: str, title: str, content: str) -> tuple[int, dict]:
        return self._call("POST", f"{API}/pages", {
            "slug": slug, "title": title, "content": content,
            "access_policy": {"access_type": "inherited"},
        })

    def update(self, idx: int, title: str, content: str, silent: bool) -> tuple[int, dict]:
        q = urllib.parse.urlencode({"is_silent": str(silent).lower()})
        return self._call("POST", f"{API}/pages/{idx}?{q}",
                          {"title": title, "content": content})


# --- CLI -----------------------------------------------------------------------

def collect(targets: list[str]) -> list[Path]:
    files: list[Path] = []
    for t in targets:
        p = Path(t)
        if p.is_dir():
            files.extend(sorted(p.glob("*.md")))
        elif p.is_file():
            files.append(p)
        else:
            print(f"пропущено (не найдено): {t}", file=sys.stderr)
    return files


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Публикация markdown-документов в Яндекс Вики.")
    ap.add_argument("targets", nargs="+", help="файлы или каталоги с .md")
    ap.add_argument("--apply", action="store_true",
                    help="выполнить запись (по умолчанию — сухой прогон)")
    ap.add_argument("--notify", action="store_true",
                    help="уведомить подписчиков страниц (по умолчанию тихо)")
    ap.add_argument("--create", action="store_true",
                    help="создавать страницу, если её нет (по умолчанию — пропуск)")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    files = collect(args.targets)
    if not files:
        print("Нечего публиковать.", file=sys.stderr)
        return 2

    slug_map = build_slug_map(repo_root)
    prepared, failed = [], 0

    for f in files:
        try:
            prepared.append(prepare(f, repo_root, slug_map))
        except ValueError as e:
            print(f"✗ {f.name}: {e}", file=sys.stderr)
            failed += 1

    if not prepared:
        return 1

    if not args.apply:
        print("СУХОЙ ПРОГОН — ничего не отправлено. Запись: --apply\n")
        for p in prepared:
            print(f"{p['path'].name}")
            print(f"  → {WIKI_HOST}/{p['slug']}")
            print(f"  title:   {p['title']}")
            print(f"  content: {len(p['content'])} символов, "
                  f"{p['content'].count(chr(10)) + 1} строк")
            if p["dropped"]:
                print(f"  ссылки развёрнуты в текст ({len(p['dropped'])}): "
                      + "; ".join(p["dropped"][:4])
                      + (" …" if len(p["dropped"]) > 4 else ""))
            print()
        print(f"Готово к публикации: {len(prepared)}"
              + (f", с ошибками: {failed}" if failed else ""))
        return 1 if failed else 0

    wiki = Wiki(*credentials())
    ok = 0
    for p in prepared:
        idx = wiki.page_id(p["slug"])
        if idx is None:
            if not args.create:
                print(f"✗ {p['slug']}: страницы нет. Создать — флаг --create",
                      file=sys.stderr)
                failed += 1
                continue
            code, body = wiki.create(p["slug"], p["title"], p["content"])
            action = "создана"
        else:
            code, body = wiki.update(idx, p["title"], p["content"],
                                     silent=not args.notify)
            action = "обновлена"

        if code == 200:
            print(f"✓ {p['slug']} — {action}")
            ok += 1
        else:
            msg = body.get("debug_message") or body.get("error_code") or body
            print(f"✗ {p['slug']} — HTTP {code}: {msg}", file=sys.stderr)
            failed += 1

    print(f"\nОпубликовано: {ok}" + (f", не прошло: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

### 6.4. `wiki-mcp.py` — чтобы в чат можно было просто вставить ссылку

MCP-сервер: подключается к Claude Desktop или Claude Code один раз, после чего
агент ходит за страницами сам — человек только даёт ссылку. Два инструмента,
`wiki_read` и `wiki_comments`. Зависимостей нет, только стандартная библиотека
Python. Подробности — в разделе 8.1.

**Только чтение, намеренно.** Записи в нём нет: правка заменяет тело страницы
целиком, и делать это из разговора, где не видно ни сухого прогона, ни диффа, —
способ незаметно затереть чужую работу.

```python
#!/usr/bin/env python3
"""MCP-сервер чтения Яндекс Вики: чат получает страницу по ссылке сам.

Решает одну задачу — избавить человека от копипаста. Подключённый к Claude
Desktop или Claude Code сервер даёт агенту два инструмента, и дальше в разговоре
достаточно дать ссылку на страницу: агент сходит за ней сам, с твоим токеном
и твоими правами.

    wiki_read      — страница в markdown, по ссылке или slug
    wiki_comments  — комментарии страницы: автор, дата, якорь, привязка к тексту

**Сервер только читает.** Записи здесь нет намеренно: правка в Вики заменяет
тело страницы целиком, и делать это из разговора, где не видно ни сухого
прогона, ни диффа, — способ незаметно затереть чужую работу. Публикация идёт
скриптом wiki-push.py, с сухим прогоном и подтверждением человека.

Зависимостей нет — только стандартная библиотека. Подключение:

    # Claude Code
    claude mcp add powbee-wiki -- python3 ~/bin/pbe-wiki/wiki-mcp.py

    # Claude Desktop — в claude_desktop_config.json:
    # "powbee-wiki": {"command": "python3", "args": ["<полный путь>/wiki-mcp.py"]}

Учётные данные ищутся там же, где их ищут остальные скрипты: переменные
окружения → ~/.config/yandex-wiki/env → Keychain → трекерные. Важно: Claude
Desktop запускает сервер без твоего профиля оболочки, поэтому `export` в
~/.zshrc он не увидит — нужен Keychain или файл в ~/.config/.

Протокол: JSON-RPC по stdin/stdout, по сообщению на строку. В stdout не должно
попадать ничего, кроме ответов, — вся диагностика идёт в stderr.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.wiki.yandex.net/v1"
WIKI_HOST = "https://wiki.yandex.ru"
SERVER_NAME = "powbee-wiki"
SERVER_VERSION = "1.0.0"
FALLBACK_PROTOCOL = "2025-06-18"


def log(msg: str) -> None:
    print(f"[{SERVER_NAME}] {msg}", file=sys.stderr, flush=True)


# --- учётные данные ------------------------------------------------------------

def keychain(service: str) -> str:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def credentials() -> tuple[str, str]:
    token = os.environ.get("YANDEX_WIKI_TOKEN", "")
    org = os.environ.get("YANDEX_WIKI_ORG_ID", "")

    if not token or not org:
        f = read_env_file(Path(os.environ.get(
            "YANDEX_WIKI_ENV_FILE", Path.home() / ".config/yandex-wiki/env")))
        token = token or f.get("YANDEX_WIKI_TOKEN", "")
        org = org or f.get("YANDEX_WIKI_ORG_ID", "")

    if not token or not org:
        f = read_env_file(Path(os.environ.get(
            "YANDEX_TRACKER_ENV_FILE", Path.home() / ".config/yandex-tracker/env")))
        token = token or keychain("yandex-wiki") or os.environ.get(
            "YANDEX_TRACKER_TOKEN", "") or f.get("YANDEX_TRACKER_TOKEN", "") \
            or keychain("yandex-tracker")
        org = org or keychain("yandex-wiki-org") or os.environ.get(
            "YANDEX_TRACKER_ORG_ID", "") or f.get("YANDEX_TRACKER_ORG_ID", "") \
            or keychain("yandex-tracker-org")

    return token, org


# --- slug ----------------------------------------------------------------------

def normalize_slug(raw: str) -> str:
    """Ссылка или slug → slug. Схема, домен, ?query и #якорь отбрасываются."""
    s = urllib.parse.unquote((raw or "").strip())
    if "://" in s:
        s = s.split("://", 1)[1]
    if "/" in s and "." in s.split("/", 1)[0]:
        s = s.split("/", 1)[1]
    s = s.split("?", 1)[0].split("#", 1)[0]
    return s.strip("/")


# --- API -----------------------------------------------------------------------

class WikiError(Exception):
    pass


def call(path: str, params: dict) -> dict:
    token, org = credentials()
    if not token or not org:
        raise WikiError(
            "Нет учётных данных Вики. Нужны YANDEX_WIKI_TOKEN и "
            "YANDEX_WIKI_ORG_ID — в Keychain (сервисы yandex-wiki, "
            "yandex-wiki-org) или в ~/.config/yandex-wiki/env. "
            "Переменные из ~/.zshrc сервер, запущенный из приложения, не видит."
        )

    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"OAuth {token}",
        "X-Org-Id": org,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        hints = {
            401: "токен не принят — у OAuth-приложения нет скоупа Вики",
            403: "нет прав на страницу либо неверный ID организации",
            404: "страница не найдена — проверь адрес",
            400: "неверный запрос к API",
        }
        raise WikiError(f"HTTP {e.code}: {hints.get(e.code, 'ошибка API')}")
    except urllib.error.URLError as e:
        raise WikiError(f"сеть недоступна: {e.reason}")


def unwrap(body):
    """Ответ приходит то объектом, то массивом, то обёрткой {results: […]}."""
    if isinstance(body, list):
        return body[0] if body else {}
    if isinstance(body, dict) and "results" in body:
        results = body.get("results") or []
        return results[0] if results else {}
    return body if isinstance(body, dict) else {}


def fetch_page(slug: str) -> dict:
    """От полного набора полей к минимальному: неизвестное поле даёт 400."""
    last: WikiError | None = None
    for fields in ("content,owner,attributes", "content", ""):
        params = {"slug": slug}
        if fields:
            params["fields"] = fields
        try:
            return unwrap(call("/pages", params))
        except WikiError as e:
            last = e
            if "HTTP 400" not in str(e):
                raise
    raise last or WikiError("страница не выгружена")


def fetch_comments(page_id: int) -> list[dict]:
    out: list[dict] = []
    cursor, prev, guard = "", None, 0
    while guard < 50:
        params = {"page_size": 100}
        if cursor:
            params["cursor"] = cursor
        body = call(f"/pages/{page_id}/comments", params)
        out.extend(body.get("results") or [])
        prev, cursor = cursor, body.get("next_cursor") or ""
        guard += 1
        if not cursor or cursor == prev:
            break
    return out


# --- рендер --------------------------------------------------------------------

def render_page(slug: str, page: dict) -> str:
    attrs = page.get("attributes") or {}
    owner = (page.get("owner") or {}).get("user") or {}
    rows = [f"| Slug | {slug} |"]
    if page.get("id"):
        rows.append(f"| ID | {page['id']} |")
    if owner:
        name = owner.get("display_name") or owner.get("username") or "—"
        rows.append(f"| Владелец | {name}"
                    f"{' (уволен)' if owner.get('is_dismissed') else ''} |")
    for key, label in (("created_at", "Создана"), ("modified_at", "Изменена")):
        if attrs.get(key):
            rows.append(f"| {label} | {attrs[key]} |")
    if attrs.get("is_draft"):
        rows.append("| Статус | ⚠️ черновик |")
    if attrs.get("is_readonly"):
        rows.append("| Доступ | только чтение |")
    if attrs.get("comments_count"):
        rows.append(f"| Комментариев | {attrs['comments_count']} |")

    content = page.get("content") or "_(текст не пришёл в ответе API)_"
    return "\n".join([
        f"# {page.get('title') or slug}", "",
        f"{WIKI_HOST}/{slug}", "",
        "| | |", "|---|---|", *rows, "",
        "> Текст ниже — в разметке Вики, а не в чистом markdown:",
        "> таблицы, выноски `{% note %}`, цветной текст, `++подчёркивание++`.", "",
        "## Содержимое", "", content,
    ])


def render_comments(slug: str, comments: list[dict]) -> str:
    live = [c for c in comments if not c.get("is_deleted")]
    out = [f"## Комментарии ({len(live)}) — {WIKI_HOST}/{slug}", ""]
    if not live:
        out.append("_(нет)_")
    for c in live:
        author = c.get("author") or {}
        name = author.get("display_name") or author.get("username") or "?"
        if author.get("is_dismissed"):
            name += " (уволен)"
        out += [f"### {name} — {c.get('created_at', '?')}", "",
                f"{WIKI_HOST}/{slug}/#comment-{c.get('id')}", ""]
        if c.get("parent_id"):
            out += [f"Ответ на комментарий #{c['parent_id']}.", ""]
        if c.get("inline_text"):
            out += [f"К тексту: «{c['inline_text']}»", ""]
        if c.get("resolve_status") == "resolved":
            out += ["Статус: решён", ""]
        out += [c.get("body") or "", ""]
    return "\n".join(out)


# --- инструменты ---------------------------------------------------------------

TOOLS = [
    {
        "name": "wiki_read",
        "description": (
            "Прочитать страницу Яндекс Вики Powbee по ссылке или slug. "
            "Возвращает шапку (владелец, даты, признаки черновика и «только "
            "чтение») и текст страницы в разметке Вики. Одна страница за вызов: "
            "обхода дерева подстраниц у API нет."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "description": "Ссылка https://wiki.yandex.ru/… или slug",
                },
                "with_comments": {
                    "type": "boolean",
                    "description": "Добавить комментарии страницы. По умолчанию нет",
                },
            },
            "required": ["page"],
        },
    },
    {
        "name": "wiki_comments",
        "description": (
            "Комментарии страницы Яндекс Вики: автор, дата, ссылка-якорь и "
            "фрагмент текста, к которому комментарий привязан."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "description": "Ссылка https://wiki.yandex.ru/… или slug",
                },
            },
            "required": ["page"],
        },
    },
]


def run_tool(name: str, args: dict) -> str:
    slug = normalize_slug(args.get("page", ""))
    if not slug:
        raise WikiError("не разобрал адрес страницы — дай ссылку или slug")

    if name == "wiki_read":
        page = fetch_page(slug)
        text = render_page(slug, page)
        if args.get("with_comments") and page.get("id"):
            text += "\n\n" + render_comments(slug, fetch_comments(page["id"]))
        return text

    if name == "wiki_comments":
        page = fetch_page(slug)
        if not page.get("id"):
            raise WikiError("не нашёл id страницы — комментарии недоступны")
        return render_comments(slug, fetch_comments(page["id"]))

    raise WikiError(f"неизвестный инструмент: {name}")


# --- протокол ------------------------------------------------------------------

def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")

    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion")
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": version or FALLBACK_PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        try:
            text = run_tool(name, params.get("arguments") or {})
            is_error = False
        except WikiError as e:
            text, is_error = f"Не получилось: {e}", True
        except Exception as e:                       # noqa: BLE001
            log(f"{name}: {type(e).__name__}: {e}")
            text, is_error = f"Внутренняя ошибка: {type(e).__name__}", True
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}], "isError": is_error}}

    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"метод не поддержан: {method}"}}


def main() -> int:
    log("запущен, читаю stdin")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log("получена строка, которая не разобралась как JSON — пропущена")
            continue

        response = handle(msg)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

## 7. Рабочие сценарии

### 7.1. Прочитать страницу

```bash
wiki-page.sh homepage/klienty/valenta/trebovanija/registr
wiki-page.sh https://wiki.yandex.ru/homepage/klienty/valenta/trebovanija/registr
wiki-page.sh slug-1 slug-2 slug-3
```

Ссылку можно давать целиком: схема, домен, `?query` и `#якорь` отбрасываются,
`%`-кодировка (кириллица, скопированная из адресной строки) раскодируется.
Несколько страниц идут в один поток, разделитель — строка `---`.

**Текст придёт в разметке Вики, а не в чистом markdown.** Для чтения это не
мешает, но помнить надо:

| В выгрузке | Что это |
|---|---|
| `{% toc %}` | автоматическое оглавление |
| блок между `#` с чертой и чертой с `#`, строки из двойных вертикальных черт | таблица |
| `{% note info "Заголовок" %}…{% endnote %}` | цветная выноска |
| `{orange}(текст)` | цветной текст |
| `++текст++` | подчёркивание — в markdown такого нет вовсе |
| `![alt](/раздел/.files/x.png =1648x230)` | картинка с заданным размером |
| `:file[имя](/раздел/.files/…)` | вложенный файл |
| `&[текст](12345)` | якорная сноска |

Если текст переносится в другой документ — разметку надо **переписать**, а не
скопировать. Для механической части этого есть `wiki2md.py`.

### 7.2. Забрать страницу себе и дальше править

```bash
wiki-page.sh <slug> --raw | jq -r .content | wiki2md.py > файл.md
```

`--raw` отдаёт сырой JSON, `jq -r .content` достаёт тело, `wiki2md.py` чистит
разметку. Шапка при этом теряется — она нужна для чтения, а не для работы;
в забранный файл её место занимает своя (см. 7.4).

**Что проверить руками после конвертации:**

- **Шапка таблицы.** Первая строка вики-таблицы становится заголовком
  markdown-таблицы. Если в исходнике заголовка не было, первая строка данных
  уедет в шапку — автоматически это не определяется. На таблице мнемоник
  очередей мы на этом уже попались.
- **Картинки.** Они превращаются в ссылки вида
  `[изображение: alt](https://wiki.yandex.ru/…)`, а не во встроенные картинки:
  файлы в Вики закрыты авторизацией, отобразить их в markdown нельзя.
- **Цветной текст** `{orange}(…)` конвертер не трогает — правь руками.

**Перезапись затирает правки.** Если документ уже дорабатывался у тебя,
выгружай во временный файл и сличай, а не пиши поверх:

```bash
wiki-page.sh <slug> --raw | jq -r .content | wiki2md.py > /tmp/svezhee.md
diff /tmp/svezhee.md файл.md
```

### 7.3. Комментарии

Комментарии не приходят вместе со страницей — это отдельная ручка, поэтому и
отдельный флаг: без него один запрос на страницу, с ним два.

```bash
wiki-page.sh <slug> --comments
wiki-page.sh <slug> --raw | jq -r '.attributes.comments_count'   # есть ли что смотреть
wiki-page.sh <slug> --comments --raw | jq -r '.comments[] | .body'
```

По каждому комментарию выгружаются автор (с пометкой «уволен», если человек
уже не в организации), дата, ссылка-якорь `#comment-<id>` — она открывает то же
место, что и в Вике, — фрагмент текста, к которому комментарий привязан, и
пометка «Ответ на комментарий #…» для веток. Удалённые пропускаются, решённые
помечаются.

Ответ страничный, скрипт идёт по курсору до конца. Если выгрузить удалось не
всё — в stderr предупреждение, а в выводе то, что успело прийти: молча
обрезанный список комментариев хуже честно неполного.

### 7.4. Опубликовать в Вики

Так мы ведём документы, у которых первоисточник — репозиторий, а Вики только
витрина: правим у себя, публикуем оттуда, **в браузере не редактируем**. Так
уехали 15 страниц жизненного цикла задачи CRM.

```bash
wiki-push.py <каталог>                  # сухой прогон — что и куда уйдёт
wiki-push.py <каталог> --apply          # запись
wiki-push.py <файл> --apply --notify    # + уведомить подписчиков
wiki-push.py <файл> --apply --create    # + создать страницу, если её нет
```

**Адрес страницы берётся из шапки самого документа** — из строки со ссылкой на
Вики в первых 12 строках файла:

```markdown
# Заголовок страницы

> **Рабочая копия — ведём и правим здесь, отсюда же публикуем.**
> Страница в Вики: [powbee/reglamenty/vpn](https://wiki.yandex.ru/powbee/reglamenty/vpn).
```

Отдельной карты «файл → страница» нет намеренно: она разошлась бы с файлами
молча. Документ без такой строки не публикуется — скрипт сообщает и идёт дальше.

**Что скрипт делает с текстом перед отправкой:**

| Что | Зачем |
|---|---|
| Убирает `H1` | Заголовок в Вики задаётся полем `title`; в теле он был бы вторым |
| Убирает выноску «Рабочая копия» сразу под `H1` | Это заметка о состоянии нашей копии, в первоисточнике она бессмысленна |
| Относительные ссылки → адреса Вики | Относительная ссылка в Вики ведёт в никуда |
| Ссылки на то, чего в Вики нет, → обычный текст | Каждый такой случай печатается в отчёте — не теряется молча |
| Пути от корня (`/раздел/…`) не трогает | Это уже адрес Вики. Так записаны картинки и вложения; развернув их в текст, мы вырезали бы из страницы все иллюстрации |

Разметку переписывать не нужно: Вики понимает обычный markdown, включая таблицы
на пайпах, цитаты и блоки кода. Обратный конвертер к `wiki2md.py` не требуется.

**Три флага и почему они выключены по умолчанию:**

| Флаг | Почему не по умолчанию |
|---|---|
| `--apply` | Запись в чужой документ — не то, что делается «на всякий случай». Сухой прогон бесплатный |
| `--notify` | Заливка раздела из 15 страниц иначе даёт 15 писем каждому подписчику |
| `--create` | Опечатка в slug создаст страницу-дубль в чужом разделе, и никто её не найдёт |

**Порядок при первой публикации раздела:** иерархия задаётся slug'ом, поэтому
файлы публикуются одним прогоном и в порядке имён — родитель до потомков. Иначе
на дочерней странице получишь `400 Immediate parent page does not exist`.

**Ограничение, о котором надо знать заранее.** Карта «файл → адрес в Вики»
строится по каталогам, перечисленным в самом скрипте (константа `KNOWN_DIRS`), и
пути в ней считаются от корня репозитория. Если запускать `wiki-push.py` не из
репозитория, карта окажется пустой — публикация пройдёт, но **все относительные
ссылки развернутся в обычный текст**. Отчёт сухого прогона это покажет строкой
«ссылки развёрнуты в текст»; если строк там больше, чем ожидалось, — дело в этом.

## 8. Режим чата: агент без рук

Claude в браузере **не может ходить в Вики сам**: закрытый контур, авторизация
персональным токеном, никакого сетевого доступа туда у чата нет. Это не
настройка, которую забыли включить, — так устроено.

Обойти это можно, и «просто вставить ссылку» — рабочий сценарий. Вопрос в том,
где именно ты работаешь:

| Где работаешь | Можно вставить ссылку? | Что для этого нужно |
|---|---|---|
| **Claude Code** в терминале | да | скрипты из раздела 6 либо MCP-сервер |
| **Claude Desktop** (приложение на компьютере) | **да** | подключить `wiki-mcp.py` — разовая настройка, раздел 8.1 |
| **Claude в браузере** (claude.ai) | нет | нужен размещённый на сервере коннектор, у нас его нет — раздел 8.2 |

Дальше по порядку: как включить ссылки там, где это возможно, и как работать
там, где нет.

### 8.1. Ссылка вместо копипаста: подключить MCP-сервер

Работает в **Claude Desktop** и в **Claude Code**. Настраивается один раз,
после этого в разговоре достаточно дать ссылку — агент сходит за страницей сам,
с твоим токеном и твоими правами.

Что нужно: Python 3 (на macOS уже есть), файл `wiki-mcp.py` из раздела 6.4 и
настроенные учётные данные из раздела 3.

**Claude Code** — одной командой:

```bash
claude mcp add powbee-wiki -- python3 ~/bin/pbe-wiki/wiki-mcp.py
```

**Claude Desktop** — через файл настроек. Открывается из самого приложения:
Настройки → Разработчик → Изменить конфигурацию; на macOS это
`~/Library/Application Support/Claude/claude_desktop_config.json`. Добавить в
него блок (полный путь к файлу — обязательно, `~` там не раскрывается):

```json
{
  "mcpServers": {
    "powbee-wiki": {
      "command": "python3",
      "args": ["/Users/<твоё-имя>/bin/pbe-wiki/wiki-mcp.py"]
    }
  }
}
```

Перезапустить приложение. В интерфейсе появятся инструменты `wiki_read` и
`wiki_comments`; при первом обращении Claude спросит разрешение.

Дальше разговор выглядит так: «посмотри
`https://wiki.yandex.ru/powbee/reglamenty/vpn` и скажи, что там про роутинг» —
и всё.

**Три грабли этой настройки:**

1. **Приложение запускает сервер без твоего профиля оболочки.** Строки `export`
   из `~/.zshrc` он не увидит. Работают только Keychain и файл
   `~/.config/yandex-wiki/env` — либо блок `"env"` прямо в конфигурации, но
   тогда токен ложится в файл настроек открытым текстом, чего делать не стоит.
2. **Путь только полный.** `~/bin/…` в конфигурации не раскроется, и сервер
   молча не запустится.
3. **Сервер читает страницы твоими правами.** Всё, что открыто тебе, попадёт
   в разговор. Это удобство, а не расширение доступа: закрытую страницу он
   не откроет, а открытую — вытащит целиком, вместе с тем, что ты не собирался
   показывать.

### 8.2. Почему в браузере так не получится

Чат на claude.ai умеет подключать внешние источники, но только **размещённые на
сервере**: сервис Anthropic должен сам достучаться до такого коннектора по сети.
Локальный скрипт на твоём ноутбуке для него недоступен в принципе.

Значит, вариант «работает и в браузере» — это поднять коннектор к Вики на нашем
сервере и открыть его наружу. Это не полчаса работы и не только техника:
появляется сервис, который ходит в Вики **не под личным токеном читателя**, и
вопрос «кто и что через него видит» надо решать до запуска, а не после.
Инфраструктура — зона технического директора; решение принимается отдельно.

Пока такого коннектора нет, в браузерном чате работает раздел 8.4 — руками.

### 8.3. Как начать разговор

Независимо от способа доставки страниц, агенту нужна эта инструкция.

Лучший способ — **Проект**: создать в Claude проект (например, «Вики Powbee»),
приложить этот документ в базу знаний проекта и дальше работать в нём.
Инструкция подтянется в каждый разговор, повторять её не придётся.

Если проекта нет — вставить документ (или нужные разделы) первым сообщением,
а вторым сообщением задачу. Короткий набор, если целиком многовато: раздел 1
(правила), раздел 7.1 (таблица разметки Вики) и раздел 8.

### 8.4. Передать страницу руками — когда MCP нет

**Способ первый, без терминала.** Открыть страницу в Вики, выделить текст,
скопировать, вставить в чат. Оговорка: из браузера копируется **отрисованный**
текст, а не разметка — таблицы и выноски приедут в виде, который агенту придётся
угадывать. Для чтения и обсуждения хватает; если текст пойдёт обратно на
страницу — лучше способ второй.

**Способ второй, одна команда.** Если терминал есть — даже без Claude Code,
просто окно терминала:

```bash
wiki-page.sh <slug> | pbcopy        # macOS: результат сразу в буфер обмена
wiki-page.sh <slug> | xclip -sel c  # Linux
```

Дальше Cmd+V в чат. Здесь приедет настоящая разметка страницы, а не её вид на
экране, — с ней агент работает точно.

**Способ третий, файлом.** Удобно для длинных страниц, которые иначе занимают
весь экран чата:

```bash
wiki-page.sh <slug> > ~/Desktop/stranica.md
```

### 8.5. Как вернуть текст в Вики

Записи нет ни в одном из чатовых вариантов — ни через копипаст, ни через
MCP-сервер: он только читает. Возвращает текст человек.

1. **Проверить, не изменилась ли страница** с момента, когда её забрали.
   Открыть, глянуть историю версий. Правка заменяет тело целиком — чужие
   изменения, сделанные за это время, исчезнут.
2. Открыть страницу на редактирование и **переключить редактор в режим
   разметки**. В визуальном режиме вставленный markdown осядет текстом со
   звёздочками и решётками.
3. Вставить и **посмотреть предпросмотр до сохранения** — таблицы и выноски
   ломаются чаще всего.

**Просить агента отдавать текст одним блоком, а не кусками.** Собирать страницу
из четырёх фрагментов, вставленных по очереди, — верный способ потерять один
из них.

### 8.6. Что в чат отправлять нельзя

Это касается любого агента, но в чате нарушить проще всего — там нет ни
скриптов, ни фильтров, только копипаст.

- **Токен и ID организации.** Никогда, ни в каком виде.
- **Персональные данные** — ФИО пациентов, врачей, контакты, всё, что попадает
  под 152-ФЗ. Страницы с клиентскими данными в Вики есть.
- **Боевые данные** — выгрузки из базы, содержимое таблиц с реальными записями.
- **Договоры и коммерческие условия клиентов** — если задача не про них.

Правило простое: **в чат идёт текст, с которым надо поработать, а не всё, что
открылось рядом**. Не уверен — вырежи спорный кусок, замени плейсхолдером и
скажи агенту, что там было по смыслу.

С подключённым MCP-сервером это правило не отменяется, а становится важнее:
страницу целиком в разговор тянет уже не человек, а агент, и притормозить
некому. Проси конкретное — «что там про роутинг», а не «загрузи весь раздел».

### 8.7. Чего в этом режиме не будет

Честно, чтобы не ждать напрасно:

- агент **не увидит вложения и картинки** страницы — только их имена;
- агент **не проверит, что страница с тех пор не менялась**, — это делает человек;
- **записи в Вики не будет** — только чтение;
- **обхода дерева нет и с MCP-сервером**: одна страница за обращение, «выгрузи
  весь раздел» не работает нигде;
- **комментарии** через копипаст из браузера приедут без авторов, дат и привязки
  к тексту. Нужны они — только выгрузкой или инструментом `wiki_comments`.

## 9. Грабли: симптом → причина → что делать

Сводка. Часть уже встречалась выше — здесь по симптому, чтобы искать было быстро.

| Симптом | Причина | Что делать |
|---|---|---|
| `200`, но текста страницы нет | В запросе не указан `fields` | Запрашивать `fields=content,owner,attributes` |
| `400 BAD_REQUEST` на чтении | Неизвестное поле в `fields`; какое — не сказано | Идти от полного набора к минимальному, как делает скрипт |
| `401` при валидном токене Трекера | У OAuth-приложения нет скоупа Вики | Завести отдельный токен со скоупом Вики |
| `403` на чтении | Неверный `X-Org-Id` — чаще, чем реальное отсутствие прав | Сверить ID организации, потом уже права |
| `403` на создании | Родительский раздел закрыт на запись | Посмотреть в шапке выгрузки признак «только чтение», просить доступ у владельца раздела |
| `400 Immediate parent page does not exist` | Родительской страницы нет: иерархия задаётся slug'ом | Публиковать раздел одним прогоном, родитель до потомков |
| Разбор ответа падает через раз | Ответ приходит то объектом, то `{results: […]}`, то массивом | Брать первый элемент, если это список |
| В забранной таблице шапкой стала строка данных | В исходной вики-таблице заголовка не было | Поправить руками — автоматически не определяется |
| Со страницы пропали все картинки после публикации | Пути от корня (`/раздел/.files/…`) развернулись в текст как «относительные ссылки» | Пути от корня не трогать — это уже адреса Вики |
| Правка в Вики исчезла | Кто-то отредактировал страницу в браузере, а мы записали поверх своей копией | Одна точка правки. Ведётся в репозитории — в браузере не редактируем |
| Наши доработки исчезли при обновлении копии | Выгрузили страницу поверх файла, который уже правили у себя | Выгружать во временный файл и сличать |
| Подписчики завалены письмами | Заливка раздела с `--notify` | Тихо по умолчанию; уведомлять — осознанно и один раз |
| Появилась страница-дубль в чужом разделе | Опечатка в slug при создании | `--create` — только когда точно знаешь адрес |
| В опубликованной странице вместо ссылок голый текст | У целей ссылок нет страниц в Вики, либо скрипт запущен вне репозитория и карта пуста | Смотреть строку «ссылки развёрнуты в текст» в сухом прогоне |
| Скрипт разобрал вывод неправильно | Опорой взят разделитель `---`, а он встречается и внутри текста страницы | Опираться на строку-заголовок или ссылку `https://wiki.yandex.ru/…` |
| «Нет доступа к Вики» при настроенных учётных данных | Значения лежат не там, где скрипт ищет: имя сервиса Keychain, путь файла или имя переменной изменены | Имена и пути из раздела 3 менять нельзя |
| Не находится страница, про которую точно знаешь | Поиска по API нет | Искать в браузере, копировать ссылку |
| «Выгрузи мне весь раздел» не работает | Обхода дерева нет, одна страница за обращение | Собрать список slug'ов и передать их одним вызовом |
| MCP-сервер не появился в приложении | В конфигурации путь с `~` — он там не раскрывается | Полный путь: `/Users/<имя>/bin/pbe-wiki/wiki-mcp.py` |
| MCP-сервер запустился, но говорит «нет учётных данных» | Приложение стартует его без профиля оболочки, `export` из `~/.zshrc` не виден | Держать учётные данные в Keychain или в `~/.config/yandex-wiki/env` |
| Чат в браузере не видит страницу по ссылке | Локальный сервер сервису Anthropic недоступен — так устроено | В браузере только руками, раздел 8.4 |

## 10. Границы

Чего этот документ не покрывает и куда идти:

- **Права и структура разделов Вики** — не через API. Кто владеет разделом,
  кому открыт доступ, куда переносить страницу — вопрос к владельцу раздела.
- **Оформление и стиль страниц** — здесь только механика. Как писать сам текст,
  задают регламенты соответствующего документа.
- **Пользовательский мануал** публикуется по своим правилам и из своего
  репозитория — механика та же, процесс другой.
- **Трекер** — отдельные скрипты и отдельная инструкция.

## 11. Как поддерживать этот документ

Служебный раздел — для того, кто документ ведёт. Читателю он не нужен.

Тексты скриптов в разделе 6 — **копии**. Это осознанная цена
самодостаточности: документ работает у человека без репозитория, но копия
разойдётся с оригиналом при первой же правке скрипта, и разойдётся молча.
Против этого есть сверка:

```bash
automation/scripts/check-wiki-doc.py          # сверить, код 1 при расхождении
automation/scripts/check-wiki-doc.py --fix    # переписать блоки по оригиналам
```

**После `--fix` документ надо вычитать.** Скрипт возвращает код 1 даже когда
всё починил, и это не ошибка, а требование посмотреть глазами: вместе с кодом
меняются флаги, коды возврата и поведение, а про них написано в разделах 5, 7
и 9 — их сверка не трогает.

Порядок при правке скрипта: правишь оригинал → `--fix` → вычитываешь разделы 5,
7, 9 → поднимаешь версию в шапке.

Грабли из раздела 9 дописываются по мере того, как на них наступают. Это
основная ценность документа: рецепты можно вывести заново, а список того, где
уже ломались, — нет.

**В Вики документ не публикуется** — страницы у него нет, и в шапке нет строки
с адресом. Публикатор такие файлы пропускает, так что случайно он туда не уедет.

## 12. Обратная связь

Скрипты и этот документ ведёт директор департамента информационных технологий.
Наступил на грабли, которых здесь нет, — скажи: они дописываются сюда, а не
пересказываются в личке следующему.
