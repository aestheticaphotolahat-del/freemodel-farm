#!/usr/bin/env python3
"""Provisioning sesi Telegram satu-kali per slot.

Langkah ini dijalankan SEKALI untuk tiap akun Telegram, DI LUAR loop farm:
hasilnya (session_string) ditempel ke config/telegram_accounts.json, dan
setelah itu seluruh verifikasi berjalan otomatis tanpa prompt.

  py -3.12 tg_session.py tg-001        # login + cetak session_string
  py -3.12 tg_session.py --status      # cek slot mana yang belum punya sesi

CATATAN: kode OTP dari Telegram pada login pertama tidak bisa diotomatiskan
oleh tools apa pun — itu batas yang dipasang Telegram. Setelah string sesi
tersimpan, tidak ada lagi langkah manual.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from farm import config as cfg


def cmd_status(pool) -> int:
    print(f"{'SLOT':<10} {'PHONE':<16} {'SESI'}")
    print("-" * 60)
    for slot in pool.slots:
        ok, reason = pool.slot_ready(slot)
        mark = "[siap]" if ok else "[BELUM]"
        print(f"{slot.slot_id:<10} {slot.phone:<16} {mark} {reason}")
    report = pool.preflight()
    if report["not_ready"]:
        print(f"\n[!] {len(report['not_ready'])} slot belum siap: "
              + ", ".join(report["not_ready"]))
        print("[*] Jalankan: py -3.12 tg_session.py <slot_id> untuk tiap slot itu.")
        return 1
    print(f"\n[+] Semua {len(report['ready'])} slot siap. Farm bisa jalan otomatis.")
    return 0


async def _provision(slot) -> int:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    file_path = cfg.SESSIONS_DIR / slot.session_name
    client = TelegramClient(str(file_path), slot.api_id, slot.api_hash)
    await client.start(phone=slot.phone)
    try:
        me = await client.get_me()
        handle = getattr(me, "username", None) or getattr(me, "id", "?")
        print(f"[+] Login sebagai @{handle}")
        print("\n===== TEMPEL string ini ke slot di config/telegram_accounts.json =====")
        print(StringSession.save(client.session))
        print("=====================================================================")
        return 0
    finally:
        await client.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provisioning sesi Telegram per slot (sekali).")
    parser.add_argument("slot_id", nargs="?", help="id slot, mis. tg-001")
    parser.add_argument("--status", action="store_true", help="tampilkan kesiapan sesi")
    args = parser.parse_args(argv)

    cfg.bootstrap_env()
    cfg.ensure_dirs()
    if not cfg.SLOTS_FILE.is_file():
        print(f"[-] {cfg.SLOTS_FILE.name} belum ada. Jalankan telegram-init dulu.")
        return 1

    pool = cfg.load_pool()
    if args.status or not args.slot_id:
        return cmd_status(pool)

    slot = pool.slot(args.slot_id)
    if slot is None:
        print(f"[-] Slot {args.slot_id} tidak ditemukan.")
        return 1
    if not slot.api_id or not slot.api_hash:
        print("[-] Isi blok 'app' (api_id/api_hash) di config dulu — lihat contoh.")
        return 1

    try:
        return asyncio.run(_provision(slot))
    except Exception as exc:  # noqa: BLE001
        print(f"[-] Provisioning gagal: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
