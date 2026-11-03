"""Auto-import API key hasil farm ke router (kompatibel 9Router).

Satu koneksi per akun, idempoten: kalau nama koneksi sudah ada di router,
key itu tidak dikirim ulang. Provider node FreeModel dibuat sekali.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import requests

TARGET_URL = "https://freemodel.in/v1"
NODE_TYPE = "openai-compatible"


@dataclass
class ImportOutcome:
    ok: bool
    status: str                 # imported | exists | failed | skipped
    detail: str = ""
    connection_id: str = ""

    def as_text(self) -> str:
        return f"{self.status} ({self.detail})" if self.detail else self.status


def connection_name(email: str) -> str:
    local = (email or "unknown").split("@")[0]
    return f"FreeModel {local}"


def node_body(node_name: str, target_url: str) -> dict[str, Any]:
    return {
        "name": node_name,
        "prefix": "freemodel",
        "type": NODE_TYPE,
        "apiType": "chat",
        "baseUrl": target_url.rstrip("/"),
    }


def find_node(nodes: list[dict[str, Any]], node_name: str, target_url: str) -> str | None:
    """Cocokkan node lewat name, prefix, atau baseUrl (schema 9Router)."""
    want_name = node_name.rstrip("/").lower()
    want_url = target_url.rstrip("/").lower()
    for node in nodes:
        if node.get("type") != NODE_TYPE:
            continue
        if str(node.get("name", "")).rstrip("/").lower() == want_name:
            return node.get("id")
        if str(node.get("prefix", "")).lower() == want_name:
            return node.get("id")
        if str(node.get("baseUrl", "")).rstrip("/").lower() == want_url:
            return node.get("id")
    return None


class RouterImporter:
    """Klien kecil untuk endpoint provider router; session bisa di-inject."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        node_name: str = "FreeModel",
        target_url: str = TARGET_URL,
        timeout: float = 20.0,
        session: requests.Session | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.node_name = node_name
        self.target_url = target_url
        self.timeout = timeout
        self.session = session or requests.Session()
        self.provider_id: str | None = None
        self._log = log or (lambda _m: None)

    # ------------------------------------------------------------- kesiapan
    def active(self) -> bool:
        return bool(self.base_url and self.api_key)

    def login(self) -> bool:
        if not self.active():
            self._log("[-] kredensial router belum diatur (ROUTER_API_KEY)")
            return False
        try:
            res = self.session.post(
                f"{self.base_url}/api/auth/login",
                json={"apiKey": self.api_key},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            self._log(f"[-] login router gagal: {exc}")
            return False
        if res.status_code == 200 and (res.json() or {}).get("success"):
            self._log("[+] login router OK")
            return True
        self._log(f"[-] login router ditolak: {res.status_code}")
        return False

    def ensure_node(self) -> str | None:
        """Ambil provider node FreeModel, buat kalau belum ada."""
        if self.provider_id:
            return self.provider_id
        try:
            res = self.session.get(f"{self.base_url}/api/provider-nodes", timeout=self.timeout)
        except requests.RequestException as exc:
            self._log(f"[-] daftar node gagal: {exc}")
            return None
        if res.status_code != 200:
            self._log(f"[-] daftar node HTTP {res.status_code}")
            return None
        found = find_node((res.json() or {}).get("nodes", []), self.node_name, self.target_url)
        if found:
            self.provider_id = found
            self._log(f"[+] provider node {self.node_name} (id={found})")
            return found

        self._log(f"[*] membuat provider node {self.node_name}")
        try:
            created = self.session.post(
                f"{self.base_url}/api/provider-nodes",
                json=node_body(self.node_name, self.target_url),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            self._log(f"[-] buat node gagal: {exc}")
            return None
        if created.status_code not in (200, 201):
            self._log(f"[-] buat node HTTP {created.status_code}")
            return None
        self.provider_id = ((created.json() or {}).get("node") or {}).get("id")
        self._log(f"[+] provider node dibuat (id={self.provider_id})")
        return self.provider_id

    def existing_names(self) -> set[str]:
        try:
            res = self.session.get(f"{self.base_url}/api/providers", timeout=self.timeout)
        except requests.RequestException:
            return set()
        if res.status_code != 200:
            return set()
        names = set()
        for conn in (res.json() or {}).get("connections", []):
            if conn.get("provider") == self.provider_id:
                names.add(conn.get("name"))
        return names

    # --------------------------------------------------------------- impor
    def import_key(self, email: str, api_key: str) -> ImportOutcome:
        if not api_key:
            return ImportOutcome(False, "skipped", "akun belum punya api key")
        if not self.ensure_node():
            return ImportOutcome(False, "failed", "provider node tidak tersedia")

        name = connection_name(email)
        if name in self.existing_names():
            return ImportOutcome(True, "exists", name)

        body = {
            "provider": self.provider_id,
            "name": name,
            "apiKey": api_key,
            "priority": 1,
            "testStatus": "unknown",
        }
        try:
            res = self.session.post(f"{self.base_url}/api/providers", json=body, timeout=self.timeout)
        except requests.RequestException as exc:
            return ImportOutcome(False, "failed", f"network: {exc}")
        if res.status_code != 201:
            return ImportOutcome(False, "failed", f"http_{res.status_code}")
        conn_id = ((res.json() or {}).get("connection") or {}).get("id", "")
        return ImportOutcome(True, "imported", name, conn_id)

    def test_connection(self, connection_id: str) -> tuple[str, str]:
        """Uji kredensial; timeout/HTTP error jadi status, bukan exception."""
        try:
            res = self.session.post(
                f"{self.base_url}/api/providers/{connection_id}/test", timeout=self.timeout
            )
        except requests.exceptions.ReadTimeout:
            return "timeout", "endpoint test menggantung"
        except requests.RequestException as exc:
            return "network_error", str(exc)
        if res.status_code != 200:
            return f"http_{res.status_code}", (res.text or "")[:160]
        try:
            data = res.json()
        except ValueError:
            return "invalid_json", ""
        return ("valid" if data.get("valid") else "invalid"), str(data)[:160]

    def close(self) -> None:
        self.session.close()
