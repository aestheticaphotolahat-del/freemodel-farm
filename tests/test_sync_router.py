#!/usr/bin/env python3
"""Test integrasi sync_router setelah refactor penyimpanan state.

Dua defek yang dijaga di sini:
1. Path akun masih menunjuk freemodel_accounts.json padahal akun baru ada di
   state/accounts.json.
2. state/accounts.json berbentuk {"accounts": {...}} (dict), bukan list —
   iterasi langsung akan mengembalikan key (str) dan meledak di .get().
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import sync_router


class AccountLoadingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, name: str, data) -> Path:
        path = self.root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_loads_new_dict_format(self) -> None:
        """Format baru: {"accounts": {id: record}} harus jadi list record."""
        path = self._write("accounts.json", {
            "accounts": {
                "acc_1": {"email": "a@x.com", "api_key": "fm_" + "a" * 32},
                "acc_2": {"email": "b@x.com", "api_key": ""},
            }
        })
        records = sync_router.load_account_records(path)
        self.assertIsInstance(records, list)
        self.assertEqual(len(records), 2)
        self.assertEqual({r["email"] for r in records}, {"a@x.com", "b@x.com"})
        # Semua elemen harus dict — inilah yang dulu gagal.
        self.assertTrue(all(isinstance(r, dict) for r in records))

    def test_loads_legacy_list_format(self) -> None:
        """Format lama: list of dict harus tetap terbaca."""
        path = self._write("legacy.json", [
            {"email": "old@x.com", "api_key": "fm_" + "b" * 32},
        ])
        records = sync_router.load_account_records(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["email"], "old@x.com")

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(sync_router.load_account_records(self.root / "nope.json"), [])

    def test_extract_sync_keys_accepts_fm_and_cc(self) -> None:
        """freemodel menerbitkan fm_; cc_ lama tetap didukung."""
        records = [
            {"email": "a@x.com", "api_key": "fm_" + "c" * 32},
            {"email": "b@x.com", "api_key": "cc_" + "d" * 32},
            {"email": "c@x.com", "api_key": ""},
            {"email": "d@x.com"},
            {"email": "e@x.com", "api_key": "garbage_key"},
        ]
        keys = sync_router.extract_sync_keys(records)
        self.assertEqual(keys, [("a@x.com", "fm_" + "c" * 32), ("b@x.com", "cc_" + "d" * 32)])

    def test_resolve_prefers_state_accounts(self) -> None:
        """state/accounts.json harus menang atas file legacy di root."""
        state_dir = self.root / "state"
        state_dir.mkdir()
        (state_dir / "accounts.json").write_text('{"accounts": {}}', encoding="utf-8")
        legacy = self.root / "freemodel_accounts.json"
        legacy.write_text("[]", encoding="utf-8")
        chosen = sync_router.resolve_accounts_file(root=self.root)
        self.assertEqual(Path(chosen).name, "accounts.json")
        self.assertEqual(Path(chosen).parent.name, "state")

    def test_resolve_falls_back_to_legacy(self) -> None:
        legacy = self.root / "freemodel_accounts.json"
        legacy.write_text("[]", encoding="utf-8")
        chosen = sync_router.resolve_accounts_file(root=self.root)
        self.assertEqual(Path(chosen).name, "freemodel_accounts.json")

    def test_resolve_honours_explicit_path(self) -> None:
        explicit = self._write("custom.json", {"accounts": {}})
        chosen = sync_router.resolve_accounts_file(str(explicit), root=self.root)
        self.assertEqual(chosen, str(explicit))


EXISTING_FREEMODEL_NODE = {
    "prefix": "frmd",
    "apiType": "chat",
    "baseUrl": "https://freemodel.in/v1",
    "brand": "openai",
    "id": "openai-compatible-chat-cea04ffa-31b0-4147-abff-326bf5797fa0",
    "type": "openai-compatible",
    "name": "https://freemodel.in/v1",
}


class ProviderNodeTest(unittest.TestCase):
    """Router 9Router memakai name/prefix yang tidak selalu sama dengan node_name."""

    def test_matches_by_name(self) -> None:
        node = dict(EXISTING_FREEMODEL_NODE, name="FreeModel", prefix="fm")
        self.assertEqual(
            sync_router.find_provider_node([node], "FreeModel", "https://freemodel.in/v1"),
            node["id"],
        )

    def test_matches_by_prefix(self) -> None:
        node = dict(EXISTING_FREEMODEL_NODE, name="lain", prefix="freemodel")
        self.assertEqual(
            sync_router.find_provider_node([node], "FreeModel", "https://freemodel.in/v1"),
            node["id"],
        )

    def test_matches_by_base_url_when_name_and_prefix_differ(self) -> None:
        """Kasus nyata: name='https://freemodel.in/v1', prefix='frmd'."""
        self.assertEqual(
            sync_router.find_provider_node(
                [EXISTING_FREEMODEL_NODE], "FreeModel", "https://freemodel.in/v1"
            ),
            EXISTING_FREEMODEL_NODE["id"],
        )

    def test_returns_none_when_absent(self) -> None:
        other = dict(EXISTING_FREEMODEL_NODE, name="lain", prefix="zzz",
                     baseUrl="https://other.example/v1")
        self.assertIsNone(
            sync_router.find_provider_node([other], "FreeModel", "https://freemodel.in/v1")
        )

    def test_payload_contains_api_type(self) -> None:
        """Tanpa apiType router menolak: 'Invalid OpenAI compatible API type'."""
        payload = sync_router.build_node_payload("FreeModel", "https://freemodel.in/v1")
        self.assertEqual(payload["apiType"], "chat")
        self.assertEqual(payload["type"], "openai-compatible")
        self.assertEqual(payload["baseUrl"], "https://freemodel.in/v1")
        self.assertTrue(payload["name"])

    def test_provider_test_timeout_is_reported_not_raised(self) -> None:
        """Timeout endpoint test tidak boleh mematikan seluruh sinkronisasi."""
        class TimeoutSession:
            def post(self, *_args, **_kwargs):
                raise sync_router.requests.exceptions.ReadTimeout("upstream timeout")

        status, detail = sync_router.test_provider_connection(
            TimeoutSession(), "https://router.test/api/providers/id/test", timeout=1
        )
        self.assertEqual(status, "timeout")
        self.assertIn("upstream timeout", detail)

    def test_provider_test_invalid_json_is_reported_not_raised(self) -> None:
        """Respons non-JSON dari router harus menjadi status error terukur."""
        class BadResponse:
            status_code = 502
            text = "bad gateway"

            def json(self):
                raise ValueError("not json")

        class BadSession:
            def post(self, *_args, **_kwargs):
                return BadResponse()

        status, detail = sync_router.test_provider_connection(
            BadSession(), "https://router.test/api/providers/id/test", timeout=1
        )
        self.assertEqual(status, "http_502")
        self.assertIn("bad gateway", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
