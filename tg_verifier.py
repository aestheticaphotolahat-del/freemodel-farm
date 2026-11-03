#!/usr/bin/env python3
"""
Telegram Auto-Verifier for FreeModel.in using Telethon MTProto Client.
Automates sending `/start verify_<code_id>` to @FreeModels_bbot.
"""

import os
import sys
import asyncio
from telethon import TelegramClient

API_ID = int(os.environ.get("TG_API_ID", "0"))
API_HASH = os.environ.get("TG_API_HASH", "")
SESSION_NAME = os.environ.get("TG_SESSION", "freemodel_tg")

async def verify_telegram_code(verify_code: str):
    bot_username = "FreeModels_bbot"
    print(f"[*] Menghubungi bot @{bot_username} dengan code: {verify_code}...")
    
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
        # Mengirim payload start langsung ke bot Telegram
        await client.send_message(bot_username, f"/start verify_{verify_code}")
        print(f"[+] Pesan verifikasi terkirim ke @{bot_username}!")
        await asyncio.sleep(3)
        
        # Baca balasan konfirmasi dari bot
        async for msg in client.iter_messages(bot_username, limit=1):
            print(f"[+] Respons bot: {msg.text}")
            return True
    return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py -3.12 tg_verifier.py <VERIFY_CODE>")
        sys.exit(1)

    code = sys.argv[1].replace("verify_", "")
    if not API_ID or not API_HASH:
        print("[-] Set TG_API_ID dan TG_API_HASH di environment atau .env terlebih dahulu.")
        sys.exit(1)

    asyncio.run(verify_telegram_code(code))
