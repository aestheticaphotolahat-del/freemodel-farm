#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path

import requests

def get_router_credentials():
    # Cek .env di project atau parent
    env_paths = [
        os.path.join(os.path.dirname(__file__), ".env"),
        r"D:\project\tokenharbor-farm\.env",
        r"C:\Users\decka_aesthetica.DECKA\.env"
    ]
    api_key = os.environ.get("ROUTER_API_KEY") or os.environ.get("TH_9ROUTER_API_KEY")
    if not api_key:
        for ep in env_paths:
            if os.path.exists(ep):
                with open(ep, "r", encoding="utf-8") as f:
                    for line in f:
                        m = re.match(r'^\s*(?:export\s+)?(?:ROUTER_API_KEY|TH_9ROUTER_API_KEY)\s*=\s*(.*)', line)
                        if m:
                            api_key = m.group(1).strip().strip("'\"")
                            break
            if api_key:
                break
    
    base_url = os.environ.get("ROUTER_BASE_URL", "https://router.aestc.id")
    return base_url, api_key


def resolve_accounts_file(explicit=None, root=None):
    """Pilih file akun: eksplisit -> state/accounts.json -> freemodel_accounts.json.

    Setelah refactor, akun baru hidup di state/accounts.json; file lama di root
    hanya dipertahankan supaya instalasi lama tetap bisa disinkronkan.
    """
    base = Path(root) if root else Path(__file__).resolve().parent
    candidates = [
        explicit,
        str(base / "state" / "accounts.json"),
        str(base / "freemodel_accounts.json"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return candidates[1]


def load_account_records(path):
    """Normalisasi dua format penyimpanan menjadi list of dict.

    Format baru: {"accounts": {id: record}}  (dict)
    Format lama: [record, record]            (list)
    """
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"[-] Gagal membaca {path}: {exc}")
        return []

    if isinstance(data, dict):
        accounts = data.get("accounts", {})
        # Toleransi bila suatu saat ditulis sebagai list di dalam dict.
        if isinstance(accounts, dict):
            return [rec for rec in accounts.values() if isinstance(rec, dict)]
        if isinstance(accounts, list):
            return [rec for rec in accounts if isinstance(rec, dict)]
        return []
    if isinstance(data, list):
        return [rec for rec in data if isinstance(rec, dict)]
    return []


def extract_sync_keys(records):
    """Ambil pasangan (email, api_key) yang layak disinkronkan."""
    pairs = []
    for record in records:
        key = record.get("api_key") or ""
        email = record.get("email") or ""
        # freemodel menerbitkan fm_; cc_ dipertahankan untuk akun lama.
        if email and key.startswith(("fm_", "cc_")):
            pairs.append((email, key))
    return pairs


def test_provider_connection(session, url, timeout=45):
    """Uji kredensial provider; timeout/HTTP error jadi status, bukan exception.

    Endpoint test di router bisa menggantung >15s saat upstream lambat. Dulu
    exception-nya lolos ke atas dan mematikan seluruh proses sinkronisasi.
    """
    try:
        response = session.post(url, timeout=timeout)
    except requests.exceptions.ReadTimeout as exc:
        return "timeout", str(exc)
    except requests.exceptions.RequestException as exc:
        return "network_error", str(exc)

    if response.status_code != 200:
        body = (getattr(response, "text", "") or "").strip()
        return f"http_{response.status_code}", body[:200] or "tanpa body"
    try:
        payload = response.json()
    except ValueError as exc:
        return "invalid_json", str(exc)
    valid = payload.get("valid")
    return ("valid" if valid else "invalid"), str(payload)[:200]


def find_provider_node(nodes, node_name, base_url):
    """Cari node FreeModel berdasarkan name, prefix, atau base URL."""
    normalized_url = base_url.rstrip("/").lower()
    normalized_name = node_name.lower()
    for node in nodes:
        if node.get("type") != "openai-compatible":
            continue
        name = str(node.get("name", "")).rstrip("/").lower()
        prefix = str(node.get("prefix", "")).lower()
        node_url = str(node.get("baseUrl", "")).rstrip("/").lower()
        if name == normalized_name or prefix == normalized_name or node_url == normalized_url:
            return node.get("id")
    return None


def build_node_payload(node_name, base_url):
    """Payload API provider-nodes sesuai schema 9Router yang teramati."""
    return {
        "name": node_name,
        "prefix": "freemodel",
        "type": "openai-compatible",
        "apiType": "chat",
        "baseUrl": base_url.rstrip("/"),
    }


class RouterSyncer:
    def __init__(self, base_url, api_key, node_name="FreeModel"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.node_name = node_name
        self.session = requests.Session()
        self.cookie = None
        self.provider_id = None

    def login(self):
        if not self.api_key:
            print("[-] API key 9Router belum diatur. Set ROUTER_API_KEY di .env")
            return False
        url = f"{self.base_url}/api/auth/login"
        r = self.session.post(url, json={"apiKey": self.api_key}, timeout=15)
        if r.status_code == 200 and r.json().get("success"):
            # Cookie tersimpan di session otomatis
            print("[+] Berhasil login ke 9Router!")
            return True
        else:
            print(f"[-] Gagal login ke 9Router: {r.status_code} - {r.text}")
            return False

    def get_or_create_provider_node(self):
        url = f"{self.base_url}/api/provider-nodes"
        r = self.session.get(url, timeout=15)
        if r.status_code != 200:
            print(f"[-] Gagal mengambil provider nodes: {r.status_code}")
            return None
        
        nodes = r.json().get("nodes", [])
        self.provider_id = find_provider_node(
            nodes, self.node_name, "https://freemodel.in/v1"
        )
        if self.provider_id:
            print(f"[+] Ditemukan provider node: {self.node_name} (ID: {self.provider_id})")
            return self.provider_id

        # Buat custom node jika belum ada
        print(f"[*] Membuat provider node baru: {self.node_name}...")
        create_url = f"{self.base_url}/api/provider-nodes"
        node_payload = build_node_payload(self.node_name, "https://freemodel.in/v1")
        r_create = self.session.post(create_url, json=node_payload, timeout=15)
        if r_create.status_code in [200, 201]:
            self.provider_id = r_create.json().get("node", {}).get("id")
            print(f"[+] Provider node {self.node_name} dibuat (ID: {self.provider_id})")
            return self.provider_id
        else:
            print(f"[-] Gagal membuat provider node: {r_create.status_code} - {r_create.text}")
            return None

    def sync_keys(self, accounts_file=None):
        accounts_file = resolve_accounts_file(accounts_file)
        records = load_account_records(accounts_file)
        if not records:
            print(f"[-] Tidak ada record akun di {accounts_file}.")
            return

        print(f"[*] Membaca {len(records)} akun dari {accounts_file}")
        keys_to_sync = extract_sync_keys(records)

        if not keys_to_sync:
            print("[*] Belum ada API key terdaftar di akun. (Selesaikan verifikasi telegram untuk mengklaim API key)")
            return

        print(f"[*] Menemukan {len(keys_to_sync)} key untuk disinkronkan ke {self.base_url}...")
        
        # Ambil daftar provider connections saat ini
        r_prov = self.session.get(f"{self.base_url}/api/providers", timeout=15)
        existing_names = set()
        if r_prov.status_code == 200:
            for c in r_prov.json().get("connections", []):
                if c.get("provider") == self.provider_id:
                    existing_names.add(c.get("name"))

        for email, key in keys_to_sync:
            conn_name = f"FreeModel {email.split('@')[0]}"
            if conn_name in existing_names:
                print(f"[=] Key {conn_name} sudah ada di 9Router, melewati.")
                continue

            payload = {
                "provider": self.provider_id,
                "name": conn_name,
                "apiKey": key,
                "priority": 1,
                "testStatus": "unknown"
            }
            res = self.session.post(f"{self.base_url}/api/providers", json=payload, timeout=15)
            if res.status_code == 201:
                conn_id = res.json().get("connection", {}).get("id")
                print(f"[+] Berhasil sinkronisasi: {conn_name}")
                # Test credential
                if conn_id:
                    status, detail = test_provider_connection(
                        self.session, f"{self.base_url}/api/providers/{conn_id}/test"
                    )
                    print(f"    - Status tes: {status} ({detail})")
            else:
                print(f"[-] Gagal menambahkan {conn_name}: {res.status_code} - {res.text}")

if __name__ == "__main__":
    base_url, api_key = get_router_credentials()
    explicit = sys.argv[1] if len(sys.argv) > 1 else None
    accounts_path = resolve_accounts_file(explicit)
    print("=" * 60)
    print(f" 9ROUTER AUTO SYNC -> {base_url}")
    print(f" sumber akun       -> {accounts_path}")
    print("=" * 60)
    syncer = RouterSyncer(base_url, api_key, node_name="FreeModel")
    if syncer.login():
        if syncer.get_or_create_provider_node():
            syncer.sync_keys(accounts_path)
