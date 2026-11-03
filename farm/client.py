"""Klien HTTP freemodel.in — satu instance = satu requests.Session terisolasi.

Kontrak yang direproduksi dari observasi live:
- GET /register  -> HTML form dengan <input name="_token" value="...">
- POST /register -> butuh _token + cookie sesi; backend lambat (bisa >25s)
- GET /dashboard/api-keys -> butuh sesi login; berisi fm_<key> dan
  globalTelegramVerifier("fm_<id>_<hex>", "https://t.me/FreeModels_bbot?start=verify_...")
"""

from __future__ import annotations

import html
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from .identity import Persona

TOKEN_RE = re.compile(r'name="_token"\s+value="([^"]+)"')
CSRF_META_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)"')
TG_CALL_RE = re.compile(r'globalTelegramVerifier\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)')
TG_CODE_RE = re.compile(r"fm_\d+_[a-f0-9]+")
API_KEY_RE = re.compile(r"fm_[a-f0-9]{32,64}")
REDIRECT_RE = re.compile(r'<title>Redirecting to\s+([^<]+)</title>', re.I)


@dataclass
class RegisterResult:
    ok: bool
    status_code: int
    email: str
    reason: str = ""
    session_cookies: dict[str, str] = field(default_factory=dict)


@dataclass
class DashboardSnapshot:
    authenticated: bool
    api_key: str = ""
    verify_code: str = ""
    bot_url: str = ""
    status_code: int = 0


class FreeModelClient:
    """Satu klien = satu sesi browser virtual, tidak dibagi antar worker."""

    def __init__(
        self,
        base_url: str = "https://freemodel.in",
        persona: Persona | None = None,
        timeout: float = 90.0,
        retries: int = 3,
        proxy: str | None = None,
        backoff_base: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.persona = persona
        self.timeout = timeout
        self.retries = retries
        self.backoff_base = backoff_base
        self.session = requests.Session()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        if persona:
            self.session.headers.update(
                {
                    "User-Agent": persona.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": persona.locale,
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                }
            )

    # ------------------------------------------------------------- primitives
    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "FreeModelClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _sleep_backoff(self, attempt: int) -> None:
        # Jitter supaya N worker tidak menabrak server di detik yang sama.
        delay = self.backoff_base * (2 ** (attempt - 1))
        time.sleep(delay * (0.5 + random.random()))

    def fetch_csrf_token(self) -> str | None:
        for attempt in range(1, self.retries + 1):
            try:
                res = self.session.get(f"{self.base_url}/register", timeout=self.timeout)
                if res.status_code == 200:
                    match = TOKEN_RE.search(res.text) or CSRF_META_RE.search(res.text)
                    if match:
                        return match.group(1)
            except requests.RequestException:
                self._sleep_backoff(attempt)
        return None

    # -------------------------------------------------------------- lifecycle
    def register(self, persona: Persona | None = None) -> RegisterResult:
        persona = persona or self.persona
        if persona is None:
            raise ValueError("Persona wajib diisi untuk registrasi")

        token = self.fetch_csrf_token()
        if not token:
            return RegisterResult(False, 0, persona.email, "Gagal mengambil cross-origin request state verification token")

        payload = {
            "_token": token,
            "name": persona.name,
            "email": persona.email,
            "phone": "",
            "password": persona.password,
            "password_confirmation": persona.password,
        }
        self.session.headers.update(
            {
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/register",
                "Content-Type": "application/x-www-form-urlencoded",
            }
        )

        for attempt in range(1, self.retries + 1):
            try:
                res = self.session.post(
                    f"{self.base_url}/register",
                    data=payload,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
            except requests.exceptions.ReadTimeout:
                # Backend lambat mengirim welcome mail; sesi biasanya sudah jadi.
                res = self.session.get(f"{self.base_url}/dashboard", timeout=30)
            except requests.RequestException as exc:
                if attempt >= self.retries:
                    return RegisterResult(False, 0, persona.email, f"network: {exc}")
                self._sleep_backoff(attempt)
                continue

            if res.status_code == 419:
                # Token kedaluwarsa -> ambil ulang, jangan pakai token lama.
                token = self.fetch_csrf_token()
                if not token:
                    return RegisterResult(False, 419, persona.email, "cross-origin request state verification token kedaluwarsa")
                payload["_token"] = token
                continue

            if res.status_code == 422 or res.status_code == 409:
                return RegisterResult(False, res.status_code, persona.email, _extract_error(res.text))

            if res.status_code in (200, 302) and self.is_authenticated():
                return RegisterResult(
                    True,
                    res.status_code,
                    persona.email,
                    session_cookies=self.session.cookies.get_dict(),
                )

            if attempt < self.retries:
                self._sleep_backoff(attempt)

        return RegisterResult(False, 0, persona.email, "Registrasi gagal setelah retry limit")

    def is_authenticated(self) -> bool:
        try:
            res = self.session.get(f"{self.base_url}/dashboard", timeout=30, allow_redirects=False)
        except requests.RequestException:
            return False
        return res.status_code == 200

    def fetch_dashboard(self) -> DashboardSnapshot:
        for path in ("/dashboard/api-keys", "/dashboard"):
            try:
                res = self.session.get(f"{self.base_url}{path}", timeout=self.timeout)
            except requests.RequestException:
                continue
            if res.status_code != 200:
                continue
            raw = html.unescape(res.text)
            key_match = API_KEY_RE.search(raw)
            code = ""
            bot_url = ""
            tg_match = TG_CALL_RE.search(raw)
            if tg_match:
                code, bot_url = tg_match.group(1), tg_match.group(2).replace("\\/", "/")
            else:
                idx = raw.find("globalTelegramVerifier(")
                if idx != -1:
                    snippet = raw[idx : idx + 400]
                    sub = TG_CODE_RE.search(snippet)
                    if sub:
                        code = sub.group(0)
                        bot_url = f"https://t.me/FreeModels_bbot?start=verify_{code}"
            return DashboardSnapshot(
                authenticated=True,
                api_key=key_match.group(0) if key_match else "",
                verify_code=code,
                bot_url=bot_url,
                status_code=res.status_code,
            )
        return DashboardSnapshot(authenticated=False)

    def test_key(self, api_key: str, model: str = "claude-mythos-preview") -> bool:
        try:
            res = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Jawab satu kata: Online"}],
                    "max_tokens": 10,
                },
                timeout=30,
            )
            return res.status_code == 200
        except requests.RequestException:
            return False

    def list_models(self) -> list[dict[str, Any]]:
        try:
            res = requests.get(f"{self.base_url}/v1/models", timeout=25)
            res.raise_for_status()
            data = res.json()
            return data.get("data", data if isinstance(data, list) else [])
        except (requests.RequestException, ValueError):
            return []


def _extract_error(body: str) -> str:
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200]
