#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Генерация и редактирование картинок через FAL (xAI Grok Imagine 2.0).

  imggen.py gen    "<промпт>" [--ar 9:16] [--n 1] [--q medium] [--res 2k] [--send]
  imggen.py edit   <файл|url> "<промпт>" [--img ещё_файл] [--ar 9:16] [--send]
  imggen.py stories <файл|url> "<промпт>" [--send]     # пресет сторис 9:16 2k
  imggen.py cost                                        # напомнить цены

Ключ FAL_KEY берётся из ~/.openclaw/.env. Локальные файлы уходят в API как
data-URI — отдельная загрузка не нужна. Результат сохраняется в /tmp
(подчищается автоочисткой) и с --send отправляется доктору через tome.
"""
import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PYBIN = os.path.expanduser("~/mailvenv/bin/python")
TOME = os.path.expanduser("~/.openclaw/workspace/skills/tome/tome.py")
ENVF = os.path.expanduser("~/.openclaw/.env")

BASE = "https://fal.run/xai/grok-imagine-image/v2.0"
T2I = BASE + "/text-to-image"
EDIT = BASE + "/edit"
MAX_INPUT_IMAGES = 3          # ограничение модели
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def env(name, default=""):
    """Читаем секрет из .env агента (в окружении его может не быть)."""
    val = os.environ.get(name)
    if val:
        return val
    try:
        for line in open(ENVF, encoding="utf-8"):
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return default


def as_image_url(src):
    """Ссылка -> как есть. Локальный файл -> data-URI."""
    if src.startswith(("http://", "https://", "data:")):
        return src
    path = os.path.expanduser(src)
    if not os.path.exists(path):
        sys.exit(f"файл не найден: {src}")
    size = os.path.getsize(path)
    if size > MAX_UPLOAD_BYTES:
        sys.exit(f"файл слишком большой ({size//1024//1024} МБ), максимум 8 МБ")
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    if not mime.startswith("image/"):
        sys.exit(f"это не картинка: {src} ({mime})")
    with open(path, "rb") as fh:
        return f"data:{mime};base64," + base64.b64encode(fh.read()).decode()


def fal(url, payload, timeout=300):
    key = env("FAL_KEY")
    if not key:
        sys.exit("FAL_KEY не найден в ~/.openclaw/.env — генерация недоступна. "
                 "Скажи об этом доктору, не выдумывай картинку.")
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": "Key " + key, "Content-Type": "application/json"})
    t0 = time.time()
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    except Exception as e:
        detail = str(e)
        if hasattr(e, "read"):
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
        sys.exit(f"FAL не ответил ({type(e).__name__}): {detail}\n"
                 f"Картинка НЕ создана — так и скажи доктору.")
    return resp, time.time() - t0


def download(url, idx, fmt="jpeg"):
    """Расширение ставим по ФАКТИЧЕСКОМУ содержимому: FAL может вернуть png,
    даже когда просили jpeg, а кривое расширение ломает зрение (400 от шлюза)."""
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
            ctype = (r.headers.get("Content-Type") or "").lower()
    except Exception as e:
        print(f"  не скачалось: {type(e).__name__}", file=sys.stderr)
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" or "png" in ctype:
        ext = ".png"
    elif data[:3] == b"\xff\xd8\xff" or "jpeg" in ctype or "jpg" in ctype:
        ext = ".jpg"
    elif data[8:12] == b"WEBP" or "webp" in ctype:
        ext = ".webp"
    else:
        ext = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}.get(fmt, ".jpg")
    path = f"/tmp/yoda_gen_{int(time.time())}_{idx}{ext}"
    with open(path, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o644)
    return path


def safety_note(path, topic=""):
    """Мягкая проверка зрением: блокируем только откровенную эротику.
    Медицинский контент (рентген, операционная, кровь в ране) — норма для врача."""
    imgs = os.path.expanduser("~/.openclaw/workspace/skills/images/imgs.py")
    if not os.path.exists(imgs):
        return True, ""
    try:
        sys.path.insert(0, os.path.dirname(imgs))
        import importlib.util
        spec = importlib.util.spec_from_file_location("imgs", imgs)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ok, label, why = mod.gate(path, "")          # без темы = только безопасность
        if not ok and label == "BLOCK" and "NUDITY" in (why or "").upper():
            return False, f"отклонено фильтром: {why[:100]}"
        return True, ""
    except Exception:
        return True, ""                              # фильтр недоступен — не мешаем


def deliver(paths, caption, send):
    for p in paths:
        if send:
            subprocess.run([PYBIN, TOME, "photo", p, "--caption", caption[:180]], check=False)
            print(f"  ✓ отправлено доктору: {os.path.basename(p)}")
        else:
            print(f"  файл: {p}")


def run(url, payload, caption, send, label):
    resp, elapsed = fal(url, payload)
    images = resp.get("images") or []
    if not images:
        sys.exit(f"FAL вернул пустой список картинок: {json.dumps(resp, ensure_ascii=False)[:200]}")
    print(f"{label}: готово за {elapsed:.0f}с, картинок: {len(images)}")
    revised = resp.get("revised_prompt")
    if revised:
        print(f"  модель уточнила промпт: {revised[:200]}")
    fmt = payload.get("output_format", "jpeg")
    saved = []
    for i, im in enumerate(images, 1):
        p = download(im.get("url", ""), i, fmt)
        if not p:
            continue
        ok, why = safety_note(p)
        if not ok:
            print(f"  ⛔ картинка {i} {why}")
            try:
                os.remove(p)
            except Exception:
                pass
            continue
        saved.append(p)
    if not saved:
        sys.exit("ни одной пригодной картинки не получилось — скажи честно, не подставляй чужую")
    deliver(saved, caption, send)
    print(f"\nИТОГ: {len(saved)} из {len(images)} готовы. "
          f"Прежде чем ставить в пост — посмотри их глазами (vision.py) и сверь с задумкой.")


def cmd_gen(a):
    payload = {"prompt": a.prompt, "num_images": max(1, min(a.n, 4)),
               "aspect_ratio": a.ar, "resolution": a.res, "quality": a.q,
               "output_format": a.fmt}
    run(T2I, payload, a.caption or a.prompt, a.send, "Генерация")


def cmd_edit(a):
    srcs = [a.image] + list(a.img or [])
    if len(srcs) > MAX_INPUT_IMAGES:
        sys.exit(f"максимум {MAX_INPUT_IMAGES} входных картинки, передано {len(srcs)}")
    urls = [as_image_url(s) for s in srcs]
    payload = {"prompt": a.prompt, "image_urls": urls,
               "num_images": max(1, min(a.n, 4)), "aspect_ratio": a.ar,
               "resolution": a.res, "quality": a.q, "output_format": a.fmt}
    local = sum(1 for s in srcs if not s.startswith(("http", "data:")))
    print(f"Вход: {len(urls)} картинк(и), из них локальных: {local}")
    run(EDIT, payload, a.caption or a.prompt, a.send, "Редактирование")


def cmd_stories(a):
    a.ar, a.res, a.q, a.n = "9:16", "2k", "medium", getattr(a, "n", 1)
    a.fmt = "jpeg"
    cmd_edit(a)


def cmd_cost(_a):
    print("Grok Imagine 2.0 на FAL (за картинку):")
    print("  1k low ~$0.04 | 1k medium ~$0.06 | 2k low ~$0.06 | 2k medium ~$0.08")
    print("  + ~$0.01 за каждую входную картинку в режиме edit")
    print("Сторис-пресет (2k medium + 1 вход) ≈ $0.09 ≈ 8 ₽ за штуку.")


def main():
    ap = argparse.ArgumentParser(description="Картинки через FAL / Grok Imagine 2.0")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--n", type=int, default=1, help="сколько вариантов (1-4)")
        p.add_argument("--ar", default="auto", help="9:16 сторис, 1:1, 16:9, 4:5 …")
        p.add_argument("--res", default="1k", choices=["1k", "2k"])
        p.add_argument("--q", default="medium", choices=["low", "medium"])
        p.add_argument("--fmt", default="jpeg", choices=["jpeg", "png", "webp"])
        p.add_argument("--caption", default="", help="подпись при отправке")
        p.add_argument("--send", action="store_true", help="отправить доктору")

    g = sub.add_parser("gen", help="текст -> картинка")
    g.add_argument("prompt")
    common(g)
    g.set_defaults(func=cmd_gen)

    e = sub.add_parser("edit", help="картинка(и) + промпт -> новая картинка")
    e.add_argument("image", help="локальный файл или URL")
    e.add_argument("prompt")
    e.add_argument("--img", action="append", help="ещё входная картинка (до 3 всего)")
    common(e)
    e.set_defaults(func=cmd_edit)

    s = sub.add_parser("stories", help="пресет сторис 9:16 2k из фото")
    s.add_argument("image")
    s.add_argument("prompt")
    s.add_argument("--img", action="append")
    s.add_argument("--n", type=int, default=1)
    s.add_argument("--caption", default="")
    s.add_argument("--send", action="store_true")
    s.set_defaults(func=cmd_stories)

    c = sub.add_parser("cost", help="цены")
    c.set_defaults(func=cmd_cost)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
