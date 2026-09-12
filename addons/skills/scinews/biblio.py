"""Дотягиваем из Europe PMC поля, без которых ссылка не библиографическая:
аббревиатура журнала по MEDLINE, том, выпуск, страницы, дата публикации."""
import json, re, urllib.request, urllib.parse

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

def _q(query, page_size=25):
    u = EPMC + "?" + urllib.parse.urlencode({"query": query, "format": "json",
                                             "resultType": "core", "pageSize": page_size})
    return json.load(urllib.request.urlopen(u, timeout=45))["resultList"]["result"]

def enrich(arts, log=print):
    by_pmid = {str(a["pmid"]): a for a in arts if a.get("pmid")}
    by_doi = {str(a["doi"]).lower(): a for a in arts if a.get("doi") and not a.get("pmid")}
    got = 0
    for ids in (list(by_pmid), list(by_doi)):
        for i in range(0, len(ids), 12):
            chunk = ids[i:i + 12]
            field = "EXT_ID" if ids is not None and chunk and chunk[0].isdigit() else "DOI"
            try:
                rows = _q(" OR ".join(f'{field}:"{x}"' for x in chunk))
            except Exception as e:
                log(f"(библиография: Europe PMC {type(e).__name__})"); continue
            for r in rows:
                a = by_pmid.get(str(r.get("pmid") or "")) or by_doi.get(str(r.get("doi") or "").lower())
                if not a:
                    continue
                ji = r.get("journalInfo") or {}; j = ji.get("journal") or {}
                a["journal_abbr"] = j.get("medlineAbbreviation") or j.get("isoabbreviation") or ""
                a["volume"] = ji.get("volume") or ""
                a["issue"] = ji.get("issue") or ""
                a["pages"] = r.get("pageInfo") or ""
                a["pubdate"] = ji.get("dateOfPublication") or r.get("firstPublicationDate") or ""
                a["issn"] = j.get("issn") or j.get("essn") or ""
                if not a.get("authors") and r.get("authorString"):
                    a["authors"] = r["authorString"]
                got += 1
    log(f"библиография: дополнено {got} из {len(arts)}")
    return arts

def vancouver(a):
    """Ссылка по стандарту Vancouver (NLM) — как требуют журналы и как её поймёт менеджер ссылок."""
    au = [x.strip().rstrip(".") for x in str(a.get("authors") or "").split(",") if x.strip()]
    authors = (", ".join(au[:6]) + ", et al") if len(au) > 6 else ", ".join(au)
    j = a.get("journal_abbr") or re.sub(r"\s*:\s*.*$", "", (a.get("journal") or "")).strip()
    title = (a.get("en") or a.get("ru") or "").strip().rstrip(".")
    year = str(a.get("year") or "")
    loc = year
    if a.get("volume"):
        loc += f";{a['volume']}"
        if a.get("issue"):
            loc += f"({a['issue']})"
        if a.get("pages"):
            loc += f":{a['pages']}"
    elif a.get("pages"):
        loc += f":{a['pages']}"
    parts = [x for x in [authors + "." if authors else "", title + "." if title else "",
                         j + "." if j else "", loc + "." if loc else ""] if x]
    s = " ".join(parts)
    if a.get("doi"):
        s += f" doi:{a['doi']}"
    return s

def ru_gost(a):
    """ГОСТ Р 7.0.5-2008 — как принято в русских журналах и диссертациях."""
    au = [x.strip().rstrip(".") for x in str(a.get("authors") or "").split(",") if x.strip()]
    first = au[0] if au else ""
    rest = ", ".join(au[1:4]) + (" [и др.]" if len(au) > 4 else "")
    j = a.get("journal_abbr") or re.sub(r"\s*:\s*.*$", "", (a.get("journal") or "")).strip()
    head = f"{first} " if first else ""
    body = f"{(a.get('en') or '').rstrip('.')}"
    tail = f" // {j}. {a.get('year','')}"
    if a.get("volume"):
        tail += f". Vol. {a['volume']}"
        if a.get("issue"):
            tail += f", № {a['issue']}"
    if a.get("pages"):
        tail += f". P. {a['pages']}"
    doi = f". DOI: {a['doi']}" if a.get("doi") else ""
    au_line = ", ".join([first] + ([rest] if rest else [])) if first else ""
    return f"{head}{body}{tail}{doi}.".replace("  ", " "), au_line
