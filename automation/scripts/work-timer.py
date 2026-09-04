#!/usr/bin/env python3
"""Секундомер работы по задаче: когда начали, сколько работали, сколько ждали человека.

Нужен затем, чтобы человек внёс в Трекер часы, а не вспоминал их к концу дня.
Никуда не отправляется и в Трекер не пишет — только печатает свод на экран.

    ./work-timer.py CRM-1234              # отметка; первая заводит секундомер
    ./work-timer.py CRM-1234 --report     # свод, отметку не ставит
    ./work-timer.py CRM-1234 --reset      # начать заново
    ./work-timer.py --list                # по каким задачам секундомер идёт

Как считается. Агент ставит отметку в начале и в конце каждого своего хода.
Разрыв между соседними отметками короче порога (по умолчанию 10 минут) —
работа; длиннее — пауза: человек читает, думает, отвечает или ушёл. Отсюда
две разные величины, которые обычно путают: сколько времени задача «шла»
и сколько по ней реально работали.

Метод грубый намеренно. Точнее его сделать нечем: агент не знает, ушёл человек
на обед или сидит и думает, — а промахнувшись, он бы приписал часы, которых
не было. Порог двигается флагом --gap, если ритм работы другой.

Считается время сессии с агентом, а не часы разработчика: сколько списать,
решает человек. Отметки лежат в ~/.cache/pbe-work-timer/, в git не попадают
и живут до --reset.

Коды возврата: 0 — всё прошло; 1 — секундомера по этой задаче нет;
2 — ошибка вызова.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

KEY_RE = re.compile(r"([A-Z][A-Z0-9]*-\d+)")
STAMP = "%Y-%m-%d %H:%M:%S"
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг",
            "пятница", "суббота", "воскресенье")


def store() -> Path:
    """Каталог отметок. Не в репозитории: это черновое состояние, не документ."""
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "pbe-work-timer"


def normalize_key(raw: str) -> str | None:
    """`CRM-1234`, `crm-1234` или ссылка целиком → `CRM-1234`."""
    found = KEY_RE.search(raw.strip().upper())
    return found.group(1) if found else None


def marks(path: Path) -> list[datetime]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(datetime.strptime(line, STAMP))
        except ValueError:
            continue  # битую строку молча пропускаем: свод важнее строгости
    return sorted(out)


def human(minutes: float) -> str:
    """Минуты → «3 ч 12 мин». Часы человек вносит в Трекер в таком виде."""
    total = int(round(minutes))
    if total < 1:
        return "меньше минуты"
    hours, mins = divmod(total, 60)
    if not hours:
        return f"{mins} мин"
    if not mins:
        return f"{hours} ч"
    return f"{hours} ч {mins} мин"


def report(key: str, stamps: list[datetime], gap: int) -> str:
    start, last = stamps[0], stamps[-1]
    span = (last - start).total_seconds() / 60

    active = 0.0
    pauses: list[float] = []
    for a, b in zip(stamps, stamps[1:]):
        delta = (b - a).total_seconds() / 60
        if delta <= gap:
            active += delta
        else:
            pauses.append(delta)

    lines = [
        f"## Время работы по {key}",
        "",
        f"Начало:            {start.strftime(STAMP)}, {WEEKDAYS[start.weekday()]}",
        f"Последняя отметка: {last.strftime(STAMP)}",
        f"Прошло всего:      {human(span)}",
        "",
    ]

    if len(stamps) == 1:
        lines.append("Отметка пока одна — работа только началась, считать нечего.")
        return "\n".join(lines)

    share = f" ({active / span * 100:.0f} % от общего)" if span > 0 else ""
    lines.append(f"Активная работа:   {human(active)}{share}")
    if pauses:
        lines.append(
            f"Паузы:             {human(sum(pauses))} — {len(pauses)} шт., "
            f"самая долгая {human(max(pauses))}")
    else:
        lines.append("Паузы:             не было")
    lines += [
        "",
        f"Отметок: {len(stamps)}. Паузой считается разрыв больше {gap} мин; "
        f"всё, что короче, идёт в работу.",
        "",
        "Это время сессии с агентом, а не часы разработчика. В Трекер вносишь "
        "своё время сам — цифры выше только ориентир.",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Секундомер работы по задаче: начало, активная работа, паузы.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("key", nargs="?", help="ключ задачи или ссылка на неё")
    ap.add_argument("--report", action="store_true",
                    help="показать свод, не ставя отметку")
    ap.add_argument("--reset", action="store_true",
                    help="стереть отметки и начать отсчёт заново")
    ap.add_argument("--list", action="store_true",
                    help="по каким задачам секундомер идёт")
    ap.add_argument("--gap", type=int, default=10, metavar="МИН",
                    help="порог паузы в минутах (по умолчанию 10)")
    args = ap.parse_args()

    if args.list:
        found = sorted(store().glob("*.log")) if store().is_dir() else []
        if not found:
            print("Секундомер не идёт ни по одной задаче.")
            return 0
        print("## Секундомеры")
        for path in found:
            stamps = marks(path)
            if not stamps:
                continue
            print(f"  {path.stem}: с {stamps[0].strftime(STAMP)}, "
                  f"отметок {len(stamps)}, последняя "
                  f"{stamps[-1].strftime(STAMP)}")
        return 0

    if not args.key:
        ap.error("нужен ключ задачи (или --list)")
    key = normalize_key(args.key)
    if key is None:
        print(f"Не похоже на ключ задачи: {args.key}", file=sys.stderr)
        return 2
    if args.gap < 1:
        ap.error("--gap меньше минуты не имеет смысла")

    path = store() / f"{key}.log"

    if args.reset:
        if path.is_file():
            path.unlink()
            print(f"Секундомер по {key} сброшен.")
        else:
            print(f"Секундомера по {key} и не было.")
        return 0

    if not args.report:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(datetime.now().strftime(STAMP) + "\n")

    stamps = marks(path)
    if not stamps:
        print(f"Секундомера по {key} нет. Первая отметка ставится вызовом "
              f"без --report.", file=sys.stderr)
        return 1

    if args.report:
        print(report(key, stamps, args.gap))
    else:
        # Отметка — служебное действие в середине работы: одна строка, чтобы
        # не засорять вывод. Свод печатается по --report, когда его спросили.
        print(f"· {key}: отметка {stamps[-1].strftime('%H:%M')}, "
              f"всего отметок {len(stamps)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
