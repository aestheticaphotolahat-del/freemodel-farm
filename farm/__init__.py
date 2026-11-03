"""freemodel-farm: registrasi multi-instance + lifecycle akun dengan Telegram 1:1."""

from .client import DashboardSnapshot, FreeModelClient, RegisterResult
from .identity import Persona, build_persona, random_token
from .runner import FarmRunner, InstanceResult, Lifecycle, RunReport
from .store import AccountRecord, AccountStore, FileLock, JsonStore
from .telegram import TelegramPool, TelegramSlot, DuplicateBindingError
from .verifier import MockVerifier, SessionVerifier, VerifyOutcome, classify_reply

__all__ = [
    "AccountRecord",
    "AccountStore",
    "DashboardSnapshot",
    "DuplicateBindingError",
    "FarmRunner",
    "FileLock",
    "FreeModelClient",
    "InstanceResult",
    "JsonStore",
    "Lifecycle",
    "MockVerifier",
    "Persona",
    "RegisterResult",
    "RunReport",
    "TelegramPool",
    "TelegramSlot",
    "SessionVerifier",
    "VerifyOutcome",
    "build_persona",
    "classify_reply",
    "random_token",
]

__version__ = "2.0.0"
