#!/usr/bin/env python3
"""Verifikasi satu kode Telegram memakai SATU slot akun Telegram tertentu.

Runner utama memanggil verifier langsung (farm/verifier.py); skrip ini untuk
verifikasi manual / recovery tanpa menjalankan registrasi ulang.

Pemakaian:
  py -3.12 tg_verifier.py fm_18708_c772fa41 --slot tg-001
"""

from __future__ import annotations

import argparse
import sys

from farm import config as cfg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verifikasi kode Telegram pada satu slot.")
    parser.add_argument("code", help="kode verifikasi, mis. fm_18708_c772fa41")
    parser.add_argument("--slot", required=False, help="slot Telegram, mis. tg-001")
    args = parser.parse_args(argv)

    cfg.bootstrap_env()
    settings = cfg.load_settings()
    if not cfg.SLOTS_FILE.is_file():
        print(f"[-] {cfg.SLOTS_FILE} belum ada. Jalankan telegram-init dulu.")
        return 1

    pool = cfg.load_pool()
    slot = pool.slot(args.slot) if args.slot else (pool.available_slots() or [None])[0]
    if slot is None:
        print("[-] Slot tidak ditemukan / pool habis. Pakai --slot atau tambah akun Telegram.")
        return 1

    binding = pool.binding_for(slot.slot_id) or {}
    bound_code = binding.get("verify_code", "")
    if binding.get("email") and bound_code not in ("", args.code):
        print(f"[-] Slot {slot.slot_id} sudah terikat ke {binding['email']} "
              f"(kode {bound_code}). Aturan 1:1 menolak pemakaian ulang.")
        return 3

    print(f"[*] Slot {slot.slot_id} ({slot.phone}) -> kode {args.code}")
    verifier = cfg.build_verifier(settings)
    outcome = verifier.verify(slot, args.code)
    if not outcome.ok:
        print(f"[-] Gagal: {outcome.detail}")
        if outcome.raw_reply:
            print(f"    balasan bot: {outcome.raw_reply}")
        return 2

    print(f"[+] Berhasil: {outcome.detail}")
    if outcome.raw_reply:
        print(f"    balasan bot: {outcome.raw_reply}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
