#!/usr/bin/env python3
"""modelcheck — что у провайдера РЕАЛЬНО работает, а не что он обещает в каталоге.

  modelcheck.py check [--notify]   # опросить и сообщить доктору об изменениях
  modelcheck.py list               # таблица текущего состояния
  modelcheck.py watch <модель>...  # добавить модель в список наблюдения

Повод (02.09.2026): в каталоге excash появилась gemini-3.8-flash, но на любой
запрос она отдавала «Unable to select a model» — объявлена, но не подключена.
Каталогу верить нельзя, надо дёргать каждую модель настоящим запросом.

Различаем четыре состояния, и это разные действия:
  работает            — можно ставить в конфиг
  не подключена       — ждать провайдера («Unable to select a model»)
  нужен доступ        — просить у провайдера («не разрешена для SK-ключа»)
  лежит               — временный сбой (500 или ошибка в потоке)
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

ENV_FILE = "/home/openclaw/.openclaw/.env"
CFG = "/home/openclaw/.openclaw/openclaw.json"
STATE = "/home/openclaw/.openclaw/workspace/memory/model_status.json"
OWNER_ID = "123456789"   # свой telegram id
UPSTREAM = "https://<ваш-агрегатор>/v1"      # напрямую, мимо сторожа: нужен сырой ответ


def _env(name):
    try:
        for line in open(ENV_FILE, encoding="utf-8"):
            m = re.match(r"\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)", line)
            if m and m.group(1) == name:
                return m.group(2).strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _tg(text):
    """Сообщение доктору голосом бота. Best-effort: сбой не ломает проверку."""
    try:
        cfg = open(CFG, encoding="utf-8").read()
        m = re.search(r'"botToken"\s*:\s*"([^"]+)"', cfg)
        if not m:
            return False
        body = json.dumps({"chat_id": OWNER_ID, "text": text[:3800]}).encode()
        req = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendMessage" % m.group(1),
            data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception:
        return False


def catalog(key):
    req = urllib.request.Request(UPSTREAM + "/models",
                                 headers={"Authorization": "Bearer " + key})
    r = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    return sorted(m.get("id", "") for m in (r.get("data") or []) if m.get("id"))


def probe(key, model):
    """Настоящий запрос. Возвращает (состояние, подробность, секунды)."""
    body = json.dumps({"model": model, "max_tokens": 5,
                       "messages": [{"role": "user", "content": "ок"}]}).encode()
    req = urllib.request.Request(UPSTREAM + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    t = time.time()
    try:
        raw = urllib.request.urlopen(req, timeout=90).read().decode()
        dt = round(time.time() - t, 1)
        j = json.loads(raw)
        if j.get("error"):
            return "лежит", str(j["error"])[:120], dt
        return "работает", "", dt
    except Exception as e:
        dt = round(time.time() - t, 1)
        body_txt = ""
        try:
            body_txt = e.read().decode()[:300]
        except Exception:
            body_txt = str(e)[:200]
        low = body_txt.lower()
        if "unable to select a model" in low:
            return "не подключена", "объявлена в каталоге, но не роутится", dt
        if "model_not_available" in low or "не разрешена" in low:
            return "нужен доступ", "существует, но закрыта для ключа", dt
        if "internal error" in low or " 500" in str(e):
            return "лежит", "internal error у провайдера", dt
        return "ошибка", body_txt[:120], dt


def load_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def save_state(st):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
    except Exception:
        pass


def cmd_check(a):
    key = _env("EXCASH_API_KEY")
    if not key:
        sys.exit("нет EXCASH_API_KEY")
    try:
        models = catalog(key)
    except Exception as e:
        sys.exit("каталог недоступен: %s" % type(e).__name__)

    # Модели, которых в каталоге нет, но мы знаем, что они существуют:
    # gemini-3.8-flash-tiered отвечает «не разрешена для SK-ключа» — значит есть,
    # просто закрыта. В каталоге такие не показываются, а следить за ними надо.
    EXTRA = ["gemini-3.8-flash-tiered", "gemini-3.8-pro"]
    for e in EXTRA:
        if e not in models:
            models.append(e)
    models = sorted(models)

    st = load_state()
    prev = st.get("models", {})
    now, changes, new_models = {}, [], []
    for m in models:
        state, detail, dt = probe(key, m)
        now[m] = {"state": state, "detail": detail, "sec": dt}
        old = (prev.get(m) or {}).get("state")
        if old is None:
            new_models.append(m)
            if state == "работает":
                changes.append("🆕 %s — уже работает (%.0f с)" % (m, dt))
        elif old != state:
            changes.append("%s %s: было «%s» → стало «%s»"
                           % ("✅" if state == "работает" else "⚠️", m, old, state))
        time.sleep(0.3)

    st = {"checked": time.strftime("%Y-%m-%d %H:%M"), "models": now}
    save_state(st)

    work = [m for m, v in now.items() if v["state"] == "работает"]
    print("проверено %d моделей, работают %d, изменений %d"
          % (len(models), len(work), len(changes)))
    for c in changes:
        print("  " + c)
    if not changes:
        print("  без изменений")

    if a.notify and changes:
        watched = st.get("watch") or []
        lines = ["🔍 Модели у провайдера — изменения:", ""] + changes
        interesting = [c for c in changes if "работает" in c]
        if interesting:
            lines += ["", "Если нужна в основные — скажи, переключу."]
        _tg("\n".join(lines))
    return 0


def cmd_list(a):
    st = load_state()
    if not st:
        sys.exit("состояние ещё не собрано — запусти check")
    print("Проверено: %s\n" % st.get("checked"))
    order = {"работает": 0, "нужен доступ": 1, "не подключена": 2, "лежит": 3, "ошибка": 4}
    rows = sorted(st.get("models", {}).items(),
                  key=lambda kv: (order.get(kv[1]["state"], 9), kv[0]))
    for m, v in rows:
        d = (" — " + v["detail"]) if v.get("detail") else ""
        sec = (" %.0fс" % v["sec"]) if v.get("sec") and v["state"] == "работает" else ""
        print("  %-26s %-14s%s%s" % (m, v["state"], sec, d))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--notify", action="store_true")
    sub.add_parser("list")
    a = ap.parse_args()
    {"check": cmd_check, "list": cmd_list}[a.cmd](a)


if __name__ == "__main__":
    main()
