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
