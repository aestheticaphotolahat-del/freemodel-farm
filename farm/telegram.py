"""Pool akun Telegram dengan pemetaan ketat 1 akun freemodel = 1 akun Telegram.

Konfigurasi 100% dari file (`config/telegram_accounts.json`), bukan environment:
blok `app` menyimpan api_id/api_hash, tiap slot membawa `session_string`
(StringSession) atau `session_name` untuk file sesi yang sudah ada.

Aturan inti:
- Satu slot Telegram hanya boleh dipakai untuk SATU akun freemodel, permanen.
- Slot di-lease (reserve) sebelum registrasi supaya dua worker paralel tidak
  pernah mengambil slot yang sama.
- Binding dicatat ke disk, jadi restart tidak membuat slot dipakai ulang.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .store import FileLock, read_json, write_json_atomic


class DuplicateBindingError(RuntimeError):
    """Slot Telegram sudah terikat ke akun lain — pelanggaran aturan 1:1."""


@dataclass(frozen=True)
class TelegramSlot:
    slot_id: str
    phone: str
    session_name: str
    api_id: int
    api_hash: str
    session_string: str | None = None
    proxy: str | None = None

    @property
    def has_session_string(self) -> bool:
        return bool(self.session_string and self.session_string.strip())

    @staticmethod
    def from_dict(raw: dict[str, Any], index: int, app: dict[str, Any] | None = None) -> "TelegramSlot":
        app = app or {}
        # Urutan: nilai slot sendiri -> blok app di file -> kosong (tidak pernah env).
        api_id = raw.get("api_id") or app.get("api_id") or 0
        api_hash = raw.get("api_hash") or app.get("api_hash") or ""
        phone = str(raw.get("phone", "")).strip()
        slot_id = str(raw.get("slot_id") or f"tg-{index:03d}")
        if not phone:
            raise ValueError(f"Slot {slot_id}: field 'phone' wajib diisi")
        session_string = raw.get("session_string")
        return TelegramSlot(
            slot_id=slot_id,
            phone=phone,
            session_name=str(raw.get("session_name") or f"fm_{slot_id}"),
            api_id=int(api_id or 0),
            api_hash=str(api_hash or ""),
            session_string=str(session_string) if session_string else None,
            proxy=raw.get("proxy") or None,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class TelegramPool:
    """Manajemen slot Telegram: lease sementara + binding permanen."""

    RESERVATION_TTL = timedelta(hours=2)

    def __init__(
        self,
        slots_path: Path,
        bindings_path: Path,
        sessions_dir: Path | None = None,
    ) -> None:
        self.slots_path = Path(slots_path)
        self.bindings_path = Path(bindings_path)
        self.sessions_dir = Path(sessions_dir) if sessions_dir else self.slots_path.parent.parent / "telegram_sessions"
        self._lock_path = self.bindings_path.with_suffix(self.bindings_path.suffix + ".lock")
        # RLock: lease()/summary() memanggil available_slots() yang juga butuh mutex.
        self._mutex = threading.RLock()
        self._load()

    # ------------------------------------------------------------------ load
    def _load(self) -> None:
        raw = read_json(self.slots_path, {"slots": []})
        if isinstance(raw, list):
            entries, app = raw, {}
        else:
            entries = raw.get("slots", [])
            app = raw.get("app", {}) or {}
        self._slots: list[TelegramSlot] = [
            TelegramSlot.from_dict(item, index, app)
            for index, item in enumerate(entries, start=1)
        ]
        state = read_json(self.bindings_path, {})
        self._state: dict[str, Any] = {
            "bindings": state.get("bindings", {}),
            "reservations": state.get("reservations", {}),
        }

    def reload(self) -> None:
        with self._mutex:
            self._load()

    # ------------------------------------------------------------------ query
    @property
    def slots(self) -> list[TelegramSlot]:
        return list(self._slots)

    def slot(self, slot_id: str) -> TelegramSlot | None:
        return next((s for s in self._slots if s.slot_id == slot_id), None)

    def binding_for(self, slot_id: str) -> dict[str, Any] | None:
        with self._mutex:
            return self._state["bindings"].get(slot_id)

    def bound_emails(self) -> dict[str, str]:
        with self._mutex:
            return {sid: b.get("email", "") for sid, b in self._state["bindings"].items()}

    def available_slots(self) -> list[TelegramSlot]:
        with self._mutex:
            now = datetime.now(timezone.utc)
            bindings = self._state["bindings"]
            reservations = self._state["reservations"]
            out: list[TelegramSlot] = []
            for slot in self._slots:
                if slot.slot_id in bindings:
                    continue
                reserved = reservations.get(slot.slot_id)
                if reserved:
                    expires = datetime.fromisoformat(reserved["expires_at"])
                    if expires > now:
                        continue
                out.append(slot)
            return out

    # --------------------------------------------------------------- preflight
    def session_path(self, slot: TelegramSlot) -> Path:
        return self.sessions_dir / f"{slot.session_name}.session"

    def slot_ready(self, slot: TelegramSlot) -> tuple[bool, str]:
        """Slot siap dipakai headless? (session string ATAU file sesi + app creds)."""
        if not slot.api_id or not slot.api_hash:
            return False, "api_id/api_hash kosong (isi blok 'app' di config)"
        if slot.has_session_string:
            return True, "session_string"
        path = self.session_path(slot)
        if path.is_file():
            return True, f"file {path.name}"
        return False, "belum ada session_string / file sesi"

    def preflight(self) -> dict[str, Any]:
        """Laporan kesiapan semua slot sebelum farm dijalankan."""
        ready: list[str] = []
        not_ready: list[str] = []
        detail: dict[str, str] = {}
        for slot in self._slots:
            ok, reason = self.slot_ready(slot)
            detail[slot.slot_id] = reason
            (ready if ok else not_ready).append(slot.slot_id)
        return {"ready": ready, "not_ready": not_ready, "detail": detail}

    # ------------------------------------------------------------- mutate API
    def lease(self) -> TelegramSlot | None:
        """Reserve satu slot bebas. Return None kalau pool habis."""
        with self._mutex:
            free = [s for s in self.available_slots() if s.slot_id not in self._state["bindings"]]
            if not free:
                return None
            slot = free[0]
            self._state["reservations"][slot.slot_id] = {
                "token": uuid.uuid4().hex,
                "expires_at": (datetime.now(timezone.utc) + self.RESERVATION_TTL).isoformat(),
            }
            self._persist_locked()
            return slot

    def bind(self, slot_id: str, account_id: str, email: str, verify_code: str = "") -> None:
        """Ikat slot ke akun secara permanen. Menolak pemakaian ganda."""
        with self._mutex:
            existing = self._state["bindings"].get(slot_id)
            if existing and existing.get("account_id") != account_id:
                raise DuplicateBindingError(
                    f"Slot {slot_id} sudah terikat ke akun {existing.get('email')}; "
                    f"aturan 1 akun = 1 Telegram dilanggar"
                )
            for sid, binding in self._state["bindings"].items():
                if sid != slot_id and binding.get("email", "").lower() == email.lower():
                    raise DuplicateBindingError(
                        f"Akun {email} sudah memakai slot Telegram {sid}"
                    )
            self._state["bindings"][slot_id] = {
                "account_id": account_id,
                "email": email,
                "verify_code": verify_code,
                "bound_at": datetime.now(timezone.utc).isoformat(),
            }
            self._state["reservations"].pop(slot_id, None)
            self._persist_locked()

    def release(self, slot_id: str) -> None:
        """Lepas reservasi (registrasi gagal) supaya slot bisa dipakai lagi."""
        with self._mutex:
            if slot_id in self._state["bindings"]:
                return
            self._state["reservations"].pop(slot_id, None)
            self._persist_locked()

    def _persist_locked(self) -> None:
        with FileLock(self._lock_path, timeout=30):
            write_json_atomic(
                self.bindings_path,
                {"bindings": self._state["bindings"], "reservations": self._state["reservations"]},
            )

    def summary(self) -> dict[str, int]:
        with self._mutex:
            return {
                "total": len(self._slots),
                "bound": len(self._state["bindings"]),
                "reserved": len(self._state["reservations"]),
                "free": len(self.available_slots()),
            }


def dump_slots_template(path: Path, count: int = 1) -> None:
    """Template konfigurasi: blok app + slot dengan session_string kosong."""
    data = {
        "app": {"api_id": 0, "api_hash": ""},
        "slots": [
            {
                "slot_id": f"tg-{i:03d}",
                "phone": f"+62XXXXXXXXX{i:02d}",
                "session_name": f"fm_tg_{i:03d}",
                "session_string": "",
                "proxy": None,
            }
            for i in range(1, count + 1)
        ],
    }
    write_json_atomic(Path(path), data)
    json.dumps(data)
