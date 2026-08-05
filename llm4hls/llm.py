"""Pluggable LLM backend for the reference agent — hardened version.

Changes vs the upstream reference:
- OpenRouterClient retries transient failures (429 / 5xx / timeouts / empty
  completions) with exponential backoff, so a flaky API never kills a 13h run.
- Per-call temperature override: the agent uses low temperature for repair
  and slightly higher for optimization.
- Call audit counters (api_calls / api_failures) so the batch runner can
  prove `real_api_only=True` and report how clean the run was.

Interface is still a single `complete(system, user) -> str` (plus an optional
temperature kwarg), so the two clients remain interchangeable.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Protocol

from . import config

class LLMClient(Protocol):
    def complete(
        self, system: str, user: str, temperature: float | None = None
    ) -> str: ...

class ScriptedClient:
    """Deterministic offline backend: returns the next canned response."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._i = 0
        self.api_calls = 0
        self.api_failures = 0

    def complete(
        self, system: str, user: str, temperature: float | None = None
    ) -> str:
        if not self._responses:
            return ""
        resp = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return resp

class OpenRouterClient:
    """Real backend via OpenRouter's OpenAI-compatible chat-completions API,
    with retry + backoff + audit."""

    # Transient HTTP statuses worth retrying.
    RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 8192,
        max_retries: int = 8,
        base_delay: float = 5.0,
        timeout: int = 300,
    ) -> None:
        self.api_key = api_key or config.OPENROUTER_API_KEY
        if not self.api_key:
            raise RuntimeError(
                "OpenRouter token missing. Set OPENROUTER_API_KEY in the environment "
                "or fill OPENROUTER_API_KEY in llm4hls/config.py."
            )
        self.model = model or config.DEFAULT_LLM_MODEL
        self.base_url = config.OPENROUTER_BASE_URL
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.timeout = timeout
        # audit counters — the batch runner reads these for FINAL_SUMMARY.md
        self.api_calls = 0       # successful completions
        self.api_failures = 0    # individual failed attempts (retried)

    def _post(self, payload: bytes) -> dict:
        req = urllib.request.Request(
            self.base_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://llm4hls.local",
                "X-Title": "LLM4HLS Track A",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def complete(
        self, system: str, user: str, temperature: float | None = None
    ) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": (
                    temperature if temperature is not None else self.temperature
                ),
                "max_tokens": self.max_tokens,
            }
        ).encode("utf-8")

        last_err = "unknown"
        for attempt in range(self.max_retries):
            try:
                body = self._post(payload)
                msg = body["choices"][0]["message"]
                content = msg.get("content") or ""
                if isinstance(content, list):  # some providers return parts
                    content = "".join(
                        p.get("text", "") for p in content if isinstance(p, dict)
                    )
                if not content.strip():
                    raise ValueError("empty completion content")
                self.api_calls += 1
                return content
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:400]
                last_err = f"HTTP {e.code}: {detail}"
                if e.code not in self.RETRY_STATUS:
                    # 4xx other than rate limits = our fault, fail fast
                    raise RuntimeError(f"OpenRouter {last_err}") from e
                self.api_failures += 1
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                json.JSONDecodeError,
                KeyError,
                IndexError,
                ValueError,
            ) as e:
                last_err = f"{type(e).__name__}: {e}"
                self.api_failures += 1

            delay = min(self.base_delay * (2 ** attempt), 120.0)
            print(
                f"  [llm] attempt {attempt + 1}/{self.max_retries} failed "
                f"({last_err}); retrying in {delay:.0f}s",
                flush=True,
            )
            time.sleep(delay)

        raise RuntimeError(
            f"OpenRouter failed after {self.max_retries} attempts: {last_err}"
        )