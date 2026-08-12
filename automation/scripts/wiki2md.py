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
