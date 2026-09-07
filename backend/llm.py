"""Gemini client with rate limiting and structured output.

The free tier's requests-per-minute cap is the binding constraint on this whole
project, so every call in the system goes through the limiter here.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time

from google import genai
from google.genai import types

MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
RPM = int(os.getenv("GEMINI_RPM", "10"))
MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "5"))


class RateLimiter:
    """Simple spaced-request limiter: at most `rpm` calls per rolling minute."""

    def __init__(self, rpm: int):
        self.min_interval = 60.0 / max(rpm, 1)
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self) -> None:
        with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()


_limiter = RateLimiter(RPM)
_client: genai.Client | None = None


def available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))


def client() -> genai.Client:
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key, "
                "or browse the pre-built data/facts.db without a key."
            )
        _client = genai.Client(api_key=key)
    return _client


def generate_json(prompt: str, schema, temperature: float = 0.0):
    """One structured call. Returns parsed JSON matching `schema`.

    Retries transient failures (429 rate limit, 5xx) with exponential backoff
    and jitter; raises on anything else.
    """
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        temperature=temperature,
        # The schema is a response shape, not a callable tool; without this the SDK
        # warns about automatic function calling on every request.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        _limiter.acquire()
        try:
            resp = client().models.generate_content(
                model=MODEL, contents=prompt, config=cfg
            )
            text = (resp.text or "").strip()
            if not text:
                raise ValueError("empty response")
            return json.loads(text)
        except Exception as e:  # noqa: BLE001 - classified below
            last_err = e
            msg = str(e).lower()
            transient = any(
                s in msg
                for s in ("429", "rate", "quota", "503", "500", "unavailable", "timeout", "deadline")
            )
            if not transient or attempt == MAX_RETRIES - 1:
                raise
            time.sleep(min(2**attempt + random.random(), 60))
    raise last_err  # unreachable
