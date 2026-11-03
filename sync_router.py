#!/usr/bin/env python3
import json
import os
import re
import sys
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
        for n in nodes:
            if n.get("type") == "openai-compatible" and (n.get("name", "").lower() == self.node_name.lower() or n.get("prefix", "").lower() == self.node_name.lower()):
                self.provider_id = n.get("id")
                print(f"[+] Ditemukan provider node: {self.node_name} (ID: {self.provider_id})")
                return self.provider_id

        # Buat custom node jika belum ada
        print(f"[*] Membuat provider node baru: {self.node_name}...")
        create_url = f"{self.base_url}/api/provider-nodes"
        payload = {
            "name": self.node_name,
            "prefix": "freemodel",
            "type": "openai-compatible",
            "baseUrl": "https://freemodel.in/v1",
            "models": ["claude-mythos-preview", "claude-fable-5", "claude-opus-5-5", "gpt-6-astra", "gemini-3.7-flash"]
        }
        r_create = self.session.post(create_url, json=payload, timeout=15)
        if r_create.status_code in [200, 201]:
            self.provider_id = r_create.json().get("node", {}).get("id")
            print(f"[+] Provider node {self.node_name} dibuat (ID: {self.provider_id})")
            return self.provider_id
        else:
            print(f"[-] Gagal membuat provider node: {r_create.status_code} - {r_create.text}")
            return None

    def sync_keys(self, accounts_file="freemodel_accounts.json"):
        if not os.path.exists(accounts_file):
            print(f"[-] File akun {accounts_file} tidak ditemukan.")
            return

        with open(accounts_file, "r", encoding="utf-8") as f:
            accounts = json.load(f)

        keys_to_sync = []
        for acc in accounts:
            key = acc.get("api_key")
            email = acc.get("email")
            if key and key.startswith("cc_"):
                keys_to_sync.append((email, key))

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
                    test_res = self.session.post(f"{self.base_url}/api/providers/{conn_id}/test", timeout=15)
                    print(f"    - Status tes: {test_res.json().get('valid')}")
            else:
                print(f"[-] Gagal menambahkan {conn_name}: {res.status_code} - {res.text}")

if __name__ == "__main__":
    base_url, api_key = get_router_credentials()
    print("=" * 60)
    print(f" 9ROUTER AUTO SYNC -> {base_url}")
    print("=" * 60)
    syncer = RouterSyncer(base_url, api_key, node_name="FreeModel")
    if syncer.login():
        if syncer.get_or_create_provider_node():
            syncer.sync_keys()
