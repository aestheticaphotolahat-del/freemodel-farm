#!/usr/bin/env python3
"""TUI interaktif freemodel-farm: menu opsi + dashboard live.

Semua opsi menulis ke config/farm_settings.json dan dipakai runner.
Klik `farm.cmd` untuk masuk ke sini; `f` = mulai farm (semua slot bebas),
`s` = berhenti setelah instance yang sedang jalan selesai.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from farm import config as cfg
from farm.client import FreeModelClient
from farm.keys import read_keys
from farm.router import RouterImporter
from farm.runner import FarmRunner, Lifecycle, auto_plan
from farm.settings import FarmSettings
from farm.sink import KeySink
from farm.store import AccountStore

SETTINGS_FILE = cfg.CONFIG_DIR / "farm_settings.json"

ESC = "\x1b["
C = {
    "reset": ESC + "0m", "bold": ESC + "1m", "dim": ESC + "2m",
    "red": ESC + "31m", "green": ESC + "32m", "yellow": ESC + "33m",
    "cyan": ESC + "36m", "gray": ESC + "90m",
}
WIDTH = 78


def _enable_ansi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # noqa: BLE001 - ANSI opsional, jangan pernah mematikan TUI
        pass


def paint(text: str, color: str) -> str:
    return f"{C[color]}{text}{C['reset']}"


def trunc(text: str, width: int) -> str:
    text = str(text)
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


class FarmTUI:
    def __init__(self) -> None:
        cfg.ensure_dirs()
        self.settings = FarmSettings(SETTINGS_FILE, root=PROJECT_ROOT)
        self.settings_path = SETTINGS_FILE
        self.lines: deque[str] = deque(maxlen=40)
        self.running = False
        self.report = None
        self.stop_requested = False

    # ------------------------------------------------------------ utilitas
    def _say(self, message: str) -> None:
        self.lines.append(message)

    def _pool(self):
        return cfg.load_pool()

    def _slot_summary(self) -> str:
        try:
            pool = self._pool()
        except Exception:  # noqa: BLE001
            return "slot tidak terbaca"
        total = len(pool.slots) if hasattr(pool, "slots") else 0
        free = len(pool.available_slots())
        return f"{total} slot · {free} bebas"

    def _keys_count(self) -> int:
        return len(read_keys(self.settings.abs_path("output.keysFile")))

    def _concurrency(self) -> int:
        return int(self.settings.get("worker.concurrency", 3))

    def _target(self) -> int:
        return int(self.settings.get("worker.target", 0))

    def _on_off(self, dotted: str) -> str:
        return paint("ON ", "green") if self.settings.get(dotted) else paint("OFF", "red")

    # --------------------------------------------------------------- menu
    def render_menu(self) -> None:
        s = self.settings
        print()
        print(paint("═" * WIDTH, "gray"))
        print(paint("  FREEMODEL-FARM", "bold") + paint("  — 1 akun = 1 Telegram", "dim"))
        print(paint("═" * WIDTH, "gray"))
        print(paint("  ─ Pengaturan ─", "bold"))
        rows = [
            ("1", "Proxy", f"{self._on_off('proxy.enabled')} "
                           f"({len(s.proxy_list())} di pool / {s.get('proxy.file')})"),
            ("2", "File proxy", str(s.get("proxy.file"))),
            ("3", "Concurrency", str(self._concurrency())),
            ("4", "Target akun", "0 = semua slot bebas" if self._target() == 0 else f"{self._target()} akun"),
            ("5", "Auto-import router", self._on_off("router.autoImport")),
            ("6", "Router", f"{s.get('router.baseUrl')} · node {s.get('router.nodeName')}"),
            ("7", "Output keys", str(s.get("output.keysFile")) + f"  ({self._keys_count()} key)"),
            ("8", "Output accounts", str(s.get("output.accountsFile"))),
            ("9", "Slot Telegram", f"{s.get('telegram.slotsFile')}  ({self._slot_summary()})"),
        ]
        for key, label, value in rows:
            print(f"  {paint(key, 'cyan')}. {label.ljust(20)} {trunc(value, WIDTH - 28)}")

        print(paint("  ─ Aksi ─", "bold"))
        print(f"  {paint('p', 'cyan')}. Preflight slot Telegram")
        print(f"  {paint('c', 'cyan')}. Cek katalog model live")
        print(f"  {paint('k', 'cyan')}. Tulis ulang keys.txt dari state")
        print(f"  {paint('f', 'green')}. ▶ START farm   (semua slot bebas, paralel)")
        print(f"  {paint('s', 'yellow')}. Stop    {paint('q', 'red')}. Keluar")
        print(paint("─" * WIDTH, "gray"))
        print(paint("  ketik pilihan lalu Enter", "gray"))

    def prompt_value(self, dotted: str, label: str, cast: Callable[[str], Any] = str,
                     minimum=None, maximum=None) -> None:
        current = self.settings.get(dotted)
        try:
            raw = input(f"  {label} [{current}]: ").strip()
        except EOFError:
            return
        if not raw:
            return
        try:
            value = cast(raw)
        except (TypeError, ValueError):
            print(paint(f"  [!] nilai tidak valid: {raw}", "red"))
            return
        if minimum is not None and value < minimum:
            print(paint(f"  [!] minimal {minimum}", "red"))
            return
        if maximum is not None and value > maximum:
            print(paint(f"  [!] maksimal {maximum}", "red"))
            return
        self.settings.set(dotted, value)
        print(paint(f"  [+] {dotted} = {value}", "green"))

    # -------------------------------------------------------------- aksi
    def preflight(self) -> None:
        print(paint("\n  Slot Telegram:", "bold"))
        try:
            pool = self._pool()
            report = pool.preflight()
        except Exception as exc:  # noqa: BLE001
            print(paint(f"  [!] gagal membaca pool: {exc}", "red"))
            return
        detail: dict[str, str] = report.get("detail", {})
        ready = set(report.get("ready", []))
        for slot_id, reason in detail.items():
            mark = paint("+", "green") if slot_id in ready else paint("-", "red")
            print(f"    {mark} {slot_id:<9} {reason}")
        if not detail:
            print(paint("    (belum ada slot di config)", "gray"))
        print(f"  siap={len(ready)} belum={len(report.get('not_ready', []))}")

    def show_models(self) -> None:
        settings = cfg.load_settings()
        client = FreeModelClient(base_url=settings.base_url, timeout=settings.timeout)
        try:
            models = client.list_models()
        finally:
            client.close()
        if not models:
            print(paint("  [!] katalog model tidak terbaca", "red"))
            return
        print(paint(f"\n  {len(models)} model live dari {settings.base_url}:", "bold"))
        for model in models[:15]:
            print(f"    · {model.get('id', '?')}")
        if len(models) > 15:
            print(paint(f"    … dan {len(models) - 15} lainnya", "dim"))

    def rebuild_keys(self) -> None:
        store = AccountStore(
            self.settings.abs_path("output.accountsFile"),
            cfg.EVENTS_FILE,
        )
        pairs = [(a.get("email", ""), a.get("api_key", "")) for a in store.accounts()]
        from farm.keys import write_keys

        count = write_keys(self.settings.abs_path("output.keysFile"), pairs)
        print(paint(f"  [+] {count} key ditulis ke {self.settings.get('output.keysFile')}", "green"))

    def start_farm(self) -> None:
        settings = cfg.load_settings()
        pool = self._pool()
        plan = auto_plan(pool)
        if plan is None:
            print(paint("  [!] tidak ada slot Telegram bebas.", "red"))
            print(paint("      tambah slot di config/telegram_accounts.json", "gray"))
            return

        free_slots, _ = plan
        target = self._target()
        count = free_slots if target == 0 else min(target, free_slots)
        concurrency = max(1, min(self._concurrency(), count))

        store = AccountStore(
            self.settings.abs_path("output.accountsFile"),
            cfg.EVENTS_FILE,
        )
        importer = None
        if self.settings.get("router.autoImport"):
            base_url, api_key = cfg.router_credentials()
            importer = RouterImporter(
                base_url,
                api_key,
                node_name=str(self.settings.get("router.nodeName")),
                log=self._say,
            )
        sink = KeySink(self.settings.abs_path("output.keysFile"), importer=importer)
        proxy_pool = self.settings.proxy_list() if self.settings.get("proxy.enabled") else []

        runner = FarmRunner(
            base_url=settings.base_url,
            pool=pool,
            store=store,
            verifier=cfg.build_verifier(settings),
            timeout=float(self.settings.get("worker.timeoutSec", 90)),
            retries=int(self.settings.get("worker.retries", 3)),
            test_model=settings.test_model,
            email_domain=settings.email_domain,
            sink=sink,
            proxy_pool=proxy_pool,
            verbose=self._say,
        )

        print(paint(f"\n  ▶ farm {count} akun · concurrency {concurrency} "
                    f"· target {settings.base_url}", "green"))
        if not proxy_pool:
            print(paint("  (proxy OFF — memakai IP bawaan)", "gray"))

        self.running = True
        self.report = None
        box: dict[str, object] = {}

        def work() -> None:
            try:
                box["report"] = runner.run(count=count, concurrency=concurrency)
            except Exception as exc:  # noqa: BLE001
                box["error"] = exc
            finally:
                self.running = False

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self._dashboard(thread)

        if "error" in box:
            print(paint(f"\n  [!] farm gagal: {box['error']}", "red"))
            return
        self.report = box.get("report")
        if self.report is not None:
            render_report(self.report)

    def _dashboard(self, thread: threading.Thread) -> None:
        """Tampilkan progres live sampai thread farm selesai."""
        start = time.time()
        while thread.is_alive():
            time.sleep(0.5)
            elapsed = int(time.time() - start)
            keys = self._keys_count()
            last = self.lines[-1] if self.lines else "…"
            sys.stdout.write(
                f"\r  {paint(str(elapsed).rjust(3) + 's', 'gray')} "
                f"{paint(str(keys) + ' key', 'green')} "
                f"{trunc(last, WIDTH - 22):<{WIDTH - 22}}"
            )
            sys.stdout.flush()
        sys.stdout.write("\r" + " " * (WIDTH - 1) + "\r")
        sys.stdout.flush()
        for line in list(self.lines)[-12:]:
            print("  " + trunc(line, WIDTH - 4))

    # --------------------------------------------------------------- loop
    def loop(self) -> int:
        while True:
            self.render_menu()
            try:
                choice = input(paint("  > ", "cyan")).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

            if choice in ("q", "quit", "keluar"):
                return 0
            if choice == "1":
                on = self.settings.toggle("proxy.enabled")
                print(paint(f"  [+] proxy {'ON' if on else 'OFF'}", "green"))
            elif choice == "2":
                self.prompt_value("proxy.file", "file proxy")
            elif choice == "3":
                self.prompt_value("worker.concurrency", "concurrency", int, 1, 50)
            elif choice == "4":
                self.prompt_value("worker.target", "target akun (0 = semua slot)", int, 0)
            elif choice == "5":
                on = self.settings.toggle("router.autoImport")
                print(paint(f"  [+] auto-import router {'ON' if on else 'OFF'}", "green"))
            elif choice == "6":
                self.prompt_value("router.baseUrl", "base URL router")
            elif choice == "7":
                self.prompt_value("output.keysFile", "file keys")
            elif choice == "8":
                self.prompt_value("output.accountsFile", "file accounts")
            elif choice == "9":
                self.prompt_value("telegram.slotsFile", "file slot Telegram")
            elif choice == "p":
                self.preflight()
            elif choice == "c":
                self.show_models()
            elif choice == "k":
                self.rebuild_keys()
            elif choice == "f":
                self.start_farm()
            elif choice == "s":
                print(paint("  [i] stop berlaku untuk instance berikutnya; "
                            "yang sedang jalan diselesaikan", "yellow"))
            else:
                print(paint(f"  [!] pilihan tidak dikenal: {choice}", "red"))


def render_report(report) -> None:
    summary = report.summary()
    print()
    print(paint("═" * WIDTH, "gray"))
    print(paint(" RINGKASAN", "bold"))
    print(paint("═" * WIDTH, "gray"))
    for key in ("requested", "success", "failed", "concurrency", "slots_used"):
        print(f"  {key:<13}: {summary.get(key)}")
    print()
    print(f"  {'EMAIL':<34} {'SLOT':<9} {'STATE':<20} KEY")
    print(paint("  " + "-" * (WIDTH - 2), "gray"))
    for row in report.results:
        color = "green" if row.ok else "red"
        key = (row.api_key[:10] + "…") if row.api_key else "-"
        print(f"  {trunc(row.email, 34):<34} {row.telegram_slot:<9} "
              f"{paint(trunc(row.state, 20), color):<29} {key}")


def main() -> int:
    _enable_ansi()
    tui = FarmTUI()
    try:
        return tui.loop()
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
