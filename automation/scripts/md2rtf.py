#!/usr/bin/env python3
"""Markdown -> RTF (форматированный текст) для вставки в почтовый клиент.

Пишет .rtf рядом с исходным .md и по умолчанию кладёт результат в буфер
обмена: в Mail вставляется обычным Cmd+V с сохранением жирного, списков и
ссылок. Markdown-разметка (##, **, [](), `) в результат не попадает.

    automation/scripts/md2rtf.py reports/2026-09-03/pismo-....md
    automation/scripts/md2rtf.py письмо.md --no-copy --keep-html

Поддерживается подмножество markdown, которого хватает для писем: заголовки,
абзацы, списки (маркированные и нумерованные), цитаты, горизонтальная черта,
жирный, курсив, моноширинный, ссылки. Таблицы не поддерживаются — если они
есть в исходнике, скрипт предупредит.
"""

import argparse
import html
import re
import shutil
import subprocess
import sys
from pathlib import Path

INLINE_CODE_STYLE = 'font-family:Menlo,Monaco,monospace;font-size:90%'
BODY_STYLE = 'font-family:-apple-system,Helvetica,Arial,sans-serif;font-size:14px'


def inline(text: str) -> str:
    """Инлайновая разметка одной строки -> HTML."""
    out = html.escape(text)
    out = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', r'<a href="\2">\1</a>', out)
    out = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', out)
    out = re.sub(r'(?<![\w*])\*([^*\n]+?)\*(?![\w*])', r'<i>\1</i>', out)
    out = re.sub(r'`([^`]+)`', rf'<span style="{INLINE_CODE_STYLE}">\1</span>', out)
    return out


def md_to_html(src: str) -> str:
    blocks: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = ''
    quote: list[str] = []

    def flush_para():
        nonlocal para
        if para:
            blocks.append('<p>' + inline(' '.join(para)) + '</p>')
            para = []

    def flush_list():
        nonlocal items, list_tag
        if items:
            body = ''.join('<li>' + inline(i) + '</li>' for i in items)
            blocks.append(f'<{list_tag}>{body}</{list_tag}>')
            items, list_tag = [], ''

    def flush_quote():
        nonlocal quote
        if quote:
            blocks.append('<blockquote><p>' + inline(' '.join(quote)) + '</p></blockquote>')
            quote = []

    def flush_all():
        flush_para()
        flush_list()
        flush_quote()

    for raw in src.split('\n'):
        line = raw.strip()

        if not line:
            flush_all()
            continue

        heading = re.match(r'^(#{1,6})\s+(.*)$', line)
        if heading:
            flush_all()
            level = min(len(heading.group(1)) + 1, 6)   # # -> h2: h1 в письме избыточен
            blocks.append(f'<h{level}>{inline(heading.group(2))}</h{level}>')
            continue

        if re.match(r'^(-{3,}|\*{3,}|_{3,})$', line):
            flush_all()
            blocks.append('<hr>')
            continue

        bullet = re.match(r'^[-*+]\s+(.*)$', line)
        if bullet:
            flush_para()
            flush_quote()
            if list_tag and list_tag != 'ul':
                flush_list()
            list_tag = 'ul'
            items.append(bullet.group(1))
            continue

        numbered = re.match(r'^\d+[.)]\s+(.*)$', line)
        if numbered:
            flush_para()
            flush_quote()
            if list_tag and list_tag != 'ol':
                flush_list()
            list_tag = 'ol'
            items.append(numbered.group(1))
            continue

        if line.startswith('>'):
            flush_para()
            flush_list()
            quote.append(line.lstrip('> ').strip())
            continue

        # продолжение пункта списка или цитаты — переносом строки, а не новым абзацем
        if items:
            items[-1] += ' ' + line
            continue
        if quote:
            quote.append(line)
            continue

        # шапка письма и подпись: каждая строка — отдельный абзац
        if re.match(r'^\*\*(Кому|Копия|Тема|От):\*\*', line) or line in ('С уважением,',):
            flush_para()
            blocks.append('<p>' + inline(line) + '</p>')
            continue
        if para and re.match(r'^\*\*[^*]+:\*\*\s*$', para[-1]):
            flush_para()

        para.append(line)

    flush_all()

    return ('<html><head><meta charset="utf-8"></head>'
            f'<body style="{BODY_STYLE}">' + '\n'.join(blocks) + '</body></html>')


def main() -> int:
    ap = argparse.ArgumentParser(description='Markdown -> RTF для вставки в почту')
    ap.add_argument('source', type=Path, help='исходный .md')
    ap.add_argument('-o', '--out', type=Path, help='куда положить .rtf (по умолчанию рядом)')
    ap.add_argument('--no-copy', action='store_true', help='не класть в буфер обмена')
    ap.add_argument('--keep-html', action='store_true', help='оставить промежуточный .html')
    args = ap.parse_args()

    if not args.source.is_file():
        print(f'нет файла: {args.source}', file=sys.stderr)
        return 1
    if not shutil.which('textutil'):
        print('нужен textutil (входит в macOS)', file=sys.stderr)
        return 1

    src = args.source.read_text(encoding='utf-8')
    if re.search(r'^\s*\|.*\|\s*$', src, re.M):
        print('внимание: таблицы markdown не конвертируются — проверьте результат',
              file=sys.stderr)

    html_path = args.source.with_suffix('.html')
    rtf_path = args.out or args.source.with_suffix('.rtf')
    html_path.write_text(md_to_html(src), encoding='utf-8')

    with rtf_path.open('wb') as fh:
        subprocess.run(['textutil', '-format', 'html', '-convert', 'rtf',
                        '-stdout', str(html_path)], stdout=fh, check=True)

    if not args.keep_html:
        html_path.unlink()

    if not args.no_copy and shutil.which('pbcopy'):
        subprocess.run(['pbcopy'], stdin=rtf_path.open('rb'), check=True)
        print(f'{rtf_path} — и скопировано в буфер обмена')
    else:
        print(rtf_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
