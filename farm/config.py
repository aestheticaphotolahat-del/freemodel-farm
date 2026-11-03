"""Konfigurasi terpusat: path proyek, .env, dan pemuatan pool Telegram."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .envfile import env_float, env_int, load_env
from .telegram import TelegramPool
from .verifier import MockVerifier, SessionVerifier, Verifier

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT_ROOT / "state"
SESSIONS_DIR = PROJECT_ROOT / "telegram_sessions"
CONFIG_DIR = PROJECT_ROOT / "config"

ACCOUNTS_FILE = STATE_DIR / "accounts.json"
EVENTS_FILE = STATE_DIR / "events.jsonl"
LEGACY_ACCOUNTS_FILE = PROJECT_ROOT / "freemodel_accounts.json"
SLOTS_FILE = CONFIG_DIR / "telegram_accounts.json"
BINDINGS_FILE = STATE_DIR / "telegram_bindings.json"
FARM_SETTINGS_FILE = CONFIG_DIR / "farm_settings.json"

# Batas tunggu balasan bot Telegram (detik). Tuning, bukan kredensial.
TG_REPLY_TIMEOUT = 45.0


@dataclass(frozen=True)
class Settings:
    base_url: str
    timeout: float
    retries: int
    concurrency: int
    test_model: str
    email_domain: str
    mock_mode: bool
    mock_tg_endpoint: str


def bootstrap_env() -> None:
    """Muat .env dari root proyek tanpa menimpa environment yang sudah ada."""
    load_env(PROJECT_ROOT / ".env")


def load_settings() -> Settings:
    bootstrap_env()
    mock_mode = os.environ.get("FM_MOCK_MODE", "").strip() in ("1", "true", "yes")
    return Settings(
        base_url=os.environ.get("FM_BASE_URL", "https://freemodel.in"),
        timeout=env_float("FM_TIMEOUT", 90.0),
        retries=env_int("FM_RETRIES", 3),
        concurrency=env_int("FM_CONCURRENCY", 1),
        test_model=os.environ.get("FM_TEST_MODEL", "claude-mythos-preview"),
        email_domain=os.environ.get("FM_EMAIL_DOMAIN", "gmail.com"),
        mock_mode=mock_mode,
        mock_tg_endpoint=os.environ.get("FM_MOCK_TG_ENDPOINT", "http://127.0.0.1:8790/tg/claim"),
    )


def ensure_dirs() -> None:
    for directory in (STATE_DIR, SESSIONS_DIR, CONFIG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def load_pool() -> TelegramPool:
    ensure_dirs()
    return TelegramPool(SLOTS_FILE, BINDINGS_FILE, sessions_dir=SESSIONS_DIR)


def router_credentials() -> tuple[str, str]:
    """Base URL + API key router. .env hanya untuk ini, bukan untuk Telegram."""
    bootstrap_env()
    return (
        os.environ.get("ROUTER_BASE_URL", "https://router.aestc.id").rstrip("/"),
        os.environ.get("ROUTER_API_KEY", ""),
    )


def build_verifier(settings: Settings) -> Verifier:
    """Verifier Telegram: sesi per slot dari config, tanpa environment."""
    if settings.mock_mode:
        return MockVerifier(settings.mock_tg_endpoint)
    # Batas tunggu balasan bot; angka tuning, bukan kredensial — tidak dari .env.
    return SessionVerifier(SESSIONS_DIR, wait_seconds=TG_REPLY_TIMEOUT)
