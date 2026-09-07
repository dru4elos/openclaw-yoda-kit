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

HOME = os.path.expanduser("~")
WS = f"{HOME}/.openclaw/workspace"
ARCHIVE = f"{WS}/memory/sci_briefs"
OUT_DIR = f"{WS}/Наука"
TXT_FOR_BRIEF = f"{WS}/memory/sci_alerts_today.txt"
TOME = f"{WS}/skills/tome/tome.py"
PY = sys.executable
MSK = dt.timezone(dt.timedelta(hours=3))
MODELS = ["gemini-3.8-flash", "gpt-5.6-sol-1m", "gemini-3.7-flash-tiered"]
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
    key, url = sci.ENV.get("EXCASH_API_KEY"), sci.ENV.get("EXCASH_API_URL")
    last = ""
    if key and url:
        for model in MODELS:
            for attempt in (1, 2):
                try:
                    txt = sci._llm_once(url, key, model, [{"role": "user", "content": prompt}],
                                        max_tokens, temperature, timeout=300)
                    if txt.strip():
                        return txt
                    last = f"{model}: пустой ответ"
                except Exception as e:
                    last = f"{model}: {type(e).__name__} {str(e)[:80]}"
                time.sleep(3 * attempt)
    dk = sci.ENV.get("DEEPSEEK_API_KEY")
    if dk:
        try:
            txt = sci._llm_once("https://api.deepseek.com/v1", dk, "deepseek-v4-flash",
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
        s.setdefault("findings", [])
        art["summary"] = s
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
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.shared import Cm, Pt, RGBColor

    NAVY, TEAL, GREY, LIGHT, LINE = "1F3A5F", "2A9D8F", "6B7280", "F4F6F9", "D9DEE5"
    RATING_FILL = {"fire": "FDE8E8", "star": "FFF4D6", "pin": "E8F1FB", "none": "EEEEEE"}
    RATING_TXT = {"fire": "B42318", "star": "8A5A00", "pin": "1F4E79", "none": "6B7280"}

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(1.7)
    sec.top_margin, sec.bottom_margin = Cm(1.6), Cm(1.5)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    normal.paragraph_format.space_after = Pt(2)
    for lvl, size in ((1, 16), (2, 12.5)):
        st = doc.styles[f"Heading {lvl}"]
        st.font.name, st.font.size, st.font.bold = "Calibri", Pt(size), True
        st.font.color.rgb = RGBColor.from_string(NAVY)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
        st.paragraph_format.space_before, st.paragraph_format.space_after = Pt(10 if lvl == 1 else 6), Pt(4)

    def shade(cell, fill):
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
        tcPr.append(shd)

    def borders(table, color, sz=4):
        tblPr = table._tbl.tblPr
        b = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            el = OxmlElement(f"w:{edge}"); el.set(qn("w:val"), "single"); el.set(qn("w:sz"), str(sz)); el.set(qn("w:color"), color)
            b.append(el)
        tblPr.append(b)

    def cell_pad(table, w=110):
        tblPr = table._tbl.tblPr
        mar = OxmlElement("w:tblCellMar")
        for side in ("top", "left", "bottom", "right"):
            el = OxmlElement(f"w:{side}"); el.set(qn("w:w"), str(w if side in ("left", "right") else w // 2)); el.set(qn("w:type"), "dxa")
            mar.append(el)
        tblPr.append(mar)

    def badge(par, text, fill, color=NAVY, size=8.5):
        run = par.add_run(f" {text} ")
        run.font.size, run.font.bold = Pt(size), True
        run.font.color.rgb = RGBColor.from_string(color)
        rPr = run._r.get_or_add_rPr()
        shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
        rPr.append(shd)
        par.add_run("  ")

    def link(par, url, text, color="1F4E79"):
        r_id = par.part.relate_to(url, RT.HYPERLINK, is_external=True)
        h = OxmlElement("w:hyperlink"); h.set(qn("r:id"), r_id)
        r = OxmlElement("w:r"); rPr = OxmlElement("w:rPr")
        c = OxmlElement("w:color"); c.set(qn("w:val"), color); rPr.append(c)
        u = OxmlElement("w:u"); u.set(qn("w:val"), "single"); rPr.append(u)
        sz = OxmlElement("w:sz"); sz.set(qn("w:val"), "19"); rPr.append(sz)
        r.append(rPr); t = OxmlElement("w:t"); t.text = text; t.set(qn("xml:space"), "preserve"); r.append(t)
        h.append(r); par._p.append(h)

    def labeled(cell, label, text, color=NAVY):
        if not (text or "").strip():
            return
        p = cell.add_paragraph()
        r = p.add_run(label + " "); r.bold = True; r.font.color.rgb = RGBColor.from_string(color); r.font.size = Pt(10)
        p.add_run(text.strip())

    def card(a, compact=False):
        t = doc.add_table(rows=1, cols=1); t.alignment = WD_TABLE_ALIGNMENT.CENTER
        borders(t, LINE); cell_pad(t)
        c = t.rows[0].cells[0]; shade(c, LIGHT)
        p = c.paragraphs[0]
        badge(p, a["id"], NAVY, "FFFFFF", 9)
        r = p.add_run(a.get("ru") or a.get("en") or "?"); r.bold = True; r.font.size = Pt(12); r.font.color.rgb = RGBColor.from_string("111827")
        if a.get("en") and a.get("ru") and a["en"].strip().lower() != a["ru"].strip().lower():
            pe = c.add_paragraph(); re_ = pe.add_run(a["en"]); re_.italic = True; re_.font.size = Pt(9); re_.font.color.rgb = RGBColor.from_string(GREY)
        pb = c.add_paragraph()
        rt = a.get("rating", "none")
        badge(pb, f"{RATING_ICON.get(rt, '')} {RATING_WORD.get(rt, '')}", RATING_FILL[rt], RATING_TXT[rt])
        meta = " · ".join(x for x in [a.get("journal"), str(a.get("year") or ""), a.get("pubtype")] if x)
        if meta:
            badge(pb, meta, "E5E7EB", "374151")
        src = a.get("summary_src") or ""
        if src:
            badge(pb, src, "DCFCE7" if "полный" in src else "FEF3C7", "14532D" if "полный" in src else "78350F")
        elif a.get("oa"):
            badge(pb, "open access", "DCFCE7", "14532D")
        s = a.get("summary") or {}
        labeled(c, "Коротко:", s.get("tldr", ""))
        if not compact:
            labeled(c, "Дизайн:", " — ".join(x for x in [s.get("design", ""), s.get("n", "")] if x))
            if s.get("findings"):
                pf = c.add_paragraph(); rf = pf.add_run("Что нашли:"); rf.bold = True; rf.font.color.rgb = RGBColor.from_string(NAVY); rf.font.size = Pt(10)
                for f in s["findings"][:6]:
                    pi = c.add_paragraph(); pi.paragraph_format.left_indent = Cm(0.4); pi.add_run("• " + str(f).strip())
            labeled(c, "Что это значит:", s.get("meaning", ""), TEAL)
            labeled(c, "Доказательность:", s.get("evidence", ""))
            labeled(c, "Оговорка:", s.get("caveat", ""), "B42318")
        elif s.get("meaning"):
            labeled(c, "Что это значит:", s.get("meaning", ""), TEAL)
        if a.get("why"):
            labeled(c, "Зачем вам:", a["why"], TEAL)
        pl = c.add_paragraph()
        L = links_of(a)
        for i, (name, url) in enumerate(L.items()):
            if i:
                pl.add_run("  ·  ").font.color.rgb = RGBColor.from_string(GREY)
            link(pl, url, name + " ↗")
        if a.get("source"):
            rs = pl.add_run(f"   ({a['source']})"); rs.font.size = Pt(8.5); rs.font.color.rgb = RGBColor.from_string(GREY)
        pa = c.add_paragraph()
        ra = pa.add_run(f"Спросить Йоду: «разбери {a['id']}» — полный Word-разбор; «что там с выборкой в {a['id']}» — уточнение по карточке.")
        ra.italic = True; ra.font.size = Pt(8.5); ra.font.color.rgb = RGBColor.from_string(GREY)
        doc.add_paragraph().paragraph_format.space_after = Pt(4)

    # колонтитулы
    hp = sec.header.paragraphs[0]; hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    hr = hp.add_run(f"Научный бриф · {brief['date_ru']} · Йода для доктора Семенова"); hr.font.size = Pt(8.5); hr.font.color.rgb = RGBColor.from_string(GREY)
    fp = sec.footer.paragraphs[0]; fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = fp.add_run("Стр. "); fr.font.size = Pt(8.5); fr.font.color.rgb = RGBColor.from_string(GREY)
    fld = OxmlElement("w:fldSimple"); fld.set(qn("w:instr"), "PAGE"); r_ = OxmlElement("w:r"); t_ = OxmlElement("w:t"); t_.text = "1"; r_.append(t_); fld.append(r_); fp._p.append(fld)

    # обложка
    cover = doc.add_table(rows=1, cols=1); borders(cover, NAVY, 2); cell_pad(cover, 220)
    cc = cover.rows[0].cells[0]; shade(cc, NAVY)
    p = cc.paragraphs[0]; r = p.add_run("НАУЧНЫЙ БРИФ"); r.bold = True; r.font.size = Pt(26); r.font.color.rgb = RGBColor.from_string("FFFFFF")
    p2 = cc.add_paragraph(); r2 = p2.add_run(brief["date_ru"]); r2.font.size = Pt(13); r2.font.color.rgb = RGBColor.from_string("CFE3F3")
    p3 = cc.add_paragraph(); r3 = p3.add_run("Что вышло по детской травматологии и ортопедии: письма-алерты из ваших подписок и свежие публикации в Europe PMC, каждая — с выжимкой, ссылками и ID для вопросов."); r3.font.size = Pt(10); r3.font.color.rgb = RGBColor.from_string("E5EEF7")
    st = brief["stats"]
    stats = doc.add_table(rows=2, cols=4); stats.alignment = WD_TABLE_ALIGNMENT.CENTER; borders(stats, LINE); cell_pad(stats, 90)
    for i, (num, lab) in enumerate([(st["mails"], "писем-алертов"), (st["articles"], "статей в письмах"),
                                     (st["relevant"], "релевантных"), (st["fresh"], "свежих в базах")]):
        c0, c1 = stats.rows[0].cells[i], stats.rows[1].cells[i]; shade(c0, LIGHT); shade(c1, LIGHT)
        c0.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER; rn = c0.paragraphs[0].add_run(str(num)); rn.bold = True; rn.font.size = Pt(20); rn.font.color.rgb = RGBColor.from_string(TEAL if i in (2, 3) else NAVY)
        c1.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER; rl = c1.paragraphs[0].add_run(lab); rl.font.size = Pt(9); rl.font.color.rgb = RGBColor.from_string(GREY)
    doc.add_paragraph()

    top = brief["top"]
    if top:
        doc.add_heading("Главное за сутки", 1)
        for a in top:
            card(a)
    rel = [a for a in brief["mail_articles"] if a["rating"] != "none" and a not in top]
    if rel:
        doc.add_heading("Ещё релевантное из ваших подписок", 1)
        for a in rel:
            card(a)
    fresh = [a for a in brief["fresh"] if a not in top]
    if fresh:
        doc.add_heading(f"Свежее в базах (Europe PMC, {brief['fresh_days']} дн.)", 1)
        for a in fresh:
            card(a, compact=True)
    rest = [a for a in brief["mail_articles"] if a["rating"] == "none"]
    if rest:
        doc.add_heading("Остальное из писем — не по вашей теме", 1)
        t = doc.add_table(rows=1, cols=2); borders(t, LINE); cell_pad(t, 80)
        h0, h1 = t.rows[0].cells; shade(h0, "E5E7EB"); shade(h1, "E5E7EB")
        h0.paragraphs[0].add_run("Статья").bold = True; h1.paragraphs[0].add_run("Журнал").bold = True
        for a in rest:
            row = t.add_row().cells
            row[0].paragraphs[0].add_run(a.get("ru") or a.get("en") or "").font.size = Pt(9.5)
            row[1].paragraphs[0].add_run(" ".join(x for x in [a.get("journal"), str(a.get("year") or "")] if x)).font.size = Pt(9.5)
    if brief["stats"]["mails"] == 0:
        p = doc.add_paragraph(); r = p.add_run("Научных писем за сутки в Gmail не было — бриф собран только из свежего в базах."); r.italic = True
    doc.add_heading("Как пользоваться", 1)
    for line in ("Каждая статья имеет ID вида S-MMDD-NN. Напишите Йоде «разбери S-0907-03» — получите полный Word-разбор статьи (по открытому полному тексту, если он есть, иначе по аннотации).",
                 "Вопрос по конкретной статье — «что там с выборкой в S-0907-03», «какие ограничения у S-0907-01» — Йода ответит по карточке и первоисточнику.",
                 "Ссылки DOI/PubMed кликабельны; ссылку можно переслать Йоде — он поймёт, о какой статье речь.",
                 "Платная статья — пришлите PDF в чат, Йода сделает разбор по нему (sci.py word --pdf)."):
        p = doc.add_paragraph(style="List Bullet"); p.add_run(line).font.size = Pt(9.5)
    doc.save(path)


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
    os.makedirs(ARCHIVE, exist_ok=True); os.makedirs(OUT_DIR, exist_ok=True)
    docx_path = os.path.join(OUT_DIR, f"Научный_бриф_{date_iso}.docx")
    build_docx(brief, docx_path)
    log(f"Word: {docx_path} ({os.path.getsize(docx_path) // 1024} КБ)")
    if a.send:
        st = brief["stats"]
        if send(tg_text(brief), docx_path, f"Научный бриф {brief['date_short']}: {st['relevant'] + st['fresh']} статей с выжимками"):
            brief["sent_at"] = dt.datetime.now(MSK).strftime("%H:%M")
    json.dump(brief, open(os.path.join(ARCHIVE, f"{date_iso}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(brief, open(os.path.join(ARCHIVE, "latest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_txt(brief, docx_path)
    print(json.dumps({"date": date_iso, **brief["stats"], "top": [x["id"] for x in top], "docx": docx_path,
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
