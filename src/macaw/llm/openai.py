from __future__ import annotations

import importlib.util

from macaw.llm.base import LlmBackend
from macaw.llm.registry import register


@register
class OpenAiLlmBackend(LlmBackend):
    """Cloud text formatting over the OpenAI chat API (or any OpenAI-compatible
    endpoint via a custom base URL — Groq, Together, a local Ollama, …).

    Pure-Python, so it lives in the MAIN env (extra: ``openai``) with no
    isolated venv or local weights. Models and provenance live in
    ``llm/models/cloud.yaml``; ``model.id`` is the API model name.
    """

    key = "openai-llm"
    cloud = True

    def available(self) -> bool:
        return importlib.util.find_spec("openai") is not None

    def load(self) -> None:  # nothing to warm up — the client is per-call
        return

    def format(self, text: str, system: str) -> str:
        if not text.strip():
            return ""
        from openai import OpenAI

        client = OpenAI(
            api_key=self._api_key or "missing",
            base_url=self._base_url or None,
        )
        out = client.chat.completions.create(
            model=self.model.id,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
        )
        return (out.choices[0].message.content or "").strip()
