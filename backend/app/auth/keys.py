"""
API keys: parsing, strength rules, generation. Standard library only (also used by start.sh).

VEGA_API_KEY holds one or more comma-separated entries. Each entry is either a bare secret or
`name:secret`. The name only labels who is calling in the audit log — clients send just the secret,
so a person can be revoked by deleting their entry without touching anyone else.

    VEGA_API_KEY=<name>:<43-char secret>,<name>:<43-char secret>      (a name is any lowercase word)
"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import NamedTuple

MIN_KEY_LENGTH = 32          # token_urlsafe(32) is 43 characters / 256 bits
MIN_DISTINCT_CHARS = 12
_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,23}$')


class Entry(NamedTuple):
    name: str
    secret: str


def split_entry(raw: str, position: int) -> Entry | None:
    """'<name>:secret' → Entry('<name>','secret'); 'secret' → Entry('key<position>','secret')."""
    raw = raw.strip()
    if not raw:
        return None
    head, sep, tail = raw.partition(':')
    if sep and _NAME.match(head) and tail:
        return Entry(head, tail.strip())
    return Entry(f'key{position}', raw)


def parse_entries(raw: str | None) -> list[Entry]:
    entries = []
    for position, part in enumerate((raw or '').split(','), start=1):
        entry = split_entry(part, position)
        if entry:
            entries.append(entry)
    return entries


def is_strong(secret: str) -> bool:
    """Long, and not trivially repetitive. Short or guessable keys are never accepted."""
    return len(secret) >= MIN_KEY_LENGTH and len(set(secret)) >= MIN_DISTINCT_CHARS


def usable_entries(raw: str | None) -> list[Entry]:
    return [e for e in parse_entries(raw) if is_strong(e.secret)]


def weak_entries(raw: str | None) -> list[Entry]:
    return [e for e in parse_entries(raw) if not is_strong(e.secret)]


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def fingerprint(secret: str) -> str:
    """Short, non-reversible label for telling two keys apart in logs (never the key itself)."""
    return hashlib.sha256(secret.encode()).hexdigest()[:8]


def first_secret(raw: str | None) -> str:
    """The secret a client on this machine should send (the first usable entry)."""
    usable = usable_entries(raw)
    return usable[0].secret if usable else ''
