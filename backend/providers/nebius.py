"""Nebius Token Factory provider (slots 2-4).

OpenAI-compatible endpoint — we reuse the openai SDK by overriding base_url.
"""

from __future__ import annotations

import asyncio
import os
import time

from openai import AsyncOpenAI

from .openai_codex import _repair_json


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=os.environ["NEBIUS_API_KEY"],
            base_url=os.environ.get("NEBIUS_BASE_URL", "https://api.studio.nebius.ai/v1/"),
        )
    return _client


async def generate(
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int = 1500,
    timeout_s: float = 45.0,
) -> dict:
    client = _get_client()
    started = time.monotonic()
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
                temperature=0.2,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        return {
            "test_code": "",
            "patch": "",
            "rationale": f"timeout after {timeout_s}s",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "input_tokens": 0,
            "output_tokens": 0,
            "error": "timeout",
        }

    elapsed_ms = int((time.monotonic() - started) * 1000)
    content = resp.choices[0].message.content or ""
    try:
        obj = _repair_json(content)
    except Exception as e:  # noqa: BLE001
        return {
            "test_code": "",
            "patch": "",
            "rationale": f"bad json from model: {e}",
            "elapsed_ms": elapsed_ms,
            "input_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(resp.usage, "completion_tokens", 0) or 0,
            "error": "bad_json",
            "raw": content[:500],
        }

    return {
        "test_code": (obj.get("test_code") or "").strip(),
        "patch": (obj.get("patch") or "").strip(),
        "rationale": (obj.get("rationale") or "").strip(),
        "elapsed_ms": elapsed_ms,
        "input_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(resp.usage, "completion_tokens", 0) or 0,
    }
