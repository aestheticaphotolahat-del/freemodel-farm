"""Identitas per-instance: persona, user-agent, dan email unik.

Setiap instance registrasi memakai identitas yang berdiri sendiri supaya
tidak ada sinyal silang antar akun (UA, email, locale, timezone).
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone

FIRST_NAMES = [
    "Alex", "Dante", "Victor", "Rhea", "Cipher", "Kael", "Nova", "Zero",
    "Ares", "Mira", "Ezra", "Lyra", "Orion", "Sable", "Rowan", "Vesper",
]
LAST_NAMES = [
    "Vance", "Ryker", "Sterling", "Kovacs", "Cross", "Stark", "Draven",
    "Hale", "Mercer", "Voss", "Kane", "Rourke", "Ashford", "Thorne",
]
LOCALES = ["en-US,en;q=0.9", "en-GB,en;q=0.8", "en-US,en;q=0.9,id;q=0.7"]

# UA per platform — dipilih acak per instance, bukan hardcode satu string.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
]

_ALPHABET = string.ascii_lowercase + string.digits


def random_token(length: int = 8, alphabet: str = _ALPHABET) -> str:
    return "".join(random.choices(alphabet, k=length))


@dataclass(frozen=True)
class Persona:
    """Satu identitas registrasi. Immutable supaya aman dibagi antar thread."""

    name: str
    email: str
    password: str
    user_agent: str
    locale: str
    tag: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "email": self.email,
            "user_agent": self.user_agent,
            "locale": self.locale,
            "tag": self.tag,
            "created_at": self.created_at,
        }


def build_persona(tag_prefix: str = "fm", email_domain: str = "gmail.com") -> Persona:
    tag = random_token(10)
    name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)} {random_token(4)}"
    password = f"P@{random_token(6)}{random.choice('!@#$%^&*')}{random_token(4)}A"
    return Persona(
        name=name,
        email=f"{tag_prefix}_{tag}@{email_domain}",
        password=password,
        user_agent=random.choice(USER_AGENTS),
        locale=random.choice(LOCALES),
        tag=tag,
    )
