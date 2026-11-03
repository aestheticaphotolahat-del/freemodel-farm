#!/usr/bin/env python3
"""Login interaktif satu slot Telegram (membuat session MTProto per slot).

Satu slot = satu file session di telegram_sessions/. Ini yang membuat aturan
"1 akun freemodel = 1 akun Telegram" benar-benar bisa ditegakkan.

Pemakaian:
  py -3.12 tg_login.py tg-001
  py -3.12 tg_login.py --all
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from farm import config as cfg


def _sessions_status() -> int:
    cfg.ensure_dirs()
    if not cfg.SLOTS_FILE.is_file():
        print(f"[-] {cfg.SLOTS_FILE} belum ada. Jalankan telegram-init dulu.")
        return 1
    pool = cfg.load_pool()
    print(f"{'SLOT':<10} {'PHONE':<18} {'SESSION FILE':<24} {'STATUS'}")
    print("-" * 72)
    for slot in pool.slots:
        path = cfg.SESSIONS_DIR / f"{slot.session_name}.session"
        status = "login" if path.is_file() else "belum login"
        print(f"{slot.slot_id:<10} {slot.phone:<18} {path.name:<24} {status}")
    return 0


async def _login(slot) -> bool:
    from telethon import TelegramClient

    if not slot.api_id or not slot.api_hash:
        print(f"[-] {slot.slot_id}: api_id/api_hash kosong di {cfg.SLOTS_FILE.name}")
        return False

    cfg.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(cfg.SESSIONS_DIR / slot.session_name), slot.api_id, slot.api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"[=] {slot.slot_id} sudah login sebagai @{getattr(me, 'username', None) or me.id}")
            return True
        print(f"[*] {slot.slot_id}: login {slot.phone} (Telegram akan minta kode OTP)")
        await client.start(phone=slot.phone)
        me = await client.get_me()
        print(f"[+] {slot.slot_id} login sebagai @{getattr(me, 'username', None) or me.id}")
        return True
    finally:
        await client.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Login sesi Telegram per slot.")
    parser.add_argument("slot_id", nargs="?", help="id slot, mis. tg-001")
    parser.add_argument("--all", action="store_true", help="login semua slot yang belum punya sesi")
    parser.add_argument("--status", action="store_true", help="tampilkan status sesi")
    args = parser.parse_args(argv)

    cfg.bootstrap_env()
    if args.status or (not args.slot_id and not args.all):
        return _sessions_status()

    pool = cfg.load_pool()
    if args.all:
        targets = list(pool.slots)
    else:
        single = pool.slot(args.slot_id or "")
        if single is None:
            print(f"[-] Slot {args.slot_id} tidak ditemukan di {cfg.SLOTS_FILE}")
            return 1
        targets = [single]

    failures = 0
    for slot in targets:
        try:
            ok = asyncio.run(_login(slot))
        except Exception as exc:  # noqa: BLE001
            print(f"[-] {slot.slot_id} gagal login: {exc}")
            ok = False
        failures += 0 if ok else 1
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
