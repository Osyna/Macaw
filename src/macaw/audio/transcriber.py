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
        vad_gate: bool = True,
    ) -> None:
        self.model_size = model_size
        self.language = language
        self.punctuation_hints = punctuation_hints
        self.vad_gate = vad_gate
        self.model_params: dict = {}  # tunables for the current model
        self._backend = None
        self._stream_fed = 0  # samples already fed to a native stream (16 kHz)

    def reset_stream(self) -> None:
        """Start a fresh live-typing utterance (engine calls this on record start)."""
        self._stream_fed = 0

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

    @staticmethod
    def _trim_silence(audio: np.ndarray) -> np.ndarray:
        """Silence gate: cut long near-silent stretches so every backend pays
        for speech only (Whisper pads to a full 30 s window regardless of
        content, and hallucinates on silence). Pure numpy — an adaptive RMS
        gate over 30 ms frames (Silero left the engine with faster-whisper).
        Conservative: 2 s minimum gap, 400 ms padding around speech, and only
        frames quieter than -46 dBFS are ever eligible for trimming; mostly-
        speech audio passes through untouched. Returns an empty array when no
        speech at all is detected."""
        frame = 480  # 30 ms @ 16 kHz
        n = audio.size // frame
        if n < 40:  # ~1.2 s — nothing worth trimming
            return audio
        try:
            frames = audio[: n * frame].reshape(n, frame)
            rms = np.sqrt(np.mean(frames * frames, axis=1))
            # Adaptive threshold: 3x the quietest-decile noise floor, clamped so
            # anything above -46 dBFS is always kept (never trims quiet speech).
            floor = float(np.percentile(rms, 10))
            thr = min(max(3.0 * floor, 1e-3), 5e-3)
            speech = rms > thr
        except Exception as exc:  # noqa: BLE001 — gate must never lose audio
            logger.warning("VAD gate failed (%s) — transcribing unfiltered", exc)
            return audio
        if not speech.any():
            return audio[:0]
        # Merge speech runs separated by < 2 s, then pad each run by 400 ms.
        gap, pad = 66, 13  # frames: 2 s, 400 ms
        idx = np.flatnonzero(speech)
        runs: list[list[int]] = [[int(idx[0]), int(idx[0])]]
        for i in idx[1:]:
            if i - runs[-1][1] <= gap:
                runs[-1][1] = int(i)
            else:
                runs.append([int(i), int(i)])
        spans = [
            (max(0, s - pad) * frame, min(n, e + 1 + pad) * frame) for s, e in runs
        ]
        if spans[-1][1] == n * frame:
            spans[-1] = (spans[-1][0], audio.size)  # keep the sub-frame tail
        kept = sum(e - s for s, e in spans)
        if kept >= 0.9 * audio.size:
            return audio  # mostly speech — skip the copy, keep exact boundaries
        logger.info(
            "VAD gate: %.1fs -> %.1fs of speech", audio.size / 16_000, kept / 16_000
        )
        return np.concatenate([audio[s:e] for s, e in spans])

    # -- transcription --------------------------------------------------

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> str:
        """Transcribe a complete audio signal. Returns stripped text or ''."""
        backend = self._ensure_backend()
        self._stream_fed = 0  # a batch pass supersedes any live stream (worker too)
        audio = self._prepare(audio, sample_rate)

        # Silence gate (dot avoids allocating a full squared copy).
        if audio.size == 0 or float(np.dot(audio, audio)) / audio.size < 1e-6:
            return ""
        if self.vad_gate:
            audio = self._trim_silence(audio)
            if audio.size == 0:
                return ""  # no speech at all — nothing to transcribe

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

        Natively streaming backends (sherpa online models) get ONLY the new
        samples each call — bounded per-tick cost instead of re-decoding the
        whole utterance. Everything else re-transcribes the full buffer.
        """
        full_text = self._streaming_pass(audio, sample_rate)
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

    def _streaming_pass(self, audio: np.ndarray, sample_rate: int) -> str:
        """One live-typing decode: native delta feed when supported, else a
        full-buffer re-transcription. Returns the full text so far."""
        backend = self._ensure_backend()
        if sample_rate == 16_000:  # delta indices only line up unresampled
            prepared = self._prepare(audio, sample_rate)
            delta = prepared[self._stream_fed :]
            if delta.size == 0 and self._stream_fed > 0:
                return ""  # active native stream, no new audio — nothing new
            if delta.size:
                partial = backend.transcribe_partial(delta, 16_000)
                if partial is not None:
                    self._stream_fed = prepared.size
                    if partial:
                        logger.info(partial)
                    return partial
        return self.transcribe(audio, sample_rate=sample_rate)
