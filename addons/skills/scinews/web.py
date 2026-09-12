# -*- coding: utf-8 -*-
"""Вёрстка научного брифа: одна колонка для чтения, слева поле с идентификатором,
уровнем доказательности и меткой значимости. Из этого же дерева собирается Word и пост в блог."""
import html, json, re, sys

RATING = {"fire": ("меняет практику", "fire"), "star": ("важно", "star"),
          "pin": ("любопытно", "pin"), "none": ("мимо", "none")}
TIERS = ["I", "II", "III", "IV"]
TIER_NAME = {"I": "РКИ, метаанализ", "II": "проспективная когорта",
             "III": "ретроспективная когорта, поперечное", "IV": "серия случаев, качественное"}

def tier_of(s):
    """Уровень доказательности. Сначала — тот, что назвали авторы. Иначе классифицируем ТОЛЬКО
    по полю «дизайн»: в поле «доказательность» модель обсуждает, чем работа НЕ является
    («ниже, чем у рандомизированных», «нет метаанализа»), и по ключевым словам оттуда
    исследование легко записать на уровень выше собственного."""
    design = str(s.get("design") or "").lower()
    both = design + " " + str(s.get("evidence") or "").lower()
    m = re.search(r"уровень\w*(?:\s+доказательности)?(?:\s+в\s+[\w\s]{0,20})?"
                  r"(?:\s+по\s+[\w\s]{0,24}классификации)?\s*:?\s*\b(iv|iii|ii|i)\b", both)
    if m:
        return m.group(1).upper()
    if not design or re.match(r"\s*(не\s+указан|нет\s+данных|неизвестн)", design):
        return ""
    neg = re.search(r"без рандомизаци|не рандомизир|нерандомизир|ниже|нет описания|отсутству", design)
    if re.search(r"метаанализ|систематическ\w+ обзор", design) and not neg: return "I"
    if re.search(r"рандомизированн", design) and not neg: return "I"
    if re.search(r"серия случаев|описани\w+ случа|качественн\w+ исследовани", design): return "IV"
    if re.search(r"проспективн", design) and "ретроспективн" not in design: return "II"
    if re.search(r"ретроспективн|поперечн|случай-контроль|опросн|регистр|наблюдательн|когорт", design): return "III"
    return ""


def esc(x): return html.escape(str(x or ""), quote=True)

def links(a):
    out = []
    if a.get("doi"): out.append(("DOI", f"https://doi.org/{a['doi']}"))
    if a.get("pmid"): out.append(("PubMed", f"https://pubmed.ncbi.nlm.nih.gov/{a['pmid']}/"))
    if a.get("pmcid"): out.append(("Полный текст", f"https://europepmc.org/article/PMC/{a['pmid']}"))
    return out

def rail(a, s):
    r = a.get("rating", "none"); word, cls = RATING.get(r, RATING["none"])
    tier = tier_of(s) if s else ""
    steps = "".join(
        f'<span class="step{" on" if t == tier else ""}">{t}</span>' for t in TIERS)
    gauge = (f'<div class="gauge" title="Уровень доказательности: {esc(TIER_NAME.get(tier, ""))}">'
             f'<span class="gauge-l">уровень</span><div class="steps">{steps}</div></div>') if tier else ""
    return (f'<aside class="rail"><span class="aid">{esc(a["id"])}</span>'
            f'<span class="tag {cls}">{word}</span>{gauge}</aside>')

def block(label, text, cls=""):
    if not text or not str(text).strip(): return ""
    return (f'<div class="row {cls}"><span class="lab">{esc(label)}</span>'
            f'<div class="val">{esc(text)}</div></div>')

def entry(a, full=True):
    s = a.get("summary") if isinstance(a.get("summary"), dict) else {}
    meta = " · ".join(x for x in [a.get("journal_abbr") or a.get("journal"),
                                  str(a.get("year") or ""), a.get("pubtype")] if x)
    src = a.get("summary_src") or ""
    src_b = (f'<span class="src {"full" if "полный" in src else "abs"}">по {esc(src)}</span>') if src else ""
    oa = '<span class="src oa">открытый доступ</span>' if a.get("oa") else ""
    en = (f'<p class="en">{esc(a.get("en"))}</p>'
          if a.get("en") and a.get("en", "").strip().lower() != (a.get("ru") or "").strip().lower() else "")
    find = ""
    if full and s.get("findings"):
        items = "".join(f"<li>{esc(str(x))}</li>" for x in s["findings"][:8])
        find = f'<div class="row"><span class="lab">Что нашли</span><ul class="val find">{items}</ul></div>'
    body = block("Коротко", s.get("tldr"))
    if full:
        body += block("Дизайн", " — ".join(x for x in [s.get("design"), re.sub(r"^who:\s*", "", str(s.get("n") or ""))] if x))
        body += find
        body += block("Что это значит", s.get("meaning"), "mean")
        body += block("Доказательность", s.get("evidence"))
        body += block("Оговорка", s.get("caveat"), "warn")
    else:
        body += block("Что это значит", s.get("meaning"), "mean")
    body += block("Зачем вам", a.get("why"), "mean")
    ls = "".join(f'<a href="{esc(u)}" target="_blank" rel="noopener">{n}</a>' for n, u in links(a))
    prov = f'<span class="prov">{esc(a.get("source"))}</span>' if a.get("source") else ""
    return f'''<article class="entry{"" if full else " brief"}" id="{esc(a["id"])}">
{rail(a, s)}
<div class="body">
<h3>{esc(a.get("ru") or a.get("en"))}</h3>
{en}
<p class="cite">{esc(a.get("cite"))}</p>
<p class="meta">{esc(meta)} {src_b} {oa}</p>
{body}
<p class="links">{ls}{prov}</p>
</div></article>'''

def section(eyebrow, title, note, items, full=True):
    if not items: return ""
    body = "\n".join(entry(a, full) for a in items)
    return (f'<section><header class="sec"><span class="eyebrow">{esc(eyebrow)}</span>'
            f'<h2>{esc(title)}</h2><p class="note">{esc(note)}</p></header>{body}</section>')

def render(b, author=""):   # имя владельца подставляется из настроек
    top_ids = {a["id"] for a in b["top"]}
    rel = [a for a in b["mail_articles"] if a["rating"] != "none" and a["id"] not in top_ids]
    fresh = [a for a in b["fresh"] if a["id"] not in top_ids]
    rest = [a for a in b["mail_articles"] if a["rating"] == "none"]
    st = b["stats"]
    rows = "".join(f'<tr><td>{esc(a.get("ru") or a.get("en"))}</td>'
                   f'<td class="j">{esc(a.get("journal_abbr") or a.get("journal"))}</td></tr>' for a in rest)
    self_cite = (f'{author} Научный бриф по детской травматологии и ортопедии за {b["date_ru"].split(",")[0]} '
                 f'[Электронный ресурс]. Дата обращения: {b["date_short"]}.{b["date"][:4]}.')
    return f'''<title>Научный бриф по детской ортопедии</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Literata:opsz,wght@7..72,400;7..72,600;7..72,700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{{
  /* палитра взята из style.css сайта владельца: bg, peach, mint, sky, lemon, coral, teal, blue, ink, line */
  --paper:#FFF9F2; --surface:#FFFFFF; --ink:#2B2330; --ink2:#4A4051; --ink3:#7A6E80;
  --rule:#E4D3C4; --rule2:#F0E2D6; --accent:#E85D3C; --accent-soft:#FFE9DC;
  --teal:#0B8570; --teal-soft:#DDF4EC;
  --fire:#E85D3C; --fire-bg:#FFE9DC; --star:#A0660A; --star-bg:#FFF3D6;
  --pin:#2B51C4; --pin-bg:#E3EEFF; --none:#7A6E80; --none-bg:#F0E2D6;
  --serif:"Literata",Georgia,"Times New Roman",serif;
  --sans:"IBM Plex Sans","Helvetica Neue",Arial,sans-serif;
  --mono:"IBM Plex Mono","SFMono-Regular",Consolas,monospace;
}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
  --paper:#1F1922; --surface:#28212C; --ink:#F6EEE6; --ink2:#CDBFC6; --ink3:#9E8F98;
  --rule:#3E3342; --rule2:#2F2733; --accent:#FF9B7F; --accent-soft:#3A2620;
  --teal:#45D2B2; --teal-soft:#14332C;
  --fire:#FF9B7F; --fire-bg:#3A2620; --star:#EDB35A; --star-bg:#382B12;
  --pin:#93AEF7; --pin-bg:#1E2647; --none:#9E8F98; --none-bg:#2F2733;
}}}}
:root[data-theme="dark"]{{
  --paper:#1F1922; --surface:#28212C; --ink:#F6EEE6; --ink2:#CDBFC6; --ink3:#9E8F98;
  --rule:#3E3342; --rule2:#2F2733; --accent:#FF9B7F; --accent-soft:#3A2620;
  --teal:#45D2B2; --teal-soft:#14332C;
  --fire:#FF9B7F; --fire-bg:#3A2620; --star:#EDB35A; --star-bg:#382B12;
  --pin:#93AEF7; --pin-bg:#1E2647; --none:#9E8F98; --none-bg:#2F2733;
}}
*{{box-sizing:border-box}}
body{{background:var(--paper);color:var(--ink);font-family:var(--serif);
  font-size:16px;line-height:1.62;margin:0;padding:0 1.25rem 5rem;
  -webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}}
.wrap{{max-width:53rem;margin:0 auto}}
a{{color:var(--accent);text-underline-offset:2px}}
a:focus-visible,summary:focus-visible{{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}}
h1,h2,h3{{text-wrap:balance;margin:0}}

/* шапка */
.mast{{border-bottom:2px solid var(--ink);padding:3rem 0 1.1rem;margin-bottom:.9rem}}
.kicker{{font-family:var(--sans);font-size:.68rem;font-weight:600;letter-spacing:.16em;
  text-transform:uppercase;color:var(--accent);margin:0 0 .7rem}}
h1{{font-size:clamp(2rem,5.2vw,3.1rem);font-weight:700;letter-spacing:-.02em;line-height:1.06}}
.date{{font-family:var(--sans);font-size:.95rem;color:var(--ink2);margin:.55rem 0 0}}
.lede{{font-size:1.04rem;color:var(--ink2);margin:1rem 0 0;max-width:44rem}}
.prov-bar{{display:flex;flex-wrap:wrap;gap:.4rem 1.6rem;font-family:var(--sans);font-size:.8rem;
  color:var(--ink2);padding:.75rem 0 0;margin:0}}
.prov-bar b{{font-weight:600;color:var(--ink);font-variant-numeric:tabular-nums}}
.howto{{font-family:var(--sans);font-size:.78rem;color:var(--ink3);border-left:2px solid var(--rule);
  padding:.35rem 0 .35rem .85rem;margin:1.15rem 0 0}}
.howto code{{font-family:var(--mono);font-size:.76rem;color:var(--ink2)}}

/* разделы */
section{{margin-top:3.2rem}}
.sec{{border-top:1px solid var(--ink);padding-top:.8rem;margin-bottom:1.6rem}}
.eyebrow{{font-family:var(--sans);font-size:.66rem;font-weight:600;letter-spacing:.15em;
  text-transform:uppercase;color:var(--accent)}}
.sec h2{{font-size:1.6rem;font-weight:600;letter-spacing:-.01em;margin:.3rem 0 0}}
.note{{font-family:var(--sans);font-size:.82rem;color:var(--ink3);margin:.35rem 0 0}}

/* статья */
.entry{{display:grid;grid-template-columns:8.5rem 1fr;gap:0 1.9rem;
  padding:1.7rem 0;border-top:1px solid var(--rule2)}}
.entry:first-of-type{{border-top:none}}
.rail{{display:flex;flex-direction:column;gap:.5rem;align-items:flex-start;
  position:sticky;top:1rem;align-self:start}}
.aid{{font-family:var(--mono);font-size:.76rem;font-weight:500;color:var(--ink);
  background:var(--accent-soft);padding:.16rem .42rem;border-radius:2px;letter-spacing:.02em}}
.tag{{font-family:var(--sans);font-size:.68rem;font-weight:600;letter-spacing:.04em;
  padding:.16rem .45rem;border-radius:2px}}
.tag.fire{{color:var(--fire);background:var(--fire-bg)}}
.tag.star{{color:var(--star);background:var(--star-bg)}}
.tag.pin{{color:var(--pin);background:var(--pin-bg)}}
.tag.none{{color:var(--none);background:var(--none-bg)}}
.gauge{{margin-top:.15rem}}
.gauge-l{{display:block;font-family:var(--sans);font-size:.6rem;letter-spacing:.12em;
  text-transform:uppercase;color:var(--ink3);margin-bottom:.25rem}}
.steps{{display:flex;gap:2px}}
.step{{font-family:var(--mono);font-size:.62rem;color:var(--ink3);background:var(--rule2);
  padding:.08rem .3rem;border-radius:1px}}
.step.on{{color:#FFFFFF;background:var(--teal);font-weight:500}}

.body h3{{font-size:1.2rem;font-weight:600;line-height:1.32;letter-spacing:-.005em}}
.en{{font-size:.86rem;font-style:italic;color:var(--ink3);margin:.3rem 0 0;line-height:1.45}}
.cite{{font-family:var(--mono);font-size:.745rem;line-height:1.55;color:var(--ink2);
  margin:.8rem 0 0;border-left:2px solid var(--accent);
  padding:.2rem 0 .2rem 1.55rem;text-indent:-.8rem;overflow-wrap:anywhere}}
.meta{{font-family:var(--sans);font-size:.75rem;color:var(--ink3);margin:.5rem 0 0;
  display:flex;flex-wrap:wrap;gap:.45rem;align-items:center}}
.src{{font-size:.67rem;font-weight:600;padding:.1rem .38rem;border-radius:2px;letter-spacing:.02em}}
.src.full,.src.oa{{color:var(--teal);background:var(--teal-soft)}}
.src.abs{{color:var(--star);background:var(--star-bg)}}

.row{{display:grid;grid-template-columns:7.4rem 1fr;gap:0 1rem;margin-top:.85rem;align-items:start}}
.lab{{font-family:var(--sans);font-size:.68rem;font-weight:600;letter-spacing:.07em;
  text-transform:uppercase;color:var(--ink3);padding-top:.33rem}}
.val{{margin:0;color:var(--ink)}}
.row.mean .val{{color:var(--teal)}}
.row.warn .lab{{color:var(--fire)}}
.row.warn .val{{color:var(--ink2)}}
ul.find{{margin:0;padding-left:1.05rem}}
ul.find li{{margin-bottom:.28rem}}
.links{{font-family:var(--sans);font-size:.8rem;margin:1rem 0 0;display:flex;flex-wrap:wrap;
  gap:.35rem .95rem;align-items:baseline}}
.links a{{font-weight:500}}
.prov{{color:var(--ink3);font-size:.73rem}}
.entry.brief .body h3{{font-size:1.08rem}}

/* хвост */
table{{width:100%;border-collapse:collapse;font-size:.85rem}}
.scroll{{overflow-x:auto}}
th,td{{text-align:left;padding:.5rem .7rem .5rem 0;border-bottom:1px solid var(--rule2);vertical-align:top}}
th{{font-family:var(--sans);font-size:.68rem;font-weight:600;letter-spacing:.07em;
  text-transform:uppercase;color:var(--ink3)}}
td.j{{font-family:var(--sans);font-size:.78rem;color:var(--ink3);white-space:nowrap}}
.foot{{margin-top:3.4rem;border-top:2px solid var(--ink);padding-top:1.1rem;
  font-family:var(--sans);font-size:.82rem;color:var(--ink2)}}
.foot h2{{font-family:var(--serif);font-size:1.15rem;margin-bottom:.6rem}}
.foot li{{margin-bottom:.4rem}}
.selfcite{{font-family:var(--mono);font-size:.75rem;color:var(--ink2);background:var(--rule2);
  padding:.7rem .85rem;border-radius:3px;margin-top:1rem;line-height:1.5}}
@media (max-width:800px){{
  .entry{{grid-template-columns:1fr;gap:.75rem}}
  .rail{{position:static;flex-direction:row;flex-wrap:wrap;align-items:center;gap:.4rem}}
  .row{{grid-template-columns:1fr;gap:.15rem}}
  .lab{{padding-top:0}}
}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}
/* печать: A4, всегда светлая палитра сайта, карточка не рвётся между страницами */
@page{{size:A4;margin:13mm 12mm}}
@media print{{
  :root{{
    --paper:#FFF9F2; --surface:#FFFFFF; --ink:#2B2330; --ink2:#4A4051; --ink3:#7A6E80;
    --rule:#E4D3C4; --rule2:#F0E2D6; --accent:#E85D3C; --accent-soft:#FFE9DC;
    --teal:#0B8570; --teal-soft:#DDF4EC;
    --fire:#E85D3C; --fire-bg:#FFE9DC; --star:#A0660A; --star-bg:#FFF3D6;
    --pin:#2B51C4; --pin-bg:#E3EEFF; --none:#7A6E80; --none-bg:#F0E2D6;
  }}
  *{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
  body{{padding:0;font-size:9.6pt;background:var(--paper)}}
  .wrap{{max-width:none}}
  .mast{{padding-top:0}}
  h1{{font-size:30pt}}
  /* карточку НЕ запрещаем рвать: она бывает выше страницы, и запрет выбрасывал
     полстраницы пустоты перед каждой. Рвём между блоками, а не внутри них. */
  .entry{{grid-template-columns:7.4rem 1fr;gap:0 1.4rem;orphans:2;widows:2}}
  .row,.cite,.rail,.meta,ul.find li,.links{{break-inside:avoid;page-break-inside:avoid}}
  .body h3{{break-after:avoid;page-break-after:avoid}}
  .rail{{position:static}}
  .sec{{break-after:avoid;page-break-after:avoid}}
  section{{margin-top:1.6rem}}
  .row{{grid-template-columns:6.6rem 1fr}}
  table{{font-size:8.6pt}}
  tr{{break-inside:avoid}}
  a{{text-decoration:none}}
  .foot{{break-before:auto}}
}}
</style>
<div class="wrap">
<header class="mast">
  <p class="kicker">Детская травматология и ортопедия · ежедневный обзор</p>
  <h1>Научный бриф</h1>
  <p class="date">{esc(b["date_ru"])}</p>
  <p class="lede">Что вышло за сутки: письма-алерты из подписок и свежие публикации в Europe PMC. Каждая статья — с библиографической ссылкой, выжимкой по первоисточнику, уровнем доказательности и оговорками.</p>
  <p class="prov-bar"><span><b>{st["mails"]}</b> писем-алертов</span><span><b>{st["articles"]}</b> статей в письмах</span>
    <span><b>{st["relevant"]}</b> релевантных</span><span><b>{st["fresh"]}</b> свежих в базах за {b["fresh_days"]} дн.</span></p>
  <p class="howto">Выжимки составлены по аннотациям и открытым полным текстам; цифры перенесены из первоисточника без пересчёта. Проверка расхождений — по ссылке DOI.</p>
</header>
{section("Отобрано редактором", "Главное за сутки", "Три работы, которые ближе всего к практике детского травматолога-ортопеда.", b["top"])}
{section("Из ваших подписок", "Ещё релевантное", "Статьи из писем-алертов, прошедшие отбор по теме.", rel)}
{section(f"Europe PMC · {b['fresh_days']} дней", "Свежее в базах", "Найдено поиском по профилю, вне писем. Короткий формат: суть и значение.", fresh, full=False)}
<section>
  <header class="sec"><span class="eyebrow">Не по профилю</span><h2>Остальное из писем</h2>
  <p class="note">{len(rest)} статей из тех же выпусков — вне детской ортопедии и травмы. Оставлены для полноты картины по номерам журналов.</p></header>
  <div class="scroll"><table><thead><tr><th>Статья</th><th>Журнал</th></tr></thead><tbody>{rows}</tbody></table></div>
</section>
<footer class="foot">
  <h2>Как пользоваться</h2>
  <ul>
    <li>У каждой статьи есть идентификатор вида <b>S-MMDD-NN</b> — по нему можно запросить полный разбор работы или уточнение по выборке и ограничениям.</li>
    <li>Уровень доказательности в поле слева проставлен по дизайну исследования; там, где авторы указали его сами, берётся авторский.</li>
    <li>Платная статья — пришлите PDF, разбор будет сделан по нему.</li>
  </ul>
  <h2 style="margin-top:1.4rem">Как цитировать этот обзор</h2>
  <p class="selfcite">{esc(self_cite)}</p>
</footer>
</div>'''

if __name__ == "__main__":
    b = json.load(open(sys.argv[1], encoding="utf-8"))
    open(sys.argv[2], "w", encoding="utf-8").write(render(b))
    print("готово:", sys.argv[2])
