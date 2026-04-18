"""OpenAI Codex provider (slot 1).

Uses the openai v2 SDK. Model names follow the gpt-5.X-codex family.
Structured output via `response_format={"type": "json_object"}`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time

from openai import AsyncOpenAI


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _repair_json(raw: str) -> dict:
    """Be forgiving: some models wrap JSON in ```json fences despite our prompt."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Last-ditch: extract the outermost {...} block.
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


async def generate(
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int = 6000,
    timeout_s: float = 60.0,
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
                max_completion_tokens=max_tokens,
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
