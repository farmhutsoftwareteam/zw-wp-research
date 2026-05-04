"""Wrapper around the `claude` CLI for headless prompting via Max plan.

Single chokepoint: every LLM call in the pipeline goes through `run_claude()`
so we can apply rate limiting, error handling, and PATH validation in one place.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass
class ClaudeResult:
    text: str
    raw: dict | None
    stderr: str


class ClaudeError(RuntimeError):
    pass


class ClaudeRateLimitError(ClaudeError):
    """Raised when the Max plan rate-limits us. Caller should sleep longer."""


_RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate_limit",
    "usage limit",
    "usage_limit",
    "429",
    "too many requests",
    "you've reached your",
    "limit reached",
    "quota exceeded",
)


def _looks_like_rate_limit(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _RATE_LIMIT_MARKERS)


def is_available() -> bool:
    """True iff `claude` CLI is on PATH."""
    return shutil.which("claude") is not None


def assert_available() -> None:
    if not is_available():
        sys.stderr.write(
            "ERROR: `claude` CLI not found on PATH.\n"
            "Install Claude Code (https://claude.com/claude-code) and ensure you're "
            "logged into your Max plan.\n"
        )
        sys.exit(2)


_LAST_CALL_AT: float = 0.0
_RATE_LOCK = asyncio.Lock()


async def _pace(rps: float) -> None:
    """Global pacing: at most `rps` calls/sec across all stages."""
    global _LAST_CALL_AT
    if rps <= 0:
        return
    interval = 1.0 / rps
    async with _RATE_LOCK:
        now = time.monotonic()
        wait = (_LAST_CALL_AT + interval) - now
        if wait > 0:
            await asyncio.sleep(wait)
            _LAST_CALL_AT = time.monotonic()
        else:
            _LAST_CALL_AT = now


def run_claude(
    prompt: str,
    model: str = "claude-haiku-4-5",
    timeout: int = 90,
) -> ClaudeResult:
    """Synchronous claude -p call. Use `arun_claude` from async code.

    Raises ClaudeRateLimitError when stderr/stdout looks like a rate-limit
    response — caller should back off (minutes), not fail the batch.
    """
    assert_available()
    try:
        proc = subprocess.run(
            [
                "claude",
                "-p",
                prompt,
                "--output-format", "json",
                "--model", model,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeError(f"claude CLI timed out after {timeout}s") from exc
    combined = (proc.stderr or "") + "\n" + (proc.stdout or "")
    if proc.returncode != 0:
        if _looks_like_rate_limit(combined):
            raise ClaudeRateLimitError(
                f"rate-limited: {(proc.stderr or proc.stdout).strip()[:300]}"
            )
        raise ClaudeError(
            f"claude CLI exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:500]}"
        )
    raw: dict | None = None
    text = proc.stdout
    try:
        raw = json.loads(proc.stdout)
        # `claude -p --output-format json` returns {"result": "...", ...}
        if isinstance(raw, dict):
            if raw.get("is_error") and _looks_like_rate_limit(json.dumps(raw)):
                raise ClaudeRateLimitError(
                    f"rate-limited (json): {str(raw)[:300]}"
                )
            if "result" in raw and isinstance(raw["result"], str):
                text = raw["result"]
    except json.JSONDecodeError:
        pass
    return ClaudeResult(text=text, raw=raw, stderr=proc.stderr)


async def arun_claude(
    prompt: str,
    model: str = "claude-haiku-4-5",
    timeout: int = 90,
    rps: float = 0.5,
    rate_limit_retries: int = 4,
    rate_limit_initial_sleep: float = 300.0,
) -> ClaudeResult:
    """Async wrapper with global pacing + automatic rate-limit backoff.

    On ClaudeRateLimitError, sleep `rate_limit_initial_sleep` seconds and retry
    up to `rate_limit_retries` times (doubling each time, cap 1800s).
    Other ClaudeErrors propagate immediately — they're not transient.
    """
    sleep_for = rate_limit_initial_sleep
    last_exc: ClaudeError | None = None
    for attempt in range(rate_limit_retries + 1):
        await _pace(rps)
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, lambda: run_claude(prompt, model, timeout))
        except ClaudeRateLimitError as exc:
            last_exc = exc
            if attempt >= rate_limit_retries:
                break
            import sys as _sys
            print(
                f"[claude] rate-limited (attempt {attempt + 1}/{rate_limit_retries + 1}); "
                f"sleeping {int(sleep_for)}s",
                file=_sys.stderr,
            )
            await asyncio.sleep(sleep_for)
            sleep_for = min(sleep_for * 2, 1800.0)
    raise last_exc if last_exc else ClaudeError("rate limit retries exhausted")


def extract_json(text: str) -> object:
    """Parse JSON from a model response, tolerating ```json fences and prose wrappers."""
    s = text.strip()
    # Strip code fences
    if s.startswith("```"):
        # remove first fence line
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[: -3]
        s = s.strip()
    # Try direct
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Find first [ or { and last matching closer
    for opener, closer in (("[", "]"), ("{", "}")):
        i = s.find(opener)
        j = s.rfind(closer)
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(s[i: j + 1])
            except json.JSONDecodeError:
                continue
    raise ClaudeError(f"Could not parse JSON from model output: {text[:300]!r}")
