"""Sink keluaran per key: berkas keys.txt + auto-import ke router.

Dipanggil runner setiap satu API key selesai diuji, jadi key langsung masuk
router tanpa langkah manual terpisah.
"""

from __future__ import annotations

from pathlib import Path

from .keys import append_key
from .router import RouterImporter


class KeySink:
    def __init__(self, keys_path: Path, importer: RouterImporter | None = None) -> None:
        self.keys_path = Path(keys_path)
        self.importer = importer
        self.router_ready = False

    def prepare(self) -> str:
        """Siapkan router sekali di awal run; kalau gagal, sink tetap jalan."""
        if self.importer is None:
            return "tanpa router: hanya menulis keys.txt"
        if not self.importer.active():
            self.importer = None
            return "tanpa router: kredensial belum diatur"
        if not self.importer.login():
            self.importer = None
            return "tanpa router: login gagal"
        self.router_ready = True
        return "router OK"

    def handle(self, email: str, api_key: str) -> str:
        notes: list[str] = []
        if append_key(self.keys_path, api_key, email):
            notes.append(f"keys.txt {self.keys_path.name} +1")
        if self.importer is None:
            notes.append("tanpa router")
            return " | ".join(notes)
        outcome = self.importer.import_key(email, api_key)
        notes.append(f"router:{outcome.status}")
        if not outcome.ok and outcome.detail:
            notes.append(outcome.detail)
        return " | ".join(notes)
