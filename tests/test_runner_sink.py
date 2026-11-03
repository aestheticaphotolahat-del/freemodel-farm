#!/usr/bin/env python3
"""Integrasi runner: setiap key yang terbit masuk keys.txt dan (opsional) router."""

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
from farm.keys import read_keys
from farm.router import ImportOutcome
from farm.runner import FarmRunner, Lifecycle
from farm.sink import KeySink
from farm.store import AccountStore
from farm.telegram import TelegramPool
from farm.verifier import MockVerifier


class FakeImporter:
    """Pengganti RouterImporter: mencatat panggilan tanpa jaringan."""

    def __init__(self, status: str = "imported", login_ok: bool = True) -> None:
        self.status = status
        self.login_ok = login_ok
        self.seen: list[tuple[str, str]] = []
        self.prepared = False

    def active(self) -> bool:
        return True

    def login(self) -> bool:
        self.prepared = True
        return self.login_ok

    def import_key(self, email: str, api_key: str) -> ImportOutcome:
        self.seen.append((email, api_key))
        return ImportOutcome(True, self.status, email, "conn-1")


def make_slots(path: Path, count: int) -> None:
    path.write_text(json.dumps({"slots": [
        {"slot_id": f"tg-{i:03d}", "phone": f"+62811{i:06d}"} for i in range(1, count + 1)
    ]}), encoding="utf-8")


class SinkIntegrationTest(unittest.TestCase):
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
        self.keys_path = self.root / "keys.txt"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _runner(self, slot_count: int, sink: KeySink | None) -> FarmRunner:
        make_slots(self.slots_path, slot_count)
        return FarmRunner(
            base_url=self.base_url,
            pool=TelegramPool(self.slots_path, self.bindings_path),
            store=self.store,
            verifier=MockVerifier(f"{self.base_url}/tg/claim"),
            timeout=15,
            retries=2,
            sink=sink,
        )

    def _events(self) -> list[dict]:
        path = self.root / "events.jsonl"
        if not path.is_file():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    # ------------------------------------------------------------------ tests
    def test_setiap_key_masuk_keys_dan_router(self) -> None:
        importer = FakeImporter()
        sink = KeySink(self.keys_path, importer=importer)  # type: ignore[arg-type]
        report = self._runner(3, sink).run(count=3, concurrency=3)

        self.assertEqual(report.success, 3, msg=[r.detail for r in report.results])
        keys = read_keys(self.keys_path)
        self.assertEqual(len(keys), 3, "setiap akun harus menyumbang satu key")
        self.assertEqual(sorted(k for _e, k in importer.seen), sorted(keys))
        self.assertTrue(importer.prepared, "router harus login sekali di awal run")

        states = [e["state"] for e in self._events()]
        self.assertEqual(states.count(Lifecycle.ROUTER_SYNCED), 3,
                         "tiap key harus punya event sinkron router")

    def test_tanpa_sink_tetap_jalan(self) -> None:
        report = self._runner(2, None).run(count=2, concurrency=2)
        self.assertEqual(report.success, 2, msg=[r.detail for r in report.results])
        self.assertEqual(read_keys(self.keys_path), [])
        self.assertNotIn(Lifecycle.ROUTER_SYNCED, [e["state"] for e in self._events()])

    def test_login_router_gagal_tidak_menggagalkan_farm(self) -> None:
        importer = FakeImporter(login_ok=False)
        sink = KeySink(self.keys_path, importer=importer)  # type: ignore[arg-type]
        report = self._runner(2, sink).run(count=2, concurrency=2)

        self.assertEqual(report.success, 2, "farm harus tetap sukses tanpa router")
        self.assertEqual(len(read_keys(self.keys_path)), 2, "keys.txt tetap ditulis")
        self.assertEqual(importer.seen, [], "impor tidak boleh dipanggil saat login gagal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
