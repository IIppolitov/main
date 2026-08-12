#!/usr/bin/env python3
"""
Проверяет относительные ссылки во всех markdown-файлах проекта.

    automation/scripts/check-links.py            # из корня проекта
    automation/scripts/check-links.py docs       # только в подкаталоге

Документы у нас связаны перекрёстными ссылками (регламенты — первоисточник,
остальное на них ссылается), поэтому переименование файла молча ломает связи
в нескольких местах сразу. Скрипт ловит это до того, как ссылка понадобится.

Внешние ссылки (http) и якоря (#) не проверяются. Код в обратных кавычках и
блоки ``` пропускаются — там встречается разметка, похожая на ссылку.

Код возврата: 0 — всё цело, 1 — есть битые ссылки.
"""

import os
import re
import sys

LINK = re.compile(r'\[([^\]]+)\]\(([^)\s]+)\)')
CODE_SPAN = re.compile(r'`[^`]*`')
FENCE = re.compile(r'^\s*```')


def strip_code(text):
    """Убрать содержимое ``` блоков и `код` — иначе примеры разметки летят в ложные срабатывания."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if FENCE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else CODE_SPAN.sub("", line))
    return "\n".join(out)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    total = broken = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "vendor")]
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(dirpath, fn)
            text = strip_code(open(path, encoding="utf-8").read())
            for m in LINK.finditer(text):
                target = m.group(2)
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                total += 1
                dest = os.path.normpath(os.path.join(dirpath, target.split("#")[0]))
                if not os.path.exists(dest):
                    broken += 1
                    print(f"БИТАЯ  {path}  →  {target}")

    print(f"\nПроверено ссылок: {total}, битых: {broken}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
