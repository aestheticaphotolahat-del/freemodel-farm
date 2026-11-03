"""Pengaturan farm yang persisten — satu sumber kebenaran untuk TUI, CLI, runner.

Setara `config.json` di pola farm lain: semua tombol menu menulis ke sini,
runner membacanya. Kredensial router tetap di .env, bukan di file ini.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .store import read_json, write_json_atomic

DEFAULTS: dict[str, Any] = {
    "proxy": {
        "enabled": False,
        "file": "pools/proxies.txt",
    },
    "worker": {
        "concurrency": 3,
        "retries": 3,
        "timeoutSec": 90,
        "target": 0,          # 0 = semua slot bebas
    },
    "output": {
        "keysFile": "keys.txt",
        "accountsFile": "state/accounts.json",
        "logsDir": "logs",
        "clearLogsOnStart": True,
    },
    "router": {
        "autoImport": True,
        "baseUrl": "https://router.aestc.id",
        "nodeName": "FreeModel",
    },
    "telegram": {
        "slotsFile": "config/telegram_accounts.json",
    },
}


def merge_defaults(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """Deep-merge: nilai di disk menang, kunci yang hilang diisi default."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_defaults(out[key], value)
        else:
            out[key] = value
    return out


class FarmSettings:
    """Dokumen pengaturan dengan akses bertitik (`router.autoImport`)."""

    def __init__(self, path: Path, root: Path | None = None) -> None:
        self.path = Path(path)
        self.root = Path(root) if root else self.path.parent.parent
        self._data = merge_defaults(DEFAULTS, read_json(self.path, {}))

    # ------------------------------------------------------------------ akses
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node: dict[str, Any] = self._data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
        self.save()

    def toggle(self, dotted: str) -> bool:
        new_value = not bool(self.get(dotted))
        self.set(dotted, new_value)
        return new_value

    def save(self) -> None:
        write_json_atomic(self.path, self._data)

    def reset(self) -> None:
        self._data = copy.deepcopy(DEFAULTS)
        self.save()

    # ------------------------------------------------------------------ path
    def abs_path(self, dotted: str) -> Path:
        raw = str(self.get(dotted, "") or "")
        path = Path(raw)
        return path if path.is_absolute() else (self.root / path)

    def proxy_list(self) -> list[str]:
        """Baca pool proxy dari disk; baris kosong dan komentar diabaikan."""
        path = self.abs_path("proxy.file")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]

    @property
    def data(self) -> dict[str, Any]:
        return self._data
