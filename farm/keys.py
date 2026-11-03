"""Berkas output API key: satu key per baris, aman untuk worker paralel."""

from __future__ import annotations

from pathlib import Path

from .store import FileLock

KEY_PREFIX = "fm_"


def read_keys(path: Path) -> list[str]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [l.strip() for l in lines if l.strip().startswith(KEY_PREFIX)]


def append_key(path: Path, api_key: str, email: str = "") -> bool:
    """Tambahkan key kalau belum ada. Return True kalau benar-benar baru."""
    if not api_key.startswith(KEY_PREFIX):
        return False
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with FileLock(lock_path, timeout=30.0):
        existing = set(read_keys(path))
        if api_key in existing:
            return False
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(api_key + "\n")
    return True


def write_keys(path: Path, pairs: list[tuple[str, str]]) -> int:
    """Tulis ulang berkas dari pasangan (email, key). Return jumlah key unik."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: list[str] = []
    for _email, key in pairs:
        if key.startswith(KEY_PREFIX) and key not in seen:
            seen.append(key)
    path.write_text("".join(k + "\n" for k in seen), encoding="utf-8")
    return len(seen)
