"""Penyimpanan state yang aman untuk akses konkuren (thread + proses).

Semua penulisan lewat file lock eksklusif dan os.replace atomik, jadi N
worker paralel tidak bisa saling menimpa record akun.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

IS_WINDOWS = os.name == "nt"


def _try_lock(handle) -> bool:
    """Coba ambil lock eksklusif non-blocking. True kalau berhasil."""
    try:
        if IS_WINDOWS:
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - POSIX
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle) -> None:
    try:
        if IS_WINDOWS:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - POSIX
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@dataclass
class LockTimeout(RuntimeError):
    path: str

    def __str__(self) -> str:  # pragma: no cover - pesan saja
        return f"Gagal mengambil lock pada {self.path}"


class FileLock:
    """Lock eksklusif lintas proses berbasis OS file locking."""

    def __init__(self, path: Path, timeout: float = 30.0) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self._handle: Any = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+b")
        deadline = time.monotonic() + self.timeout
        while True:
            if _try_lock(self._handle):
                return self
            if time.monotonic() >= deadline:
                self._handle.close()
                self._handle = None
                raise LockTimeout(str(self.path))
            time.sleep(0.02)

    def __exit__(self, *_exc: object) -> None:
        if self._handle is None:
            return
        try:
            _unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class JsonStore:
    """Dokumen JSON tunggal dengan lock; aman untuk update paralel."""

    def __init__(self, path: Path, lock_timeout: float = 30.0) -> None:
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.lock_timeout = lock_timeout

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        with FileLock(self._lock_path, self.lock_timeout):
            data = read_json(self.path, {})
            if not isinstance(data, dict):
                data = {}
            yield data
            write_json_atomic(self.path, data)

    def read(self) -> dict[str, Any]:
        data = read_json(self.path, {})
        return data if isinstance(data, dict) else {}


@dataclass
class AccountRecord:
    """Satu akun freemodel + slot Telegram yang terikat 1:1."""

    account_id: str
    email: str
    name: str
    password: str
    telegram_slot: str
    telegram_verify_code: str = ""
    telegram_bot_url: str = ""
    api_key: str = ""
    state: str = "pending"
    attempts: int = 0
    last_error: str = ""
    created_at: str = ""
    updated_at: str = ""
    history: list[dict[str, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.created_at = self.created_at or now
        self.updated_at = now
        if self.history is None:
            self.history = []

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class AccountStore(JsonStore):
    """Registry akun: daftar record + event log lifecycle."""

    def __init__(self, path: Path, events_path: Path, lock_timeout: float = 30.0) -> None:
        super().__init__(path, lock_timeout)
        self.events_path = Path(events_path)

    def upsert(self, record: AccountRecord) -> None:
        record.updated_at = datetime.now(timezone.utc).isoformat()
        with self.transaction() as doc:
            accounts = doc.setdefault("accounts", {})
            accounts[record.account_id] = record.as_dict()

    def accounts(self) -> list[dict[str, Any]]:
        return list(self.read().get("accounts", {}).values())

    def find_by_email(self, email: str) -> dict[str, Any] | None:
        for acc in self.accounts():
            if acc.get("email", "").lower() == email.lower():
                return acc
        return None

    def log_event(self, account_id: str, state: str, detail: str = "") -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "account_id": account_id,
            "state": state,
            "detail": detail,
        }
        line = json.dumps(entry, ensure_ascii=False)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.events_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
