#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imggen v2 — картинки через fal.ai. Много моделей, модель выбирается ПО ЗАДАЧЕ.

  imggen.py models                                        меню: какая модель для чего, цена, время
  imggen.py gen  "<промпт>" --model KEY [--ar 4:5] [--n 1] [--q low|medium|high] [--res 1k|2k] [--send]
  imggen.py edit <файл|url> "<промпт>" --model KEY [--img ещё] [--ar ...] [--send]
  imggen.py stories <фото> "<промпт>" [--model grok] [--send]         пресет 9:16, 2k
  imggen.py compare "<промпт>" --models seedream,gpt,muse [--ar] [--send]   один промпт, 2-4 модели
  imggen.py vector "<промпт>" [--colors "#E85D3C,#0B8570"] [--send]  настоящий SVG
  imggen.py nobg <файл|url> [--send]                      убрать фон, PNG с прозрачностью
  imggen.py upscale <файл|url> [--x 2|4] [--send]         увеличить без перерисовки
  imggen.py cost [--days 30]                              расход по журналу
  imggen.py bench "<промпт>" [--models a,b] [--ar 4:5]    прогон моделей без отправки (проверка)

Ключ FAL_KEY — из ~/.openclaw/.env. Вызовы идут через очередь fal (queue.fal.run):
медленные модели работают минутами, синхронный запрос их обрывал.
Результат — в /tmp (подчищается автоочисткой), с --send уходит владельцу через tome.
"""
import argparse
import base64
import io
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

PYBIN = sys.executable            # тот же питон, что запустил скилл: у каждой Йоды свой venv
TOME = os.path.expanduser("~/.openclaw/workspace/skills/tome/tome.py")
ENVF = os.path.expanduser("~/.openclaw/.env")
LOG = os.path.expanduser("~/.openclaw/workspace/memory/imggen_log.jsonl")
QUEUE = "https://queue.fal.run/"
USD_RUB = 90                      # для прикидки расхода в рублях
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# ---------------------------------------------------------------------------
# Реестр моделей. size: как модель принимает размер ("ar" — aspect_ratio строкой,
# "image_size" — пресет или {width,height}, "seedream" — площадь в диапазоне);
# quality: как принимает качество; multi: умеет ли num_images; fmt: есть ли output_format.
# price — долларов за картинку по прайсу fal; у токенных моделей (GPT) это оценка.
# ---------------------------------------------------------------------------
MODELS = {
    "grok": {
        "title": "Grok Imagine 2.0 (xAI)",
        "t2i": "xai/grok-imagine-image/v2.0/text-to-image", "edit": "xai/grok-imagine-image/v2.0/edit",
        "refs": 3, "size": "ar", "quality": "grok", "multi": True, "fmt": True,
        "price": {"low": 0.04, "medium": 0.06, "high": 0.06}, "speed": "20-90 с",
        "best": "сторис и обложки из фото владельца (пресет stories); русский текст пишет верно",
    },
    "gpt": {
        "title": "GPT Image 2.5 Flare (OpenAI)",
        "t2i": "openai/gpt-image-2.5/flare/text-to-image", "edit": "openai/gpt-image-2.5/flare/edit",
        "refs": 16, "size": "image_size", "auto_size": True, "quality": "gpt", "q": "medium",
        "multi": True, "fmt": True, "transparent": True,
        "price": {"low": 0.02, "medium": 0.06, "high": 0.2}, "speed": "~20 с",
        "best": "плакаты и обложки с русским текстом, сложные сцены, прозрачный фон, правка по многим фото",
    },
    "gpt-max": {
        "title": "GPT Image 2.5 Sunburst (OpenAI)",
        "t2i": "openai/gpt-image-2.5/sunburst/text-to-image", "edit": "openai/gpt-image-2.5/sunburst/edit",
        "refs": 16, "size": "image_size", "auto_size": True, "quality": "gpt", "q": "high",
        "multi": True, "fmt": True, "transparent": True,
        "price": {"low": 0.03, "medium": 0.09, "high": 0.25}, "speed": "~40 с",
        "best": "премиум: то же, что gpt, но детальнее — в печать, на главную сайта, крупный постер",
    },
    "seedream": {
        "title": "Seedream 5.0 Pro (ByteDance)",
        "t2i": "bytedance/seedream/v5/pro/text-to-image", "edit": "bytedance/seedream/v5/pro/edit",
        "refs": 10, "size": "seedream", "multi": True, "fmt": True,
        "price": 0.0675, "price_2k": 0.135, "speed": "~1,5 мин",
        "best": "памятки и инфографика для пациентов: шаги, карточки, пояснения — лучшая вёрстка с русским текстом",
    },
    "muse": {
        "title": "Muse Image (Meta)",
        "t2i": "meta/muse-image/text-to-image", "edit": "meta/muse-image/edit",
        "refs": 10, "size": "ar", "multi": True, "fmt": True,
        "price": 0.05, "price_note": "прайс не опубликован", "speed": "~15 с",
        "best": "аккуратная правка фото с сохранением композиции; чистые плакаты с русским текстом; реализм",
    },
    "banana": {
        "title": "Nano Banana 2 (Google)",
        "t2i": "fal-ai/nano-banana-2", "edit": "fal-ai/nano-banana-2/edit",
        "refs": 10, "size": "ar", "quality": "banana", "multi": True, "fmt": True,
        "price": 0.08, "price_2k": 0.12, "speed": "~15 с",
        "best": "правка словами по нескольким фото: собрать людей и предметы из разных снимков",
    },
    "draft": {
        "title": "Krea 2 Medium Turbo",
        "t2i": "krea/v2/medium/turbo/text-to-image", "edit": None,
        "size": "ar", "multi": False, "fmt": False,
        "price": 0.015, "speed": "~15 с",
        "best": "фотосцены без текста — реализм как у дорогих моделей за полтора цента; черновики идей",
    },
    "fibo": {
        "title": "FIBO Gen 1.5 (Bria)",
        "t2i": "bria/fibo-gen-1.5/text-to-image", "edit": None,
        "size": "ar", "multi": False, "fmt": False,
        "price": 0.04, "price_note": "прайс не опубликован", "speed": "~20 с",
        "best": "фотосцены для публикации, где важна чистота прав (обучена на лицензированных данных); текст НЕ пишет",
    },
}
VECTOR = {"title": "Recraft V4.1 (вектор)", "t2i": "fal-ai/recraft/v4.1/text-to-vector", "price": 0.08}
NOBG = {"title": "FeyNobg (удаление фона)", "t2i": "fal-ai/feynobg", "price": 0.01}
UPSCALE = {"title": "Bria Increase Resolution", "t2i": "bria/increase-resolution", "price": 0.04}
DEFAULT_MODEL = "grok"

PRESET = {"1:1": "square_hd", "4:3": "landscape_4_3", "3:4": "portrait_4_3",
          "16:9": "landscape_16_9", "9:16": "portrait_16_9"}


class FalError(Exception):
    def __init__(self, code, detail):
        self.code = code
        self.text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
        super().__init__(f"HTTP {code}: {self.text[:400]}")


def env(name, default=""):
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


# ---------------------------- размеры и качество ----------------------------
def ratio_of(ar):
    try:
        a, b = ar.split(":")
        return float(a) / float(b)
    except Exception:
        return None


def wh(ar, area):
    r = ratio_of(ar) or 1.0
    w = math.sqrt(area * r)
    return {"width": int(round(w / 16) * 16), "height": int(round(w / r / 16) * 16)}


def size_fields(m, ar, res):
    kind = m.get("size")
    if ar == "auto":
        if kind == "image_size" and m.get("auto_size"):
            return {"image_size": "auto"}
        if kind == "seedream":
            return {"image_size": "auto_2K" if res == "2k" else "auto_1K"}
        return {}
    if kind == "ar":
        return {"aspect_ratio": ar}
    if kind == "image_size":
        if res == "2k":
            return {"image_size": wh(ar, 2048 * 2048 * 0.9)}
        return {"image_size": PRESET.get(ar) or wh(ar, 1024 * 1024)}
    if kind == "seedream":             # площадь строго между 1024² и 2048²
        return {"image_size": wh(ar, 2048 * 2048 * 0.95 if res == "2k" else 1024 * 1024 * 1.1)}
    return {}


def quality_fields(m, q, res):
    kind = m.get("quality")
    if kind == "grok":
        return {"quality": "low" if q == "low" else "medium", "resolution": res}
    if kind == "gpt":
        return {"quality": q}
    if kind == "ideogram":
        return {"rendering_speed": {"low": "TURBO", "medium": "BALANCED", "high": "QUALITY"}[q]}
    if kind == "banana":
        return {"resolution": "2K" if res == "2k" else "1K"}
    return {}


def build_payload(m, mode, prompt, ar, res, q, n, refs=None, transparent=False):
    p = {"prompt": prompt}
    p.update(size_fields(m, ar, res))
    p.update(quality_fields(m, q, res))
    p.update(m.get("extra") or {})
    if m.get("fmt"):
        p["output_format"] = "png" if transparent else "jpeg"
    if transparent and m.get("transparent"):
        p["background"] = "transparent"
    if m.get("multi") and n > 1:
        p["num_images"] = n
    if mode == "edit":
        if m.get("ref_field") == "image_url":
            p["image_url"] = refs[0]
        else:
            p["image_urls"] = refs
    return p


def price_of(m, q, res, ar="1:1"):
    pr = m.get("price", 0)
    if res == "2k" and m.get("price_2k"):
        return m["price_2k"]
    if isinstance(pr, dict):
        pr = pr.get(q, pr.get("medium", 0))
    if m.get("per_mp"):
        pr *= 4 if res == "2k" else 1
    if m.get("quality") == "grok" and res == "2k":
        pr += 0.02
    return pr


def repair_payload(payload, err_text):
    """422 от схемы: подгоняем то поле, на которое модель ругнулась, и пробуем ещё раз."""
    p, changed = dict(payload), False
    low = err_text.lower()
    if "aspect_ratio" in low and "aspect_ratio" in p:
        allowed = re.findall(r"'(\d+(?:\.\d+)?:\d+(?:\.\d+)?)'", err_text)
        want = ratio_of(p["aspect_ratio"])
        if allowed and want:
            p["aspect_ratio"] = min(allowed, key=lambda x: abs(math.log((ratio_of(x) or 1) / want)))
        else:
            p.pop("aspect_ratio")
        changed = True
    if "image_size" in low and "image_size" in p:
        p.pop("image_size")
        changed = True
    for field in ("num_images", "output_format", "quality", "resolution", "rendering_speed", "background"):
        if field in low and field in p:
            p.pop(field)
            changed = True
    return p if changed else None


# ---------------------------- вход и выход ----------------------------
def as_image_url(src, max_side=2048):
    """Ссылка — как есть. Локальный файл — data-URI; большое фото ужимаем до 2048 px:
    меньше тело запроса, реже отказы, а моделям больше и не нужно."""
    if src.startswith(("http://", "https://", "data:")):
        return src
    path = os.path.expanduser(src)
    if not os.path.exists(path):
        sys.exit(f"файл не найден: {src}")
    size = os.path.getsize(path)
    if size > MAX_UPLOAD_BYTES:
        sys.exit(f"файл слишком большой ({size // 1024 // 1024} МБ), максимум 20 МБ")
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    if not mime.startswith("image/"):
        sys.exit(f"это не картинка: {src} ({mime})")
    data = open(path, "rb").read()
    if max_side:
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            if max(im.size) > max_side or size > 4 * 1024 * 1024:
                im.thumbnail((max_side, max_side))
                buf = io.BytesIO()
                if im.mode in ("RGBA", "LA", "P"):
                    im.save(buf, "PNG"); mime = "image/png"
                else:
                    im.convert("RGB").save(buf, "JPEG", quality=92); mime = "image/jpeg"
                data = buf.getvalue()
        except Exception:
            pass
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def http(method, url, key, body=None, timeout=60):
    req = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Key " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode() or "{}"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"detail": raw[:600]}


STORAGE = "https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3"


def upload_bytes(data, mime, name):
    """Файл в хранилище fal → ссылка. Нужен моделям, которые не принимают data-URI."""
    key = env("FAL_KEY")
    code, init = http("POST", STORAGE, key, {"content_type": mime, "file_name": name})
    if code >= 400 or not init.get("upload_url"):
        raise FalError(code, init)
    req = urllib.request.Request(init["upload_url"], data=data, method="PUT", headers={"Content-Type": mime})
    urllib.request.urlopen(req, timeout=180).read()
    return init["file_url"]


def rehost(payload):
    def one(v):
        if isinstance(v, str) and v.startswith("data:"):
            head, b64 = v.split(",", 1)
            mime = head[5:].split(";")[0] or "image/png"
            return upload_bytes(base64.b64decode(b64), mime, "input" + (mimetypes.guess_extension(mime) or ".png"))
        return v
    p = dict(payload)
    if "image_url" in p:
        p["image_url"] = one(p["image_url"])
    if "image_urls" in p:
        p["image_urls"] = [one(x) for x in p["image_urls"]]
    return p


def fal_run(endpoint, payload, timeout=600):
    key = env("FAL_KEY")
    if not key:
        sys.exit("FAL_KEY не найден в ~/.openclaw/.env — генерация недоступна. Скажи об этом честно.")
    code, sub = http("POST", QUEUE + endpoint, key, payload, timeout=120)
    if code >= 400 or not sub.get("status_url"):
        raise FalError(code, sub)
    t0, delay = time.time(), 1.5
    while True:
        code, st = http("GET", sub["status_url"], key)
        if st.get("status") == "COMPLETED":
            break
        if code >= 400 and code not in (202,):
            raise FalError(code, st)
        if time.time() - t0 > timeout:
            raise FalError(408, f"не дождались за {timeout} с (request_id {sub.get('request_id')})")
        time.sleep(delay)
        delay = min(delay * 1.3, 6)
    code, res = http("GET", sub["response_url"], key, timeout=120)
    if code >= 400:
        raise FalError(code, res)
    return res, time.time() - t0


def call(m, mode, payload, timeout=600):
    """Запуск с двумя починками по ответу модели: размер не из её списка → ближайший;
    не берёт встроенный файл → перезалить в хранилище fal."""
    endpoint = m[mode]
    repaired = rehosted = False
    while True:
        try:
            return fal_run(endpoint, payload, timeout)
        except FalError as e:
            if "scheme 'data'" in e.text and not rehosted:
                rehosted = True
                print(f"  {m['title']}: не берёт файл напрямую — загрузил в хранилище fal, повторяю")
                payload = rehost(payload)
                continue
            if e.code == 422 and not repaired:
                fixed = repair_payload(payload, e.text)
                if fixed is not None:
                    repaired = True
                    gone = sorted(set(payload) - set(fixed)) + [k for k in fixed if fixed[k] != payload.get(k)]
                    print(f"  {m['title']}: схема не приняла {', '.join(gone)} — поправил и повторяю")
                    payload = fixed
                    continue
            raise


def images_of(res):
    ims = res.get("images")
    if isinstance(ims, list) and ims:
        return ims
    if isinstance(res.get("image"), dict):
        return [res["image"]]
    return []


def download(url, tag, idx):
    try:
        with urllib.request.urlopen(url, timeout=180) as r:
            data, ctype = r.read(), (r.headers.get("Content-Type") or "").lower()
    except Exception as e:
        print(f"  не скачалось: {type(e).__name__}", file=sys.stderr)
        return None
    head = data[:200].lstrip()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    elif data[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    elif data[8:12] == b"WEBP":
        ext = ".webp"
    elif head.startswith((b"<?xml", b"<svg")) or "svg" in ctype:
        ext = ".svg"
    elif "png" in ctype:
        ext = ".png"
    elif "webp" in ctype:
        ext = ".webp"
    else:
        ext = ".jpg"
    safe = re.sub(r"[^a-z0-9-]+", "", tag.lower())[:20] or "img"
    path = f"/tmp/yoda_gen_{int(time.time())}_{uuid.uuid4().hex[:6]}_{safe}_{idx}{ext}"
    with open(path, "wb") as fh:
        fh.write(data)
    if ext == ".webp":                    # Telegram и зрение надёжнее едят JPEG
        try:
            from PIL import Image
            jpg = path[:-5] + ".jpg"
            Image.open(path).convert("RGB").save(jpg, "JPEG", quality=93)
            os.remove(path)
            path = jpg
        except Exception:
            pass
    os.chmod(path, 0o644)
    return path


def safety_ok(path):
    """Мягкая проверка зрением: режем только откровенную эротику.
    Медицинское (рентген, операционная, рана) — нормальная работа врача."""
    imgs = os.path.expanduser("~/.openclaw/workspace/skills/images/imgs.py")
    if not os.path.exists(imgs) or path.endswith(".svg"):
        return True, ""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("imgs", imgs)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ok, label, why = mod.gate(path, "")
        if not ok and label == "BLOCK" and "NUDITY" in (why or "").upper():
            return False, f"отклонено фильтром: {why[:100]}"
    except Exception:
        pass
    return True, ""


def deliver(paths, caption, send, as_file=False):
    for p in paths:
        if send:
            kind = "file" if as_file or p.endswith(".svg") else "photo"
            subprocess.run([PYBIN, TOME, kind, p, "--caption", caption[:180]], check=False)
            print(f"  ✓ отправлено: {os.path.basename(p)}")
        else:
            print(f"  файл: {p}")


def journal(**row):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def pick(key, need_edit=False):
    if not key:
        key = DEFAULT_MODEL
        print(f"(модель не выбрана — взял {key}; для другой задачи смотри: imggen.py models)")
    m = MODELS.get(key)
    if not m:
        sys.exit(f"нет модели «{key}». Есть: {', '.join(MODELS)}")
    if need_edit and not m.get("edit"):
        can = [k for k, v in MODELS.items() if v.get("edit")]
        sys.exit(f"{m['title']} не умеет править фото. Правят: {', '.join(can)}")
    return key, m


def generate(key, m, mode, prompt, ar, res, q, n, refs=None, transparent=False, quiet=False):
    """Один запуск модели: n картинок (если модель не умеет num_images — по одной)."""
    q = q or m.get("q", "medium")
    runs = 1 if m.get("multi") else n
    per_call = n if m.get("multi") else 1
    paths, seconds, err = [], 0.0, ""
    for _ in range(runs):
        payload = build_payload(m, mode, prompt, ar, res, q, per_call, refs, transparent)
        try:
            res_json, dt = call(m, mode, payload)
        except FalError as e:
            err = str(e)
            break
        seconds += dt
        for i, im in enumerate(images_of(res_json), len(paths) + 1):
            p = download(im.get("url", ""), key, i)
            if not p:
                continue
            ok, why = safety_ok(p)
            if not ok:
                print(f"  ⛔ {m['title']}: картинка {i} {why}")
                os.remove(p)
                continue
            paths.append(p)
    est = round(price_of(m, q, res, ar) * max(len(paths), 0), 4)
    journal(model=key, mode=mode, n=len(paths), seconds=round(seconds, 1), usd=est,
            ok=bool(paths), error=err[:200], prompt=prompt[:200])
    if not quiet:
        if paths:
            print(f"{m['title']}: готово за {seconds:.0f} с, картинок {len(paths)}, ≈ ${est:.3f} (≈{est * USD_RUB:.0f} ₽)")
        else:
            print(f"{m['title']}: НЕ получилось. {err[:300]}")
    return paths, seconds, est, err


# ---------------------------- команды ----------------------------
def cmd_models(_a):
    print("МОДЕЛИ ДЛЯ КАРТИНОК (выбирай под задачу, ключ — в --model)\n")
    for k, m in MODELS.items():
        pr = m["price"]
        pr_s = (f"${min(pr.values()):.3f}–{max(pr.values()):.2f}" if isinstance(pr, dict) else f"${pr:.3f}")
        if m.get("per_mp"):
            pr_s += " за Мп"
        ed = f"правит фото (до {m['refs']})" if m.get("edit") else "только из текста"
        note = f" ({m['price_note']})" if m.get("price_note") else ""
        print(f"  {k:12s} {m['title']}")
        print(f"               для чего: {m['best']}")
        print(f"               {pr_s}{note} за картинку · {m['speed']} · {ed}\n")
    print("  vector       Recraft V4.1 — настоящий SVG: логотипы, иконки, пиктограммы для печати")
    print("  nobg         убрать фон у фото → PNG с прозрачностью (~$0.01)")
    print("  upscale      увеличить в 2 или 4 раза без перерисовки деталей ($0.04)")
    print("  compare      один промпт на 2-4 моделях — когда не ясно, какая лучше")


def cmd_gen(a):
    key, m = pick(a.model)
    if a.transparent and not m.get("transparent"):
        sys.exit("прозрачный фон умеют только gpt и gpt-max (или сделай картинку и потом nobg)")
    paths, _, _, err = generate(key, m, "t2i", a.prompt, a.ar, a.res, a.q, max(1, min(a.n, 4)),
                                transparent=a.transparent)
    if not paths:
        sys.exit("Картинка НЕ создана — скажи честно, не подставляй чужую.")
    deliver(paths, a.caption or a.prompt, a.send, as_file=a.transparent)
    print("\nПрежде чем отдавать — посмотри глазами (vision.py) и сверь с задумкой.")


def cmd_edit(a):
    key, m = pick(a.model, need_edit=True)
    srcs = [a.image] + list(a.img or [])
    if len(srcs) > m["refs"]:
        sys.exit(f"{m['title']} принимает до {m['refs']} входных картинок, передано {len(srcs)}")
    refs = [as_image_url(s) for s in srcs]
    print(f"Вход: {len(refs)} картинк(и)")
    paths, _, _, _ = generate(key, m, "edit", a.prompt, a.ar, a.res, a.q, max(1, min(a.n, 4)), refs=refs)
    if not paths:
        sys.exit("Картинка НЕ создана — скажи честно, не подставляй чужую.")
    deliver(paths, a.caption or a.prompt, a.send)
    print("\nПрежде чем отдавать — посмотри глазами (vision.py): не уплыло ли лицо, нет ли кривого текста.")


def cmd_stories(a):
    a.ar, a.res = "9:16", "2k"
    a.model = a.model or "grok"
    a.q = a.q or None
    cmd_edit(a)


def run_many(keys, prompt, ar, res, q, send, label):
    for k in keys:
        if k not in MODELS:
            sys.exit(f"нет модели «{k}». Есть: {', '.join(MODELS)}")
    print(f"{label}: {len(keys)} моделей параллельно, промпт: {prompt[:100]}\n")
    results, total = [], 0.0
    print(f"{'модель':12s} {'время':>7s} {'цена':>8s}  результат", flush=True)
    with ThreadPoolExecutor(max_workers=min(4, len(keys))) as ex:
        futs = {ex.submit(generate, k, MODELS[k], "t2i", prompt, ar, res, q, 1, None, False, True): k for k in keys}
        for f in as_completed(futs):
            k = futs[f]
            paths, sec, est, err = f.result()
            results.append((k, (paths, sec, est, err)))
            total += est
            res_s = paths[0] if paths else "ОШИБКА " + err[:120]
            print(f"{k:12s} {sec:6.0f}с {'$%.3f' % est:>8s}  {res_s}", flush=True)
            if paths and send:
                deliver(paths, f"{MODELS[k]['title']} — {prompt[:120]}", True)
    print(f"\nитого ≈ ${total:.2f} (≈{total * USD_RUB:.0f} ₽)")
    return results


def cmd_compare(a):
    keys = [k.strip() for k in a.models.split(",") if k.strip()]
    if not 2 <= len(keys) <= 4:
        sys.exit("сравнивать можно 2-4 модели: --models seedream,gpt,muse")
    run_many(keys, a.prompt, a.ar, a.res, a.q, a.send, "Сравнение")


def cmd_bench(a):
    keys = [k.strip() for k in (a.models or ",".join(MODELS)).split(",") if k.strip()]
    run_many(keys, a.prompt, a.ar, a.res, a.q, False, "Прогон")


def hex_rgb(h):
    h = h.strip().lstrip("#")
    return {"r": int(h[0:2], 16), "g": int(h[2:4], 16), "b": int(h[4:6], 16)}


def single(spec, payload, tag, caption, send):
    try:
        res, dt = call(spec, "t2i", payload)
    except FalError as e:
        journal(model=tag, mode=tag, n=0, seconds=0, usd=0, ok=False, error=str(e)[:200], prompt=caption[:200])
        sys.exit(f"{spec['title']}: не получилось — {e}")
    paths = [p for p in (download(im.get("url", ""), tag, i) for i, im in enumerate(images_of(res), 1)) if p]
    journal(model=tag, mode=tag, n=len(paths), seconds=round(dt, 1), usd=spec["price"] * len(paths),
            ok=bool(paths), error="", prompt=caption[:200])
    if not paths:
        sys.exit(f"{spec['title']}: пустой ответ — {json.dumps(res, ensure_ascii=False)[:200]}")
    print(f"{spec['title']}: готово за {dt:.0f} с")
    return paths


def cmd_vector(a):
    payload = {"prompt": a.prompt, "image_size": PRESET.get(a.ar, "square_hd")}
    if a.colors:
        payload["colors"] = [hex_rgb(c) for c in a.colors.split(",") if c.strip()]
    paths = single(VECTOR, payload, "vector", a.prompt, a.send)
    extra = []
    conv = shutil.which("rsvg-convert")
    for p in paths:
        if conv and p.endswith(".svg"):          # превью: Telegram SVG не показывает
            png = p[:-4] + "_preview.png"
            if subprocess.run([conv, "-w", "1024", "-o", png, p], capture_output=True).returncode == 0:
                extra.append(png)
    deliver(paths, a.caption or ("SVG: " + a.prompt), a.send, as_file=True)
    if extra:
        deliver(extra, "превью SVG", a.send)


def cmd_nobg(a):
    paths = single(NOBG, {"image_url": as_image_url(a.image, max_side=None)}, "nobg", "удаление фона", a.send)
    deliver(paths, a.caption or "без фона (PNG с прозрачностью)", a.send, as_file=True)


def cmd_upscale(a):
    payload = {"image_url": as_image_url(a.image, max_side=None), "desired_increase": a.x, "output_type": "png"}
    paths = single(UPSCALE, payload, "upscale", f"увеличение ×{a.x}", a.send)
    deliver(paths, a.caption or f"увеличено ×{a.x}", a.send, as_file=True)


def cmd_cost(a):
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - a.days * 86400))
    rows = []
    try:
        rows = [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]
    except FileNotFoundError:
        pass
    rows = [r for r in rows if r.get("ts", "") >= since]
    by = {}
    for r in rows:
        b = by.setdefault(r.get("model", "?"), [0, 0, 0.0, 0])
        b[0] += 1; b[1] += r.get("n", 0); b[2] += r.get("usd", 0.0); b[3] += 0 if r.get("ok") else 1
    print(f"Расход на картинки за {a.days} дн. (по прайсу fal; у GPT и моделей без прайса — оценка):")
    tot = 0.0
    for k, (calls, n, usd, fails) in sorted(by.items(), key=lambda kv: -kv[1][2]):
        tot += usd
        print(f"  {k:12s} запусков {calls:3d} · картинок {n:3d} · ${usd:6.2f} ≈{usd * USD_RUB:5.0f} ₽" +
              (f" · ошибок {fails}" if fails else ""))
    print(f"  ИТОГО ≈ ${tot:.2f} ≈ {tot * USD_RUB:.0f} ₽" if by else "  генераций не было")


def main():
    ap = argparse.ArgumentParser(description="Картинки через fal.ai — модель под задачу")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, model=True):
        if model:
            p.add_argument("--model", default=None, help="ключ модели, см. imggen.py models")
        p.add_argument("--n", type=int, default=1, help="сколько вариантов (1-4)")
        p.add_argument("--ar", default="auto", help="9:16 сторис, 1:1, 16:9, 4:5, 3:4 …")
        p.add_argument("--res", default="1k", choices=["1k", "2k"])
        p.add_argument("--q", default=None, choices=["low", "medium", "high"])
        p.add_argument("--caption", default="")
        p.add_argument("--send", action="store_true")

    sub.add_parser("models").set_defaults(func=cmd_models)
    g = sub.add_parser("gen"); g.add_argument("prompt"); common(g)
    g.add_argument("--transparent", action="store_true", help="прозрачный фон (gpt, gpt-max)")
    g.set_defaults(func=cmd_gen)
    e = sub.add_parser("edit"); e.add_argument("image"); e.add_argument("prompt")
    e.add_argument("--img", action="append"); common(e); e.set_defaults(func=cmd_edit)
    s = sub.add_parser("stories"); s.add_argument("image"); s.add_argument("prompt")
    s.add_argument("--img", action="append"); common(s); s.set_defaults(func=cmd_stories)
    c = sub.add_parser("compare"); c.add_argument("prompt"); c.add_argument("--models", required=True)
    common(c, model=False); c.set_defaults(func=cmd_compare)
    b = sub.add_parser("bench"); b.add_argument("prompt"); b.add_argument("--models", default="")
    common(b, model=False); b.set_defaults(func=cmd_bench)
    v = sub.add_parser("vector"); v.add_argument("prompt"); v.add_argument("--colors", default="")
    v.add_argument("--ar", default="1:1"); v.add_argument("--caption", default="")
    v.add_argument("--send", action="store_true"); v.set_defaults(func=cmd_vector)
    nb = sub.add_parser("nobg"); nb.add_argument("image"); nb.add_argument("--caption", default="")
    nb.add_argument("--send", action="store_true"); nb.set_defaults(func=cmd_nobg)
    up = sub.add_parser("upscale"); up.add_argument("image"); up.add_argument("--x", type=int, default=2, choices=[2, 4])
    up.add_argument("--caption", default=""); up.add_argument("--send", action="store_true")
    up.set_defaults(func=cmd_upscale)
    co = sub.add_parser("cost"); co.add_argument("--days", type=int, default=30); co.set_defaults(func=cmd_cost)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
