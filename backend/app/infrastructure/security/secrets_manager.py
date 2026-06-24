"""
Secrets Manager — secure access to API keys and credentials.

Responsibilities:
  1. Validate that required secrets are present at startup
  2. Provide masked representations for logging/audit
  3. Rotate secrets at runtime (e.g. LLM key pool)
  4. Never expose raw secrets in logs, exports, or error messages

All secrets come from environment variables via Pydantic Settings.
This service provides a controlled access layer with audit awareness.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ...shared.logger import get_logger

logger = get_logger('secrets_manager')


class SecretCategory(str, Enum):
    LLM       = 'llm'
    BROKER    = 'broker'
    EXCHANGE  = 'exchange'
    AWS       = 'aws'
    MEMORY    = 'memory'
    OTHER     = 'other'


@dataclass(frozen=True, slots=True)
class SecretSpec:
    """Definition of a secret the system may need."""
    key: str                            # Settings field name
    category: SecretCategory
    required: bool = False              # required for system to function?
    description: str = ''


# ── Registry of known secrets ────────────────────────────────────────────────

KNOWN_SECRETS: list[SecretSpec] = [
    SecretSpec('llm_api_key',           SecretCategory.LLM,      required=False, description='Primary OpenAI API key'),
    SecretSpec('llm_api_key_2',         SecretCategory.LLM,      required=False, description='Secondary OpenAI key (pool)'),
    SecretSpec('llm_api_key_3',         SecretCategory.LLM,      required=False, description='Tertiary OpenAI key (pool)'),
    SecretSpec('aws_access_key_id',     SecretCategory.AWS,      required=False, description='AWS access key for Bedrock'),
    SecretSpec('aws_secret_access_key', SecretCategory.AWS,      required=False, description='AWS secret key for Bedrock'),
    SecretSpec('indmoney_access_token', SecretCategory.BROKER,   required=False, description='IndMoney broker session token'),
    SecretSpec('zep_api_key',           SecretCategory.MEMORY,   required=False, description='Zep memory API key'),
]


class SecretsManager:
    """Secure accessor for API keys and credentials.

    Thread-safe. Never logs or exports raw secret values.
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._lock = threading.Lock()

    # ── Access ────────────────────────────────────────────────────────────

    def get(self, key: str) -> str:
        """Get a raw secret value. Use only where you actually need the key."""
        val = getattr(self._settings, key, '')
        return str(val) if val else ''

    def get_masked(self, key: str) -> str:
        """Get a masked representation for logging."""
        val = self.get(key)
        return _mask(val) if val else '(not set)'

    def is_set(self, key: str) -> bool:
        """Check if a secret is configured (non-empty)."""
        return bool(self.get(key))

    # ── Validation ────────────────────────────────────────────────────────

    def validate(self, provider: str = '') -> list[str]:
        """Validate that required secrets are present.

        If `provider` is specified, validates only secrets needed for
        that provider (e.g. 'openai' → needs llm_api_key).
        """
        errors: list[str] = []

        # Provider-specific checks
        if provider == 'openai':
            if not self.is_set('llm_api_key'):
                errors.append('LLM_API_KEY is required for OpenAI provider')
        elif provider == 'bedrock':
            if not self.is_set('aws_access_key_id'):
                errors.append('AWS_ACCESS_KEY_ID is required for Bedrock provider')
            if not self.is_set('aws_secret_access_key'):
                errors.append('AWS_SECRET_ACCESS_KEY is required for Bedrock provider')

        # General required secrets
        for spec in KNOWN_SECRETS:
            if spec.required and not self.is_set(spec.key):
                errors.append(f'{spec.key} is required ({spec.description})')

        return errors

    # ── Status ────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Status of all known secrets (masked). Safe for API/logging."""
        result: dict[str, Any] = {}
        for spec in KNOWN_SECRETS:
            result[spec.key] = {
                'category': spec.category.value,
                'set': self.is_set(spec.key),
                'masked': self.get_masked(spec.key),
                'required': spec.required,
                'description': spec.description,
            }
        return result

    def summary(self) -> dict[str, int]:
        """Quick summary: how many secrets are set by category."""
        counts: dict[str, int] = {}
        for spec in KNOWN_SECRETS:
            cat = spec.category.value
            if cat not in counts:
                counts[cat] = 0
            if self.is_set(spec.key):
                counts[cat] += 1
        return counts


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mask(value: str) -> str:
    """Mask a secret for safe display."""
    if not value:
        return '(not set)'
    if len(value) <= 8:
        return '***'
    return value[:4] + '***' + value[-4:]
