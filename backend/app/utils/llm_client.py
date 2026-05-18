"""
LLM client wrapper — OpenAI-compatible APIs, AWS Bedrock (via litellm), or Ollama native API.

LLM_PROVIDER=bedrock  → uses litellm + AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY directly
LLM_PROVIDER=openai   → uses OpenAI SDK with any OpenAI-compatible base_url
                        Special case: if base_url contains :11434 (Ollama), uses the
                        native /api/chat endpoint directly — the OpenAI-compat path has
                        a confirmed bug where think:false is ignored and content is empty
                        (github.com/ollama/ollama/issues/15288, #15293).
"""

import json
import re
import threading
import time
import urllib.request
from typing import Optional, Dict, Any, List

from ..config import Config


def _bedrock_mode() -> bool:
    return Config.LLM_PROVIDER == 'bedrock'


def parse_llm_json(text: str) -> dict:
    """Strip reasoning envelopes + markdown fences then parse JSON.

    Handles ```json fences, <thought>/<thinking>/<reasoning>/<reflection>/
    <scratchpad> tags emitted by Gemini/Gemma/Claude/etc., and falls back to
    extracting the outermost {...} if extra prose slipped through.
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI client pool — used only on the OpenAI-compatible path.
#
# Round-robin across N keys. On 429: cool that key for 60s, rotate to the
# next. On 5xx: rotate to the next without cooling. No preemptive RPM
# tracking — Google's 429 response is fast and clear, so we let calls fly
# and react to actual rate-limit signals rather than guessing.
#
# Bedrock path does not construct the pool.
# ─────────────────────────────────────────────────────────────────────────────

_COOLDOWN_SEC = 60.0


class _PoolSlot:
    __slots__ = ('client', 'cooldown_until', 'idx')

    def __init__(self, client, idx: int):
        self.client = client
        self.cooldown_until: float = 0.0
        self.idx = idx


class _OpenAIPool:
    def __init__(self, clients: list):
        self.slots = [_PoolSlot(c, i) for i, c in enumerate(clients)]
        self._lock = threading.Lock()
        self._rr = 0

    def acquire(self, timeout_sec: float) -> _PoolSlot:
        """Round-robin to the next non-cooling slot. If all are cooling,
        block until one frees or timeout fires."""
        deadline = time.monotonic() + max(0.1, timeout_sec)
        n = len(self.slots)
        while True:
            now = time.monotonic()
            soonest_free = float('inf')
            with self._lock:
                # Try up to n slots starting from current round-robin pointer.
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
            time.sleep(min(max(wait, 0.05), 1.0))

    def cooldown(self, slot: _PoolSlot, seconds: float = _COOLDOWN_SEC):
        with self._lock:
            slot.cooldown_until = time.monotonic() + seconds

    def notify_release(self):
        # No-op kept for API compatibility with the previous pool. There's
        # no shared waiter condition any more — acquire() polls.
        pass


def _is_rate_limit(exc: Exception) -> bool:
    """Detect 429 across openai SDK exception shapes + plain HTTP errors."""
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
    """Detect transient server errors (500/502/503/504). Provider-side, not
    the key's fault — retry on next slot without cooldown."""
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


class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._bedrock = _bedrock_mode()
        self._local = threading.local()

        if self._bedrock:
            import litellm
            self._ll = litellm
            raw = model or Config.BEDROCK_MODEL_ID or Config.LLM_MODEL_NAME
            self.model = raw if raw.startswith('bedrock/') else f'bedrock/{raw}'
            self._aws_key    = Config.AWS_ACCESS_KEY_ID
            self._aws_secret = Config.AWS_SECRET_ACCESS_KEY
            self._aws_region = Config.AWS_REGION
            self._ollama = False
            return

        self.api_key  = api_key  or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model    = model    or Config.LLM_MODEL_NAME
        # Detect Ollama: use native /api/chat which correctly honours think:false.
        # The OpenAI-compat /v1/chat/completions path silently ignores think:false
        # for Gemma 4, leaving content empty (Ollama bugs #15288, #15293).
        self._ollama = '11434' in (self.base_url or '')
        # Gemma 3 on Google AI Studio rejects response_format; JSON via prompt works fine.
        self._no_json_mode = 'gemma-3' in (self.model or '').lower()
        if self._ollama:
            self._ollama_base = self.base_url.rstrip('/').removesuffix('/v1')
            return

        # OpenAI-compatible path — build the rate-aware pool.
        from openai import OpenAI
        if not self.api_key:
            raise ValueError("LLM_API_KEY is not configured")
        keys = [self.api_key]
        for extra in (Config.LLM_API_KEY_2, Config.LLM_API_KEY_3):
            if extra and extra not in keys:
                keys.append(extra)
        clients = [
            OpenAI(api_key=k, base_url=self.base_url,
                   timeout=Config.LLM_REQUEST_TIMEOUT_SEC, max_retries=0)
            for k in keys
        ]
        self._pool = _OpenAIPool(clients)

    # ── public API ────────────────────────────────────────────────────────────

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        json_mode: bool = False,
    ) -> str:
        if self._bedrock:
            return self._chat_bedrock(messages, temperature, max_tokens, response_format)
        if self._ollama:
            return self._chat_ollama(messages, temperature, max_tokens, json_mode=json_mode)
        return self._chat_openai(messages, temperature, max_tokens, response_format)

    def complete(self, agent_id: str, prompt: str, max_tokens: int = 1000) -> str:
        self._local.agent_id = agent_id
        return self.chat(messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens)

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

    # ── internals ─────────────────────────────────────────────────────────────

    def _chat_ollama(self, messages, temperature, max_tokens, json_mode: bool = False) -> str:
        """Call Ollama native /api/chat — think:false works here unlike /v1/."""
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
        self._record_ollama_usage(data)
        return content

    def _chat_bedrock(self, messages, temperature, max_tokens, response_format):
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
        content  = response.choices[0].message.content or ''
        content  = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        content  = re.sub(r'<thought>[\s\S]*?</thought>', '', content).strip()
        self._record_usage(response)
        return content

    def _chat_openai(self, messages, temperature, max_tokens, response_format):
        """OpenAI-compatible call through the rate-aware pool.

          1. Acquire a pool slot — picks least-loaded non-cooling key.
          2. On 429: cool that slot 60s, retry on next slot.
          3. On 5xx: rotate to next slot (no cooldown — provider blip).
          4. On any other failure: bubble up.
        """
        kwargs: Dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format and not self._no_json_mode:
            kwargs['response_format'] = response_format

        timeout = float(Config.LLM_ACQUIRE_TIMEOUT_SEC)
        max_attempts = max(1, len(self._pool.slots))
        last_exc: Optional[Exception] = None

        for _ in range(max_attempts):
            slot = self._pool.acquire(timeout)
            try:
                response = slot.client.chat.completions.create(**kwargs)
                content  = response.choices[0].message.content or ''
                content  = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
                content  = re.sub(r'<thought>[\s\S]*?</thought>', '', content).strip()
                self._record_usage(response)
                return content
            except Exception as e:
                last_exc = e
                if _is_rate_limit(e):
                    self._pool.cooldown(slot)
                    continue   # try next slot
                if _is_transient_5xx(e):
                    # Provider blip — try the next key without cooling
                    # this one. If all slots fail, exception bubbles up
                    # and the agent's existing fallback kicks in.
                    continue
                raise
            finally:
                self._pool.notify_release()
        raise last_exc if last_exc else RuntimeError("LLM pool exhausted")

    def _record_usage(self, response):
        try:
            from ..services.kernel import budget as _budget
            usage = response.usage
            if usage:
                _budget.record(
                    agent_id=getattr(self._local, 'agent_id', 'llm'),
                    model=self.model,
                    prompt_tokens=usage.prompt_tokens or 0,
                    completion_tokens=usage.completion_tokens or 0,
                )
        except Exception:
            pass
        finally:
            self._local.agent_id = 'llm'

    def _record_ollama_usage(self, data: dict):
        try:
            from ..services.kernel import budget as _budget
            _budget.record(
                agent_id=getattr(self._local, 'agent_id', 'llm'),
                model=self.model,
                prompt_tokens=data.get('prompt_eval_count', 0),
                completion_tokens=data.get('eval_count', 0),
            )
        except Exception:
            pass
        finally:
            self._local.agent_id = 'llm'
