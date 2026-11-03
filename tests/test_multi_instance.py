#!/usr/bin/env python3
"""Test multi-instance farm: isolasi sesi, konkurensi, dan aturan 1 akun = 1 Telegram."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_server
from farm.client import FreeModelClient
from farm.identity import build_persona
from farm.runner import FarmRunner, Lifecycle
from farm.store import AccountStore
from farm.telegram import DuplicateBindingError, TelegramPool, TelegramSlot
from farm.verifier import MockVerifier, VerifyOutcome


class FailingVerifier:
    def verify(self, slot: TelegramSlot, verify_code: str) -> VerifyOutcome:
        return VerifyOutcome(False, "bot menolak: kode invalid")


def make_slots(path: Path, count: int) -> None:
    path.write_text(json.dumps({
        "slots": [
            {"slot_id": f"tg-{i:03d}", "phone": f"+62812000{i:04d}",
             "session_name": f"fm_tg_{i:03d}", "api_id": 1, "api_hash": "x"}
            for i in range(1, count + 1)
        ]
    }), encoding="utf-8")


class FarmTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server, cls.port = mock_server.start_server()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def setUp(self) -> None:
        mock_server.reset_state()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.slots_path = self.root / "slots.json"
        self.bindings_path = self.root / "bindings.json"
        self.store = AccountStore(self.root / "accounts.json", self.root / "events.jsonl")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _runner(self, slot_count: int, verifier=None) -> FarmRunner:
        make_slots(self.slots_path, slot_count)
        pool = TelegramPool(self.slots_path, self.bindings_path)
        return FarmRunner(
            base_url=self.base_url,
            pool=pool,
            store=self.store,
            verifier=verifier or MockVerifier(f"{self.base_url}/tg/claim"),
            timeout=15,
            retries=2,
            test_model="claude-mythos-preview",
        )

    # ------------------------------------------------------------------ tests
    def test_register_rejects_missing_csrf(self) -> None:
        """POST /register tanpa _token harus 419, bukan diam-diam membuat akun."""
        import requests

        res = requests.post(
            f"{self.base_url}/register",
            data={"email": "x@y.z", "password": "Abcdefg1!", "password_confirmation": "Abcdefg1!"},
            timeout=10,
        )
        self.assertEqual(res.status_code, 419)
        self.assertEqual(mock_server.STATE.users, {})

    def test_concurrent_eight_instances_isolated(self) -> None:
        """8 instance paralel -> 8 akun unik, 8 slot Telegram unik, tanpa silang sesi."""
        runner = self._runner(slot_count=8)
        report = runner.run(count=8, concurrency=8)

        self.assertEqual(report.success, 8, msg=[r.detail for r in report.results])
        emails = [r.email for r in report.results]
        slots = [r.telegram_slot for r in report.results]
        keys = [r.api_key for r in report.results if r.api_key]

        self.assertEqual(len(set(emails)), 8, "email harus unik per instance")
        self.assertEqual(len(set(slots)), 8, "slot Telegram tidak boleh dipakai dua kali")
        self.assertEqual(len(set(keys)), 8, "api key harus unik per akun")
        self.assertTrue(all(k.startswith("fm_") for k in keys))

        # Setiap akun harus terverifikasi oleh slot Telegram-nya sendiri.
        with mock_server.STATE.lock:
            claims = dict(mock_server.STATE.tg_claims)
            users = dict(mock_server.STATE.users)
        self.assertEqual(len(claims), 8)
        for result in report.results:
            self.assertEqual(claims[result.telegram_slot], result.email,
                             "slot Telegram terikat ke akun yang salah")
            self.assertTrue(users[result.email]["verified"])
            self.assertEqual(users[result.email]["tg_slot"], result.telegram_slot)

        states = {r.state for r in report.results}
        self.assertEqual(states, {Lifecycle.DONE})

    def test_telegram_binding_is_permanent_1to1(self) -> None:
        """Satu slot Telegram tidak boleh diikat ke dua akun berbeda."""
        make_slots(self.slots_path, 1)
        pool = TelegramPool(self.slots_path, self.bindings_path)
        pool.bind("tg-001", "acc_a", "a@x.com", "fm_1_aaaa")
        with self.assertRaises(DuplicateBindingError):
            pool.bind("tg-001", "acc_b", "b@x.com", "fm_2_bbbb")

        # Binding bertahan setelah pool dimuat ulang (persisten di disk).
        reloaded = TelegramPool(self.slots_path, self.bindings_path)
        binding = reloaded.binding_for("tg-001") or {}
        self.assertEqual(binding.get("email"), "a@x.com")
        self.assertEqual(reloaded.available_slots(), [])

    def test_slot_exhaustion_is_reported_not_crashed(self) -> None:
        """Permintaan melebihi jumlah akun Telegram -> sisa instance di-skip jelas."""
        runner = self._runner(slot_count=2)
        report = runner.run(count=4, concurrency=4)
        self.assertEqual(report.success, 2)
        self.assertEqual(report.failed, 2)
        skipped = [r for r in report.results if r.state == Lifecycle.SKIPPED]
        self.assertEqual(len(skipped), 2)
        self.assertTrue(all("Slot Telegram habis" in r.detail for r in skipped))

    def test_failed_verification_releases_slot(self) -> None:
        """Kalau bot menolak, slot harus dilepas supaya bisa dipakai akun lain."""
        runner = self._runner(slot_count=1, verifier=FailingVerifier())
        report = runner.run(count=1, concurrency=1)
        self.assertEqual(report.success, 0)
        self.assertEqual(report.results[0].state, Lifecycle.FAILED)

        pool = TelegramPool(self.slots_path, self.bindings_path)
        self.assertEqual(pool.available_slots()[0].slot_id, "tg-001")

    def test_rerun_skips_when_pool_exhausted(self) -> None:
        """Run kedua tanpa slot baru tidak boleh membuat akun tambahan."""
        runner = self._runner(slot_count=3)
        first = runner.run(count=3, concurrency=3)
        self.assertEqual(first.success, 3)
        calls_after_first = mock_server.STATE.register_calls

        second = runner.run(count=1, concurrency=1)
        self.assertEqual(second.success, 0)
        self.assertEqual(mock_server.STATE.register_calls, calls_after_first,
                         "tidak boleh ada registrasi baru saat pool habis")

    def test_dry_run_does_not_touch_target(self) -> None:
        runner = self._runner(slot_count=3)
        report = runner.run(count=3, concurrency=2, dry_run=True)
        self.assertEqual(report.success, 3)
        self.assertEqual(mock_server.STATE.register_calls, 0)
        pool = TelegramPool(self.slots_path, self.bindings_path)
        self.assertEqual(len(pool.available_slots()), 3)

    def test_client_session_isolation(self) -> None:
        """Dua klien tidak boleh berbagi cookie/sesi."""
        c1 = FreeModelClient(base_url=self.base_url, persona=build_persona(), timeout=15)
        c2 = FreeModelClient(base_url=self.base_url, persona=build_persona(), timeout=15)
        try:
            self.assertIsNot(c1.session, c2.session)
            r1 = c1.register()
            r2 = c2.register()
            self.assertTrue(r1.ok and r2.ok)
            self.assertNotEqual(r1.session_cookies.get("laravel_session"),
                                r2.session_cookies.get("laravel_session"))
            snap1 = c1.fetch_dashboard()
            snap2 = c2.fetch_dashboard()
            self.assertTrue(snap1.authenticated and snap2.authenticated)
            self.assertNotEqual(snap1.api_key, snap2.api_key)
        finally:
            c1.close()
            c2.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
