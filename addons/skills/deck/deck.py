#!/usr/bin/env python3
"""deck — научные презентации по методу assertion-evidence.

Слайд пишется в markdown, вёрстку держит тема sci.css: кегли, поля и место
под иллюстрацию заданы в CSS, поэтому испортить их содержанием нельзя.

  deck.py new  доклад.md --title "..." --author "..."
  deck.py render доклад.md --to pdf        # pdf | pptx | png | html
  deck.py check доклад.md [--vision]       # перегруз, перелив, «ярлык вместо мысли»
  deck.py layouts                          # шпаргалка по макетам

Метод: M. Alley, Penn State. Заголовок — предложение с главной мыслью слайда
(8-14 слов, максимум две строки), под ним визуальное доказательство, а не
буллеты. В исследованиях Penn State аудитория запоминала такой материал
достоверно лучше (p < .01).
"""
import argparse
import os
import re
import subprocess
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
THEME = os.path.join(HERE, "themes", "sci.css")
VISION = "/home/openclaw/.openclaw/vision.py"

# Ярлыки-темы: именно их метод и просит заменить на утверждение
LABELS = {
    "введение", "актуальность", "цель", "цели", "цель и задачи", "задачи",
    "материалы и методы", "материалы", "методы", "методика", "методология",
    "результаты", "результат", "обсуждение", "выводы", "вывод", "заключение",
    "литература", "источники", "спасибо за внимание", "благодарности",
    "клинический случай", "статистика", "эпидемиология", "патогенез",
    "диагностика", "лечение", "прогноз", "план", "содержание", "о себе",
}
NO_FIGURE_OK = {"title", "section-break", "statement", "quote"}


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def marp(args_str, cwd):
    """Запуск marp с граблями, которые иначе ловишь глазами:
    --allow-local-files (иначе картинки молча не подгружаются),
    CHROME_NO_SANDBOX (snap-хромиум), --no-stdin (иначе ждёт ввода)."""
    env = ("export NVM_DIR=$HOME/.nvm; . $NVM_DIR/nvm.sh; "
           "export CHROME_PATH=${CHROME_PATH:-/snap/bin/chromium} CHROME_NO_SANDBOX=true; ")
    return sh(f'bash -lc \'{env} cd "{cwd}" && marp {args_str} '
              f'--theme "{THEME}" --allow-local-files --no-stdin\'')


# ─────────────────────────── разбор колоды ────────────────────────────
def parse(path):
    """[{n, classes, headline, kicker, body_words, images, bullets, has_table}]"""
    raw = open(path, encoding="utf-8").read()
    if raw.startswith("---"):
        raw = raw.split("---", 2)[2] if raw.count("---") >= 2 else raw
    slides = []
    for i, chunk in enumerate(re.split(r"\n---\s*\n", raw), 1):
        cls = re.findall(r"<!--\s*_?class:\s*([^>]+?)\s*-->", chunk)
        classes = set(" ".join(cls).split())
        h = re.search(r"^#{1,2}\s+(.+)$", chunk, re.M)
        kick = re.search(r'class="kicker"[^>]*>(.*?)<', chunk, re.S)
        imgs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", chunk)
        text = re.sub(r"<[^>]+>", " ", chunk)
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
        text = re.sub(r"^#{1,6}\s+.+$", " ", text, flags=re.M)
        text = re.sub(r"\|.*\|", " ", text)
        slides.append({
            "n": i,
            "classes": classes,
            "headline": (h.group(1).strip() if h else ""),
            "kicker": (kick.group(1).strip() if kick else ""),
            "body_words": len([w for w in re.findall(r"[\w\-]+", text) if len(w) > 1]),
            "images": imgs,
            "bullets": len(re.findall(r"^\s*[-*]\s+", chunk, re.M)),
            "nested": bool(re.search(r"^\s{2,}[-*]\s+", chunk, re.M)),
            "has_table": "|" in chunk and re.search(r"\|\s*-{2,}", chunk) is not None,
            "has_src": bool(re.search(r'class="src"|Источник|doi|DOI', chunk)),
            "has_number": bool(re.search(r"\d+\s*%|\bn\s*=\s*\d+|\d{2,}", chunk)),
        })
    return slides


def check_text(slides, base):
    """Механические претензии к разметке. Возвращает [(слайд, уровень, текст)]."""
    out = []
    for s in slides:
        n, cls = s["n"], s["classes"]
        if "title" in cls:
            continue
        h, words = s["headline"], s["headline"].split()

        if not h:
            out.append((n, "СТОП", "нет заголовка — слайд без мысли"))
        else:
            low = h.lower().rstrip(":.").strip()
            if low in LABELS or len(words) <= 2:
                out.append((n, "СТОП", f"заголовок «{h}» — ярлык темы, а не мысль. "
                                       "Ярлык унеси в кикер, в заголовок вынеси вывод"))
            elif len(words) > 14:
                out.append((n, "правка", f"заголовок {len(words)} слов — по методу максимум 14, "
                                         "иначе не читается из зала"))
            elif len(words) < 4 and "section-break" not in cls:
                out.append((n, "правка", f"заголовок из {len(words)} слов — похоже на ярлык"))
            if h.endswith(":"):
                out.append((n, "правка", "заголовок с двоеточием — это подводка, а не утверждение"))

        limit = 30 if "air" in cls else (70 if ("dense" in cls or s["has_table"]) else 45)
        if s["body_words"] > limit:
            out.append((n, "правка", f"{s['body_words']} слов в теле (предел {limit}) — "
                                     "слайд перегружен, часть уводи в устный текст"))
        if s["bullets"] > 5:
            out.append((n, "правка", f"{s['bullets']} буллетов — метод просит не больше пяти"))
        if s["nested"]:
            out.append((n, "правка", "вложенные буллеты — верный признак перегруза"))

        if not (s["images"] or s["has_table"] or cls & NO_FIGURE_OK):
            out.append((n, "СТОП", "нет ни иллюстрации, ни таблицы — "
                                   "по методу доказательство должно быть визуальным"))
        for img in s["images"]:
            if img.lower().endswith(".svg"):
                out.append((n, "СТОП", f"{img}: SVG молча не рендерится в PDF/PNG — "
                                       "переведи в PNG"))
            elif img.startswith(("http://", "https://")):
                out.append((n, "СТОП", f"{img}: внешние ссылки хромиум не тянет — "
                                       "скачай файл рядом"))
            elif not os.path.exists(os.path.join(base, img)):
                out.append((n, "СТОП", f"{img}: файла нет"))
            elif os.path.basename(img) == "placeholder.png":
                out.append((n, "правка", "стоит заглушка — поставь настоящую "
                                         "иллюстрацию (скилл imggen) или убери слайд"))
        if s["has_number"] and not s["has_src"] and "title" not in cls:
            out.append((n, "мелочь", "есть цифры, но нет источника"))
    return out


# ─────────────────────────── пиксельные проверки ───────────────────────
def check_pixels(pngs, slides):
    """Перелив за поля и пустота/плотность — то, что видно только на рендере."""
    try:
        from PIL import Image
    except ImportError:
        return [(0, "мелочь", "PIL не установлен — пиксельные проверки пропущены")]
    out = []
    for path, s in zip(pngs, slides):
        if "figure-full" in s["classes"]:
            continue                      # там картинка во всё поле — это норма
        im = Image.open(path).convert("L")
        w, h = im.size
        px = im.load()
        # фон = самый частый уровень серого
        hist = im.histogram()
        bg = hist.index(max(hist))
        step = max(1, w // 320)

        def ink(x0, y0, x1, y1):
            c = 0
            for y in range(y0, y1, step):
                for x in range(x0, x1, step):
                    if abs(px[x, y] - bg) > 14:
                        c += 1
            return c

        band = max(6, int(h * 0.028))     # надёжно внутри поля темы
        # отступаем от краёв: слева у титула декоративная полоса во всю высоту,
        # справа внизу — номер страницы; и то и другое не перелив
        edge = int(w * 0.035)
        bottom = ink(edge, h - band, int(w * 0.86), h)
        right = ink(w - band, int(h * 0.06), w, int(h * 0.90))
        if bottom > 12:
            out.append((s["n"], "СТОП", "текст уходит за нижнее поле — "
                                        "сократи или поставь класс dense"))
        if right > 12:
            out.append((s["n"], "СТОП", "содержимое вылезает за правое поле"))

        total = ink(0, 0, w, h)
        cells = (w // step) * (h // step)
        frac = total / max(cells, 1)
        if frac < 0.012 and "section-break" not in s["classes"]:
            out.append((s["n"], "правка", f"слайд почти пуст ({frac:.1%} заполнения)"))
        elif frac > 0.34:
            out.append((s["n"], "правка", f"слайд перегружен ({frac:.0%} заполнения)"))
    return out


def check_vision(pngs, slides):
    """Последняя инстанция — посмотреть на слайд. Ловит то, что метрики не видят."""
    out = []
    q = (
        "Ты смотришь на слайд научного доклада, свёрстанный по методу "
        "assertion-evidence. В этой вёрстке НОРМА, а не дефект: мелкая "
        "полупрозрачная надпись-кикер над заголовком (служебное имя раздела, "
        "оно и должно быть незаметным); много воздуха и незанятые поля; "
        "титульный слайд без иллюстрации; слайд из одной крупной фразы. "
        "ДЕФЕКТ - только это: текст обрезан или налезает на картинку либо на "
        "другой текст; заголовок не отражает содержимого слайда или является "
        "названием темы (Результаты, Методы) вместо вывода; заголовок - "
        "инструкция или рыба из шаблона; основной текст мельче примерно 18 pt "
        "и не читается из зала; картинка-заглушка, битая или не по теме; "
        "таблица с рыбой вместо данных. "
        "Ответь СТРОГО JSON без пояснений, формат: "
        "{\"ok\": true, \"проблемы\": []} . "
        "Дефектов нет - ok true и пустой список. Вкусовые придирки не пиши."
    )
    for path, s in zip(pngs, slides):
        # список аргументов, а не строка для shell: вопрос содержит кавычки,
        # фигурные скобки и |, и через shell они рвали команду
        r = subprocess.run([sys.executable, VISION, os.path.abspath(path), q],
                           capture_output=True, text=True, timeout=180)
        txt = (r.stdout or "").strip()
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            why = (r.stderr or txt or "").strip().splitlines()
            out.append((s["n"], "мелочь",
                        "зрение не ответило" + (f": {why[-1][:120]}" if why else "")))
            continue
        try:
            v = json.loads(m.group(0))
        except Exception:
            continue
        if not v.get("ok", True):
            for p in (v.get("проблемы") or [])[:3]:
                out.append((s["n"], "правка", f"глазами: {p}"))
    return out


# ─────────────────────────── команды ───────────────────────────────────
def cmd_new(a):
    tpl = open(os.path.join(HERE, "templates", "skeleton.md"), encoding="utf-8").read()
    tpl = (tpl.replace("{{TITLE}}", a.title).replace("{{AUTHOR}}", a.author)
              .replace("{{AFFIL}}", a.affil or "").replace("{{DATE}}", a.date or ""))
    if os.path.exists(a.path) and not a.force:
        sys.exit(f"{a.path} уже есть — добавь --force, если правда хочешь перезаписать")
    os.makedirs(os.path.dirname(os.path.abspath(a.path)) or ".", exist_ok=True)
    open(a.path, "w", encoding="utf-8").write(tpl)
    print(f"OK: {a.path}\nДальше: пиши слайды, потом deck.py check {a.path} --vision")


def cmd_render(a):
    base = os.path.dirname(os.path.abspath(a.md)) or "."
    name = os.path.splitext(os.path.basename(a.md))[0]
    flag = {"pdf": "--pdf", "pptx": "--pptx", "png": "--images png", "html": "--html"}[a.to]
    out = a.out or (f"{name}.{a.to}" if a.to != "png" else f"{name}.png")
    extra = " --image-scale 2" if a.to == "png" else ""
    extra += " --pdf-notes" if (a.to == "pdf" and a.notes) else ""
    r = marp(f'"{os.path.basename(a.md)}" {flag}{extra} -o "{out}"', base)
    print((r.stdout + r.stderr).strip()[-1500:])
    if r.returncode:
        sys.exit("рендер не удался")
    print(f"OK: {os.path.join(base, out)}")


def cmd_check(a):
    base = os.path.dirname(os.path.abspath(a.md)) or "."
    slides = parse(a.md)
    issues = check_text(slides, base)

    tmp = os.path.join(base, ".deck_check")
    os.makedirs(tmp, exist_ok=True)
    r = marp(f'"{os.path.basename(a.md)}" --images png --image-scale 1 '
             f'-o ".deck_check/s.png"', base)
    pngs = sorted(os.path.join(tmp, f) for f in os.listdir(tmp) if f.endswith(".png"))
    if pngs and len(pngs) == len(slides):
        issues += check_pixels(pngs, slides)
        if a.vision:
            issues += check_vision(pngs, slides)
    else:
        issues.append((0, "мелочь", f"рендер дал {len(pngs)} картинок на {len(slides)} "
                                    f"слайдов — пиксельные проверки пропущены"))
        if r.returncode:
            issues.append((0, "СТОП", (r.stderr or "")[-300:]))

    print(f"КОЛОДА: {a.md} — {len(slides)} слайд(ов)\n")
    if not issues:
        print("Замечаний нет: заголовки несут мысль, ничего не переливается, "
              "у каждого слайда есть визуальное доказательство.")
    else:
        order = {"СТОП": 0, "правка": 1, "мелочь": 2}
        for n, lvl, msg in sorted(issues, key=lambda x: (order[x[1]], x[0])):
            where = f"слайд {n}" if n else "колода"
            print(f"  [{lvl:6}] {where}: {msg}")
        stop = sum(1 for i in issues if i[1] == "СТОП")
        print(f"\nИТОГО: стоп-замечаний {stop}, правок "
              f"{sum(1 for i in issues if i[1] == 'правка')}.")
        if stop:
            print("Стоп-замечания чини до показа: это не вкусовщина, "
                  "а сломанный слайд или ярлык вместо мысли.")
    if not a.keep:
        for f in pngs:
            os.remove(f)
        try:
            os.rmdir(tmp)
        except OSError:
            pass
    else:
        print(f"\nКартинки слайдов: {tmp}")


def cmd_layouts(a):
    print(open(os.path.join(HERE, "templates", "layouts.md"), encoding="utf-8").read())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="создать каркас доклада")
    n.add_argument("path"); n.add_argument("--title", required=True)
    n.add_argument("--author", required=True); n.add_argument("--affil")
    n.add_argument("--date"); n.add_argument("--force", action="store_true")
    r = sub.add_parser("render", help="собрать pdf/pptx/png/html")
    r.add_argument("md"); r.add_argument("--to", default="pdf",
                                         choices=["pdf", "pptx", "png", "html"])
    r.add_argument("-o", "--out"); r.add_argument("--notes", action="store_true")
    c = sub.add_parser("check", help="проверить колоду перед показом")
    c.add_argument("md"); c.add_argument("--vision", action="store_true",
                                         help="ещё и посмотреть на каждый слайд")
    c.add_argument("--keep", action="store_true", help="оставить картинки слайдов")
    sub.add_parser("layouts", help="шпаргалка по макетам")
    a = ap.parse_args()
    {"new": cmd_new, "render": cmd_render, "check": cmd_check, "layouts": cmd_layouts}[a.cmd](a)


if __name__ == "__main__":
    main()
