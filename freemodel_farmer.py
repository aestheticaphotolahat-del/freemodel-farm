import html
#!/usr/bin/env python3
# ==============================================================================
# FREEMODEL.IN ACCOUNT FARMER & API TESTER
# ==============================================================================

import requests
import re
import random
import string
import time
import json
from typing import Optional, Dict, Any, List

BASE_URL = "https://freemodel.in"
API_V1 = f"{BASE_URL}/v1"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive"
}

def random_string(length: int = 8) -> str:
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def random_name() -> str:
    first_names = ["Alex", "Dante", "Victor", "Rhea", "Cipher", "Kael", "Nova", "Zero", "Ares"]
    last_names = ["Vance", "Ryker", "Sterling", "Kovacs", "Cross", "Stark", "Draven"]
    return f"{random.choice(first_names)} {random.choice(last_names)} {random_string(4)}"

class FreeModelFarmer:
    def __init__(self, timeout: int = 90, retries: int = 3):
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def _fetch_csrf_token(self) -> Optional[str]:
        for attempt in range(1, self.retries + 1):
            try:
                r = self.session.get(f"{BASE_URL}/register", timeout=self.timeout)
                if r.status_code == 200:
                    match = re.search(r'name="_token"\s+value="([^"]+)"', r.text)
                    if match:
                        return match.group(1)
            except requests.RequestException:
                time.sleep(2)
        return None

    def register_account(self, email: Optional[str] = None, password: Optional[str] = None) -> Optional[Dict[str, Any]]:
        tag = random_string(6)
        email = email or f"farm_{tag}@gmail.com"
        password = password or f"P@ss_{tag}_{random_string(4)}!"
        name = random_name()

        print(f"[*] Menyiapkan registrasi: {email}")
        csrf_token = self._fetch_csrf_token()
        if not csrf_token:
            print("[-] Gagal mengekstrak CSRF token dari /register.")
            return None

        payload = {
            "_token": csrf_token,
            "name": name,
            "email": email,
            "phone": "",
            "password": password,
            "password_confirmation": password
        }

        self.session.headers.update({
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/register"
        })

        for attempt in range(1, self.retries + 1):
            try:
                print(f"    [->] Mengirim data registrasi (percobaan {attempt}/{self.retries})...")
                try:
                    res = self.session.post(f"{BASE_URL}/register", data=payload, timeout=self.timeout, allow_redirects=True)
                except requests.exceptions.ReadTimeout:
                    print("    [*] Server timeout kirim email, memverifikasi sesi login...")
                    res = self.session.get(f"{BASE_URL}/dashboard", timeout=30)
                
                if res.status_code in [200, 302]:
                    # Ekstraksi kode aktivasi Telegram & API key
                    tg_match = re.search(r'globalTelegramVerifier\("([^"]+)",\s*"([^"]+)"\)', res.text)
                    verify_code = tg_match.group(1) if tg_match else None
                    bot_url = tg_match.group(2).replace(r"\/", "/") if tg_match else None
                    
                    cookies = self.session.cookies.get_dict()
                    api_key, verify_code, bot_url = self._extract_key_and_telegram()

                    
                    # Auto Telegram Verifier jika dikonfigurasi
                    if verify_code and os.path.exists(os.path.join(os.path.dirname(__file__), "tg_verifier.py")):
                        try:
                            import subprocess
                            print(f"    [*] Mencoba auto-verify Telegram via @FreeModels_bbot...")
                            cmd = ["py", "-3.12", os.path.join(os.path.dirname(__file__), "tg_verifier.py"), verify_code]
                            subp = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                            if subp.returncode == 0:
                                print(f"    [+] Telegram auto-verified: {subp.stdout.strip()}")
                            else:
                                print(f"    [!] Telegram auto-verify skip (cek config TG_API_ID): {subp.stdout or subp.stderr}")
                        except Exception as tg_err:
                            print(f"    [!] Telegram auto-verify error: {tg_err}")

                    account_data = {
                        "name": name,
                        "email": email,
                        "password": password,
                        "cookies": cookies,
                        "telegram_verify_code": verify_code,
                        "telegram_bot_url": bot_url,
                        "api_key": api_key,
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
                    }

                    print(f"[+] Akun berhasil dibuat: {email}")
                    if verify_code:
                        print(f"    - Telegram Verify Code: {verify_code}")
                        print(f"    - Bot Claim Link: {bot_url}")
                    if api_key:
                        print(f"    - API Key: {api_key}")

                    return account_data

            except requests.exceptions.RequestException as e:
                print(f"    [!] Timeout/Network issue: {e}")
                time.sleep(3)

        print("[-] Gagal menyelesaikan registrasi setelah retry limit.")
        return None

    def _extract_key_and_telegram(self):
        api_key = None
        verify_code = None
        bot_url = None
        try:
            r = self.session.get(f"{BASE_URL}/dashboard/api-keys", timeout=self.timeout)
            if r.status_code == 200:
                raw = html.unescape(r.text)
                matches = re.findall(r'fm_[a-f0-9]{32,64}', raw)
                if matches:
                    api_key = matches[0]

                idx = raw.find('globalTelegramVerifier(')
                if idx != -1:
                    snippet = raw[idx:idx+300]
                    sub = re.findall(r'fm_[0-9]+_[a-f0-9]+', snippet)
                    if sub:
                        verify_code = sub[0]
                        bot_url = f"https://t.me/FreeModels_bbot?start=verify_{verify_code}"
        except Exception as e:
            print(f"    [!] Gagal scraping api-keys: {e}")

        return api_key, verify_code, bot_url
class FreeModelAPI:
    @staticmethod
    def list_models() -> List[Dict[str, Any]]:
        print(f"\n[*] Mengambil daftar model dari {API_V1}/models...")
        try:
            r = requests.get(f"{API_V1}/models", headers={"Accept": "application/json"}, timeout=25)
            r.raise_for_status()
            data = r.json()
            models = data.get("data", [])
            print(f"[+] Total model terdaftar: {len(models)}\n")
            print(f"{'ID MODEL':<30} | {'NAMA':<25} | {'CONTEXT':<10} | {'CAPABILITIES'}")
            print("-" * 90)
            for m in models:
                mid = m.get("id", "-")
                name = m.get("name", "-")
                ctx = str(m.get("context_window", "-"))
                caps = ",".join(m.get("capabilities", []))
                print(f"{mid:<30} | {name:<25} | {ctx:<10} | {caps}")
            return models
        except Exception as e:
            print(f"[-] Gagal mengambil model list: {e}")
            return []

    @staticmethod
    def test_completion(api_key: str, model: str = "claude-haiku-5.5") -> bool:
        print(f"\n[*] Menguji chat completion via API key: {api_key[:10]}...")
        url = f"{API_V1}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "Jawab singkat dalam 1 kata: 'Online'"}],
            "max_tokens": 10
        }

        try:
            r = requests.post(url, headers=headers, json=body, timeout=30)
            if r.status_code == 200:
                resp = r.json()
                reply = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                print(f"[+] Response model ({model}): {reply.strip()}")
                return True
            else:
                print(f"[-] API Error {r.status_code}: {r.text}")
                return False
        except Exception as e:
            print(f"[-] Request gagal: {e}")
            return False

def run_farm(count: int = 1, output_file: str = "freemodel_accounts.json"):
    accounts = []
    farmer = FreeModelFarmer(timeout=90, retries=3)

    for i in range(count):
        print(f"\n--- Farming akun #{i+1} dari {count} ---")
        acc = farmer.register_account()
        if acc:
            accounts.append(acc)
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(accounts, f, indent=2)
            print(f"[✓] Data tersimpan di {output_file}")
        time.sleep(2)

    return accounts

if __name__ == "__main__":
    print("=" * 60)
    print(" FREEMODEL.IN AUTOMATION & TEST RUNNER")
    print("=" * 60)

    models = FreeModelAPI.list_models()

    print("\n" + "=" * 60)
    print(" MEMULAI FARMING AKUN")
    print("=" * 60)
    farmed = run_farm(count=1)

    if farmed and farmed[0].get("api_key"):
        FreeModelAPI.test_completion(farmed[0]["api_key"])
