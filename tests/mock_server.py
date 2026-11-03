#!/usr/bin/env python3
"""Mock freemodel.in yang mereproduksi kontrak hasil observasi live.

Dipakai untuk menguji orkestrasi multi-instance tanpa menyentuh layanan asli:
- GET  /register            -> form + <input name="_token">, Set-Cookie sesi
- POST /register            -> 419 tanpa token valid, 409 email duplikat, 302 sukses
- GET  /dashboard           -> 302 ke /login bila belum login
- GET  /dashboard/api-keys  -> fm_<key> + globalTelegramVerifier("fm_..","https://t.me/..")
- POST /tg/claim            -> simulasi bot; menegakkan 1 Telegram = 1 akun
- GET  /v1/models, POST /v1/chat/completions
- GET  /_state              -> dump state untuk asersi test
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LATENCY = float(os.environ.get("FM_MOCK_LATENCY", "0"))


class MockState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, dict] = {}
        self.tokens: dict[str, str] = {}          # token -> session_id
        self.users: dict[str, dict] = {}          # email -> record
        self.codes: dict[str, str] = {}           # verify_code -> email
        self.tg_claims: dict[str, str] = {}       # tg_slot -> email
        self.keys: dict[str, str] = {}            # api_key -> email
        self.register_calls = 0
        self.reject_reason: str | None = None


STATE = MockState()

REGISTER_HTML = """<!doctype html><html><head><title>Daftar</title>
<meta name="robots" content="index, follow"></head><body>
<form method="POST" action="/register">
<input type="hidden" name="_token" value="{token}">
<input name="name"><input name="email"><input name="phone">
<input type="password" name="password"><input type="password" name="password_confirmation">
<button type="submit">Daftar</button></form></body></html>"""

DASHBOARD_HTML = """<!doctype html><html><head><title>Dashboard</title></head><body>
<div>API Key: <code>{api_key}</code></div>
<script>
  globalTelegramVerifier("{code}", "https://t.me/FreeModels_bbot?start=verify_{code}");
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature bawaan
        pass

    # ------------------------------------------------------------------ util
    def _send(self, status: int, body: str = "", content_type: str = "text/html",
              headers: dict[str, str] | None = None) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data), "application/json")

    def _session_id(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        match = re.search(r"laravel_session=([^;]+)", raw)
        return match.group(1) if match else None

    def _session(self) -> dict | None:
        sid = self._session_id()
        return STATE.sessions.get(sid) if sid else None

    def _new_session(self) -> str:
        sid = secrets.token_hex(20)
        with STATE.lock:
            STATE.sessions[sid] = {"email": None}
        return sid

    # ------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path

        if path == "/register":
            sid = self._session_id() or self._new_session()
            token = secrets.token_hex(24)
            with STATE.lock:
                STATE.tokens[token] = sid
            self._send(200, REGISTER_HTML.format(token=token),
                       headers={"Set-Cookie": f"laravel_session={sid}; Path=/"})
            return

        if path == "/dashboard":
            if not (self._session() or {}).get("email"):
                self._send(302, "", headers={"Location": "/login"})
                return
            self._send(200, "<html><body>Dashboard</body></html>")
            return

        if path == "/dashboard/api-keys":
            session = self._session() or {}
            email = session.get("email")
            if not email:
                self._send(302, "", headers={"Location": "/login"})
                return
            with STATE.lock:
                user = STATE.users.get(email, {})
            self._send(200, DASHBOARD_HTML.format(
                api_key=user.get("api_key", ""), code=user.get("verify_code", "")))
            return

        if path == "/v1/models":
            self._json(200, {"data": [
                {"id": "claude-mythos-preview", "name": "Claude Mythos Preview",
                 "context_window": 1000000, "capabilities": ["reasoning", "tools"]},
                {"id": "gpt-6-astra", "name": "GPT-6 Astra",
                 "context_window": 1000000, "capabilities": ["reasoning"]},
            ]})
            return

        if path == "/_state":
            with STATE.lock:
                self._json(200, {
                    "users": {e: {k: v for k, v in u.items() if k != "password"}
                              for e, u in STATE.users.items()},
                    "tg_claims": STATE.tg_claims,
                    "keys": STATE.keys,
                    "register_calls": STATE.register_calls,
                })
            return

        self._send(404, "not found")

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""

        if path == "/register":
            self._handle_register(raw)
            return

        if path == "/tg/claim":
            self._handle_tg_claim(raw)
            return

        if path == "/v1/chat/completions":
            self._handle_completion(raw)
            return

        self._send(404, "not found")

    # -------------------------------------------------------------- handlers
    def _handle_register(self, raw: bytes) -> None:
        if LATENCY:
            time.sleep(LATENCY)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8")).items()}
        sid = self._session_id()
        token = form.get("_token", "")

        with STATE.lock:
            STATE.register_calls += 1

        if not sid or not token or STATE.tokens.get(token) != sid:
            self._send(419, "<html>Page Expired</html>")
            return

        email = (form.get("email") or "").strip().lower()
        password = form.get("password") or ""
        confirmation = form.get("password_confirmation") or ""
        if not email or "@" not in email or password != confirmation or len(password) < 8:
            self._send(422, "<html>Validasi gagal</html>")
            return

        with STATE.lock:
            if email in STATE.users:
                self._send(409, "<html>Email sudah terdaftar</html>")
                return
            code = f"fm_{1000 + len(STATE.users)}_{secrets.token_hex(4)}"
            key = f"fm_{secrets.token_hex(32)}"
            STATE.users[email] = {
                "email": email, "name": form.get("name", ""), "password": password,
                "verify_code": code, "api_key": key, "verified": False,
            }
            STATE.codes[code] = email
            STATE.keys[key] = email
            STATE.sessions[sid]["email"] = email
            STATE.tokens.pop(token, None)

        self._send(302, "", headers={"Location": "/dashboard"})

    def _handle_tg_claim(self, raw: bytes) -> None:
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            self._json(400, {"error": "invalid json"})
            return
        tg_slot = str(data.get("tg_slot", ""))
        code = str(data.get("code", "")).replace("verify_", "")

        with STATE.lock:
            email = STATE.codes.get(code)
            if not email:
                self._json(404, {"error": "kode verifikasi tidak dikenal"})
                return
            owner = STATE.tg_claims.get(tg_slot)
            if owner and owner != email:
                self._json(409, {
                    "error": "telegram_already_used",
                    "detail": f"slot {tg_slot} sudah dipakai akun {owner}",
                })
                return
            STATE.tg_claims[tg_slot] = email
            STATE.users[email]["verified"] = True
            STATE.users[email]["tg_slot"] = tg_slot

        self._json(200, {"message": f"verified: akun {email} aktif, kuota terbuka"})

    def _handle_completion(self, raw: bytes) -> None:
        auth = self.headers.get("Authorization", "").replace("Bearer ", "").strip()
        with STATE.lock:
            known = auth in STATE.keys
        if not known:
            self._json(401, {"error": {"message": "invalid api key"}})
            return
        self._json(200, {"choices": [{"message": {"role": "assistant", "content": "Online"}}]})


def start_server(port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def reset_state() -> None:
    with STATE.lock:
        STATE.sessions.clear()
        STATE.tokens.clear()
        STATE.users.clear()
        STATE.codes.clear()
        STATE.tg_claims.clear()
        STATE.keys.clear()
        STATE.register_calls = 0


if __name__ == "__main__":
    port = int(os.environ.get("FM_MOCK_PORT", "8790"))
    srv, actual = start_server(port)
    print(f"Mock freemodel.in berjalan di http://127.0.0.1:{actual}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()
