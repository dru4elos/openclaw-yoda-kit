#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Голос Йоды: синтез речи (piper, локально) и отправка голосового доктору в Telegram.

  say.py "текст"                 — озвучить и отправить доктору
  say.py --file /путь/текст.txt  — озвучить длинный текст из файла
  say.py "текст" --keep          — не отправлять, только сохранить .ogg и напечатать путь
"""
import argparse, os, re, subprocess, sys, tempfile
import requests

CFG = os.path.expanduser("~/.openclaw/openclaw.json")
OWNER = 123456789  # <-- ваш Telegram ID (узнать: @userinfobot)
VOICE = "/opt/piper-voices/ru_RU-dmitri-medium.onnx"
PIPER = os.path.expanduser("~/mailvenv/bin/piper")
MAX_CHARS = 2500

def token():
    s = open(CFG, encoding="utf-8").read()
    m = re.search(r'botToken"\s*:\s*"([0-9]+:[A-Za-z0-9_-]+)"', s)
    if not m:
        sys.exit("botToken не найден")
    return m.group(1)

def clean(t):
    """Убираем разметку — её не надо произносить."""
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"[*_#`>|]+", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "ссылка", t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{2,}", "\n", t).strip()

def synth(text):
    wav = tempfile.mktemp(suffix=".wav")
    ogg = tempfile.mktemp(suffix=".ogg")
    p = subprocess.run([PIPER, "-m", VOICE, "-f", wav], input=text.encode("utf-8"),
                       capture_output=True, timeout=300)
    if not os.path.exists(wav) or os.path.getsize(wav) < 1000:
        sys.exit("piper не синтезировал: " + p.stderr.decode()[-200:])
    subprocess.run(["ffmpeg", "-y", "-i", wav, "-c:a", "libopus", "-b:a", "32k", ogg],
                   capture_output=True, timeout=120)
    os.remove(wav)
    if not os.path.exists(ogg):
        sys.exit("ffmpeg не сконвертировал")
    return ogg

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--file")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    text = a.text
    if a.file and os.path.exists(a.file):
        text = open(a.file, encoding="utf-8").read()
    text = clean(text)
    if not text:
        sys.exit("нечего озвучивать")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(".", 1)[0] + "."
    ogg = synth(text)
    if a.keep:
        print(ogg)
        return
    with open(ogg, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{token()}/sendVoice",
                          data={"chat_id": OWNER}, files={"voice": ("voice.ogg", f)}, timeout=120)
    os.remove(ogg)
    j = r.json()
    if not j.get("ok"):
        sys.exit("Telegram: " + str(j.get("description"))[:200])
    print(f"✓ голосовое отправлено доктору ({len(text)} симв)")

main()
