"""
LLM client wrapper — OpenAI-compatible APIs or AWS Bedrock (via litellm, no proxy).

LLM_PROVIDER=bedrock  → uses litellm + AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY directly
LLM_PROVIDER=openai   → uses OpenAI SDK with any OpenAI-compatible base_url
"""

import json
import re
import threading
from typing import Optional, Dict, Any, List

from ..config import Config


def _bedrock_mode() -> bool:
    return Config.LLM_PROVIDER == 'bedrock'


def _revive_litellm_executors() -> None:
    """Replace any shut-down ThreadPoolExecutor inside the litellm package.

    litellm uses module-level executors for async logging callbacks. Once one
    is shut down (Flask debug reload, atexit, signal handler), subsequent
    completion() calls fail with `cannot schedule new futures after shutdown`.
    We walk the litellm package, find every ThreadPoolExecutor whose internal
    `_shutdown` flag is set, and replace it with a fresh one of the same size.
    """
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
            # litellm expects "bedrock/<model-id>"
            self.model = raw if raw.startswith('bedrock/') else f'bedrock/{raw}'
            self._aws_key    = Config.AWS_ACCESS_KEY_ID
            self._aws_secret = Config.AWS_SECRET_ACCESS_KEY
            self._aws_region = Config.AWS_REGION
        else:
            from openai import OpenAI
            self.api_key  = api_key  or Config.LLM_API_KEY
            self.base_url = base_url or Config.LLM_BASE_URL
            self.model    = model    or Config.LLM_MODEL_NAME
            if not self.api_key:
                raise ValueError("LLM_API_KEY is not configured")
            self._oa = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=60.0,
                max_retries=1,
            )

    # ── public API ────────────────────────────────────────────────────────────

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
    ) -> str:
        if self._bedrock:
            return self._chat_bedrock(messages, temperature, max_tokens, response_format)
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
        )
        cleaned = response.strip()
        # Strip reasoning/thought envelopes some models emit before the JSON
        # (Gemini: <thought>…</thought>, Claude: <thinking>…</thinking>, etc.).
        cleaned = re.sub(
            r'<\s*(thought|thinking|reasoning|reflection|scratchpad)\s*>.*?<\s*/\s*\1\s*>',
            '', cleaned, flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\n?```\s*$', '', cleaned).strip()
        # Last-resort fallback: extract the outermost {...} JSON object if the
        # model wrapped it in extra prose.
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

    # ── internals ─────────────────────────────────────────────────────────────

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
            # litellm keeps a module-level ThreadPoolExecutor for async logging
            # callbacks. Flask debug-mode auto-reload (or an atexit hook firing
            # in the worker) can shut it down, after which every subsequent
            # call dies with "cannot schedule new futures after shutdown".
            # Recreate the executor in-place and retry once.
            if 'cannot schedule new futures after shutdown' not in str(e):
                raise
            _revive_litellm_executors()
            response = self._ll.completion(**kwargs)
        content  = response.choices[0].message.content or ''
        content  = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        self._record_usage(response)
        return content

    def _chat_openai(self, messages, temperature, max_tokens, response_format):
        kwargs: Dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format:
            kwargs['response_format'] = response_format
        response = self._oa.chat.completions.create(**kwargs)
        content  = response.choices[0].message.content or ''
        content  = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        self._record_usage(response)
        return content

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
