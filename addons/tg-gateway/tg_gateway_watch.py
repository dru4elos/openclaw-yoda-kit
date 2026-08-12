#!/usr/bin/env python3
"""🩺 Сторож шлюза Телеграма — ловит смерть сессии раньше, чем врач упрётся в неё в мини-аппе.

Проверяет /health. Ключ аннулируется, когда одним auth_key подключаются двое
(AuthKeyDuplicatedError) — тогда помогает только новый вход по QR с Мака,
поэтому шлём алерт Bot API (он от сессии не зависит) и пишем причину.
Алерт не чаще раза в час, чтобы не спамить.
"""
import json
import os
import time
import urllib.parse
import urllib.request

STATE = "/root/tg_gateway_data/watch_state.json"
GW = "http://127.0.0.1:8099/health"
QUIET_SEC = 3600


def keyenv(name: str) -> str:
    try:
        for line in open("/home/knee_bot/keys.env", encoding="utf-8"):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def notify(text: str) -> None:
    tok, admin = keyenv("BOT_TOKEN"), keyenv("ADMIN_ID")
    if not tok or not admin:
        return
    data = urllib.parse.urlencode({"chat_id": admin, "text": text}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=25).read()
    except Exception:
        pass



def _try_restart(reason: str) -> bool:
    """Рестарт службы шлюза при обрыве/зависании (НЕ помогает только при аннулированном ключе)."""
    import subprocess
    try:
        subprocess.run(["systemctl", "restart", "tg-gateway"], timeout=90, capture_output=True)
    except Exception:
        return False
    for _ in range(12):
        time.sleep(5)
        try:
            h = json.loads(urllib.request.urlopen(GW, timeout=20).read())
            if h.get("ok") and h.get("authorized"):
                notify(f"🔧 Шлюз завис ({reason[:120]}) — перезапустил, снова работает.")
                return True
        except Exception:
            continue
    return False


def main() -> None:
    try:
        health = json.loads(urllib.request.urlopen(GW, timeout=25).read())
    except Exception as exc:
        health = {"ok": False, "error": f"шлюз не отвечает: {exc}"}

    state = {}
    try:
        state = json.load(open(STATE))
    except Exception:
        pass

    if health.get("ok") and health.get("authorized"):
        if state.get("bad"):
            notify("✅ Телеграм-шлюз снова в строю — публикация работает.")
        json.dump({"bad": False, "ts": int(time.time())}, open(STATE, "w"))
        return

    err = str(health.get("error") or "не авторизован")
    dup = "two different IP" in err or "AuthKeyDuplicated" in err

    # Сначала пробуем починить сами: зависшее соединение лечится рестартом.
    # При аннулированном ключе рестарт бесполезен — сразу зовём доктора.
    if not dup and not state.get("restarted_recently"):
        if _try_restart(err):
            json.dump({"bad": False, "ts": int(time.time()),
                       "restarted_recently": True, "restart_ts": int(time.time())},
                      open(STATE, "w"))
            return
    # даём следующему запуску снова право на рестарт, если прошло >30 мин
    if state.get("restart_ts", 0) and time.time() - state["restart_ts"] > 1800:
        state["restarted_recently"] = False
    last = state.get("alert_ts", 0)
    if time.time() - last > QUIET_SEC:
        msg = ("🔴 Телеграм-шлюз не работает: сессия аннулирована — кто-то подключился "
               "тем же ключом мимо шлюза.\nНужен вход по QR с Мака."
               if dup else f"🔴 Телеграм-шлюз не отвечает.\n{err[:300]}")
        notify(msg)
        last = time.time()
    json.dump({"bad": True, "alert_ts": last, "err": err[:300],
               "restarted_recently": state.get("restarted_recently", False),
               "restart_ts": state.get("restart_ts", 0)}, open(STATE, "w"))


if __name__ == "__main__":
    main()
