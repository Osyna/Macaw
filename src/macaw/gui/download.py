from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

from macaw.audio.transcriber import Transcriber

logger = logging.getLogger("macaw")


class _DownloadWorker(QThread):
    """Downloads and loads a Whisper model in a background thread."""

    progress = pyqtSignal(int)
    finished = pyqtSignal(str)  # local model path
    error = pyqtSignal(str)

    def __init__(self, transcriber: Transcriber, load_after: bool = True) -> None:
        super().__init__()
        self._transcriber = transcriber
        self._load_after = load_after
        self._cancelled = False

    def run(self) -> None:
        try:
            path = self._transcriber.download_model(
                progress_callback=self._on_progress,
            )
            if self._cancelled:
                return
            if self._load_after:
                self._transcriber.load_model(model_path=path)
            self.finished.emit(path)
        except Exception as exc:
            if not self._cancelled:
                logger.error("Model download failed: %s", exc)
                self.error.emit(str(exc))

    def _on_progress(self, pct: int) -> None:
        if not self._cancelled:
            self.progress.emit(pct)

    def cancel(self) -> None:
        self._cancelled = True


_LOADING_QUOTES = [
    "Warming up the vocal cords…",
    "Teaching the parrot new words…",
    "Summoning the phonemes…",
    "Bribing the GPU…",
    "Untangling the neural spaghetti…",
    "Aligning the mel spectrograms…",
    "Waking the transformer…",
    "Feeding the parrot a cracker…",
    "Quantizing the vibes…",
    "Reticulating splines…",
    "Polishing the attention heads…",
    "Convincing the tensors to cooperate…",
    "Decoding the squawks…",
    "Spinning up the beam search…",
    "Loading loquacious layers…",
    "Tuning the eardrums…",
]
