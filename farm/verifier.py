"""Verifikasi Telegram headless: sesi per slot, nol prompt, nol environment.

Setiap slot memakai sesi Telegram miliknya sendiri (StringSession dari config
atau file .session yang sudah ada). Verifikasi bot dikirim otomatis oleh kode:
`/start verify_<code>` lalu balasan bot dipolling sampai batas waktu.

Tidak ada input()/OTP di jalur ini — kalau sesi belum sah, kembalikan
VerifyOutcome gagal yang jelas supaya runner bisa melewati slot tersebut.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .telegram import TelegramSlot

BOT_USERNAME = "FreeModels_bbot"
SUCCESS_HINTS = ("verified", "success", "berhasil", "aktif", "active", "claimed", "granted")
FAILURE_HINTS = ("invalid", "already", "used", "expired", "gagal", "tidak valid", "sudah")


@dataclass
class VerifyOutcome:
    ok: bool
    detail: str
    raw_reply: str = ""


class Verifier(Protocol):
    def verify(self, slot: TelegramSlot, verify_code: str) -> VerifyOutcome: ...


def classify_reply(reply: str) -> VerifyOutcome:
    """Terjemahkan balasan bot jadi verdict yang bisa dipakai runner."""
    text = (reply or "").strip()
    lowered = text.lower()
    if any(hint in lowered for hint in FAILURE_HINTS):
        return VerifyOutcome(False, "Bot menolak verifikasi", text)
    if any(hint in lowered for hint in SUCCESS_HINTS):
        return VerifyOutcome(True, "Terverifikasi", text)
    return VerifyOutcome(bool(text), "Balasan diterima, tanpa sinyal gagal", text)


class SessionVerifier:
    """Verifikasi via sesi Telegram yang sudah terotorisasi (headless)."""

    def __init__(self, sessions_dir: Path, wait_seconds: float = 45.0, poll_interval: float = 2.0) -> None:
        self.sessions_dir = Path(sessions_dir)
        self.wait_seconds = wait_seconds
        self.poll_interval = poll_interval
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ public
    def verify(self, slot: TelegramSlot, verify_code: str) -> VerifyOutcome:
        if not slot.api_id or not slot.api_hash:
            return VerifyOutcome(
                False,
                f"api_id/api_hash kosong untuk slot {slot.slot_id} "
                f"(isi blok 'app' di config/telegram_accounts.json)",
            )
        if not slot.has_session_string and not self._session_file(slot).is_file():
            return VerifyOutcome(
                False,
                f"Sesi Telegram untuk slot {slot.slot_id} belum ada; "
                f"set 'session_string' di config untuk slot ini",
            )
        try:
            return asyncio.run(self._verify_async(slot, verify_code))
        except RuntimeError as exc:
            # asyncio.run di dalam loop yang sudah jalan (mis. dari async caller).
            return VerifyOutcome(False, f"Gagal menjalankan verifier: {exc}")

    # ----------------------------------------------------------------- intern
    def _session_file(self, slot: TelegramSlot) -> Path:
        return self.sessions_dir / f"{slot.session_name}.session"

    def _build_client(self, slot: TelegramSlot):
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        proxy = _parse_proxy(slot.proxy) if slot.proxy else None
        if slot.has_session_string:
            return TelegramClient(StringSession(slot.session_string), slot.api_id, slot.api_hash, proxy=proxy)
        return TelegramClient(str(self._session_file(slot)), slot.api_id, slot.api_hash, proxy=proxy)

    async def _verify_async(self, slot: TelegramSlot, verify_code: str) -> VerifyOutcome:
        try:
            import telethon  # noqa: F401
        except ImportError:
            return VerifyOutcome(False, "Telethon belum terpasang (py -3.12 -m pip install telethon)")

        code = (verify_code or "").replace("verify_", "").strip()
        if not code:
            return VerifyOutcome(False, "Kode verifikasi kosong")

        client = self._build_client(slot)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return VerifyOutcome(
                    False,
                    f"Sesi slot {slot.slot_id} tidak terotorisasi; perbarui session_string",
                )
            await client.send_message(BOT_USERNAME, f"/start verify_{code}")
            reply = await self._await_reply(client)
            outcome = classify_reply(reply)
            if not reply:
                return VerifyOutcome(
                    False, f"Bot tidak membalas dalam {self.wait_seconds:.0f}s untuk {code}"
                )
            return outcome
        except Exception as exc:  # noqa: BLE001 - laporkan apa adanya ke runner
            return VerifyOutcome(False, f"Telethon error: {exc}")
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    async def _await_reply(self, client) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.wait_seconds
        last = ""
        while loop.time() < deadline:
            await asyncio.sleep(self.poll_interval)
            messages = await client.get_messages(BOT_USERNAME, limit=1)
            if not messages:
                continue
            last = messages[0].message or messages[0].text or ""
            lowered = last.lower()
            if any(h in lowered for h in SUCCESS_HINTS + FAILURE_HINTS):
                return last
        return last


class MockVerifier:
    """Simulator bot Telegram untuk test end-to-end tanpa akun Telegram asli."""

    def __init__(self, endpoint: str, timeout: float = 10.0) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.calls: list[tuple[str, str]] = []

    def verify(self, slot: TelegramSlot, verify_code: str) -> VerifyOutcome:
        import requests

        self.calls.append((slot.slot_id, verify_code))
        try:
            res = requests.post(
                self.endpoint,
                json={"tg_slot": slot.slot_id, "tg_phone": slot.phone, "code": verify_code},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return VerifyOutcome(False, f"MockVerifier network: {exc}")
        if res.status_code == 200:
            return VerifyOutcome(True, res.json().get("message", "verified"))
        return VerifyOutcome(False, f"MockVerifier HTTP {res.status_code}: {res.text[:120]}")


# Nama lama dipertahankan agar import lama tidak pecah.
TelethonVerifier = SessionVerifier


def _parse_proxy(proxy: str):
    """Format: socks5://user:pass@host:port atau http://host:port."""
    match = re.match(r"(\w+)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)", proxy)
    if not match:
        return None
    scheme, user, pwd, host, port = match.groups()
    try:
        import socks  # type: ignore
    except ImportError:
        return None
    types = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}
    return (types.get(scheme, socks.SOCKS5), host, int(port), True, user, pwd)
