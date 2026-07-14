"""Backend-agnostic text-formatting facade.

The engine holds one Formatter, mirroring how it holds one Transcriber. The
active model is either a local catalog id (llama.cpp, kept warm) or a cloud
``provider:<id>`` served over HTTP. Either way it turns final STT text into
cleaned, formatted text, and degrades to the original text if anything fails.
"""

from __future__ import annotations

import logging
import time

from macaw.llm import providers
from macaw.llm.prompts import resolve_system
from macaw.llm.registry import create_backend, get_model_info

logger = logging.getLogger("macaw")

_PROVIDER_PREFIX = "provider:"


class Formatter:
    def __init__(
        self,
        model_id: str = "",
        custom_prompt: str = "",
        provider: dict | None = None,
        ssl_verify: bool = True,
    ) -> None:
        self.model_id = model_id
        self.custom_prompt = custom_prompt
        self.provider = provider  # resolved provider dict when model is a provider
        self.ssl_verify = ssl_verify
        self._backend = None  # local backend cache
        self.last_tps = 0.0  # last format() speed (local worker); 0 if unknown
        self.last_secs = 0.0

    # -- selection ----------------------------------------------------

    def is_provider(self) -> bool:
        return self.model_id.startswith(_PROVIDER_PREFIX)

    def system_prompt(self) -> str:
        return resolve_system(self.custom_prompt)

    def _info(self):
        if not self.model_id or self.is_provider():
            return None
        return get_model_info(self.model_id)

    # -- capability ---------------------------------------------------

    def available(self) -> bool:
        if self.is_provider():
            return True  # urllib client is always present
        info = self._info()
        if info is None:
            return False
        try:
            return create_backend(self.model_id).available()
        except Exception:  # noqa: BLE001
            return False

    def is_ready(self) -> bool:
        if self.is_provider():
            return bool(self.provider) and providers.is_ready(self.provider)
        info = self._info()
        if info is None:
            return False
        try:
            return create_backend(self.model_id).is_ready()
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
        return self._backend

    def load(self) -> None:
        """Warm the local model (no-op for cloud providers). Raises on failure."""
        if self.is_provider():
            return
        b = self._ensure_backend()
        if b is not None:
            b.load()

    def format(self, text: str) -> str:
        """Return the formatted text. Raises on failure (caller keeps the raw)."""
        if not text or not text.strip():
            return text
        self.last_tps = 0.0
        self.last_secs = 0.0
        if self.is_provider():
            if not self.provider:
                return text
            t0 = time.monotonic()
            out = providers.chat(
                self.provider, self.system_prompt(), text, ssl_verify=self.ssl_verify
            )
            self.last_secs = round(time.monotonic() - t0, 2)
            return out or text
        b = self._ensure_backend()
        if b is None:
            return text
        result = b.format(text, self.system_prompt()) or text
        self.last_tps = getattr(b, "last_tps", 0.0)
        self.last_secs = getattr(b, "last_secs", 0.0)
        return result

    def apply(
        self,
        model_id: str,
        custom_prompt: str,
        provider: dict | None,
        ssl_verify: bool,
    ) -> None:
        """Adopt new config; drop the local backend if the model changed."""
        if model_id != self.model_id:
            self.unload()
        self.model_id = model_id
        self.custom_prompt = custom_prompt
        self.provider = provider
        self.ssl_verify = ssl_verify

    def unload(self) -> None:
        if self._backend is not None:
            try:
                self._backend.unload()
            except Exception:  # noqa: BLE001
                pass
            self._backend = None
