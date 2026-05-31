"""Async OpenAI-compatible streaming client — see design doc §15.

One client instance owns one underlying httpx.AsyncClient (connection pool).
Each `chat()` / `completions()` call returns a fully-populated `RequestSample`.

Token counts come from `usage` in the final SSE chunk when the server emits it
(vLLM supports `stream_options.include_usage`). Fallback: count delta chunks
with non-empty content for output, leave input_tokens=0 if server omits usage.
"""
from __future__ import annotations

import json
import time
from types import TracebackType
from typing import Any

import httpx

from pping_lang.bench.measurement import RequestSample


def synthesize_prompt(target_tokens: int) -> str:
    """Generate filler text whose tokenized length is roughly `target_tokens`.

    ~4 chars/token English rule of thumb, padded with varied words to avoid
    pathological tokenization. Good enough for shape-controlled benching; not
    a substitute for real prompts when measuring quality.
    """
    if target_tokens <= 0:
        return ""
    base = "the quick brown fox jumps over the lazy dog and runs through the meadow "
    base_words = 13  # rough word count of `base`
    repeats = max(1, target_tokens // base_words + 1)
    return (base * repeats).strip()[: max(target_tokens * 5, 8)]


class OpenAIStreamClient:
    """Async streaming client for OpenAI-compatible servers (vLLM, etc.).

    Use as an async context manager so the underlying httpx pool is closed:

        async with OpenAIStreamClient(base_url, timeout_s=30) as c:
            sample = await c.chat(model, prompt, output_tokens=100)
    """

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 30.0,
        api_key: str | None = None,
        max_keepalive: int = 64,
    ) -> None:
        # Strip trailing slash and any explicit /v1 (we add the /v1 path explicitly)
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self._timeout_s = timeout_s
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # connect timeout short (catch endpoint down fast); read tied to overall budget
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s)),
            headers=headers,
            limits=httpx.Limits(
                max_keepalive_connections=max_keepalive,
                max_connections=max_keepalive * 2,
            ),
        )

    async def __aenter__(self) -> OpenAIStreamClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        model: str,
        prompt: str,
        output_tokens: int,
    ) -> RequestSample:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        return await self._stream("/v1/chat/completions", payload, parse_chat=True)

    async def completions(
        self,
        model: str,
        prompt: str,
        output_tokens: int,
    ) -> RequestSample:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "max_tokens": output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        return await self._stream("/v1/completions", payload, parse_chat=False)

    async def _stream(
        self,
        path: str,
        payload: dict[str, Any],
        parse_chat: bool,
    ) -> RequestSample:
        sample = RequestSample(started_ns=time.monotonic_ns())
        try:
            async with self._client.stream("POST", path, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    text = body.decode(errors="replace")[:300]
                    sample.error = f"http_{resp.status_code}: {text}"
                    sample.finished_ns = time.monotonic_ns()
                    return sample

                output_count = 0
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    # SSE wire format: 'data: <payload>'
                    if line.startswith("data: "):
                        data = line[6:]
                    elif line.startswith("data:"):
                        data = line[5:].lstrip()
                    else:
                        continue
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    # Usage may appear in any chunk; final chunk with include_usage
                    usage = chunk.get("usage")
                    if usage:
                        pt = usage.get("prompt_tokens")
                        ct = usage.get("completion_tokens")
                        if pt is not None:
                            sample.input_tokens = int(pt)
                        if ct is not None:
                            sample.output_tokens = int(ct)

                    content = _extract_content(chunk, parse_chat)
                    if content:
                        if sample.first_token_ns is None:
                            sample.first_token_ns = time.monotonic_ns()
                        output_count += 1

                sample.finished_ns = time.monotonic_ns()
                # Fall back to chunk count when server omits usage
                if sample.output_tokens == 0:
                    sample.output_tokens = output_count

        except httpx.TimeoutException:
            sample.error = "timeout"
            sample.finished_ns = time.monotonic_ns()
        except httpx.HTTPError as e:
            sample.error = f"http_error: {type(e).__name__}: {e}"
            sample.finished_ns = time.monotonic_ns()
        except Exception as e:  # noqa: BLE001 — record-and-continue
            sample.error = f"unexpected: {type(e).__name__}: {e}"
            sample.finished_ns = time.monotonic_ns()

        return sample


def _extract_content(chunk: dict[str, Any], parse_chat: bool) -> str | None:
    """Extract textual delta from an SSE chunk; None means 'no new content'."""
    choices = chunk.get("choices") or []
    if not choices:
        return None
    c0 = choices[0]
    if parse_chat:
        delta = c0.get("delta") or {}
        text = delta.get("content")
        return text if text else None
    text = c0.get("text")
    return text if text else None
