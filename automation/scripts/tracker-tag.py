#!/usr/bin/env python3
"""
Правка тегов задач Трекера: проставить мнемонику и заменить теги-двойники.

    tracker-tag.py --set CRM-1102=ROC CONSOLE-10=PBE        # сухой прогон
    tracker-tag.py --set-file пары.tsv --apply
    tracker-tag.py --rename Roche=ROC Boehringer=BOE        # массово, по всем задачам
    tracker-tag.py --normalize                              # все известные двойники разом
    tracker-tag.py --normalize --apply

**По умолчанию скрипт ничего не пишет** — печатает, что было бы сделано. Запись
включается `--apply`, и тогда же в reports/ ложится журнал изменений: массовая
правка тегов задним числом иначе неотличима от того, что теги стояли всегда.

Пара режимов, а не один:

* `--set KEY=TAG` — добавить тег конкретным задачам. Прочие теги остаются.
* `--rename СТАРЫЙ=НОВЫЙ` — найти все задачи со старым тегом, снять его и
  поставить новый. Это разные операции: первая закрывает пропуск, вторая —
  расхождение с [регламентом](../../docs/regulations/tracker-queues.md).

Тег-двойник (`Roche` вместо `ROC`) хуже отсутствующего: задача выглядит
протегированной, но в разрез по клиентам не попадает — расхождение видно только
при пересчёте. Отсюда `--normalize` с картой известных двойников в коде.

Уведомления подписчикам по умолчанию не шлются: при правке сотни задач подряд
иначе получается спам. Включить — `--notify`.

Токен и ID организации — как в tracker-issue.sh: переменные окружения → Keychain
(yandex-tracker / yandex-tracker-org) → ~/.config/yandex-tracker/env.

Коды возврата: 2 — ошибка вызова, 3 — нет учётных данных, 1 — часть задач не
обновилась.
"""

import argparse
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

_SIBLING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker-project-report.py")
_spec = importlib.util.spec_from_file_location("tracker_project_report", _SIBLING)
tpr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tpr)

CLIENTS = tpr.CLIENTS

# Теги-двойники, встреченные в Трекере, → мнемоника по регламенту. Карта живёт в
# коде, а не в аргументах: это накопленное знание о том, как именно пишут теги,
# и каждый новый двойник должен дописываться сюда, а не набираться руками заново.
#
# `SLV` (Сэлвим) намеренно не в карте: в Трекере он встречается чаще, чем
# регламентный `SAL`, — здесь расходятся не тег и норма, а норма и практика,
# и решать это правкой регламента, а не задач. См. docs/backlog.md.
ALIASES = {
    "ALC": "ALCEA",
    "Alcea": "ALCEA",
    "Avexima": "AVX",
    "Bayer": "BAY",
    "Besins": "BSN",
    "Boehringer": "BOE",
    "Roche": "ROC",
    "Xantis": "XNS",
}


def bail(message):
    print(message, file=sys.stderr)
    sys.exit(2)


def api(path, method="GET", body=None, params=None):
    url = f"{tpr.API}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"OAuth {tpr.TOKEN}")
    req.add_header("X-Org-ID", tpr.ORG)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def issues_with_tag(tag):
    """Все задачи с этим тегом. Кавычки обязательны: иначе ALC цепляет ALCEA."""
    found, page = [], 1
    while True:
        batch = api("issues/_search", method="POST",
                    body={"query": f'Tags: "{tag}"'},
                    params={"perPage": 200, "page": page})
        found.extend(batch)
        if len(batch) < 200:
            return found
        page += 1


def set_tags(key, tags, notify=False):
    """Замещает список тегов целиком.

    Не `{"add": …, "remove": …}` намеренно. Поиск по тегам в Трекере
    регистронезависим (`Tags: "Alcea"` находит задачи с `ALCEA`), и если снятие
    ведёт себя так же, то `remove: ["Alcea"]` на задаче с каноническим `ALCEA`
    снял бы именно его — задача осталась бы вообще без тега. Полный список
    не зависит от того, как сервер сравнивает строки.
    """
    issue = api(f"issues/{key}", method="PATCH", body={"tags": list(tags)},
                params={"notify": "true" if notify else "false"})
    return issue.get("tags") or []


def parse_pairs(values, what):
    pairs = []
    for raw in values:
        if "=" not in raw:
            bail(f"Не разобрал пару {what}: '{raw}'. Ожидаю вид ЛЕВОЕ=ПРАВОЕ.")
        left, right = raw.split("=", 1)
        left, right = left.strip(), right.strip()
        if not left or not right:
            bail(f"Пустая половина в '{raw}'")
        pairs.append((left, right))
    return pairs


def read_pairs_file(path):
    """KEY=TAG, KEY<таб>TAG или KEY TAG — по строке на задачу; # — комментарий."""
    values = []
    with open(path) as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            values.append(line.replace("\t", "=").replace(" ", "=", 1)
                          if "=" not in line else line)
    return values


def plan_set(pairs):
    """[(ключ, добавить, снять, почему-пропущено)] — состояние читается заранее."""
    plan = []
    for key, tag in pairs:
        try:
            issue = api(f"issues/{key}")
        except urllib.error.HTTPError as e:
            plan.append((key, tag, [], f"не открывается: HTTP {e.code}"))
            continue
        current = issue.get("tags") or []
        if tag in current:
            plan.append((key, tag, current, "тег уже стоит"))
        else:
            plan.append((key, tag, current, ""))
    return plan


def plan_rename(pairs):
    """[(ключ, было, станет, какие двойники сработали)].

    Решение принимается по фактическим тегам задачи, а не по тому, что нашёл
    поиск: поиск регистронезависим и приносит лишнее. Тег меняется, только если
    отличается от канона посимвольно, — `ALCEA` при карте `Alcea → ALCEA`
    остаётся как есть, `Alcea` и `ALC` заменяются.
    """
    canon_by_upper = {old.upper(): new for old, new in pairs}
    plan, seen = [], set()
    for old, _ in pairs:
        for issue in issues_with_tag(old):
            key = issue["key"]
            if key in seen:      # задача может прийти по двум двойникам сразу
                continue
            seen.add(key)
            current = issue.get("tags") or []
            wanted, hit = [], []
            for tag in current:
                canon = canon_by_upper.get((tag or "").strip().upper())
                if canon and tag != canon:
                    hit.append(f"{tag}→{canon}")
                    if canon not in wanted:
                        wanted.append(canon)
                elif tag not in wanted:
                    wanted.append(tag)
            if hit:
                plan.append((key, current, wanted, hit))
    return plan


def journal(lines, mode):
    """Журнал массовой правки — в reports/ (папка вне git)."""
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out_dir = os.path.join(repo, "reports")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    path = os.path.join(out_dir, f"tracker-tag-{mode}-{stamp}.log")
    with open(path, "w") as fh:
        fh.write(f"# Правка тегов, режим {mode}, {stamp}\n")
        fh.write(f"# Команда: automation/scripts/tracker-tag.py {' '.join(sys.argv[1:])}\n")
        for line in lines:
            fh.write(line + "\n")
    return path


def main():
    p = argparse.ArgumentParser(
        description="Правка тегов задач Трекера: проставить мнемонику, заменить двойники.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--set", nargs="+", metavar="KEY=TAG", default=[],
                   help="добавить тег указанным задачам")
    p.add_argument("--set-file", metavar="ФАЙЛ",
                   help="то же списком из файла: по строке на задачу")
    p.add_argument("--rename", nargs="+", metavar="OLD=NEW", default=[],
                   help="во всех задачах заменить тег OLD на NEW")
    p.add_argument("--normalize", action="store_true",
                   help="заменить все известные теги-двойники (карта ALIASES в скрипте)")
    p.add_argument("--apply", action="store_true", help="выполнить запись")
    p.add_argument("--notify", action="store_true",
                   help="уведомить подписчиков (по умолчанию тихо)")
    args = p.parse_args()

    if not (args.set or args.set_file or args.rename or args.normalize):
        p.error("нечего делать: укажите --set, --set-file, --rename или --normalize")

    try:
        tpr.TOKEN, tpr.ORG = tpr.credentials()
    except SystemExit as e:
        print(e, file=sys.stderr)
        sys.exit(3)

    failures = 0
    log = []

    # --- проставление тега конкретным задачам ---
    values = list(args.set) + (read_pairs_file(args.set_file) if args.set_file else [])
    if values:
        pairs = parse_pairs(values, "--set")
        unknown = sorted({t for _, t in pairs if t.upper() not in CLIENTS})
        if unknown:
            print(f"Внимание: не мнемоники по регламенту — {', '.join(unknown)}\n",
                  file=sys.stderr)
        plan = plan_set(pairs)
        todo = [x for x in plan if not x[3]]
        print(f"## Проставить тег: {len(todo)} задач(и) из {len(plan)}\n")
        for key, tag, current, skip in plan:
            was = ", ".join(current) if current else "—"
            if skip:
                print(f"  ПРОПУСК {key:22} +{tag:6} (было: {was}) — {skip}")
            else:
                print(f"  {key:22} +{tag:6} (было: {was})")
        print()
        if args.apply:
            for key, tag, current, skip in todo:
                try:
                    now = set_tags(key, list(current) + [tag], notify=args.notify)
                    log.append(f"{key}\t+{tag}\t{', '.join(current) or '—'} → {', '.join(now)}")
                except urllib.error.HTTPError as e:
                    failures += 1
                    print(f"  ОШИБКА {key}: HTTP {e.code} {e.read().decode()[:200]}",
                          file=sys.stderr)
            print(f"Проставлено: {len(todo) - failures} из {len(todo)}\n")

    # --- замена двойников ---
    rename = parse_pairs(args.rename, "--rename")
    if args.normalize:
        have = {o for o, _ in rename}
        rename += [(o, n) for o, n in sorted(ALIASES.items()) if o not in have]
    if rename:
        plan = plan_rename(rename)
        print(f"## Заменить теги-двойники: {len(plan)} задач(и)\n")
        by_pair = {}
        for key, current, wanted, hit in plan:
            for h in hit:
                by_pair.setdefault(h, []).append((key, current, wanted))
        for h, items in sorted(by_pair.items(), key=lambda x: -len(x[1])):
            print(f"  {h.replace('→', ' → ')}: {len(items)} задач(и)")
            for key, current, wanted in items[:3]:
                print(f"      {key:22} {', '.join(current)} → {', '.join(wanted)}")
            if len(items) > 3:
                print(f"      … ещё {len(items) - 3}")
        print()
        if args.apply:
            done = 0
            for key, current, wanted, hit in plan:
                try:
                    now = set_tags(key, wanted, notify=args.notify)
                    log.append(f"{key}\t{'; '.join(hit)}\t"
                               f"{', '.join(current)} → {', '.join(now)}")
                    done += 1
                except urllib.error.HTTPError as e:
                    failures += 1
                    print(f"  ОШИБКА {key}: HTTP {e.code} {e.read().decode()[:200]}",
                          file=sys.stderr)
            print(f"Заменено: {done} из {len(plan)}\n")

    if not args.apply:
        print("Сухой прогон. Записать — тот же вызов с --apply.")
        return

    if log:
        mode = "normalize" if (args.rename or args.normalize) else "set"
        print(f"Журнал: {journal(log, mode)}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
