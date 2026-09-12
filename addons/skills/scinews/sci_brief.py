#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci_brief.py — ежедневный научный бриф для врача: письма-алерты + свежее в Europe PMC →
выжимка по каждой статье → Word с дизайном + короткое сообщение в Telegram. Без агента.

  sci_brief.py run   [--hours 24] [--fresh-days 2] [--fresh-top 8] [--send] [--no-fresh]
  sci_brief.py show  S-0907-03          # карточка статьи из архива (для ответов на вопросы)
  sci_brief.py list  [дата]             # что было в брифе за день

Каждая статья получает ID вида S-MMDD-NN. Архив: ~/.openclaw/workspace/memory/sci_briefs/<дата>.json,
Word: ~/.openclaw/workspace/Наука/Научный_бриф_<дата>.docx, сводка для утреннего брифа:
~/.openclaw/workspace/memory/sci_alerts_today.txt.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sci  # noqa: E402  (llm-вызов, Europe PMC, Gmail-алерты, полнотекст)
import lang    # noqa: E402  вычитка русского языка
import biblio  # noqa: E402  библиография Vancouver из Europe PMC
import web     # noqa: E402  вёрстка страницы (палитра сайта владельца)

HOME = os.path.expanduser("~")
WS = f"{HOME}/.openclaw/workspace"
ARCHIVE = f"{WS}/memory/sci_briefs"
OUT_DIR = f"{WS}/Наука"
TXT_FOR_BRIEF = f"{WS}/memory/sci_alerts_today.txt"
TOME = f"{WS}/skills/tome/tome.py"
PY = sys.executable
MSK = dt.timezone(dt.timedelta(hours=3))
MODELS = ["gpt-5.3-codex-spark", "gemini-3.8-flash", "gpt-5.6-sol-1m"]   # Spark первым: письма, ранжирование, выжимки — быстро
READER = sci.ENV.get("SCI_READER", "детский травматолог-ортопед, к.м.н.; интересы: детская травма и ортопедия, "
                     "переломы и остеосинтез, ПКС/мениск, дисплазия ТБС, сколиоз, плоскостопие, косолапость, "
                     "болезнь Пертеса, артроскопия, реабилитация, детская хирургия, ИИ в медицине")
RATING_ORDER = {"fire": 0, "star": 1, "pin": 2, "none": 3}
RATING_ICON = {"fire": "🔥", "star": "⭐", "pin": "📌", "none": "⬜"}
RATING_WORD = {"fire": "меняет практику", "star": "важно", "pin": "любопытно", "none": "мимо"}
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
          "октября", "ноября", "декабря"]


def log(msg):
    print(f"[{dt.datetime.now(MSK).strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ---------- LLM ----------
def ask(prompt, max_tokens=8000, temperature=0.2):
    key = sci.ENV.get("EXCASH_API_KEY")
    bases = sci.excash_bases() if hasattr(sci, "excash_bases") else [sci.ENV.get("EXCASH_API_URL", "")]
    last = ""
    if key and bases:
        for model in MODELS:
            for base in bases:                      # страж-прокси первым: CDN excash режет прямые тела >10 КБ
                for attempt in (1, 2):
                    try:
                        txt = sci._llm_once(base, key, model, [{"role": "user", "content": prompt}],
                                            max_tokens, temperature, timeout=300)
                        if txt.strip():
                            return txt
                        last = f"{model}: пустой ответ"
                    except Exception as e:
                        last = f"{model}@{base.split('//')[-1][:20]}: {type(e).__name__} {str(e)[:60]}"
                        if "400" in str(e) or "404" in str(e) or "413" in str(e):
                            break
                    time.sleep(3 * attempt)
    dk = sci.ENV.get("DEEPSEEK_API_KEY")
    if dk:
        try:
            txt = sci._llm_once("https://api.deepseek.com/v1", dk, "deepseek-flash",
                                [{"role": "user", "content": prompt}], max(max_tokens, 16000), temperature, timeout=600)
            if txt.strip():
                return txt
        except Exception as e:
            last += f"; deepseek: {e}"
    raise RuntimeError("все модели отказали: " + last)


def ask_json(prompt, kind="array", **kw):
    raw = ask(prompt, **kw).strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:] if raw.lower().startswith("json") else raw
    if kind == "array":
        i, j = raw.find("["), raw.rfind("]")
    else:
        i, j = raw.find("{"), raw.rfind("}")
    if i == -1:
        raise ValueError("в ответе нет JSON")
    return json.loads(raw[i:j + 1])


# ---------- сбор статей ----------
def extract_from_mail(mail):
    prompt = (
        f"Ниже письмо-алерт научного журнала, пришедшее врачу ({READER}).\n\n"
        "Выпиши ВСЕ статьи из письма — не выборку. Верни СТРОГО JSON-массив без пояснений. Элемент:\n"
        '{"ru":"название по-русски","en":"оригинальное название как в письме","journal":"журнал",'
        '"year":"год","desc":"2-3 предложения строго по тому, что есть в письме (о чём, дизайн, что нашли)",'
        '"rating":"fire|star|pin|none","why":"одна фраза: чем полезно именно этому врачу; для none — пусто"}\n'
        "rating: fire = меняет практику/прорыв; star = важно и близко к его теме; pin = любопытно, косвенно; "
        "none = мимо интересов. Только заголовок без аннотации — в desc напиши «в письме только заголовок». "
        "Не выдумывай.\n\n"
        f"=== ПИСЬМО: {mail['subject']}\nОт: {mail['from']}\n{mail['body'][:11000]}")
    try:
        arr = ask_json(prompt, max_tokens=12000)
    except Exception as e:
        log(f"письмо «{mail['subject'][:50]}» не разобрано: {e}")
        return []
    out = []
    for x in arr if isinstance(arr, list) else []:
        if not isinstance(x, dict) or not (x.get("en") or x.get("ru")):
            continue
        x["rating"] = (x.get("rating") or "none").lower()
        x["source"] = "письмо: " + re.sub(r"\s+", " ", mail["subject"])[:70]
        out.append(x)
    return out


def enrich_epmc(art):
    """Europe PMC по названию: DOI/PMID/PMCID/аннотация/OA. Только при реальном совпадении названия."""
    title = art.get("en") or ""
    if len(title) < 12:
        return
    try:
        for query in (f'TITLE:"{title[:180]}"', title[:180]):
            for rec in (sci.epmc_search(query, n=3) or []):
                m = sci.fmt_meta(rec)
                if sci._same_article(title, m.get("title", "")):
                    art.update({"doi": m["doi"], "pmid": m["pmid"], "pmcid": m["pmcid"], "oa": m["oa"],
                                "abstract": (m["abstract"] or "").strip(), "authors": m["authors"],
                                "pubtype": ", ".join(rec.get("pubTypeList", {}).get("pubType", [])[:2]),
                                "journal": art.get("journal") or m["journal"], "year": art.get("year") or m["year"]})
                    return
            time.sleep(0.3)
    except Exception as e:
        log(f"EPMC {title[:40]}: {e}")


STREAMS = {
    "детская ортопедия и травма": '(pediatric OR paediatric OR children OR child OR adolescent*) AND (fracture* OR orthop* OR '
        '"anterior cruciate" OR meniscus OR meniscal OR scoliosis OR "hip dysplasia" OR DDH OR clubfoot OR flatfoot OR '
        'Perthes OR "slipped capital femoral" OR osteotomy OR physeal OR "growth plate" OR "limb lengthening" OR '
        '"bone healing" OR "spinal deformity" OR supracondylar OR "forearm fracture" OR "femoral shaft")',
    "взрослая травма и ортопедия": '(fracture* OR osteosynthesis OR "intramedullary" OR "plate fixation" OR nonunion OR '
        '"anterior cruciate" OR meniscus OR "sports injur*" OR "polytrauma" OR "orthopaedic trauma" OR "orthopedic trauma") '
        'AND (randomized OR "systematic review" OR "meta-analysis" OR cohort OR guideline)',
    "ИИ в ортопедии и медицине": '("artificial intelligence" OR "deep learning" OR "machine learning" OR "large language model") '
        'AND (orthop* OR fracture* OR radiograph* OR trauma OR "clinical decision") AND (pediatric OR children OR surgery OR emergency)',
}


def _seen_before(days=21):
    """DOI и названия статей из брифов за последние N дней — чтобы не показывать повторно."""
    dois, titles = set(), []
    if not os.path.isdir(ARCHIVE):
        return dois, titles
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    for f in os.listdir(ARCHIVE):
        if not f.endswith(".json") or f == "latest.json" or f[:10] < cutoff or f[:10] == dt.date.today().isoformat():
            continue
        try:
            b = json.load(open(os.path.join(ARCHIVE, f), encoding="utf-8"))
        except Exception:
            continue
        for a in b.get("mail_articles", []) + b.get("fresh", []):
            if a.get("doi"):
                dois.add(a["doi"].lower())
            if a.get("en"):
                titles.append(a["en"])
    return dois, titles


def fresh_from_epmc(days, top):
    d2 = dt.date.today()
    d1 = d2 - dt.timedelta(days=days)
    seen_dois, seen_titles = _seen_before()
    cands, have = [], set()
    for stream, q in STREAMS.items():
        try:
            recs = sci.epmc_search(f'({q}) AND (FIRST_PDATE:[{d1} TO {d2}]) AND (SRC:MED OR SRC:PPR)', n=30) or []
        except Exception as e:
            log(f"EPMC «{stream}»: {e}")
            continue
        for rec in recs:
            m = sci.fmt_meta(rec)
            key = (m["doi"] or m["title"]).lower()
            if not m["title"] or key in have or (m["doi"] and m["doi"].lower() in seen_dois):
                continue
            if any(sci._same_article(m["title"], s) for s in seen_titles):
                continue
            have.add(key)
            cands.append({"en": m["title"], "journal": m["journal"], "year": m["year"], "doi": m["doi"],
                          "pmid": m["pmid"], "pmcid": m["pmcid"], "oa": m["oa"], "abstract": (m["abstract"] or "").strip(),
                          "authors": m["authors"], "pubtype": ", ".join(rec.get("pubTypeList", {}).get("pubType", [])[:2]),
                          "stream": stream, "source": f"Europe PMC · {stream}, {days} дн."})
        time.sleep(0.3)
    log(f"  кандидатов из баз: {len(cands)}")
    if not cands:
        return []
    ranked = []
    for i0 in range(0, len(cands), 40):
        chunk = cands[i0:i0 + 40]
        blob = "\n\n".join(f"[{i0 + i}] {c['en']}\n{c['journal']} {c['year']} · {c['pubtype']} · поток: {c['stream']}\n{c['abstract'][:450]}"
                             for i, c in enumerate(chunk))
        prompt = (f"Врач: {READER}.\nНиже свежие публикации. Оцени каждую для ЭТОГО врача. Верни СТРОГО JSON-массив элементов "
                  '{"i":<номер>,"ru":"название по-русски","score":0-10,"rating":"fire|star|pin|none","why":"одна фраза, чем полезно"}. '
                  "score ≥ 7 — только действительно близкое к его практике (детская травма/ортопедия, остеосинтез, ПКС, сколиоз, "
                  "дисплазия, ИИ для травматолога); обзоры, RCT, мета-анализы и клинические рекомендации ценнее кейсов. "
                  "Реклама, не-ортопедия, чисто лабораторное — none. Не выдумывай.\n\n" + blob)
        try:
            ranked += ask_json(prompt, max_tokens=10000)
        except Exception as e:
            log(f"ранжирование fresh: {e}")
    out = []
    for r in ranked if isinstance(ranked, list) else []:
        try:
            c = cands[int(r.get("i"))]
        except Exception:
            continue
        c.update({"ru": r.get("ru") or c["en"], "score": r.get("score", 0), "rating": (r.get("rating") or "none").lower(),
                  "why": r.get("why", "")})
        if c["rating"] != "none" and (c.get("score") or 0) >= 6:
            out.append(c)
    out.sort(key=lambda x: (-(x.get("score") or 0), RATING_ORDER.get(x["rating"], 3)))
    return out[:top]


def _flat(v):
    """Любое значение из JSON модели → строка (Spark любит вернуть объект там, где ждали строку)."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "; ".join(_flat(x) for x in v if _flat(x))
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_flat(x)}" for k, x in v.items() if _flat(x))
    return str(v)


def _norm_summary(s):
    s = s if isinstance(s, dict) else {}
    out = {k: _flat(s.get(k)) for k in ("tldr", "design", "n", "meaning", "evidence", "caveat")}
    f = s.get("findings")
    if isinstance(f, str):
        f = [f]
    out["findings"] = [_flat(x) for x in (f or []) if _flat(x)]
    return out


def summarize(art):
    """Выжимка по аннотации или полному тексту (OA). Только заголовок — честно без LLM."""
    text, src = "", ""
    if art.get("pmcid"):
        try:
            ft = sci._pmc_xml_text(art["pmcid"])
            if ft and len(ft) > 1500:
                text, src = ft[:45000], "полный текст (PMC)"
        except Exception:
            pass
    if not text and art.get("abstract"):
        text, src = art["abstract"], "аннотация"
    if not text and (art.get("desc") or "").strip() and not art["desc"].lower().startswith("в письме только заголовок"):
        text, src = art["desc"], "описание из письма"
    art["summary_src"] = src
    if not text:
        art["summary"] = {"tldr": "В письме только заголовок, аннотация в базах не найдена — содержание статьи неизвестно.",
                          "design": "", "n": "", "findings": [], "meaning": art.get("why", ""), "evidence": "",
                          "caveat": "Оценить можно только по названию."}
        return
    prompt = (f"Врач-читатель: {READER}. Ниже {src} статьи «{art.get('en')}» ({art.get('journal')}, {art.get('year')}). "
              "Сделай выжимку СТРОГО по тексту, ничего не додумывая; чего нет — пиши «не указано». "
              "Верни JSON-объект:\n"
              '{"tldr":"2-3 предложения: что сделали и что нашли, с главными цифрами",'
              '"design":"дизайн исследования одной строкой (RCT/когорта/ретроспектива/обзор/мета-анализ/кейс…)",'
              '"n":"выборка: кто и сколько",'
              '"findings":["3-6 ключевых результатов, каждый с цифрами/эффектом, если есть"],'
              '"meaning":"что это значит для практики этого врача, 1-2 предложения",'
              '"evidence":"уровень доказательности и главное ограничение одной строкой",'
              '"caveat":"на что не стоит опираться / оговорка"}\n\n' + text)
    try:
        s = ask_json(prompt, kind="object", max_tokens=6000)
        if not isinstance(s, dict):
            raise ValueError("не объект")
        art["summary"] = _norm_summary(s)
    except Exception as e:
        log(f"выжимка «{art.get('en', '')[:40]}»: {e}")
        art["summary"] = {"tldr": (art.get("abstract") or art.get("desc") or "")[:600], "design": "", "n": "",
                          "findings": [], "meaning": art.get("why", ""), "evidence": "", "caveat": "выжимка не собралась — выше аннотация как есть"}


def links_of(a):
    L = {}
    if a.get("doi"):
        L["DOI"] = "https://doi.org/" + a["doi"]
    if a.get("pmid"):
        L["PubMed"] = f"https://pubmed.ncbi.nlm.nih.gov/{a['pmid']}/"
    if a.get("pmcid"):
        L["Полный текст (PMC)"] = f"https://europepmc.org/article/PMC/{a['pmcid']}"
    if not L and a.get("en"):
        L["Поиск в Europe PMC"] = "https://europepmc.org/search?query=" + requests.utils.quote(a["en"][:120])
    return L


# ---------- Word ----------
def build_docx(brief, path):
    """Word в палитре docsemenov.ru: кремовый фон карточек, коралловый акцент, бирюзовый
    для «что это значит». Слева поле с идентификатором, значимостью и уровнем доказательности,
    первой строкой — библиографическая ссылка."""
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.shared import Cm, Pt, RGBColor

    PAPER, INK, INK2, MUTED = "FFF9F2", "2B2330", "4A4051", "7A6E80"
    CORAL, TEAL, LINE, LINE2 = "E85D3C", "0B8570", "E4D3C4", "F0E2D6"
    PEACH, MINT, SKY, LEMON = "FFE9DC", "DDF4EC", "E3EEFF", "FFF3D6"
    SERIF, SANS, MONO = "Georgia", "Calibri", "Consolas"
    RATING_FILL = {"fire": PEACH, "star": LEMON, "pin": SKY, "none": LINE2}
    RATING_TXT = {"fire": CORAL, "star": "A0660A", "pin": "2B51C4", "none": MUTED}

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(1.6)
    sec.top_margin, sec.bottom_margin = Cm(1.5), Cm(1.4)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = SERIF, Pt(10)
    normal.font.color.rgb = RGBColor.from_string(INK)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), SERIF)
    normal.paragraph_format.space_after = Pt(2)

    def xml(tag, **kw):
        el = OxmlElement(tag)
        for k, v in kw.items():
            el.set(qn(f"w:{k}"), str(v))
        return el

    def shade(cell, fill):
        cell._tc.get_or_add_tcPr().append(xml("w:shd", val="clear", color="auto", fill=fill))

    def no_borders(table):
        b = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            b.append(xml(f"w:{edge}", val="none", sz=0, color="auto"))
        table._tbl.tblPr.append(b)

    def cell_pad(table, w=120):
        mar = OxmlElement("w:tblCellMar")
        for side in ("top", "left", "bottom", "right"):
            mar.append(xml(f"w:{side}", w=(w if side in ("left", "right") else w // 2), type="dxa"))
        table._tbl.tblPr.append(mar)

    def rule(par, color=LINE, sz=6, edge="top", space=8):
        pPr = par._p.get_or_add_pPr()
        b = pPr.find(qn("w:pBdr")) or OxmlElement("w:pBdr")
        b.append(xml(f"w:{edge}", val="single", sz=sz, space=space, color=color))
        pPr.append(b)

    def run(par, text, font=SERIF, size=10, color=INK, bold=False, italic=False, caps=False, space=0):
        r = par.add_run(text.upper() if caps else text)
        r.font.name, r.font.size, r.font.bold, r.font.italic = font, Pt(size), bold, italic
        r.font.color.rgb = RGBColor.from_string(color)
        r._r.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), font)
        if space:
            r._r.get_or_add_rPr().append(xml("w:spacing", val=space))
        return r

    def badge(par, text, fill, color=INK, size=8, font=SANS):
        r = run(par, f" {text} ", font=font, size=size, color=color, bold=True)
        r._r.get_or_add_rPr().append(xml("w:shd", val="clear", color="auto", fill=fill))
        par.add_run(" ")

    def link(par, url, text, color=CORAL, size=9):
        rid = par.part.relate_to(url, RT.HYPERLINK, is_external=True)
        h = xml("w:hyperlink", **{})
        h.set(qn("r:id"), rid)
        r = OxmlElement("w:r"); rPr = OxmlElement("w:rPr")
        rPr.append(xml("w:rFonts", ascii=SANS, hAnsi=SANS))
        rPr.append(xml("w:color", val=color)); rPr.append(xml("w:u", val="single"))
        rPr.append(xml("w:sz", val=int(size * 2)))
        r.append(rPr)
        t = OxmlElement("w:t"); t.text = text; t.set(qn("xml:space"), "preserve"); r.append(t)
        h.append(r); par._p.append(h)

    def labeled(cell, label, text, color=MUTED, val_color=INK):
        text = _flat(text)
        if not text.strip():
            return
        p = cell.add_paragraph(); p.paragraph_format.space_before = Pt(5)
        run(p, label + "  ", font=SANS, size=7.5, color=color, bold=True, caps=True, space=12)
        run(p, text.strip(), size=10, color=val_color)

    def rail_cell(c, a, s):
        p = c.paragraphs[0]
        badge(p, a["id"], PEACH, INK, 8, MONO)
        rt = a.get("rating", "none")
        p2 = c.add_paragraph(); p2.paragraph_format.space_before = Pt(3)
        badge(p2, RATING_WORD.get(rt, ""), RATING_FILL[rt], RATING_TXT[rt], 7.5)
        tier = web.tier_of(s) if s else ""
        if tier:
            p3 = c.add_paragraph(); p3.paragraph_format.space_before = Pt(5)
            run(p3, "уровень", font=SANS, size=6.5, color=MUTED, caps=True, space=14)
            p4 = c.add_paragraph(); p4.paragraph_format.space_before = Pt(1)
            for t in ("I", "II", "III", "IV"):
                on = t == tier
                badge(p4, t, TEAL if on else LINE2, "FFFFFF" if on else MUTED, 7, MONO)

    def card(a, full=True):
        s = _norm_summary(a.get("summary") or {})
        t = doc.add_table(rows=1, cols=2); t.alignment = WD_TABLE_ALIGNMENT.CENTER
        no_borders(t); cell_pad(t, 0)
        t.columns[0].width, t.columns[1].width = Cm(3.1), Cm(14.6)
        rail, c = t.rows[0].cells
        rail.width, c.width = Cm(3.1), Cm(14.6)
        rail_cell(rail, a, s)
        p = c.paragraphs[0]
        run(p, _flat(a.get("ru")) or _flat(a.get("en")) or "?", size=12.5, bold=True, color=INK)
        if a.get("en") and (a.get("ru") or "").strip().lower() != a["en"].strip().lower():
            pe = c.add_paragraph(); run(pe, a["en"], size=8.5, italic=True, color=MUTED)
        if a.get("cite"):
            pc = c.add_paragraph()
            pc.paragraph_format.space_before = Pt(6); pc.paragraph_format.left_indent = Cm(0.35)
            rule(pc, CORAL, 12, "left", 6)
            run(pc, a["cite"], font=MONO, size=7.5, color=INK2)
        meta = " · ".join(x for x in [a.get("journal_abbr") or a.get("journal"),
                                      str(a.get("year") or ""), a.get("pubtype")] if x)
        pm = c.add_paragraph(); pm.paragraph_format.space_before = Pt(5)
        if meta:
            run(pm, meta + "   ", font=SANS, size=8, color=MUTED)
        src = a.get("summary_src") or ""
        if src:
            badge(pm, f"по {src}", MINT if "полный" in src else LEMON,
                  TEAL if "полный" in src else "A0660A", 7)
        elif a.get("oa"):
            badge(pm, "открытый доступ", MINT, TEAL, 7)
        labeled(c, "Коротко", s.get("tldr", ""))
        if full:
            labeled(c, "Дизайн", " — ".join(x for x in [s.get("design", ""),
                    re.sub(r"^who:\s*", "", str(s.get("n") or ""))] if x))
            if s.get("findings"):
                pf = c.add_paragraph(); pf.paragraph_format.space_before = Pt(5)
                run(pf, "Что нашли", font=SANS, size=7.5, color=MUTED, bold=True, caps=True, space=12)
                for f in s["findings"][:8]:
                    pi = c.add_paragraph(); pi.paragraph_format.left_indent = Cm(0.45)
                    pi.paragraph_format.space_after = Pt(1)
                    run(pi, "• " + str(f).strip(), size=9.5)
            labeled(c, "Что это значит", s.get("meaning", ""), MUTED, TEAL)
            labeled(c, "Доказательность", s.get("evidence", ""))
            labeled(c, "Оговорка", s.get("caveat", ""), CORAL, INK2)
        elif s.get("meaning"):
            labeled(c, "Что это значит", s.get("meaning", ""), MUTED, TEAL)
        if a.get("why"):
            labeled(c, "Зачем вам", _flat(a["why"]), MUTED, TEAL)
        pl = c.add_paragraph(); pl.paragraph_format.space_before = Pt(6)
        for i, (name, url) in enumerate(links_of(a).items()):
            if i:
                run(pl, "   ", font=SANS, size=9, color=MUTED)
            link(pl, url, name)
        if a.get("source"):
            run(pl, f"    {a['source']}", font=SANS, size=7.5, color=MUTED)
        sp = doc.add_paragraph(); sp.paragraph_format.space_after = Pt(7)
        rule(sp, LINE2, 4, "bottom", 6)

    def heading(eyebrow, title, note):
        pe = doc.add_paragraph()
        pe.paragraph_format.space_before = Pt(16); pe.paragraph_format.space_after = Pt(2)
        rule(pe, INK, 8, "top", 10)
        run(pe, eyebrow, font=SANS, size=7, color=CORAL, bold=True, caps=True, space=20)
        pt = doc.add_paragraph(); pt.paragraph_format.space_after = Pt(2)
        run(pt, title, size=16, bold=True, color=INK)
        if note:
            pn = doc.add_paragraph(); pn.paragraph_format.space_after = Pt(8)
            run(pn, note, font=SANS, size=8.5, color=MUTED)

    # ---- колонтитулы
    hp = sec.header.paragraphs[0]
    run(hp, f"Научный бриф · {brief['date_ru']} · детская травматология и ортопедия",
        font=SANS, size=8, color=MUTED)
    fp = sec.footer.paragraphs[0]; fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run(fp, "Стр. ", font=SANS, size=8, color=MUTED)
    fld = OxmlElement("w:fldSimple"); fld.set(qn("w:instr"), "PAGE")
    fp._p.append(fld)

    # ---- шапка
    mast = doc.add_table(rows=1, cols=1); no_borders(mast); cell_pad(mast, 170)
    mc = mast.rows[0].cells[0]; shade(mc, PAPER)
    p = mc.paragraphs[0]
    run(p, "Детская травматология и ортопедия · ежедневный обзор",
        font=SANS, size=7.5, color=CORAL, bold=True, caps=True, space=20)
    p1 = mc.add_paragraph(); p1.paragraph_format.space_before = Pt(6)
    run(p1, "Научный бриф", size=27, bold=True, color=INK)
    p2 = mc.add_paragraph(); run(p2, brief["date_ru"], font=SANS, size=11, color=INK2)
    p3 = mc.add_paragraph(); p3.paragraph_format.space_before = Pt(6)
    run(p3, "Что вышло за сутки: письма-алерты из подписок и свежие публикации в Europe PMC. "
            "Каждая статья — с библиографической ссылкой, выжимкой по первоисточнику, "
            "уровнем доказательности и оговорками.", size=10, color=INK2)
    st = brief["stats"]
    p4 = mc.add_paragraph(); p4.paragraph_format.space_before = Pt(8)
    for num, lab in ((st["mails"], "писем-алертов"), (st["articles"], "статей в письмах"),
                     (st["relevant"], "релевантных"), (st["fresh"], f"свежих за {brief['fresh_days']} дн.")):
        run(p4, str(num), font=SANS, size=11, bold=True, color=INK)
        run(p4, f" {lab}      ", font=SANS, size=8.5, color=MUTED)
    p5 = mc.add_paragraph(); p5.paragraph_format.space_before = Pt(7)
    p5.paragraph_format.left_indent = Cm(0.3); rule(p5, LINE, 10, "left", 6)
    run(p5, "Выжимки составлены по аннотациям и открытым полным текстам; цифры перенесены "
            "из первоисточника без пересчёта. Проверка расхождений — по ссылке DOI.",
        font=SANS, size=8, color=MUTED)

    top = brief["top"]
    top_ids = {a["id"] for a in top}
    if top:
        heading("Отобрано редактором", "Главное за сутки",
                "Три работы, которые ближе всего к практике детского травматолога-ортопеда.")
        for a in top:
            card(a)
    rel = [a for a in brief["mail_articles"] if a["rating"] != "none" and a["id"] not in top_ids]
    if rel:
        heading("Из ваших подписок", "Ещё релевантное",
                "Статьи из писем-алертов, прошедшие отбор по теме.")
        for a in rel:
            card(a)
    fresh = [a for a in brief["fresh"] if a["id"] not in top_ids]
    if fresh:
        heading(f"Europe PMC · {brief['fresh_days']} дней", "Свежее в базах",
                "Найдено поиском по профилю, вне писем. Короткий формат: суть и значение.")
        for a in fresh:
            card(a, full=False)
    rest = [a for a in brief["mail_articles"] if a["rating"] == "none"]
    if rest:
        heading("Не по профилю", "Остальное из писем",
                f"{len(rest)} статей из тех же выпусков — вне детской ортопедии и травмы.")
        t = doc.add_table(rows=1, cols=2); no_borders(t); cell_pad(t, 80)
        h0, h1 = t.rows[0].cells
        run(h0.paragraphs[0], "Статья", font=SANS, size=7.5, color=MUTED, bold=True, caps=True, space=12)
        run(h1.paragraphs[0], "Журнал", font=SANS, size=7.5, color=MUTED, bold=True, caps=True, space=12)
        for a in rest:
            row = t.add_row().cells
            run(row[0].paragraphs[0], a.get("ru") or a.get("en") or "", size=9)
            run(row[1].paragraphs[0], a.get("journal_abbr") or a.get("journal") or "",
                font=SANS, size=8, color=MUTED)
    if brief["stats"]["mails"] == 0:
        p = doc.add_paragraph()
        run(p, "Научных писем за сутки в Gmail не было — бриф собран только из свежего в базах.",
            italic=True, color=MUTED)
    heading("Справка", "Как пользоваться", "")
    for line in ("У каждой статьи есть идентификатор вида S-MMDD-NN — по нему можно запросить полный "
                 "разбор работы или уточнение по выборке и ограничениям.",
                 "Уровень доказательности слева проставлен по дизайну исследования; где авторы указали "
                 "его сами, берётся авторский.",
                 "Ссылки DOI и PubMed кликабельны; ссылку можно переслать — по ней статья опознаётся.",
                 "Платная статья — пришлите PDF, разбор будет сделан по нему."):
        p = doc.add_paragraph(); p.paragraph_format.left_indent = Cm(0.45)
        p.paragraph_format.space_after = Pt(2)
        run(p, "• " + line, size=9.5, color=INK2)
    ph = doc.add_paragraph(); ph.paragraph_format.space_before = Pt(10)
    run(ph, "Как цитировать этот обзор", font=SANS, size=7.5, color=MUTED, bold=True, caps=True, space=12)
    pc = doc.add_paragraph(); pc.paragraph_format.left_indent = Cm(0.3)
    rule(pc, LINE, 10, "left", 6)
    run(pc, f"Научный бриф по детской травматологии и ортопедии за {brief['date_ru'].split(',')[0]} "
            f"[Электронный ресурс]. Дата обращения: {brief['date_short']}.{brief['date'][:4]}.",
        font=MONO, size=7.5, color=INK2)
    doc.save(path)


def build_html(brief, path):
    """Та же вёрстка для блога: одна страница, палитра сайта, тёмная тема по системе."""
    open(path, "w", encoding="utf-8").write(
        "<!doctype html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        + web.render(brief) + "\n</html>")


CHROME = "/opt/chrome-full/chrome-linux64/chrome"


def build_pdf(html_path, pdf_path):
    """PDF печатается из ТОЙ ЖЕ страницы — Word и PDF не разъезжаются по дизайну.
    Возвращает путь или None: не собрался PDF — бриф уйдёт вордовским файлом."""
    import urllib.parse
    if not os.path.exists(CHROME):
        log("PDF: браузер не найден, отправлю Word")
        return None
    try:
        r = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
             "--hide-scrollbars", "--disable-dev-shm-usage",
             "--virtual-time-budget=25000",          # ждём веб-шрифты, иначе текст поедет
             "--run-all-compositor-stages-before-draw",
             "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
             "file://" + urllib.parse.quote(html_path)],
            capture_output=True, text=True, timeout=300)
    except Exception as e:
        log(f"PDF: {type(e).__name__} — отправлю Word")
        return None
    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 20000:
        log(f"PDF не собрался ({r.stderr[-160:] if r.stderr else 'пусто'}) — отправлю Word")
        return None
    return pdf_path


# ---------- сводка для утреннего брифа и Telegram ----------
def write_txt(brief, docx_path):
    st = brief["stats"]
    lines = [f"НАУКА ЗА {brief['hours']} Ч — писем: {st['mails']} · статей в письмах: {st['articles']} · релевантных: {st['relevant']} · свежих в базах: {st['fresh']}",
             f"ПОДРОБНЫЙ БРИФ (Word) отправлен доктору отдельно в {brief['sent_at'] or '—'}: {docx_path}",
             "=" * 62, f"## ТОП-{len(brief['top'])}"]
    for a in brief["top"]:
        s = a.get("summary") or {}
        lines += [f"{a['id']} {RATING_ICON.get(a['rating'], '')} {a.get('ru')}",
                  f"   {a.get('journal', '')}, {a.get('year', '')} · {(s.get('tldr') or '')[:220]}",
                  f"   🔗 {next(iter(links_of(a).values()), '')}"]
    lines += ["", "В самом брифе — только эти строки; файл уже у доктора, слать повторно не нужно."]
    os.makedirs(os.path.dirname(TXT_FOR_BRIEF), exist_ok=True)
    open(TXT_FOR_BRIEF, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def tg_text(brief):
    st = brief["stats"]
    out = [f"🔬 Научный бриф {brief['date_short']} ({brief['weekday']})",
           f"писем {st['mails']} · статей {st['articles']} · релевантных {st['relevant']} · свежих в базах {st['fresh']}", ""]
    if brief["top"]:
        out.append("Главное:")
        for a in brief["top"]:
            s = a.get("summary") or {}
            out.append(f"{RATING_ICON.get(a['rating'], '')} {a['id']} — {a.get('ru')}")
            out.append(f"   {a.get('journal', '')}, {a.get('year', '')}. {(s.get('tldr') or '')[:260]}")
            u = next(iter(links_of(a).values()), "")
            if u:
                out.append(f"   {u}")
    out += ["", "📎 Полный бриф с выжимками по каждой статье — файлом ниже.",
            "Подробнее о статье: напиши «разбери S-…» или пришли ссылку."]
    return "\n".join(out)


def send(text, docx_path, caption):
    tf = "/tmp/sci_brief_msg.txt"
    open(tf, "w", encoding="utf-8").write(text)
    r1 = subprocess.run([PY, TOME, "msgfile", tf], capture_output=True, text=True, timeout=120)
    r2 = subprocess.run([PY, TOME, "file", docx_path, "--caption", caption], capture_output=True, text=True, timeout=300)
    ok = r1.returncode == 0 and r2.returncode == 0
    log(f"отправка: текст {'ok' if r1.returncode == 0 else r1.stderr[-120:]}, файл {'ok' if r2.returncode == 0 else r2.stderr[-120:]}")
    return ok


# ---------- команды ----------
def cmd_run(a):
    today = dt.datetime.now(MSK)
    date_iso = today.strftime("%Y-%m-%d")
    prefix = today.strftime("S-%m%d-")
    log("письма из Gmail…")
    mails = sci._gmail_alert_bodies(a.hours) if not a.no_mail else []
    mail_articles = []
    for m in mails:
        got = extract_from_mail(m)
        log(f"  «{m['subject'][:50]}»: {len(got)} статей")
        mail_articles += got
    uniq, seen_t = [], []
    for x in mail_articles:                       # одна статья из двух писем — один раз
        if any(sci._same_article(x.get("en", ""), s) for s in seen_t):
            continue
        seen_t.append(x.get("en", "")); uniq.append(x)
    mail_articles = uniq
    mail_articles.sort(key=lambda x: RATING_ORDER.get(x["rating"], 3))
    for art in mail_articles:
        if art["rating"] != "none":
            enrich_epmc(art)
    fresh = []
    if not a.no_fresh:
        log("свежее в Europe PMC…")
        fresh = fresh_from_epmc(a.fresh_days, a.fresh_top)
        seen = {x.get("doi") for x in mail_articles if x.get("doi")}
        fresh = [f for f in fresh if not (f.get("doi") and f["doi"] in seen)
                 and not any(sci._same_article(f["en"], x.get("en", "")) for x in mail_articles)]
        log(f"  свежих релевантных: {len(fresh)}")
    n = 0
    for art in [x for x in mail_articles if x["rating"] != "none"] + fresh + [x for x in mail_articles if x["rating"] == "none"]:
        n += 1
        art["id"] = f"{prefix}{n:02d}"
    log("выжимки…")
    for art in [x for x in mail_articles if x["rating"] != "none"] + fresh:
        summarize(art)
        log(f"  {art['id']} {art.get('summary_src') or 'только заголовок'}")
    pool = [x for x in mail_articles if x["rating"] != "none"] + fresh
    pool.sort(key=lambda x: (RATING_ORDER.get(x["rating"], 3), -(x.get("score") or 0),
                             0 if (x.get("summary_src") or "").startswith("полный") else 1))
    top = pool[:3]
    brief = {"date": date_iso, "date_ru": f"{today.day} {MONTHS[today.month - 1]} {today.year}, {WEEKDAYS[today.weekday()]}",
             "date_short": today.strftime("%d.%m"), "weekday": WEEKDAYS[today.weekday()][:2], "hours": a.hours,
             "fresh_days": a.fresh_days, "mail_articles": mail_articles, "fresh": fresh, "top": top,
             "stats": {"mails": len(mails), "articles": len(mail_articles),
                       "relevant": sum(1 for x in mail_articles if x["rating"] != "none"), "fresh": len(fresh)},
             "sent_at": ""}
    log("библиография…")
    all_arts = mail_articles + fresh + brief["top"]
    biblio.enrich([x for x in all_arts if x.get("pmid") or x.get("doi")], log)
    # одна статья может лежать двумя объектами (в top — копия после архива), а Europe PMC
    # отвечает на идентификатор один раз: раздаём дополненные поля всем тёзкам по ID
    donor = {a["id"]: a for a in all_arts if a.get("id") and a.get("journal_abbr")}
    for art in all_arts:
        src = donor.get(art.get("id"))
        if src is not None and src is not art:
            for k in ("journal_abbr", "volume", "issue", "pages", "pubdate", "issn", "authors"):
                if src.get(k):
                    art[k] = src[k]
        art["cite"] = biblio.vancouver(art)
    log("вычитка языка…")
    lang.pass_brief(brief, lang.make_llm(
        lambda p: ask_json(p, kind="object", max_tokens=6000, temperature=0.1)), log)
    os.makedirs(ARCHIVE, exist_ok=True); os.makedirs(OUT_DIR, exist_ok=True)
    docx_path = os.path.join(OUT_DIR, f"Научный_бриф_{date_iso}.docx")
    build_docx(brief, docx_path)
    log(f"Word: {docx_path} ({os.path.getsize(docx_path) // 1024} КБ)")
    html_path = os.path.join(OUT_DIR, f"Научный_бриф_{date_iso}.html")
    build_html(brief, html_path)
    log(f"страница для блога: {html_path} ({os.path.getsize(html_path) // 1024} КБ)")
    pdf_path = build_pdf(html_path, os.path.join(OUT_DIR, f"Научный_бриф_{date_iso}.pdf"))
    if pdf_path:
        log(f"PDF: {pdf_path} ({os.path.getsize(pdf_path) // 1024} КБ)")
    if a.send:
        st = brief["stats"]
        if send(tg_text(brief), pdf_path or docx_path,
                f"Научный бриф {brief['date_short']}: {st['relevant'] + st['fresh']} статей с выжимками"):
            brief["sent_at"] = dt.datetime.now(MSK).strftime("%H:%M")
    json.dump(brief, open(os.path.join(ARCHIVE, f"{date_iso}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(brief, open(os.path.join(ARCHIVE, "latest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_txt(brief, docx_path)
    print(json.dumps({"date": date_iso, **brief["stats"], "top": [x["id"] for x in top], "docx": docx_path, "html": html_path, "pdf": pdf_path,
                      "sent": brief["sent_at"] or None}, ensure_ascii=False))


def _find(aid):
    m = re.match(r"S-(\d{2})(\d{2})-(\d{2})", aid.upper())
    if not m:
        sys.exit("ID вида S-MMDD-NN")
    files = sorted(f for f in os.listdir(ARCHIVE) if f.endswith(".json") and f != "latest.json") if os.path.isdir(ARCHIVE) else []
    for f in reversed(files):
        if f[5:7] == m.group(1) and f[8:10] == m.group(2):
            b = json.load(open(os.path.join(ARCHIVE, f), encoding="utf-8"))
            for a in b["mail_articles"] + b["fresh"]:
                if a.get("id", "").upper() == aid.upper():
                    return a, f
    sys.exit(f"{aid}: в архиве брифов не найден")


def cmd_show(a):
    art, f = _find(a.id)
    print(f"# {art['id']} · бриф {f[:-5]}")
    for k in ("ru", "en", "journal", "year", "authors", "pubtype", "rating", "why", "source", "doi", "pmid", "pmcid", "oa", "summary_src"):
        if art.get(k):
            print(f"{k}: {art[k]}")
    print("links:", json.dumps(links_of(art), ensure_ascii=False))
    print("summary:", json.dumps(art.get("summary"), ensure_ascii=False, indent=1))
    if art.get("abstract"):
        print("abstract:", art["abstract"][:3000])
    if art.get("doi") or art.get("pmid"):
        print(f"\nПолный Word-разбор: {PY} {HERE}/sci.py word --{'doi ' + art['doi'] if art.get('doi') else 'pmid ' + art['pmid']} --send")


def cmd_list(a):
    f = os.path.join(ARCHIVE, (a.date or "latest") + ".json")
    b = json.load(open(f, encoding="utf-8"))
    for x in b["mail_articles"] + b["fresh"]:
        print(f"{x['id']} {RATING_ICON.get(x['rating'], '')} {x.get('ru')} — {x.get('journal')} {x.get('year')} · {next(iter(links_of(x).values()), '')}")


def main():
    ap = argparse.ArgumentParser(description="Научный бриф")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--hours", type=int, default=24); r.add_argument("--fresh-days", type=int, default=7)
    r.add_argument("--fresh-top", type=int, default=10); r.add_argument("--send", action="store_true")
    r.add_argument("--no-fresh", action="store_true"); r.add_argument("--no-mail", action="store_true"); r.set_defaults(func=cmd_run)
    s = sub.add_parser("show"); s.add_argument("id"); s.set_defaults(func=cmd_show)
    l = sub.add_parser("list"); l.add_argument("date", nargs="?"); l.set_defaults(func=cmd_list)
    a = ap.parse_args(); a.func(a)


if __name__ == "__main__":
    main()
