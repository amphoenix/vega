"""
LLM Client — multi-provider LLM integration.

Ported from v1's llm_client.py. Supports:
  - OpenAI-compatible APIs with key-pool round-robin + rate-limit cooldown
  - AWS Bedrock via litellm
  - Ollama native /api/chat (bypasses OpenAI-compat bug with think:false)
"""

from __future__ import annotations

import json
import re
import threading
import urllib.request
from typing import Any, Dict, List, Optional

from ...shared.logger import get_logger
from ...shared.time import monotonic, sleep as _sleep

logger = get_logger('llm')


# ── JSON parser — strips reasoning envelopes + markdown fences ────────────────

def parse_llm_json(text: str) -> dict:
    """Strip reasoning envelopes + markdown fences then parse JSON."""
    cleaned = text.strip()
    cleaned = re.sub(
        r'<\s*(thought|thinking|reasoning|reflection|scratchpad)\s*>.*?<\s*/\s*\1\s*>',
        '', cleaned, flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', cleaned, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError(f"LLM returned invalid JSON: {cleaned[:500]}")


# ── OpenAI key pool with rate-limit cooldown ──────────────────────────────────

_COOLDOWN_SEC = 60.0


class _PoolSlot:
    __slots__ = ('client', 'cooldown_until', 'idx')

    def __init__(self, client: Any, idx: int):
        self.client = client
        self.cooldown_until: float = 0.0
        self.idx = idx


class _OpenAIPool:
    def __init__(self, clients: list):
        self.slots = [_PoolSlot(c, i) for i, c in enumerate(clients)]
        self._lock = threading.Lock()
        self._rr = 0

    def acquire(self, timeout_sec: float) -> _PoolSlot:
        deadline = monotonic() + max(0.1, timeout_sec)
        n = len(self.slots)
        while True:
            now = monotonic()
            soonest_free = float('inf')
            with self._lock:
                for offset in range(n):
                    s = self.slots[(self._rr + offset) % n]
                    if s.cooldown_until <= now:
                        self._rr = (s.idx + 1) % n
                        return s
                    if s.cooldown_until < soonest_free:
                        soonest_free = s.cooldown_until
            wait = min(soonest_free - now, deadline - now)
            if wait <= 0:
                raise TimeoutError(
                    f"All {n} keys cooling down; none available in {timeout_sec:.1f}s"
                )
            _sleep(min(max(wait, 0.05), 1.0))

    def cooldown(self, slot: _PoolSlot, seconds: float = _COOLDOWN_SEC):
        with self._lock:
            slot.cooldown_until = monotonic() + seconds


def _is_rate_limit(exc: Exception) -> bool:
    s = repr(exc).lower()
    if '429' in s or 'rate limit' in s or 'rate_limit' in s or 'too many requests' in s:
        return True
    try:
        from openai import RateLimitError
        if isinstance(exc, RateLimitError):
            return True
    except Exception:
        pass
    status = getattr(exc, 'status_code', None) or getattr(exc, 'http_status', None)
    return status == 429


def _is_transient_5xx(exc: Exception) -> bool:
    status = getattr(exc, 'status_code', None) or getattr(exc, 'http_status', None)
    if status in (500, 502, 503, 504):
        return True
    s = repr(exc).lower()
    return any(tag in s for tag in (
        "'code': 500", "'code': 502", "'code': 503", "'code': 504",
        'internal error', 'service unavailable', 'bad gateway',
        'gateway timeout', "'status': 'internal'",
    ))


def _revive_litellm_executors() -> None:
    """Replace any shut-down ThreadPoolExecutor inside the litellm package."""
    import sys
    from concurrent.futures import ThreadPoolExecutor

    for mod_name, module in list(sys.modules.items()):
        if not mod_name.startswith('litellm') or module is None:
            continue
        for attr in dir(module):
            try:
                obj = getattr(module, attr)
            except Exception:
                continue
            if isinstance(obj, ThreadPoolExecutor) and getattr(obj, '_shutdown', False):
                try:
                    workers = obj._max_workers
                except Exception:
                    workers = 1
                try:
                    setattr(module, attr, ThreadPoolExecutor(max_workers=workers))
                except Exception:
                    pass


# ── Main client ───────────────────────────────────────────────────────────────

class LLMClient:
    """Multi-provider LLM client with key-pool rotation and rate-limit handling.

    Providers:
      openai   — OpenAI-compatible API (Google AI Studio, OpenRouter, etc.)
      bedrock  — AWS Bedrock via litellm
      ollama   — native /api/chat (detected by :11434 in base_url)
    """

    def __init__(
        self,
        provider: str = 'openai',
        api_keys: list[str] | None = None,
        base_url: str = '',
        model: str = '',
        request_timeout: float = 60.0,
        acquire_timeout: float = 5.0,
        # Bedrock-specific
        aws_access_key_id: str = '',
        aws_secret_access_key: str = '',
        aws_region: str = 'us-east-1',
    ) -> None:
        self._provider = provider.lower()
        self._local = threading.local()
        self.model = model
        self._acquire_timeout = acquire_timeout

        if self._provider == 'bedrock':
            import litellm
            self._ll = litellm
            self.model = model if model.startswith('bedrock/') else f'bedrock/{model}'
            self._aws_key = aws_access_key_id
            self._aws_secret = aws_secret_access_key
            self._aws_region = aws_region
            self._ollama = False
            return

        self._base_url = base_url
        self._ollama = '11434' in (base_url or '')
        self._no_json_mode = 'gemma' in (model or '').lower()

        if self._ollama:
            self._ollama_base = base_url.rstrip('/').removesuffix('/v1')
            return

        # OpenAI-compatible path — build rate-aware pool
        from openai import OpenAI
        keys = api_keys or []
        if not keys:
            raise ValueError("LLM API keys required for OpenAI provider")
        clients = [
            OpenAI(api_key=k, base_url=base_url, timeout=request_timeout, max_retries=0)
            for k in keys
        ]
        self._pool = _OpenAIPool(clients)

    @classmethod
    def from_settings(cls, settings: Any) -> 'LLMClient':
        """Factory: build from a Settings instance."""
        return cls(
            provider=settings.llm_provider,
            api_keys=settings.llm_api_keys,
            base_url=settings.llm_base_url,
            model=settings.llm_model_name,
            request_timeout=settings.llm_request_timeout_sec,
            acquire_timeout=settings.llm_acquire_timeout_sec,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            aws_region=settings.aws_region,
        )

    # ── Public API ────────────────────────────────────────────────────────

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        json_mode: bool = False,
    ) -> str:
        if self._provider == 'bedrock':
            return self._chat_bedrock(messages, temperature, max_tokens, response_format)
        if self._ollama:
            return self._chat_ollama(messages, temperature, max_tokens, json_mode=json_mode)
        return self._chat_openai(messages, temperature, max_tokens, response_format)

    def complete(self, agent_id: str, prompt: str, max_tokens: int = 1000) -> str:
        self._local.agent_id = agent_id
        messages: List[Dict[str, str]] = []
        # Gemma doesn't support response_format — enforce JSON via system prompt
        if self._no_json_mode:
            messages.append({"role": "system", "content": "You are a JSON-only API. Return ONLY valid JSON — no markdown, no explanation, no code fences."})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages=messages, max_tokens=max_tokens, temperature=0.3)

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            json_mode=True,
        )
        return parse_llm_json(response)

    # ── Internals ─────────────────────────────────────────────────────────

    def _chat_ollama(self, messages, temperature, max_tokens, json_mode: bool = False) -> str:
        body: Dict[str, Any] = {
            'model':    self.model,
            'messages': messages,
            'think':    False,
            'stream':   False,
            'options': {
                'temperature': temperature,
                'num_predict': max_tokens,
            },
        }
        if json_mode:
            body['format'] = 'json'
        payload = json.dumps(body).encode()
        url = f'{self._ollama_base}/api/chat'
        req = urllib.request.Request(
            url, data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
        content = data.get('message', {}).get('content', '')
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        content = re.sub(r'<thought>[\s\S]*?</thought>', '', content).strip()
        return content

    def _chat_bedrock(self, messages, temperature, max_tokens, response_format) -> str:
        kwargs: Dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            aws_access_key_id=self._aws_key,
            aws_secret_access_key=self._aws_secret,
            aws_region_name=self._aws_region,
        )
        if response_format:
            kwargs['response_format'] = response_format
        try:
            response = self._ll.completion(**kwargs)
        except RuntimeError as e:
            if 'cannot schedule new futures after shutdown' not in str(e):
                raise
            _revive_litellm_executors()
            response = self._ll.completion(**kwargs)
        content = response.choices[0].message.content or ''
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        content = re.sub(r'<thought>[\s\S]*?</thought>', '', content).strip()
        return content

    def _chat_openai(self, messages, temperature, max_tokens, response_format) -> str:
        kwargs: Dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format and not self._no_json_mode:
            kwargs['response_format'] = response_format

        timeout = self._acquire_timeout
        max_attempts = max(1, len(self._pool.slots))
        last_exc: Optional[Exception] = None

        for attempt in range(max_attempts):
            slot = self._pool.acquire(timeout)
            try:
                response = slot.client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content or ''
                content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
                content = re.sub(r'<thought>[\s\S]*?</thought>', '', content).strip()
                # Google AI Studio returns 200 with empty body on soft rate-limit
                if not content and attempt < max_attempts - 1:
                    logger.warning('LLM key %d returned empty content — rotating to next key', slot.idx)
                    self._pool.cooldown(slot, 30.0)
                    continue
                return content
            except Exception as e:
                last_exc = e
                if _is_rate_limit(e):
                    self._pool.cooldown(slot)
                    continue
                if _is_transient_5xx(e):
                    continue
                raise
        raise last_exc if last_exc else RuntimeError("LLM pool exhausted")
