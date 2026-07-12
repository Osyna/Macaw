from __future__ import annotations

import logging
import typing

import numpy as np

from macaw.stt import create_backend, get_model_info

logger = logging.getLogger(__name__)


class Transcriber:
    """Backend-agnostic transcription facade.

    Owns the generic audio pipeline (mono/float32/16 kHz + silence gate) and
    delegates the actual model work to a pluggable backend from `macaw.stt`.
    Swap models by setting `model_size` to any registered model id.
    """

    def __init__(
        self,
        model_size: str = "large-v3-turbo",
        language: str = "en",
        punctuation_hints: bool = True,
    ) -> None:
        self.model_size = model_size
        self.language = language
        self.punctuation_hints = punctuation_hints
        self.model_params: dict = {}  # tunables for the current model
        self._backend = None

    # -- backend lifecycle ---------------------------------------------

    def _ensure_backend(self):
        """(Re)create the backend if the selected model changed."""
        wanted = get_model_info(self.model_size).id
        if self._backend is None or self._backend.model.id != wanted:
            self._backend = create_backend(
                self.model_size, self.language, self.punctuation_hints
            )
        else:
            # Same model, but language/punctuation may have been reconfigured.
            self._backend.language = self.language
            self._backend.punctuation_hints = self.punctuation_hints
        self._backend.set_params(self.model_params)
        return self._backend

    def load_model(self, model_path: str | None = None) -> None:
        self._ensure_backend().load(model_path or None)

    def download_model(
        self,
        progress_callback: typing.Callable[[int], None] | None = None,
    ) -> str:
        return self._ensure_backend().download(progress_callback)

    def is_ready(self) -> bool:
        """True if the active model can transcribe now (dep present + downloaded)."""
        if not self.model_size:
            return False  # nothing selected yet
        try:
            return self._ensure_backend().is_ready()
        except Exception:
            return False

    def unload_model(self) -> None:
        if self._backend is not None:
            self._backend.unload()
        self._backend = None

    # -- audio prep -----------------------------------------------------

    @staticmethod
    def _prepare(audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Normalize to mono float32 at 16 kHz."""
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if sample_rate != 16_000:
            samples = int(len(audio) * 16_000 / sample_rate)
            audio = np.interp(
                np.linspace(0, len(audio), samples, endpoint=False),
                np.arange(len(audio)),
                audio,
            ).astype(np.float32)
        return audio

    # -- transcription --------------------------------------------------

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> str:
        """Transcribe a complete audio signal. Returns stripped text or ''."""
        backend = self._ensure_backend()
        audio = self._prepare(audio, sample_rate)

        # Silence gate (dot avoids allocating a full squared copy).
        if audio.size == 0 or float(np.dot(audio, audio)) / audio.size < 1e-6:
            return ""

        logger.info("Transcribing (%s, %s)...", backend.model.id, self.language)
        # Backend failures propagate: the engine turns them into an overlay
        # error flash + toast; swallowing them here made failures look like
        # silence (nothing delivered, no feedback).
        text = backend.transcribe(audio, 16_000)
        if text:
            logger.info(text)
        return text

    def transcribe_streaming(
        self,
        audio: np.ndarray,
        sample_rate: int = 16_000,
        prev_text: str = "",
    ) -> tuple[str, str]:
        """Incremental transcription using word-level local agreement.

        Returns (confirmed_new, full_text): the text this run and the previous
        run agree on (minus what was already confirmed), and the full current
        transcription.
        """
        full_text = self.transcribe(audio, sample_rate=sample_rate)
        if not full_text:
            return "", prev_text
        if not prev_text:
            return "", full_text

        prev_words = prev_text.split()
        curr_words = full_text.split()
        common_len = 0
        for pw, cw in zip(prev_words, curr_words):
            if pw != cw:
                break
            common_len += 1

        if common_len == 0:
            return "", full_text
        return " ".join(curr_words[:common_len]), full_text
