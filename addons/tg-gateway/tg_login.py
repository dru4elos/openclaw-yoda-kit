#!/usr/bin/env python3
"""🔑 ЕДИНЫЙ ЛОГИН TELEGRAM для всех сервисов vps (30.07.2026).

Одна команда чинит любую сессию:
    ssh -t vps /root/tradebot/venv/bin/python3 /root/tg_login.py studio
    ssh -t vps /root/tradebot/venv/bin/python3 /root/tg_login.py copier
    ssh -t vps /root/tradebot/venv/bin/python3 /root/tg_login.py brief

Координаты берутся ТОЛЬКО из /root/telethon.env (единый паспорт).
Транспорт: WARP-socks (прямой коннект к DC заблокирован РКН с 25.07.2026).
"""
import asyncio
import os
import shutil
import subprocess
import sys
import time

ENV = "/root/telethon.env"


def cfg() -> dict:
    d = {}
    try:
        for line in open(ENV):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip().strip('"')
    except FileNotFoundError:
        sys.exit(f"нет паспорта {ENV}")
    return d


C = cfg()
SERVICES = {
    "studio":   (C.get("TG_SESSION_STUDIO", "/home/knee_bot/publish_session"), "content-studio"),
    "copier":   (C.get("TG_SESSION_COPIER", "/root/ng_meta_paper/copier_session"), "signal-copier"),
    "brief":    (C.get("TG_SESSION_BRIEF", "/root/ng_meta_paper/brief_session"), ""),
    "antispam": (C.get("TG_SESSION_ANTISPAM", "/root/tradebot/antispam_bot"), "antispam-guard"),
}


def proxy_tuple():
    raw = C.get("TG_PROXY", "socks5://127.0.0.1:40111")
    if not raw or raw.lower() in ("none", "direct", ""):
        return None
    from urllib.parse import urlparse
    u = urlparse(raw)
    return ("socks5" if "socks" in (u.scheme or "") else "http", u.hostname, u.port)


async def main():
    which = (sys.argv[1] if len(sys.argv) > 1 else "studio").lower()
    if which not in SERVICES:
        sys.exit(f"сервис '{which}'? доступны: {', '.join(SERVICES)}")
    sess, unit = SERVICES[which]
    px = proxy_tuple()
    print(f"🔑 Сессия «{which}» -> {sess}.session")
    print(f"   транспорт: {'WARP ' + str(px[1]) + ':' + str(px[2]) if px else 'ПРЯМОЙ'}")

    if unit:
        print(f"   останавливаю {unit}, чтобы никто не держал сессию…")
        subprocess.run(["systemctl", "stop", unit], capture_output=True, timeout=60)

    # битую сессию в сторону (не удаляем — вдруг пригодится)
    if os.path.exists(sess + ".session"):
        bak = f"{sess}.session.dead_{int(time.time())}"
        shutil.move(sess + ".session", bak)
        print(f"   старая сессия отложена: {os.path.basename(bak)}")

    from telethon import TelegramClient
    from telethon.network.connection.tcpabridged import ConnectionTcpAbridged
    cl = TelegramClient(sess, int(C["TG_API_ID"]), C["TG_API_HASH"],
                        connection=ConnectionTcpAbridged, proxy=px,
                        connection_retries=2, timeout=25)
    await asyncio.wait_for(cl.connect(), 40)
    print("   ✅ соединение с Telegram установлено")

    phone = input(f"\n📱 Номер аккаунта [{C.get('TG_PHONE','')}]: ").strip() or C.get("TG_PHONE", "")
    await cl.send_code_request(phone)
    print("   код отправлен в Telegram (ищи сообщение от Telegram, не SMS)")
    code = input("🔢 Код из Telegram: ").strip()
    try:
        await cl.sign_in(phone, code)
    except Exception as exc:
        if "password" in str(exc).lower() or "2fa" in str(exc).lower():
            pw = input("🔒 Пароль двухфакторки: ")
            await cl.sign_in(password=pw)
        else:
            raise
    me = await cl.get_me()
    print(f"\n✅ ГОТОВО: сессия «{which}» жива — @{getattr(me, 'username', None)} (id {me.id})")
    await cl.disconnect()

    if unit:
        subprocess.run(["systemctl", "start", unit], capture_output=True, timeout=60)
        print(f"   {unit} запущен обратно")
    print("\nПроверить в любой момент:  ssh vps '/root/tradebot/venv/bin/python3 /root/tg_check.py'")


if __name__ == "__main__":
    asyncio.run(main())
