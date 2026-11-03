#!/usr/bin/env python3
"""Router tiruan untuk membuktikan auto-import tanpa menyentuh router asli.

Jalankan: py -3.12 tests/fake_router.py [port]
Lalu arahkan ROUTER_BASE_URL ke http://127.0.0.1:<port>.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG = Path(__file__).parent.parent / "state" / "fake_router_calls.jsonl"
CONNECTIONS: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):  # senyap
        pass

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _record(self, method: str, body: object) -> None:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"method": method, "path": self.path, "body": body}) + "\n")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode() if length else ""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = raw
        self._record("POST", body)

        if self.path.endswith("/api/auth/login"):
            return self._send(200, {"success": True})
        if self.path.endswith("/api/provider-nodes"):
            return self._send(201, {"node": {"id": "node-1"}})
        if self.path.endswith("/api/providers"):
            CONNECTIONS.append(body or {})
            return self._send(201, {"connection": {"id": f"conn-{len(CONNECTIONS)}"}})
        if self.path.endswith("/test"):
            return self._send(200, {"valid": True})
        return self._send(404, {"error": "tidak ada"})

    def do_GET(self) -> None:
        self._record("GET", None)
        if self.path.endswith("/api/provider-nodes"):
            return self._send(200, {"nodes": []})
        if self.path.endswith("/api/providers"):
            return self._send(200, {"connections": CONNECTIONS})
        if self.path.endswith("/_state"):
            return self._send(200, {"connections": CONNECTIONS})
        return self._send(404, {"error": "tidak ada"})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
    print(f"router tiruan di http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
