"""Orkestrator farm multi-instance dengan lifecycle yang bisa diaudit.

Satu instance = satu persona + satu requests.Session + satu slot Telegram.
Aturan 1 akun freemodel = 1 akun Telegram ditegakkan di lapisan pool, bukan
sekadar konvensi, sehingga worker paralel tidak bisa melanggarnya.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .client import FreeModelClient
from .identity import Persona, build_persona
from .store import AccountRecord, AccountStore
from .telegram import DuplicateBindingError, TelegramPool, TelegramSlot
from .verifier import VerifyOutcome, Verifier


class Lifecycle:
    LEASED = "telegram_leased"
    REGISTERED = "registered"
    CODE_FOUND = "verify_code_extracted"
    AWAITING_CODE = "awaiting_verify_code"
    TG_VERIFIED = "telegram_verified"
    KEY_TESTED = "api_key_tested"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class InstanceResult:
    index: int
    account_id: str
    email: str
    telegram_slot: str
    state: str
    ok: bool
    detail: str = ""
    api_key: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "account_id": self.account_id,
            "email": self.email,
            "telegram_slot": self.telegram_slot,
            "state": self.state,
            "ok": self.ok,
            "detail": self.detail,
            "api_key": self.api_key,
        }


@dataclass
class RunReport:
    results: list[InstanceResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    concurrency: int = 1

    @property
    def success(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    def summary(self) -> dict[str, Any]:
        return {
            "requested": len(self.results),
            "success": self.success,
            "failed": self.failed,
            "concurrency": self.concurrency,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "slots_used": sorted({r.telegram_slot for r in self.results if r.ok}),
        }


class FarmRunner:
    """Jalankan N registrasi paralel dengan isolasi penuh per instance."""

    def __init__(
        self,
        base_url: str,
        pool: TelegramPool,
        store: AccountStore,
        verifier: Verifier,
        timeout: float = 90.0,
        retries: int = 3,
        test_model: str = "claude-mythos-preview",
        email_domain: str = "gmail.com",
        verbose: Callable[[str], None] | None = None,
    ) -> None:
        self.base_url = base_url
        self.pool = pool
        self.store = store
        self.verifier = verifier
        self.timeout = timeout
        self.retries = retries
        self.test_model = test_model
        self.email_domain = email_domain
        self._say = verbose or (lambda _msg: None)
        self._print_lock = threading.Lock()

    def _log(self, message: str) -> None:
        with self._print_lock:
            self._say(message)

    # ------------------------------------------------------------------- run
    def run(self, count: int, concurrency: int = 1, dry_run: bool = False) -> RunReport:
        concurrency = max(1, min(concurrency, count))
        report = RunReport(
            started_at=datetime.now(timezone.utc).isoformat(),
            concurrency=concurrency,
        )
        self._log(f"[*] Memulai {count} instance, konkurensi={concurrency}, target={self.base_url}")

        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="farm") as pool:
            futures: list[Future[InstanceResult]] = [
                pool.submit(self._run_one, index, dry_run) for index in range(1, count + 1)
            ]
            for future in futures:
                try:
                    report.results.append(future.result())
                except Exception as exc:  # noqa: BLE001 - jangan biarkan satu worker mematikan run
                    report.results.append(
                        InstanceResult(0, "", "", "", Lifecycle.FAILED, False, f"worker crash: {exc}")
                    )

        report.finished_at = datetime.now(timezone.utc).isoformat()
        self._log(
            f"[+] Selesai: {report.success} sukses / {report.failed} gagal dari {len(report.results)} instance"
        )
        return report

    # --------------------------------------------------------------- 1 worker
    def _run_one(self, index: int, dry_run: bool) -> InstanceResult:
        slot = self.pool.lease()
        if slot is None:
            return InstanceResult(
                index, "", "", "", Lifecycle.SKIPPED, False,
                "Slot Telegram habis — tambah akun Telegram untuk akun baru (aturan 1 akun = 1 Telegram)",
            )

        persona = build_persona(email_domain=self.email_domain)
        account_id = f"acc_{uuid.uuid4().hex[:12]}"
        record = AccountRecord(
            account_id=account_id,
            email=persona.email,
            name=persona.name,
            password=persona.password,
            telegram_slot=slot.slot_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.store.upsert(record)
        self.store.log_event(account_id, Lifecycle.LEASED, f"slot={slot.slot_id} phone={slot.phone}")

        if dry_run:
            self.pool.release(slot.slot_id)
            return InstanceResult(
                index, account_id, persona.email, slot.slot_id, Lifecycle.SKIPPED, True, "dry-run"
            )

        try:
            return self._execute(index, account_id, persona, slot, record)
        except DuplicateBindingError as exc:
            self.pool.release(slot.slot_id)
            self.store.log_event(account_id, Lifecycle.FAILED, str(exc))
            return InstanceResult(index, account_id, persona.email, slot.slot_id, Lifecycle.FAILED, False, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.pool.release(slot.slot_id)
            record.state = Lifecycle.FAILED
            record.last_error = str(exc)
            self.store.upsert(record)
            self.store.log_event(account_id, Lifecycle.FAILED, str(exc))
            return InstanceResult(index, account_id, persona.email, slot.slot_id, Lifecycle.FAILED, False, str(exc))

    def _execute(
        self,
        index: int,
        account_id: str,
        persona: Persona,
        slot: TelegramSlot,
        record: AccountRecord,
    ) -> InstanceResult:
        client = FreeModelClient(
            base_url=self.base_url,
            persona=persona,
            timeout=self.timeout,
            retries=self.retries,
        )
        try:
            self._log(f"    [->] #{index} register {persona.email} (slot {slot.slot_id})")
            result = client.register(persona)
            if not result.ok:
                self.pool.release(slot.slot_id)
                record.state = Lifecycle.FAILED
                record.last_error = result.reason
                self.store.upsert(record)
                self.store.log_event(account_id, Lifecycle.FAILED, result.reason)
                return InstanceResult(
                    index, account_id, persona.email, slot.slot_id, Lifecycle.FAILED,
                    False, f"registrasi gagal: {result.reason}",
                )
            record.state = Lifecycle.REGISTERED
            self.store.upsert(record)
            self.store.log_event(account_id, Lifecycle.REGISTERED, f"http={result.status_code}")

            snapshot = client.fetch_dashboard()
            record.telegram_verify_code = snapshot.verify_code
            record.telegram_bot_url = snapshot.bot_url
            record.api_key = snapshot.api_key
            if snapshot.verify_code:
                self.store.log_event(account_id, Lifecycle.CODE_FOUND, snapshot.verify_code)
                self._log(f"    [+] #{index} kode verifikasi: {snapshot.verify_code}")
            else:
                self.store.log_event(account_id, Lifecycle.CODE_FOUND, "kode tidak ditemukan di dashboard")

            if snapshot.verify_code:
                outcome: VerifyOutcome = self.verifier.verify(slot, snapshot.verify_code)
                record.state = Lifecycle.TG_VERIFIED if outcome.ok else Lifecycle.FAILED
                if outcome.ok:
                    self.store.log_event(account_id, Lifecycle.TG_VERIFIED, outcome.raw_reply)
                    self._log(f"    [+] #{index} Telegram {slot.slot_id} verified")
                else:
                    # Verifikasi gagal -> slot WAJIB dilepas, jangan bakar akun Telegram.
                    self.pool.release(slot.slot_id)
                    record.last_error = outcome.detail
                    self.store.upsert(record)
                    self.store.log_event(account_id, Lifecycle.FAILED, outcome.detail)
                    self._log(f"    [!] #{index} verifikasi Telegram gagal: {outcome.detail}")
                    return InstanceResult(
                        index, account_id, persona.email, slot.slot_id, Lifecycle.FAILED,
                        False, f"verifikasi telegram: {outcome.detail}",
                    )
            else:
                # Akun jadi tapi kode belum tampil di dashboard: slot tetap diikat
                # ke akun ini supaya verifikasinya nanti tetap 1:1.
                self.pool.bind(slot.slot_id, account_id, persona.email, "")
                record.state = Lifecycle.AWAITING_CODE
                self.store.upsert(record)
                self.store.log_event(account_id, Lifecycle.AWAITING_CODE, "kode tidak ada di dashboard")
                return InstanceResult(
                    index, account_id, persona.email, slot.slot_id, Lifecycle.AWAITING_CODE,
                    True, "akun dibuat, kode verifikasi belum muncul — verifikasi manual",
                    record.api_key,
                )

            # Binding permanen 1:1 SETELAH registrasi sukses.
            self.pool.bind(slot.slot_id, account_id, persona.email, snapshot.verify_code)

            if record.api_key and client.test_key(record.api_key, self.test_model):
                record.state = Lifecycle.DONE
                self.store.log_event(account_id, Lifecycle.KEY_TESTED, self.test_model)
            else:
                record.state = Lifecycle.DONE
                self.store.log_event(account_id, Lifecycle.DONE, "key belum teruji / belum terbit")

            record.history.append({"state": record.state, "at": datetime.now(timezone.utc).isoformat()})
            self.store.upsert(record)
            return InstanceResult(
                index, account_id, persona.email, slot.slot_id, record.state, True, "selesai",
                record.api_key,
            )
        finally:
            client.close()
