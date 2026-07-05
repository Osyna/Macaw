from __future__ import annotations

import logging
import os
import typing

import numpy as np
from tqdm.auto import tqdm as _tqdm_base

from macaw.stt.base import Backend
from macaw.stt.registry import register

logger = logging.getLogger("macaw")

PUNCTUATION_PROMPTS: dict[str, str] = {
    "en": "Hello, how are you? I'm doing well. Let me explain the situation.",
    "fr": "Bonjour, comment allez-vous ? Je vais bien. Laissez-moi vous expliquer.",
    "de": "Hallo, wie geht es Ihnen? Mir geht es gut. Lassen Sie mich das erklären.",
    "es": "Hola, ¿cómo estás? Estoy bien. Déjame explicarte la situación.",
    "it": "Ciao, come stai? Sto bene. Lasciami spiegare la situazione.",
    "pt": "Olá, como vai? Estou bem. Deixe-me explicar a situação.",
    "nl": "Hallo, hoe gaat het? Het gaat goed. Laat me de situatie uitleggen.",
    "pl": "Cześć, jak się masz? Dobrze. Pozwól, że wyjaśnię sytuację.",
    "ru": "Привет, как дела? У меня всё хорошо. Позвольте мне объяснить ситуацию.",
    "ja": "こんにちは、お元気ですか？元気です。状況を説明させてください。",
    "zh": "你好，你好吗？我很好。让我解释一下情况。",
}

_ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]


def _detect_device() -> tuple[str, str]:
    """Pick the best device + compute type. Set MACAW_FORCE_CPU=1 to skip GPU."""
    if os.environ.get("MACAW_FORCE_CPU"):
        logger.info("MACAW_FORCE_CPU set — forcing CPU mode.")
        return "cpu", "int8"
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            supported = ctranslate2.get_supported_compute_types("cuda")
            logger.info("GPU detected, compute types: %s", ", ".join(sorted(supported)))
            for preferred in ("float16", "int8_float16", "int8"):
                if preferred in supported:
                    return "cuda", preferred
            return "cuda", "default"
    except Exception as exc:
        logger.debug("GPU detection failed: %s", exc)
    return "cpu", "int8"


class _disabled_tqdm(_tqdm_base):
    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        kwargs["disable"] = True
        super().__init__(*args, **kwargs)


def _make_progress_tqdm(callback: typing.Callable[[int], None]) -> type:
    class _ProgressTqdm(_tqdm_base):
        def update(self, n: int = 1) -> bool | None:
            result = super().update(n)
            if self.total and self.total > 0:
                callback(int(self.n / self.total * 100))
            return result

    return _ProgressTqdm


@register
class WhisperBackend(Backend):
    """faster-whisper (CTranslate2). CUDA/ROCm with automatic CPU fallback.

    Models, params and provenance live in ``stt/models/whisper.yaml``; this
    class only loads the weights and transcribes.
    """

    key = "whisper"

    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)
        self._model = None
        self._device = "cpu"

    # -- lifecycle ------------------------------------------------------

    def load(self, model_path: str | None = None) -> None:
        if self._model is not None:
            return
        device, compute_type = _detect_device()
        self._device = device
        logger.info("Loading Whisper (%s, %s)...", device, compute_type)
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            model_path or self.model.id, device=device, compute_type=compute_type
        )
        logger.info("Whisper loaded (%s).", device)

    def unload(self) -> None:
        self._model = None

    def is_cached(self) -> bool:
        try:
            import huggingface_hub

            huggingface_hub.snapshot_download(
                self.model.repo, local_files_only=True, allow_patterns=_ALLOW_PATTERNS
            )
            return True
        except Exception:
            return False

    def download(self, progress_callback=None) -> str:
        import huggingface_hub

        tqdm_cls = (
            _make_progress_tqdm(progress_callback)
            if progress_callback
            else _disabled_tqdm
        )
        return huggingface_hub.snapshot_download(
            self.model.repo, allow_patterns=_ALLOW_PATTERNS, tqdm_class=tqdm_cls
        )

    # -- transcription --------------------------------------------------

    def _run(self, audio: np.ndarray) -> str:
        segments, _info = self._model.transcribe(
            audio,
            language=self.language,
            initial_prompt=(
                PUNCTUATION_PROMPTS.get(self.language)
                if self.punctuation_hints
                else None
            ),
            beam_size=int(self.param("beam_size") or 1),
            temperature=float(self.param("temperature") or 0.0),
            vad_filter=bool(self.param("vad_filter")),
            vad_parameters=dict(min_silence_duration_ms=300),
            without_timestamps=True,
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> str:
        self.load()
        try:
            return self._run(audio)
        except Exception as exc:
            if self._device != "cuda":
                raise
            logger.error("CUDA transcription failed: %s — reloading on CPU", exc)
            self._model = None
            self._device = "cpu"
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model.id, device="cpu", compute_type="int8")
            return self._run(audio)
