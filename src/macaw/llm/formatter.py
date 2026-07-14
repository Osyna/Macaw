"""Backend-agnostic text-formatting facade.

The engine holds one Formatter, mirroring how it holds one Transcriber. It owns
the active LLM backend's lifecycle (create / warm / swap / drop), resolves the
system prompt (custom or the built-in smart default) and turns final STT text
into cleaned, formatted text — degrading to the original text if anything fails.
"""

from __future__ import annotations

import logging

from macaw.llm.prompts import resolve_system
from macaw.llm.registry import create_backend, get_model_info

logger = logging.getLogger("macaw")


class Formatter:
    def __init__(
        self,
        model_id: str = "",
        custom_prompt: str = "",
        api_key: str = "",
        base_url: str = "",
    ) -> None:
        self.model_id = model_id
        self.custom_prompt = custom_prompt
        self.api_key = api_key
        self.base_url = base_url
        self._backend = None

    # -- config -------------------------------------------------------

    def system_prompt(self) -> str:
        return resolve_system(self.custom_prompt)

    def _info(self):
        return get_model_info(self.model_id) if self.model_id else None

    def available(self) -> bool:
        """True if the selected model's dependency is present."""
        info = self._info()
        if info is None:
            return False
        try:
            return create_backend(self.model_id).available()
        except Exception:  # noqa: BLE001
            return False

    def is_ready(self) -> bool:
        """True if the selected model can format right now."""
        info = self._info()
        if info is None:
            return False
        try:
            b = create_backend(self.model_id)
            b.configure(self.api_key, self.base_url)
            return b.is_ready()
        except Exception:  # noqa: BLE001
            return False

    # -- lifecycle ----------------------------------------------------

    def _ensure_backend(self):
        wanted = self.model_id
        if self._backend is not None and self._backend.model.id == wanted:
            return self._backend
        self.unload()
        if not wanted or self._info() is None:
            return None
        self._backend = create_backend(wanted)
        self._backend.configure(self.api_key, self.base_url)
        return self._backend

    def load(self) -> None:
        """Warm the active model (no-op for cloud). Raises on failure."""
        b = self._ensure_backend()
        if b is not None:
            b.load()

    def format(self, text: str) -> str:
        """Return the formatted text, or the original on any failure."""
        if not text or not text.strip():
            return text
        b = self._ensure_backend()
        if b is None:
            return text
        try:
            out = b.format(text, self.system_prompt())
            return out or text
        except Exception as exc:  # noqa: BLE001 — never lose the transcription
            logger.error("LLM formatting failed: %s", exc)
            raise

    def apply(
        self, model_id: str, custom_prompt: str, api_key: str, base_url: str
    ) -> None:
        """Adopt new config; drop the backend if the model changed."""
        if model_id != self.model_id:
            self.unload()
        self.model_id = model_id
        self.custom_prompt = custom_prompt
        self.api_key = api_key
        self.base_url = base_url
        if self._backend is not None:
            self._backend.configure(api_key, base_url)

    def unload(self) -> None:
        if self._backend is not None:
            try:
                self._backend.unload()
            except Exception:  # noqa: BLE001
                pass
            self._backend = None
