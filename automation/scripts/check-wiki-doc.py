#!/usr/bin/env python3
"""Сверяет тексты скриптов, вшитые в инструкцию по Вики, с оригиналами.

Инструкция docs/regulations/claude-code/wiki.md самодостаточна: тексты
wiki-page.sh, wiki2md.py, wiki-push.py и wiki-mcp.py лежат прямо в ней, чтобы её можно было
отдать целиком человеку без репозитория. Цена этого — копии, которые молча
разойдутся с оригиналами при первой же правке скрипта.

Скрипт достаёт блоки кода из разделов 6.1–6.4 и сравнивает с файлами рядом.

    ./check-wiki-doc.py            # сверить
    ./check-wiki-doc.py --fix      # переписать блоки в инструкции по оригиналам

Коды возврата: 0 — сходится, 1 — расхождение (или оно исправлено с --fix).
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

DOC = "docs/regulations/claude-code/wiki.md"

# Заголовок раздела → (файл скрипта, язык блока кода).
BLOCKS = {
    "### 6.1. `wiki-page.sh` — прочитать страницу": ("wiki-page.sh", "bash"),
    "### 6.2. `wiki2md.py` — перевести разметку Вики в markdown": ("wiki2md.py", "python"),
    "### 6.3. `wiki-push.py` — опубликовать обратно": ("wiki-push.py", "python"),
    "### 6.4. `wiki-mcp.py` — чтобы в чат можно было просто вставить ссылку":
        ("wiki-mcp.py", "python"),
}


def find_block(lines: list[str], heading: str) -> tuple[int, int]:
    """Границы первого блока кода после заголовка: (строка ```lang, строка ```)."""
    try:
        start = lines.index(heading)
    except ValueError:
        raise LookupError(f"в {DOC} нет раздела «{heading}»")

    opened = None
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("```"):
            if opened is None:
                opened = i
            else:
                return opened, i
    raise LookupError(f"после «{heading}» не найден закрытый блок кода")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Сверка вшитых в инструкцию текстов скриптов с оригиналами.")
    ap.add_argument("--fix", action="store_true",
                    help="переписать блоки в инструкции по оригиналам")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[2]
    doc_path = root / DOC
    if not doc_path.is_file():
        print(f"Не найден {DOC}", file=sys.stderr)
        return 1

    lines = doc_path.read_text(encoding="utf-8").split("\n")
    drift = 0

    # Снизу вверх: правка нижнего блока не сдвигает номера строк верхних.
    for heading in reversed(list(BLOCKS)):
        name, lang = BLOCKS[heading]
        src = (root / "automation/scripts" / name)
        if not src.is_file():
            print(f"✗ {name}: оригинал не найден", file=sys.stderr)
            drift += 1
            continue

        try:
            open_i, close_i = find_block(lines, heading)
        except LookupError as e:
            print(f"✗ {name}: {e}", file=sys.stderr)
            drift += 1
            continue

        embedded = lines[open_i + 1:close_i]
        original = src.read_text(encoding="utf-8").split("\n")
        if original and original[-1] == "":
            original = original[:-1]          # хвостовой перевод строки

        if embedded == original:
            print(f"✓ {name}")
            continue

        drift += 1
        if args.fix:
            lines[open_i:close_i + 1] = [f"```{lang}", *original, "```"]
            print(f"↻ {name} — блок в инструкции переписан")
        else:
            print(f"✗ {name} — расходится с оригиналом:", file=sys.stderr)
            diff = difflib.unified_diff(embedded, original, "в инструкции",
                                        f"automation/scripts/{name}", lineterm="")
            for line in list(diff)[:20]:
                print("   " + line, file=sys.stderr)

    if args.fix and drift:
        doc_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n{DOC} обновлён. Вычитай раздел 6 и остальной текст: "
              "флаги и поведение могли измениться вместе с кодом.")
        return 1

    if drift:
        print(f"\nРасхождений: {drift}. Исправить — ./check-wiki-doc.py --fix",
              file=sys.stderr)
        return 1

    print("\nИнструкция по Вики сходится с оригиналами скриптов.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
