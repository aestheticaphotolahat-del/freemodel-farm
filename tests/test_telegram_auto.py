#!/usr/bin/env python3
"""Kontrak Telegram otomatis: konfigurasi dari file, nol env, nol prompt.

Yang dijaga:
1. Slot Telegram bisa membawa session_string sendiri (StringSession) dan
   mewarisi api_id/api_hash dari blok "app" level atas.
2. Tidak ada pembacaan TG_API_ID/TG_API_HASH/TG_PHONE dari environment.
3. Verifier mengembalikan error terukur (bukan prompt/exception mentah) bila
   slot belum punya sesi sah.
4. Preflight melaporkan slot mana yang belum siap sebelum farm dijalankan.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from farm.telegram import TelegramPool, TelegramSlot
from farm.verifier import SessionVerifier

CONFIG = {
    "app": {"api_id": 987654, "api_hash": "deadbeefcafe"},
    "slots": [
        {
            "slot_id": "tg-001",
            "phone": "+62811111111",
            "session_string": "1BVtsOK1_string_untuk_slot_1",
        },
        {
            "slot_id": "tg-002",
            "phone": "+62822222222",
            "session_name": "fm_tg_002",
        },
    ],
}


class SlotConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.slots_file = self.root / "telegram_accounts.json"
        self.slots_file.write_text(json.dumps(CONFIG), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_slot_carries_own_session_string(self) -> None:
        pool = TelegramPool(self.slots_file, self.root / "bindings.json")
        slot = pool.slot("tg-001")
        self.assertEqual(slot.session_string, "1BVtsOK1_string_untuk_slot_1")
        self.assertEqual(slot.phone, "+62811111111")

    def test_slot_inherits_app_credentials(self) -> None:
        """api_id/api_hash diambil dari blok app, bukan environment."""
        os.environ.pop("TG_API_ID", None)
        os.environ.pop("TG_API_HASH", None)
        pool = TelegramPool(self.slots_file, self.root / "bindings.json")
        slot = pool.slot("tg-002")
        self.assertEqual(slot.api_id, 987654)
        self.assertEqual(slot.api_hash, "deadbeefcafe")

    def test_slot_without_session_string_has_none(self) -> None:
        pool = TelegramPool(self.slots_file, self.root / "bindings.json")
        self.assertIsNone(pool.slot("tg-002").session_string)

    def test_slot_reports_ready_state(self) -> None:
        pool = TelegramPool(self.slots_file, self.root / "bindings.json")
        self.assertTrue(pool.slot("tg-001").has_session_string)

    def test_preflight_flags_slots_without_session(self) -> None:
        """Slot tanpa session string dan tanpa file sesi harus dilaporkan."""
        pool = TelegramPool(self.slots_file, self.root / "bindings.json",
                            sessions_dir=self.root / "sessions")
        report = pool.preflight()
        self.assertEqual(report["ready"], ["tg-001"])
        self.assertEqual(report["not_ready"], ["tg-002"])

    def test_preflight_accepts_existing_session_file(self) -> None:
        sessions = self.root / "sessions"
        sessions.mkdir()
        (sessions / "fm_tg_002.session").write_bytes(b"x")
        pool = TelegramPool(self.slots_file, self.root / "bindings.json",
                            sessions_dir=sessions)
        report = pool.preflight()
        self.assertEqual(sorted(report["ready"]), ["tg-001", "tg-002"])
        self.assertEqual(report["not_ready"], [])


class SessionVerifierTest(unittest.TestCase):
    def test_missing_session_is_reported_not_prompted(self) -> None:
        """Tanpa sesi sah, kembalikan outcome gagal yang jelas (tanpa input())."""
        verifier = SessionVerifier(sessions_dir=Path(tempfile.gettempdir()) / "fm_none")
        slot = TelegramSlot(
            slot_id="tg-001", phone="+62811111111", session_name="tidak_ada",
            api_id=12345, api_hash="hash", session_string=None,
        )
        outcome = verifier.verify(slot, "fm_1_abc")
        self.assertFalse(outcome.ok)
        self.assertIn("sesi", outcome.detail.lower())
        self.assertIn("session_string", outcome.detail.lower())

    def test_missing_app_credentials_is_reported(self) -> None:
        verifier = SessionVerifier(sessions_dir=Path(tempfile.gettempdir()) / "fm_none2")
        slot = TelegramSlot(
            slot_id="tg-002", phone="+62822222222", session_name="x",
            api_id=0, api_hash="", session_string="tok",
        )
        outcome = verifier.verify(slot, "fm_2_def")
        self.assertFalse(outcome.ok)
        self.assertIn("api_id", outcome.detail.lower())


class NoTelegramEnvTest(unittest.TestCase):
    def test_env_example_has_no_tg_vars(self) -> None:
        """.env hanya untuk router; tidak boleh ada variabel Telegram."""
        text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        for forbidden in ("TG_API_ID", "TG_API_HASH", "TG_PHONE", "TG_SESSION"):
            self.assertNotIn(forbidden, text, f"{forbidden} masih ada di .env.example")

    def test_config_module_does_not_read_tg_env(self) -> None:
        source = (PROJECT_ROOT / "farm" / "config.py").read_text(encoding="utf-8")
        self.assertNotIn("TG_API_ID", source)
        self.assertNotIn("TG_API_HASH", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
