"""Remove credentials from text before it leaves the server (log API, audit)."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'), '<jwt>'),
    (re.compile(r'AIza[0-9A-Za-z_-]{30,}'), '<api-key>'),
    (re.compile(r'(?i)(x-vega-key|authorization|proxy-authorization)(["\']?\s*[:=]\s*["\']?)(?:bearer\s+|basic\s+)?[^\s,"\'}]+'),
     r'\1\2<redacted>'),
    # cookie headers: a quoted value (as in a printed dict) ends at its closing quote, a bare one at ';' or end of line
    (re.compile(r'(?i)((?:set-)?cookie["\']?\s*[:=]\s*)(["\'])(.*?)\2'), r'\1\2<redacted>\2'),
    (re.compile(r'(?i)((?:set-)?cookie\s*[:=]\s*)[^"\'\n]+'), r'\1<redacted>'),
    (re.compile(r'(?i)([?&](?:key|token|access_token|api_key|apikey|secret|password)=)[^&\s"\']+'), r'\1<redacted>'),
    (re.compile(r'(?i)\b(api[_-]?key|api[_-]?secret|access[_-]?token|client[_-]?secret|secret|password|passwd|token)'
                r'(["\']?\s*[:=]\s*["\']?)[^\s,"\'}\]]{6,}'), r'\1\2<redacted>'),
    # a bare `key=…` / `apikey: …` (word boundary, so `cache_key=` and `keyword=` are untouched)
    (re.compile(r'(?i)\b(key)(\s*[:=]\s*["\']?)[^\s,"\'}\]]{6,}'), r'\1\2<redacted>'),
    (re.compile(r'__cf_bm=[^;\s"\']+'), '__cf_bm=<redacted>'),
]


def redact(text: str, extra_secrets: list[str] | None = None) -> str:
    """Blank credentials. `extra_secrets` are exact strings (e.g. the configured API keys) to remove anywhere."""
    for secret in extra_secrets or []:
        if len(secret) >= 8:
            text = text.replace(secret, '<redacted>')
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
