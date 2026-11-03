#!/usr/bin/env python3
"""CLI freemodel-farm: registrasi multi-instance, 1 akun = 1 akun Telegram.

Contoh:
  py -3.12 freemodel_farmer.py pool
  py -3.12 freemodel_farmer.py telegram-init 3
  py -3.12 freemodel_farmer.py run --count 5 --concurrency 5
  py -3.12 freemodel_farmer.py run --count 5 --dry-run
  py -3.12 freemodel_farmer.py report
  py -3.12 freemodel_farmer.py models
"""

from __future__ import annotations

import argparse
import json
import sys

if sys.version_info < (3, 10):
    print("[-] Butuh Python 3.10+. Jalankan dengan: py -3.12 freemodel_farmer.py")
    sys.exit(1)

from farm import config as cfg
from farm.client import FreeModelClient
from farm.router import RouterImporter
from farm.runner import FarmRunner, Lifecycle, auto_plan
from farm.settings import FarmSettings
from farm.sink import KeySink
from farm.store import AccountRecord, AccountStore, read_json
from farm.telegram import dump_slots_template


def cmd_pool(_args: argparse.Namespace) -> int:
    cfg.ensure_dirs()
    if not cfg.SLOTS_FILE.is_file():
        print(f"[-] Belum ada {cfg.SLOTS_FILE}")
        print("[*] Jalankan: py -3.12 freemodel_farmer.py telegram-init <jumlah>")
        return 1
    pool = cfg.load_pool()
    summary = pool.summary()
    bound = pool.bound_emails()
    report = pool.preflight()
    print("=" * 64)
    print(" STATUS POOL TELEGRAM (aturan: 1 akun freemodel = 1 akun Telegram)")
    print("=" * 64)
    print(f" total slot  : {summary['total']}")
    print(f" terikat     : {summary['bound']}")
    print(f" ter-reserve : {summary['reserved']}")
    print(f" bebas       : {summary['free']}")
    print(f" siap sesi   : {len(report['ready'])}   belum siap: {len(report['not_ready'])}")
    for slot in pool.slots:
        ok, reason = pool.slot_ready(slot)
        bound_to = bound.get(slot.slot_id, "-")
        mark = "siap" if ok else "BELUM"
        print(f"   {slot.slot_id:<9} {mark:<6} {reason:<34} -> {bound_to}")
    if report["not_ready"]:
        print("\n[*] Lengkapi sesi: py -3.12 tg_session.py <slot_id>")
    return 0


def cmd_preflight(_args: argparse.Namespace) -> int:
    """Cek kesiapan semua slot sebelum farm; keluar non-nol bila ada yang belum."""
    cfg.ensure_dirs()
    if not cfg.SLOTS_FILE.is_file():
        print(f"[-] {cfg.SLOTS_FILE.name} belum ada. Jalankan telegram-init dulu.")
        return 1
    pool = cfg.load_pool()
    report = pool.preflight()
    for slot_id in report["ready"]:
        print(f"[+] {slot_id:<9} siap — {report['detail'][slot_id]}")
    for slot_id in report["not_ready"]:
        print(f"[-] {slot_id:<9} belum siap — {report['detail'][slot_id]}")
    print(f"\n siap={len(report['ready'])} belum={len(report['not_ready'])}")
    return 0 if not report["not_ready"] else 1


def cmd_telegram_init(args: argparse.Namespace) -> int:
    cfg.ensure_dirs()
    dump_slots_template(cfg.SLOTS_FILE, count=args.count)
    print(f"[+] Template {args.count} slot dibuat: {cfg.SLOTS_FILE}")
    print("[*] Isi blok 'app' (api_id/api_hash dari my.telegram.org) di file itu.")
    print("[*] Lalu provisioning sesi tiap slot: py -3.12 tg_session.py tg-001")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg.ensure_dirs()
    settings = cfg.load_settings()
    if not cfg.SLOTS_FILE.is_file():
        print(f"[-] {cfg.SLOTS_FILE.name} belum ada. Jalankan 'telegram-init' dulu.")
        return 1

    if settings.mock_mode:
        print("[!] FM_MOCK_MODE aktif — target mock, bukan freemodel.in")

    pool = cfg.load_pool()

    if args.auto:
        plan = auto_plan(pool)
        if plan is None:
            print("[-] Mode 1-klik: tidak ada slot Telegram bebas (semua sudah terikat).")
            print("[*] Tambah slot di config/telegram_accounts.json lalu jalankan lagi,\n"
                  "    atau pakai --count/--concurrency untuk mode manual.")
            return 1
        args.count, args.concurrency = plan
        print(f"[*] Mode 1-klik: {args.count} slot bebas -> farm {args.count} akun "
              f"sekaligus (concurrency={args.concurrency})")

    if not settings.mock_mode and not args.dry_run:
        report = pool.preflight()
        if report["not_ready"]:
            print(f"[-] {len(report['not_ready'])} slot belum punya sesi Telegram: "
                  + ", ".join(report["not_ready"]))
            print("[*] Lengkapi dulu: py -3.12 tg_session.py <slot_id>")
            return 1

    farm_settings = FarmSettings(cfg.FARM_SETTINGS_FILE, root=cfg.PROJECT_ROOT)
    target = int(farm_settings.get("worker.target", 0))
    if not args.auto and target > 0 and args.count == 1:
        args.count = target
    if args.auto:
        plan = auto_plan(pool)
        if plan is None:
            print("[-] Mode 1-klik: tidak ada slot Telegram bebas (semua sudah terikat).")
            return 1
        args.count, args.concurrency = plan

    output_accounts = farm_settings.abs_path("output.accountsFile")
    output_keys = farm_settings.abs_path("output.keysFile")
    store = AccountStore(output_accounts, cfg.EVENTS_FILE)
    importer = None
    if farm_settings.get("router.autoImport"):
        router_url, router_key = cfg.router_credentials()
        importer = RouterImporter(
            router_url,
            router_key,
            node_name=str(farm_settings.get("router.nodeName", "FreeModel")),
            log=print,
        )
    sink = KeySink(output_keys, importer=importer)
    proxy_pool = farm_settings.proxy_list() if farm_settings.get("proxy.enabled") else []
    runner = FarmRunner(
        base_url=settings.base_url,
        pool=pool,
        store=store,
        verifier=cfg.build_verifier(settings),
        timeout=float(farm_settings.get("worker.timeoutSec", settings.timeout)),
        retries=int(farm_settings.get("worker.retries", settings.retries)),
        test_model=settings.test_model,
        email_domain=settings.email_domain,
        sink=sink,
        proxy_pool=proxy_pool,
        verbose=print,
    )
    report = runner.run(count=args.count, concurrency=args.concurrency, dry_run=args.dry_run)


    print("\n" + "=" * 64)
    print(" RINGKASAN")
    print("=" * 64)
    for key, value in report.summary().items():
        print(f" {key:<13}: {value}")
    print(f" accounts file: {output_accounts}")
    print(f" keys file    : {output_keys}")
    print(f" event log    : {cfg.EVENTS_FILE}")
    return 0 if report.failed == 0 else 2


def cmd_tui(_args: argparse.Namespace) -> int:
    """Buka UI interaktif: menu opsi + dashboard live."""
    import farm_tui

    return farm_tui.main()


def cmd_report(args: argparse.Namespace) -> int:
    farm_settings = FarmSettings(cfg.FARM_SETTINGS_FILE, root=cfg.PROJECT_ROOT)
    accounts_path = farm_settings.abs_path("output.accountsFile")
    store = AccountStore(accounts_path, cfg.EVENTS_FILE)
    accounts = store.accounts()
    if not accounts:
        print(f"[*] Belum ada akun tercatat di {accounts_path}")
        return 0
    if args.json:
        print(json.dumps(accounts, indent=2, ensure_ascii=False))
        return 0
    print(f"{'EMAIL':<34} {'SLOT':<10} {'STATE':<22} {'API KEY':<14}")
    print("-" * 84)
    for acc in sorted(accounts, key=lambda a: a.get("created_at", "")):
        key = acc.get("api_key") or "-"
        masked = (key[:8] + "...") if len(key) > 11 else key
        print(f"{acc.get('email',''):<34} {acc.get('telegram_slot',''):<10} "
              f"{acc.get('state',''):<22} {masked:<14}")
    done = sum(1 for a in accounts if a.get("state") in (Lifecycle.DONE, Lifecycle.KEY_TESTED))
    failed = sum(1 for a in accounts if a.get("state") == Lifecycle.FAILED)
    print(f"\n total={len(accounts)} selesai={done} gagal={failed}")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    settings = cfg.load_settings()
    client = FreeModelClient(base_url=settings.base_url, timeout=30)
    try:
        models = client.list_models()
    finally:
        client.close()
    if not models:
        print("[-] Gagal mengambil katalog model.")
        return 1
    print(f"[+] {len(models)} model tersedia:\n")
    print(f"{'ID MODEL':<32} {'NAMA':<28} {'CONTEXT':>9}")
    print("-" * 74)
    for model in models[: args.limit]:
        print(f"{str(model.get('id','')):<32} {str(model.get('name','')):<28} "
              f"{str(model.get('context_window','-')):>9}")
    return 0


def cmd_migrate(_args: argparse.Namespace) -> int:
    """Pindahkan freemodel_accounts.json lama ke state/accounts.json."""
    cfg.ensure_dirs()
    legacy = read_json(cfg.LEGACY_ACCOUNTS_FILE, [])
    if not isinstance(legacy, list) or not legacy:
        print("[*] Tidak ada akun lama untuk dimigrasi.")
        return 0
    store = AccountStore(cfg.ACCOUNTS_FILE, cfg.EVENTS_FILE)
    imported = 0
    for index, old in enumerate(legacy, start=1):
        email = old.get("email", "")
        if not email or store.find_by_email(email):
            continue
        record = AccountRecord(
            account_id=f"legacy_{index:03d}",
            email=email,
            name=old.get("name", ""),
            password=old.get("password", ""),
            telegram_slot="unmapped",
            telegram_verify_code=old.get("telegram_verify_code", "") or "",
            telegram_bot_url=old.get("telegram_bot_url", "") or "",
            api_key=old.get("api_key", "") or "",
            state=Lifecycle.DONE,
            created_at=old.get("created_at", ""),
        )
        store.upsert(record)
        store.log_event(record.account_id, Lifecycle.DONE, "migrasi dari freemodel_accounts.json")
        imported += 1
    print(f"[+] {imported} akun lama dimigrasi ke {cfg.ACCOUNTS_FILE}")
    print("[!] Akun legacy belum punya slot Telegram — petakan manual bila mau dilanjutkan.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="freemodel_farmer.py",
        description="Farm akun freemodel.in multi-instance dengan pemetaan 1 akun = 1 Telegram.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("pool", help="tampilkan status pool akun Telegram").set_defaults(func=cmd_pool)

    sub.add_parser(
        "preflight", help="cek kesiapan sesi Telegram semua slot sebelum farm"
    ).set_defaults(func=cmd_preflight)

    p_init = sub.add_parser("telegram-init", help="buat template daftar akun Telegram")
    p_init.add_argument("count", type=int, nargs="?", default=1)
    p_init.set_defaults(func=cmd_telegram_init)

    p_run = sub.add_parser("run", help="jalankan registrasi multi-instance")
    p_run.add_argument("--count", type=int, default=1, help="jumlah akun yang dibuat")
    p_run.add_argument("--concurrency", type=int, default=1, help="instance paralel")
    p_run.add_argument("--auto", action="store_true",
                       help="mode 1-klik: pakai semua slot bebas sekaligus (count=concurrency)")
    p_run.add_argument("--dry-run", action="store_true", help="uji alur tanpa menyentuh target")
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="tampilkan akun yang sudah tercatat")
    p_report.add_argument("--json", action="store_true")
    p_report.set_defaults(func=cmd_report)

    p_models = sub.add_parser("models", help="ambil katalog model dari target")
    p_models.add_argument("--limit", type=int, default=50)
    p_models.set_defaults(func=cmd_models)

    sub.add_parser("migrate", help="impor freemodel_accounts.json lama").set_defaults(
        func=cmd_migrate
    )

    sub.add_parser("tui", help="UI interaktif: menu opsi + dashboard live").set_defaults(
        func=cmd_tui
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg.bootstrap_env()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
