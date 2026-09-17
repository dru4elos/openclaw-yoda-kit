#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Механическая проверка текста на следы нейросети + выбор захода, чтобы посты не были на одно лицо.

Зачем машинка, если есть агент-редактор: модель, которая сама пишет шаблонами, их же и не видит.
Регулярка видит всегда и стоит ноль. Агент разбирает смысл, машинка — подпись.

    antislop.py check пост.md            находки списком, код возврата 1 если что-то нашлось
    antislop.py check - --json           текст со стандартного входа, находки в JSON (для студии)
    antislop.py shape                    заход, который давно не использовался
    antislop.py shape --use              то же и пометить использованным

Правила словами — в SKILL.md рядом. Здесь только то, что ловится автоматом.
"""
import argparse
import datetime as dt
import json
import os
import random
import re
import sys

STATE = os.path.expanduser("~/.openclaw/workspace/memory/antislop_shapes.json")

SHAPES = [
    "случай из практики: возраст, что привело, что сделали",
    "частый вопрос родителя — и прямой ответ на него",
    "ошибка, которую видишь каждую неделю",
    "разбор мифа: что говорят и как на самом деле",
    "цифра из исследования и что она меняет на приёме",
    "что делать сегодня вечером, по шагам",
    "что изменилось в подходе за последние годы",
    "письмо самому себе десять лет назад",
]

# (имя, регулярка, что с этим делать). Всё ищется без учёта регистра.
PATTERNS = [
    ("шаблонное противопоставление",
     r"не\s+просто\s+[^.,;!?]{2,40}[,]?\s+а\s|это\s+не\s+про\s+[^.]{2,40}\s+это\s+про|"
     r"дело\s+не\s+в\s+[^.]{2,40}\s+дело\s+в|—\s*это\s+ещё\s+не\s",
     "сказать прямо, что есть и что делать"),
    ("пустая вводная",
     r"важно\s+понимать|стоит\s+отметить|следует\s+отметить|давайте\s+разбер|^итак[,\s]|"
     r"не\s+секрет,?\s+что|в\s+современном\s+мире|сегодня\s+мы\s+(погово|разбер)",
     "вычеркнуть, смысл не пострадает"),
    ("мораль в конце",
     r"главное\s+—\s|главное,\s+вовремя|берегите\s+(себя|сво)|в\s+ваших\s+руках|"
     r"подводя\s+итог|в\s+заключение",
     "закончить на последнем деле: признак, срок, действие"),
    ("усилитель вместо содержания",
     r"ключев(ой|ая|ые|ым)\s|критически\s+важно|мощн(ый|ое)\s+инструмент|поистине|"
     r"невероятн|колоссальн|в\s+корне\s+мен",
     "убрать слово; если смысл не изменился — оно и было пустым"),
    ("фальшивая личность",
     r"как\s+врач\s+с\s+(много|\d+)|в\s+моей\s+практике\s+(нередко|часто)|за\s+годы\s+работы\s+я",
     "либо конкретный случай с возрастом и исходом, либо ничего"),
    ("канцелярский оборот",
     r"осуществля|производится\s+осмотр|проведение\s+(осмотра|обследования)|в\s+целях\s+",
     "глагол вместо отглагольного существительного"),
]

LINKERS = r"кроме\s+того|более\s+того|таким\s+образом|в\s+заключение|следует\s+отметить"
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")


def findings(text):
    """Список находок: что нашли, где и что с этим делать."""
    out = []
    lines = text.splitlines()

    def line_of(pos):
        return text.count("\n", 0, pos) + 1

    for name, rx, fix in PATTERNS:
        for m in re.finditer(rx, text, re.I | re.M):
            quote = text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ").strip()
            out.append({"что": name, "строка": line_of(m.start()), "цитата": quote, "как быть": fix})

    n_link = len(re.findall(LINKERS, text, re.I))
    if n_link > 2:
        out.append({"что": "канцелярских связок пачкой", "строка": 0, "цитата": f"{n_link} шт.",
                    "как быть": "оставить максимум две, остальные переходы переписать"})

    n_dash = len(re.findall(r"\s—\s", text))
    if n_dash > 2:
        out.append({"что": "длинных тире", "строка": 0, "цитата": f"{n_dash} шт.",
                    "как быть": "оставить максимум два, остальное — обычные предложения"})

    if re.search(r"\.\.\.|…", text):
        out.append({"что": "многоточие", "строка": line_of(re.search(r"\.\.\.|…", text).start()),
                    "цитата": "…", "как быть": "договорить мысль"})

    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if not s:
            continue
        head_or_bullet = (s.startswith(("#", "-", "•", "*")) or bool(EMOJI.match(s))
                          or (len(s) < 60 and s.endswith(":")))
        if head_or_bullet and EMOJI.search(s):
            out.append({"что": "эмодзи в заголовке или пункте", "строка": i, "цитата": s[:60],
                        "как быть": "убрать; эмодзи допустимы только там, где их разрешает жанр"})

    # Вопросы подряд: один уместен, два — приём нейросети. Считаем и соседние строки,
    # и два вопроса внутри одного абзаца («Что делать? Когда идти к врачу?»).
    qs = [i for i, ln in enumerate(lines) if ln.strip().endswith("?")]
    pair_line = next((a + 1 for a, b in zip(qs, qs[1:]) if b - a == 1), 0)
    if not pair_line:
        for i, ln in enumerate(lines, 1):
            if len(re.findall(r"\?", ln)) >= 2:
                pair_line = i
                break
    if pair_line:
        out.append({"что": "вопросы подряд", "строка": pair_line,
                    "цитата": lines[pair_line - 1].strip()[:60],
                    "как быть": "оставить один вопрос или заменить утверждением"})

    first = next((l.strip() for l in lines if l.strip()), "")
    if re.search(r"разбер(ём|ем)|расскажу|погово|в\s+этом\s+посте|сегодня\s+о\s+том", first, re.I) \
            or first.endswith("?"):
        out.append({"что": "первая строка-обещание", "строка": 1, "цитата": first[:70],
                    "как быть": "начать с факта, случая или цифры"})

    # Три абзаца одинаковой длины подряд — та самая машинная симметрия.
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for i in range(len(paras) - 2):
        ls = [len(p) for p in paras[i:i + 3]]
        if min(ls) > 120 and max(ls) - min(ls) <= 0.1 * max(ls):
            out.append({"что": "три абзаца одной длины", "строка": 0,
                        "цитата": f"{ls} знаков", "как быть": "сбить ритм: один укоротить или слить"})
            break

    return out


def cmd_check(a):
    text = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
    f = findings(text)
    if a.json:
        print(json.dumps(f, ensure_ascii=False, indent=1))
    elif not f:
        print("чисто: машинных следов не нашёл")
    else:
        print(f"находок: {len(f)}\n")
        for x in f:
            где = f"строка {x['строка']}" if x["строка"] else "по всему тексту"
            print(f"• {x['что']} ({где})\n  «{x['цитата']}»\n  → {x['как быть']}\n")
    return 1 if f else 0


def cmd_shape(a):
    """Заход, который давно не брали: иначе все посты начинаются одинаково."""
    try:
        used = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        used = {}
    fresh = [s for s in SHAPES if s not in list(used)[-3:]] or SHAPES
    pick = random.choice(fresh)
    print(pick)
    if a.use:
        used.pop(pick, None)                       # перенести в конец очереди
        used[pick] = dt.datetime.now().isoformat(timespec="minutes")
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(used, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="найти машинные следы в тексте")
    c.add_argument("file", help="файл или - для стандартного входа")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_check)
    s = sub.add_parser("shape", help="заход, который давно не использовался")
    s.add_argument("--use", action="store_true", help="пометить использованным")
    s.set_defaults(fn=cmd_shape)
    a = p.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
