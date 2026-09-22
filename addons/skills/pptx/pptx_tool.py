#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Правка ГОТОВОЙ презентации без смены дизайна: копия, разбор, дубль слайда, замена текста, проверка, рендер.

Зачем. 22.09 доктор попросил вставить пару слайдов в готовую продуктовую презентацию «в том же стиле».
Инструмента для правки PPTX не было — агент пересобрал слайды своей темой, а потом, клонируя слайд
вручную, оставил старые подписи под новым текстом. Здесь всё это делается штатно: новый слайд —
всегда ДУБЛЬ существующего слайда той же презентации (фон, шрифты, цвета, расположение — родные),
текст меняется с сохранением оформления первого фрагмента, проверка ловит наложения и вылеты.

    pptx_tool.py copy ИСХОДНИК КОПИЯ          копия + отпечаток исходника (исходник потом не меняется)
    pptx_tool.py info ФАЙЛ [--slide N]        что на слайдах: фигуры, их id, текст, кегль, позиция
    pptx_tool.py dup ФАЙЛ N [--to M]          дублировать слайд N (нумерация с 1), вставить на место M
    pptx_tool.py text ФАЙЛ N ID "текст"       заменить текст фигуры ID на слайде N; строки через \\n
    pptx_tool.py text ФАЙЛ N ID "текст" --cell R,C    то же для ячейки таблицы (с 1)
    pptx_tool.py clear ФАЙЛ N ID              убрать фигуру (например, лишнюю подпись в дубле)
    pptx_tool.py move ФАЙЛ N M                переставить слайд N на место M
    pptx_tool.py delete ФАЙЛ N                удалить слайд N
    pptx_tool.py renumber ФАЙЛ                перенумеровать страницы после вставки/удаления, формат «03» сохраняется
    pptx_tool.py check ФАЙЛ                   наложения текста, вылеты за слайд, риск переполнения,
                                              сбитые номера страниц, исходник не тронут; то, что было
                                              в исходнике (родной дизайн), не считается ошибкой
    pptx_tool.py render ФАЙЛ [--out ПАПКА]    PDF + PNG каждой страницы (LibreOffice) — смотреть глазами
"""
import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

from pptx import Presentation
from pptx.util import Emu

R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EMU_PT = 12700


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def meta_path(f):
    return f + ".source.json"


def slide_at(prs, n):
    if not 1 <= n <= len(prs.slides):
        sys.exit(f"слайда {n} нет: в файле их {len(prs.slides)}")
    return prs.slides[n - 1]


def shape_by_id(slide, sid):
    for sh in slide.shapes:
        if sh.shape_id == sid:
            return sh
    sys.exit(f"на слайде нет фигуры с id {sid} — посмотри `info`")


def font_pt(shape):
    """Кегль первого фрагмента текста, если задан явно."""
    if not shape.has_text_frame:
        return None
    for p in shape.text_frame.paragraphs:
        for r in p.runs:
            if r.font.size:
                return round(r.font.size.pt)
    return None


# ───────────────────────── команды ─────────────────────────

def cmd_copy(a):
    if os.path.abspath(a.src) == os.path.abspath(a.dst):
        sys.exit("копия должна лежать в другом файле — исходник не трогаем")
    shutil.copy2(a.src, a.dst)
    json.dump({"source": os.path.abspath(a.src), "sha256": sha(a.src)},
              open(meta_path(a.dst), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"копия: {a.dst}\nотпечаток исходника записан — `check` скажет, если исходник изменится")


def cmd_info(a):
    prs = Presentation(a.file)
    W, H = prs.slide_width, prs.slide_height
    print(f"слайдов: {len(prs.slides)} · размер {Emu(W).inches:.2f}×{Emu(H).inches:.2f} дюйма")
    rng = [a.slide] if a.slide else range(1, len(prs.slides) + 1)
    for n in rng:
        s = slide_at(prs, n)
        print(f"\n━━ слайд {n} · макет «{s.slide_layout.name}»")
        for sh in s.shapes:
            kind = sh.shape_type if sh.shape_type is not None else "placeholder"
            pos = f"x{sh.left // EMU_PT if sh.left is not None else '?'} y{sh.top // EMU_PT if sh.top is not None else '?'} " \
                  f"{sh.width // EMU_PT if sh.width is not None else '?'}×{sh.height // EMU_PT if sh.height is not None else '?'}pt"
            txt = ""
            if sh.has_text_frame and sh.text_frame.text.strip():
                txt = " «" + sh.text_frame.text.strip().replace("\n", " ⏎ ")[:90] + "»"
            elif getattr(sh, "has_table", False) and sh.has_table:
                t = sh.table
                txt = f" таблица {len(t.rows)}×{len(t.columns)}: «{t.cell(0, 0).text[:40]}…»"
            fp = font_pt(sh)
            print(f"  id {sh.shape_id:<4} {str(kind):<22} {pos:<26} {('%dpt' % fp) if fp else '':<5}{txt}")


def cmd_dup(a):
    prs = Presentation(a.file)
    src = slide_at(prs, a.n)
    new = prs.slides.add_slide(src.slide_layout)
    # Слайд целиком: фон, фигуры, цветовая схема, переходы — копии родных элементов
    for child in list(new._element):
        new._element.remove(child)
    for child in src._element:
        new._element.append(copy.deepcopy(child))
    # Связи (картинки, диаграммы, ссылки) — новые rId, и переписываем их в скопированной разметке
    rid_map = {}
    for rid, rel in src.part.rels.items():
        if rel.reltype.endswith(("/slideLayout", "/notesSlide")):
            continue
        if rel.is_external:
            rid_map[rid] = new.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
        else:
            rid_map[rid] = new.part.relate_to(rel.target_part, rel.reltype)
    for el in new._element.iter():
        for attr in ("embed", "link", "id", "pict"):
            key = f"{{{R_NS}}}{attr}"
            if el.get(key) in rid_map:
                el.set(key, rid_map[el.get(key)])
    lst = prs.slides._sldIdLst
    moved = lst[-1]
    lst.remove(moved)
    to = a.to if a.to else a.n + 1
    lst.insert(to - 1, moved)
    prs.save(a.file)
    print(f"слайд {a.n} продублирован → стоит на месте {to}. Дальше: `info {a.file} --slide {to}` и `text`.")


def _set_paragraphs(tf, lines):
    """Заменить текст, сохранив оформление: абзац и фрагмент берутся из первого абзаца как образец."""
    tpl_p = copy.deepcopy(tf.paragraphs[0]._p)
    txBody = tf._txBody
    for p in list(tf.paragraphs):
        txBody.remove(p._p)
    for line in lines:
        p = copy.deepcopy(tpl_p)
        runs = p.findall("{http://schemas.openxmlformats.org/drawingml/2006/main}r")
        for extra in runs[1:]:
            p.remove(extra)
        for br in p.findall("{http://schemas.openxmlformats.org/drawingml/2006/main}br"):
            p.remove(br)
        for fld in p.findall("{http://schemas.openxmlformats.org/drawingml/2006/main}fld"):
            p.remove(fld)
        if runs:
            t = runs[0].find("{http://schemas.openxmlformats.org/drawingml/2006/main}t")
            t.text = line
        else:
            from lxml import etree
            A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
            r = etree.SubElement(p, A + "r")
            etree.SubElement(r, A + "t").text = line
            end = p.find(A + "endParaRPr")
            if end is not None:
                p.remove(end)
                p.append(end)
        txBody.append(p)


def cmd_text(a):
    prs = Presentation(a.file)
    sh = shape_by_id(slide_at(prs, a.n), a.id)
    lines = a.text.replace("\\n", "\n").split("\n")
    if a.cell:
        if not (getattr(sh, "has_table", False) and sh.has_table):
            sys.exit("--cell только для таблиц")
        r, c = (int(x) for x in a.cell.split(","))
        _set_paragraphs(sh.table.cell(r - 1, c - 1).text_frame, lines)
    else:
        if not sh.has_text_frame:
            sys.exit(f"у фигуры {a.id} нет текста")
        _set_paragraphs(sh.text_frame, lines)
    prs.save(a.file)
    print(f"слайд {a.n}, фигура {a.id}: текст заменён ({len(lines)} строк), оформление сохранено")


def cmd_clear(a):
    prs = Presentation(a.file)
    sh = shape_by_id(slide_at(prs, a.n), a.id)
    sh._element.getparent().remove(sh._element)
    prs.save(a.file)
    print(f"слайд {a.n}: фигура {a.id} убрана")


def cmd_move(a):
    prs = Presentation(a.file)
    slide_at(prs, a.n)
    lst = prs.slides._sldIdLst
    el = lst[a.n - 1]
    lst.remove(el)
    lst.insert(a.m - 1, el)
    prs.save(a.file)
    print(f"слайд {a.n} → место {a.m}")


def cmd_delete(a):
    prs = Presentation(a.file)
    slide_at(prs, a.n)
    lst = prs.slides._sldIdLst
    el = lst[a.n - 1]
    prs.part.drop_rel(el.get(f"{{{R_NS}}}id"))
    lst.remove(el)
    prs.save(a.file)
    print(f"слайд {a.n} удалён")


def _box(sh):
    if None in (sh.left, sh.top, sh.width, sh.height):
        return None
    return sh.left, sh.top, sh.left + sh.width, sh.top + sh.height


def _page_numbers(prs):
    """[(слайд, число, id фигуры, ширина записи)] — короткое число в нижней части слайда."""
    import re as _re
    H = prs.slide_height
    out = []
    for n, s in enumerate(prs.slides, 1):
        for sh in s.shapes:
            t = sh.text_frame.text.strip() if sh.has_text_frame else ""
            if t and _box(sh) and _re.fullmatch(r"\d{1,3}", t) and sh.top > H * 0.8:
                out.append((n, int(t), sh.shape_id, len(t)))
    return out


def cmd_renumber(a):
    prs = Presentation(a.file)
    nums = _page_numbers(prs)
    if not nums:
        sys.exit("номеров страниц не нашёл")
    off = nums[0][1] - nums[0][0]
    fixed = 0
    for n, v, sid, w in nums:
        want = n + off
        if v != want:
            sh = shape_by_id(prs.slides[n - 1], sid)
            _set_paragraphs(sh.text_frame, [str(want).zfill(w)])
            fixed += 1
    prs.save(a.file)
    print(f"номера страниц: исправлено {fixed}, отсчёт как у первого слайда с номером (слайд {nums[0][0]} = {nums[0][1]})")


def _problems(prs):
    """Находки по файлу: (слайд, ключ без номера слайда, сообщение). Ключ нужен, чтобы отличать
    родные особенности исходника (плашка поверх карточки — это дизайн) от того, что сломали мы."""
    W, H = prs.slide_width, prs.slide_height
    out = []
    # Номера страниц. Точка отсчёта — первый слайд с номером (титул бывает без номера): «большинство»
    # врёт, когда вставили слайд в начало — тогда съехали все следующие, и правыми кажутся они.
    nums = _page_numbers(prs)
    if nums:
        off = nums[0][1] - nums[0][0]
        for n, v, sid, _w in nums:
            if v - n != off:
                out.append((n, ("page", n, v), f"слайд {n}: номер страницы «{v}», а по порядку должен быть {n + off} "
                                               f"(фигура {sid}) — `renumber` поправит все разом"))
    for n, s in enumerate(prs.slides, 1):
        texts = [sh for sh in s.shapes if sh.has_text_frame and sh.text_frame.text.strip() and _box(sh)]
        for sh in texts:
            t = sh.text_frame.text.strip()[:40]
            x1, y1, x2, y2 = _box(sh)
            if x1 < 0 or y1 < 0 or x2 > W or y2 > H:
                out.append((n, ("edge", t), f"слайд {n}: фигура {sh.shape_id} «{t}» вылезает за край слайда"))
            pt = font_pt(sh) or 18
            chars_per_line = max(1, (sh.width / EMU_PT) / (0.5 * pt))
            lines = sum(max(1, -(-len(p.text) // int(chars_per_line))) for p in sh.text_frame.paragraphs)
            need, have = lines * pt * 1.2, sh.height / EMU_PT
            if need > have * 1.25 and sh.text_frame.auto_size is None:
                out.append((n, ("fit", t), f"слайд {n}: фигура {sh.shape_id} «{t}» — текст может не влезать "
                                             f"(~{int(need)}pt при высоте {int(have)}pt)"))
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                a1, b1 = _box(texts[i]), _box(texts[j])
                ix = max(0, min(a1[2], b1[2]) - max(a1[0], b1[0]))
                iy = max(0, min(a1[3], b1[3]) - max(a1[1], b1[1]))
                smaller = min((a1[2] - a1[0]) * (a1[3] - a1[1]), (b1[2] - b1[0]) * (b1[3] - b1[1])) or 1
                if ix * iy / smaller > 0.15:
                    t1, t2 = texts[i].text_frame.text.strip()[:30], texts[j].text_frame.text.strip()[:30]
                    out.append((n, ("overlap", t1, t2, a1, b1),
                                f"слайд {n}: тексты НАЛЕЗАЮТ — id {texts[i].shape_id} «{t1}» и id {texts[j].shape_id} "
                                f"«{t2}» ({int(100 * ix * iy / smaller)}%)"))
    return out


def cmd_check(a):
    prs = Presentation(a.file)
    found = _problems(prs)
    m = meta_path(a.file)
    bad = 0
    if os.path.exists(m):
        info = json.load(open(m, encoding="utf-8"))
        if not os.path.exists(info["source"]):
            print(f"⚠️ исходник пропал: {info['source']}")
        else:
            if sha(info["source"]) != info["sha256"]:
                print(f"⛔ ИСХОДНИК ИЗМЕНЁН: {info['source']} — его нельзя было трогать")
                bad += 1
            else:
                print("✓ исходник не тронут")
            native = {k for _n, k, _m in _problems(Presentation(info["source"]))}
            skipped = sum(1 for _n, k, _m in found if k in native)
            found = [f for f in found if f[1] not in native]
            if skipped:
                print(f"  (пропущено {skipped}: то же самое есть в исходнике — это его дизайн, не наша поломка)")
    for _n, _k, msg in found:
        print(msg)
    bad += len(found)
    print("✓ наложений, вылетов и сбитых номеров не нашёл" if not bad else f"\nнаходок: {bad} — исправь и проверь снова")
    print("Глазами смотреть всё равно обязательно: `render`.")
    return 1 if bad else 0


def cmd_render(a):
    out = a.out or os.path.splitext(a.file)[0] + "_render"
    os.makedirs(out, exist_ok=True)
    prof = tempfile.mkdtemp(prefix="lo_profile_")
    subprocess.run(["soffice", f"-env:UserInstallation=file://{prof}", "--headless", "--convert-to", "pdf",
                    "--outdir", out, a.file], check=True, capture_output=True, timeout=300)
    shutil.rmtree(prof, ignore_errors=True)
    pdf = os.path.join(out, os.path.splitext(os.path.basename(a.file))[0] + ".pdf")
    subprocess.run(["pdftoppm", "-r", str(a.dpi), "-png", pdf, os.path.join(out, "page")], check=True, timeout=300)
    pages = sorted(f for f in os.listdir(out) if f.startswith("page") and f.endswith(".png"))
    print(f"PDF: {pdf}\nкартинки страниц ({len(pages)}): {out}/page-*.png — открой изменённые слайды и посмотри глазами")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("copy"); s.add_argument("src"); s.add_argument("dst"); s.set_defaults(fn=cmd_copy)
    s = sub.add_parser("info"); s.add_argument("file"); s.add_argument("--slide", type=int); s.set_defaults(fn=cmd_info)
    s = sub.add_parser("dup"); s.add_argument("file"); s.add_argument("n", type=int); s.add_argument("--to", type=int)
    s.set_defaults(fn=cmd_dup)
    s = sub.add_parser("text"); s.add_argument("file"); s.add_argument("n", type=int); s.add_argument("id", type=int)
    s.add_argument("text"); s.add_argument("--cell"); s.set_defaults(fn=cmd_text)
    s = sub.add_parser("clear"); s.add_argument("file"); s.add_argument("n", type=int); s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_clear)
    s = sub.add_parser("move"); s.add_argument("file"); s.add_argument("n", type=int); s.add_argument("m", type=int)
    s.set_defaults(fn=cmd_move)
    s = sub.add_parser("delete"); s.add_argument("file"); s.add_argument("n", type=int); s.set_defaults(fn=cmd_delete)
    s = sub.add_parser("check"); s.add_argument("file"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("renumber"); s.add_argument("file"); s.set_defaults(fn=cmd_renumber)
    s = sub.add_parser("render"); s.add_argument("file"); s.add_argument("--out"); s.add_argument("--dpi", type=int, default=70)
    s.set_defaults(fn=cmd_render)
    a = p.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
