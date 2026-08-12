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
KNOWN_DIRS = ["docs/regulations", "docs/regulations/crm-lifecycle"]

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
    """
    dropped: list[str] = []

    def repl(m: re.Match) -> str:
        label, href = m.group(1), m.group(2)
        if href.startswith(("http://", "https://", "mailto:", "#")):
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
