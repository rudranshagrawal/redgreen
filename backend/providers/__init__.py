"""Provider dispatch.

Each provider exposes a single coroutine:

    async def generate(*, system: str, user: str, model: str, max_tokens: int) -> ProviderResult

where ProviderResult is a dict:

    {"test_code": str, "patch": str, "rationale": str,
     "elapsed_ms": int, "input_tokens": int, "output_tokens": int,
     "error": Optional[str]}

Providers MUST:
  - return raw-string fields (no leading code fences, no markdown).
  - never raise on upstream JSON weirdness — return with error="bad_json".
  - respect max_tokens. Hackathon budget guard.
"""

from __future__ import annotations

from .openai_codex import generate as openai_codex_generate
from .nebius import generate as nebius_generate


DEFAULT_OPENAI_MODEL = "gpt-5-mini"  # chat-compatible + fast. Codex variants need /v1/responses — revisit at M2.
DEFAULT_NEBIUS_LLAMA = "meta-llama/Llama-3.3-70B-Instruct"
DEFAULT_NEBIUS_QWEN = "Qwen/Qwen3-32B"
DEFAULT_NEBIUS_DEEPSEEK = "deepseek-ai/DeepSeek-V3.2-fast"


__all__ = [
    "openai_codex_generate",
    "nebius_generate",
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_NEBIUS_LLAMA",
    "DEFAULT_NEBIUS_QWEN",
    "DEFAULT_NEBIUS_DEEPSEEK",
]
