"""Opsi persisten + auto-import router + berkas keys."""
# cSpell:disable

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Callable

import requests as real_requests

from farm.keys import append_key, read_keys, write_keys
from farm.router import (
    ImportOutcome,
    RouterImporter,
    connection_name,
    find_node,
    node_body,
)
from farm.settings import DEFAULTS, FarmSettings


# ------------------------------------------------------------ fake HTTP
class FakeResponse:
    def __init__(self, status_code: int = 200, data=None, text: str = "") -> None:
        self.status_code = status_code
        self._data = data
        self.text = text if text else (json.dumps(data) if data is not None else "")

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class StubSession:
    """Session tiruan: rute didaftarkan per (METHOD, akhiran-URL)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.routes: dict[tuple[str, str], object] = {}

    def _hit(self, method: str, url: str, body: object):
        self.calls.append((method, url, body))
        for (route_method, suffix), handler in self.routes.items():
            if route_method == method and url.endswith(suffix):
                return handler(url, body) if callable(handler) else handler
        raise AssertionError(f"rute tak terduga: {method} {url}")

    def post(self, url: str, **kwargs):
        return self._hit("POST", url, kwargs.get("json"))

    def get(self, url: str, **kwargs):
        return self._hit("GET", url, None)


def logged() -> tuple[list[str], Callable[[str], None]]:
    box: list[str] = []

    def log(message: str) -> None:
        box.append(message)

    return box, log


# ---------------------------------------------------------------- settings
class SettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "farm_settings.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_defaults_hadir_tanpa_file(self) -> None:
        s = FarmSettings(self.path)
        self.assertEqual(s.get("worker.concurrency"), DEFAULTS["worker"]["concurrency"])
        self.assertTrue(s.get("router.autoImport"))
        self.assertEqual(s.get("telegram.slotsFile"), "config/telegram_accounts.json")

    def test_disk_menang_tapi_kunci_hilang_diisi_default(self) -> None:
        partial = {"worker": {"concurrency": 7}, "tambahan": {"x": 1}}
        self.path.write_text(json.dumps(partial), encoding="utf-8")
        s = FarmSettings(self.path)
        self.assertEqual(s.get("worker.concurrency"), 7)
        self.assertEqual(s.get("worker.retries"), DEFAULTS["worker"]["retries"])
        self.assertEqual(s.get("tambahan.x"), 1)

    def test_set_tersimpan_dan_terbaca_ulang(self) -> None:
        s = FarmSettings(self.path)
        s.set("router.autoImport", False)
        self.assertFalse(FarmSettings(self.path).get("router.autoImport"))

    def test_toggle_membalik(self) -> None:
        s = FarmSettings(self.path)
        self.assertFalse(s.get("proxy.enabled"))          # default OFF
        self.assertTrue(s.toggle("proxy.enabled"))        # -> ON
        self.assertTrue(FarmSettings(self.path).get("proxy.enabled"))
        self.assertFalse(s.toggle("proxy.enabled"))       # -> OFF lagi
        self.assertFalse(FarmSettings(self.path).get("proxy.enabled"))

    def test_proxy_list_abaikan_komentar_dan_kosong(self) -> None:
        pool = Path(self.tmp.name) / "proxies.txt"
        pool.write_text("# komentar\n\n1.1.1.1:8080\n2.2.2.2:3128:u:p\n", encoding="utf-8")
        s = FarmSettings(self.path, root=Path(self.tmp.name))
        s.set("proxy.file", "proxies.txt")
        self.assertEqual(s.proxy_list(), ["1.1.1.1:8080", "2.2.2.2:3128:u:p"])


# ----------------------------------------------------------------- router
def make_importer(**over) -> tuple[RouterImporter, StubSession, list[str]]:
    stub = StubSession()
    box, log = logged()
    kwargs: dict[str, object] = {"api_key": "KUNCI", "session": stub, "log": log}
    kwargs.update(over)
    imp = RouterImporter("https://router.test", **kwargs)  # type: ignore[arg-type]
    return imp, stub, box


class RouterPureTest(unittest.TestCase):
    def test_connection_name(self) -> None:
        self.assertEqual(connection_name("a@b.id"), "FreeModel a")

    def test_node_body_schema(self) -> None:
        body = node_body("FreeModel", "https://freemodel.in/v1/")
        self.assertEqual(body["prefix"], "freemodel")
        self.assertEqual(body["type"], "openai-compatible")
        self.assertEqual(body["apiType"], "chat")
        self.assertEqual(body["baseUrl"], "https://freemodel.in/v1")

    def test_find_node_lewat_prefix(self) -> None:
        nodes = [{"id": "n1", "type": "openai-compatible", "name": "X",
                  "prefix": "freemodel", "baseUrl": "https://x/"}]
        self.assertEqual(find_node(nodes, "FreeModel", "https://freemodel.in/v1"), "n1")

    def test_find_node_abaikan_tipe_lain(self) -> None:
        nodes = [{"id": "n9", "type": "anthropic", "name": "FreeModel",
                  "prefix": "", "baseUrl": ""}]
        self.assertIsNone(find_node(nodes, "FreeModel", "https://freemodel.in/v1"))


class RouterImporterTest(unittest.TestCase):
    def test_tidak_aktif_tanpa_api_key(self) -> None:
        imp, stub, box = make_importer(api_key="")
        self.assertFalse(imp.active())
        self.assertFalse(imp.login())
        self.assertEqual(stub.calls, [])

    def test_login_sukses(self) -> None:
        imp, stub, _box = make_importer()
        stub.routes[("POST", "login")] = FakeResponse(200, {"success": True})
        self.assertTrue(imp.login())

    def test_login_ditolak(self) -> None:
        imp, stub, _box = make_importer()
        stub.routes[("POST", "login")] = FakeResponse(401, {"success": False})
        self.assertFalse(imp.login())

    def test_ensure_node_pakai_yang_ada(self) -> None:
        imp, stub, _box = make_importer()
        stub.routes[("GET", "provider-nodes")] = FakeResponse(
            200, {"nodes": [{"id": "n1", "type": "openai-compatible",
                             "name": "FreeModel", "prefix": "freemodel",
                             "baseUrl": "https://freemodel.in/v1"}]})
        self.assertEqual(imp.ensure_node(), "n1")
        self.assertEqual(len([c for c in stub.calls if c[0] == "POST"]), 0)

    def test_ensure_node_buat_baru(self) -> None:
        imp, stub, _box = make_importer()
        stub.routes[("GET", "provider-nodes")] = FakeResponse(200, {"nodes": []})
        stub.routes[("POST", "provider-nodes")] = FakeResponse(201, {"node": {"id": "n7"}})
        self.assertEqual(imp.ensure_node(), "n7")

    def test_import_key_lewati_yang_sudah_ada(self) -> None:
        imp, stub, _box = make_importer()
        imp.provider_id = "n1"
        stub.routes[("GET", "providers")] = FakeResponse(
            200, {"connections": [{"provider": "n1", "name": "FreeModel a"}]})
        out = imp.import_key("a@b.id", "fm_" + "k" * 40)
        self.assertEqual(out.status, "exists")
        self.assertTrue(out.ok)
        self.assertEqual([c for c in stub.calls if c[0] == "POST"], [])

    def test_import_key_baru_dikirim_sekali(self) -> None:
        imp, stub, _box = make_importer()
        imp.provider_id = "n1"
        stub.routes[("GET", "providers")] = FakeResponse(200, {"connections": []})
        saved: list[object] = []

        def capture(_url: str, body: object):
            saved.append(body)
            return FakeResponse(201, {"connection": {"id": "c1"}})

        stub.routes[("POST", "providers")] = capture
        out = imp.import_key("a@b.id", "fm_" + "k" * 40)
        self.assertEqual((out.ok, out.status, out.connection_id), (True, "imported", "c1"))
        self.assertEqual(len(saved), 1)

    def test_import_key_tanpa_key_milik_akun(self) -> None:
        imp, stub, _box = make_importer()
        out = imp.import_key("a@b.id", "")
        self.assertEqual(out.status, "skipped")
        self.assertEqual(stub.calls, [])

    def test_test_connection_petakan_timeout(self) -> None:
        imp, stub, _box = make_importer()

        def boom(_u: str, _b: object):
            raise real_requests.exceptions.ReadTimeout("lambat")

        stub.routes[("POST", "test")] = boom
        status, _detail = imp.test_connection("c1")
        self.assertEqual(status, "timeout")


# ------------------------------------------------------------------- keys
class KeysTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "keys.txt"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_append_baru_dan_duplikat(self) -> None:
        key = "fm_" + "a" * 40
        self.assertTrue(append_key(self.path, key))
        self.assertFalse(append_key(self.path, key))
        self.assertEqual(read_keys(self.path), [key])

    def test_tolak_bukan_key_freemodel(self) -> None:
        self.assertFalse(append_key(self.path, "bukan-key"))
        self.assertEqual(read_keys(self.path), [])

    def test_write_keys_dedupe(self) -> None:
        pairs = [("a@x", "fm_" + "1" * 40), ("b@x", "fm_" + "1" * 40), ("c@x", "nope")]
        self.assertEqual(write_keys(self.path, pairs), 1)


# ------------------------------------------------------------------- sink
class KeySinkTest(unittest.TestCase):
    """KeySink: prepare sekali di awal, handle tiap key (keys.txt + router)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_handle_tanpa_router_cukup_tulis_keys(self) -> None:
        from farm.sink import KeySink

        sink = KeySink(self.root / "keys.txt", importer=None)
        note = sink.handle("a@b.id", "fm_" + "k" * 40)
        self.assertIn("keys.txt", note)
        self.assertEqual(len(read_keys(self.root / "keys.txt")), 1)

    def test_prepare_login_gagal_mematikan_importir(self) -> None:
        from farm.sink import KeySink

        imp, stub, _box = make_importer()
        stub.routes[("POST", "login")] = FakeResponse(401, {})
        sink = KeySink(self.root / "keys.txt", importer=imp)
        note = sink.prepare()
        self.assertIn("login", note.lower())
        after = sink.handle("a@b.id", "fm_" + "k" * 40)
        self.assertIn("tanpa router", after)
        self.assertEqual(len(read_keys(self.root / "keys.txt")), 1)

    def test_handle_importir_sukses(self) -> None:
        from farm.sink import KeySink

        imp, stub, _box = make_importer()
        stub.routes[("POST", "login")] = FakeResponse(200, {"success": True})
        stub.routes[("GET", "provider-nodes")] = FakeResponse(
            200, {"nodes": [{"id": "n1", "type": "openai-compatible",
                             "name": "FreeModel", "prefix": "freemodel",
                             "baseUrl": "https://freemodel.in/v1"}]})
        stub.routes[("GET", "providers")] = FakeResponse(200, {"connections": []})
        stub.routes[("POST", "providers")] = FakeResponse(201, {"connection": {"id": "c9"}})
        sink = KeySink(self.root / "keys.txt", importer=imp)
        self.assertIn("OK", sink.prepare())
        note = sink.handle("a@b.id", "fm_" + "k" * 40)
        self.assertIn("router:imported", note)


if __name__ == "__main__":
    unittest.main()
