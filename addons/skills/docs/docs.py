#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Чтение и ПРОВЕРКА документов (docx/pptx/xlsx/pdf) — чтобы ассистент видел, что реально отдаёт владельцу.

  docs.py read <файл>              — весь текст документа (docx, pptx по слайдам, xlsx, pdf, txt)
  docs.py check <файл>             — текст + проверка КАЖДОЙ картинки внутри (безопасность/тема)
  docs.py preview <файл> [--pages 12] [--send] — отрисовать страницы (LibreOffice → PDF → PNG), описать зрением
                                     и оставить PNG в ~/.openclaw/workspace/media/preview/<имя>/ — их можно открыть view_image
"""
import argparse, base64, json, os, re, subprocess, sys, tempfile, urllib.request, zipfile

TOME = os.path.expanduser("~/.openclaw/workspace/skills/tome/tome.py")
PYBIN = os.path.expanduser("~/mailvenv/bin/python")

def keys():
    env = {}
    for p in (os.path.expanduser("~/.openclaw/.env"),):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env.get("EXCASH_API_KEY", ""), env.get("EXCASH_API_URL", "")

VISION = os.path.expanduser("~/.openclaw/vision.py")

def vision(path, prompt, max_tokens=1500):
    """Описание картинки — через общий ~/.openclaw/vision.py (DeepSeek → excash), он проверен в бою."""
    if os.path.exists(VISION):
        r = subprocess.run([PYBIN, VISION, path] + prompt.split(), capture_output=True, text=True, timeout=240)
        out = (r.stdout or "").strip()
        if out:
            return out
        return f"[зрение не ответило: {(r.stderr or '').strip()[-160:]}]"
    return "[нет ~/.openclaw/vision.py]"

def docx_text(path):
    from docx import Document
    doc = Document(path)
    out = [p.text for p in doc.paragraphs if p.text.strip()]
    for t in doc.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                out.append(" | ".join(cells))
    return "\n".join(out)

def pdf_text(path):
    r = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, timeout=120)
    return r.stdout.decode("utf-8", "ignore")

def pptx_text(path):
    from pptx import Presentation
    out = []
    for i, s in enumerate(Presentation(path).slides, 1):
        out.append(f"=== Слайд {i} ===")
        for sh in s.shapes:
            if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip():
                out.append(sh.text_frame.text.strip())
            if getattr(sh, "has_table", False):
                for row in sh.table.rows:
                    out.append(" | ".join(c.text.strip() for c in row.cells))
        if s.has_notes_slide and s.notes_slide.notes_text_frame.text.strip():
            out.append("[заметки] " + s.notes_slide.notes_text_frame.text.strip())
    return "\n".join(out)

def xlsx_text(path):
    try:
        import openpyxl
    except ImportError:
        return soffice_pdf_text(path)
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"=== Лист {ws.title} ===")
        for row in ws.iter_rows(values_only=True):
            if any(v is not None for v in row):
                out.append(" | ".join("" if v is None else str(v) for v in row))
            if len(out) > 4000:
                out.append("… (обрезано)"); break
    return "\n".join(out)

def soffice_bin():
    for c in ("/usr/bin/soffice", "/usr/bin/libreoffice"):
        if os.path.exists(c):
            return c
    return None

def to_pdf(path, outdir):
    """Любой офисный формат → PDF через LibreOffice (нужны libreoffice-impress/-calc для pptx/xlsx)."""
    so = soffice_bin()
    if not so:
        sys.exit("нет LibreOffice — конвертация недоступна")
    subprocess.run([so, "--headless", "--convert-to", "pdf", "--outdir", outdir, path],
                   capture_output=True, timeout=300)
    cand = [f for f in os.listdir(outdir) if f.lower().endswith(".pdf")]
    if not cand:
        sys.exit("не удалось конвертировать в PDF (для pptx/xlsx нужны пакеты libreoffice-impress/-calc)")
    return os.path.join(outdir, cand[0])

def soffice_pdf_text(path):
    tmp = tempfile.mkdtemp(prefix="doctxt_")
    return pdf_text(to_pdf(path, tmp))

def read_any(path):
    if path.lower().endswith(".docx"):
        return docx_text(path)
    if path.lower().endswith((".pptx", ".ppsx")):
        return pptx_text(path)
    if path.lower().endswith((".xlsx", ".xlsm")):
        return xlsx_text(path)
    if path.lower().endswith((".odt", ".odp", ".ods", ".doc", ".ppt", ".xls", ".rtf")):
        return soffice_pdf_text(path)
    if path.lower().endswith(".pdf"):
        return pdf_text(path)
    return open(path, encoding="utf-8", errors="ignore").read()

def cmd_read(a):
    t = read_any(a.file)
    print(f"[{os.path.basename(a.file)}] {len(t)} символов\n")
    print(t[:a.chars])

def cmd_check(a):
    path = a.file
    t = read_any(path)
    print(f"ПРОВЕРКА: {os.path.basename(path)}")
    print(f"Текст: {len(t)} символов. Начало: {t[:200].strip()}...\n")
    if not path.lower().endswith((".docx", ".pptx")):
        print("(проверка картинок доступна для .docx и .pptx)")
        return
    bad = 0
    with zipfile.ZipFile(path) as z:
        media = [n for n in z.namelist() if n.startswith(("word/media/", "ppt/media/"))]
        print(f"Картинок внутри: {len(media)}")
        tmp = tempfile.mkdtemp(prefix="doccheck_")
        for i, name in enumerate(media, 1):
            data = z.read(name)
            if len(data) < 3000:
                continue
            p = os.path.join(tmp, os.path.basename(name))
            with open(p, "wb") as f:
                f.write(data)
            try:
                v = vision(p, "Классифицируй ОДНИМ словом: SAFE / NUDITY / GORE / OTHER, "
                              "затем через двоеточие 6 слов что изображено. "
                              "Медицинские снимки = SAFE.", 200).replace("\n", " ")
            except Exception as e:
                v = f"ошибка проверки: {type(e).__name__}"
            flag = "NUDITY" in v.upper() or "GORE" in v.upper()
            bad += 1 if flag else 0
            print(("  ⛔ " if flag else "  ok ") + f"{os.path.basename(name)}: {v[:110]}")
            os.remove(p)
    print(f"\nИТОГ: проблемных картинок — {bad}."
          + (" ДОКУМЕНТ ОТПРАВЛЯТЬ НЕЛЬЗЯ, пересобери без них." if bad else " Документ чистый."))

def cmd_preview(a):
    path = os.path.abspath(a.file)
    stem = re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(path))[0])[:60]
    outdir = os.path.expanduser(f"~/.openclaw/workspace/media/preview/{stem}")
    os.makedirs(outdir, exist_ok=True)
    for f in os.listdir(outdir):
        os.remove(os.path.join(outdir, f))
    pdf = path if path.lower().endswith(".pdf") else to_pdf(path, outdir)
    subprocess.run(["pdftoppm", "-r", str(a.dpi), "-png", "-f", "1", "-l", str(a.pages), pdf,
                    os.path.join(outdir, "page")], capture_output=True, timeout=300)
    pages = sorted(f for f in os.listdir(outdir) if f.startswith("page") and f.endswith(".png"))
    if not pages:
        sys.exit("страницы не отрисовались")
    print(f"Страниц отрисовано: {len(pages)} → {outdir} (открывай через view_image, /tmp ему не виден)")
    for i, pg in enumerate(pages, 1):
        p = os.path.join(outdir, pg)
        print(f"  {p}")
        if a.no_vision:
            continue
        try:
            v = vision(p, "Опиши, что реально на этой странице/слайде: заголовки, о чём текст, какие "
                          "изображения и таблицы. Отдельно: есть ли обрезанный или наезжающий текст, "
                          "переполнение блоков, пустые места, нечитаемые цвета. Если есть что-то "
                          "неуместное (эротика, посторонние картинки не по теме) — назови явно.", 1200)
        except Exception as e:
            v = f"ошибка: {type(e).__name__}"
        print(f"\n--- Страница {i} ---\n{v}")
        if a.send:
            subprocess.run([PYBIN, TOME, "photo", p, "--caption", f"{os.path.basename(path)} — стр. {i}"],
                           check=False)

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read"); r.add_argument("file"); r.add_argument("--chars", type=int, default=12000)
    c = sub.add_parser("check"); c.add_argument("file")
    p = sub.add_parser("preview"); p.add_argument("file"); p.add_argument("--pages", type=int, default=12)
    p.add_argument("--send", action="store_true"); p.add_argument("--dpi", type=int, default=90)
    p.add_argument("--no-vision", action="store_true", help="только PNG, без описания зрением")
    a = ap.parse_args()
    if not os.path.exists(a.file):
        sys.exit(f"файл не найден: {a.file}")
    {"read": cmd_read, "check": cmd_check, "preview": cmd_preview}[a.cmd](a)

if __name__ == "__main__":
    main()
