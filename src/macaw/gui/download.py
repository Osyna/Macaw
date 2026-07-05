from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from macaw.audio.transcriber import Transcriber
from macaw.gui.theme import active_theme

logger = logging.getLogger("macaw")

_T = active_theme()
BG = _T.bg
FG = _T.fg
MUTED = _T.muted
BORDER = _T.border


def refresh_theme() -> None:
    """Re-resolve the palette from the active theme (live switching)."""
    global _T, BG, FG, MUTED, BORDER
    _T = active_theme()
    BG, FG, MUTED, BORDER = _T.bg, _T.fg, _T.muted, _T.border


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


class ModelDownloadDialog(QDialog):
    """Modal dialog showing model download + load progress."""

    model_ready = pyqtSignal()

    def __init__(
        self,
        transcriber: Transcriber,
        parent=None,
        load_after: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Macaw")
        self.setFixedSize(360, 180)
        self.setModal(True)
        self._transcriber = transcriber
        self._load_after = load_after
        self._worker: _DownloadWorker | None = None
        self._success = False

        self.setStyleSheet(f"""
            QDialog {{
                background: {BG};
                color: {FG};
                font-family: system-ui, -apple-system, sans-serif;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        self._title = QLabel(f"Downloading {transcriber.model_size}...")
        self._title.setStyleSheet(f"color: {FG}; font-size: 13px; font-weight: 500;")
        layout.addWidget(self._title)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setStyleSheet(f"""
            QProgressBar {{
                background: {BORDER};
                border: 1px solid {BORDER};
                height: 8px;
                text-align: center;
                color: transparent;
            }}
            QProgressBar::chunk {{
                background: {FG};
            }}
        """)
        layout.addWidget(self._bar)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        layout.addWidget(self._status)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedSize(80, 30)
        self._cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {MUTED};
                border: 1px solid {BORDER};
                padding: 6px 12px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                color: {FG};
                border-color: {MUTED};
            }}
        """)
        self._cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self._cancel_btn)

    def start(self) -> None:
        """Start the download and show the dialog."""
        self._worker = _DownloadWorker(self._transcriber, load_after=self._load_after)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()
        self.exec()

    def succeeded(self) -> bool:
        return self._success

    def _on_progress(self, pct: int) -> None:
        self._bar.setValue(pct)
        self._status.setText(f"{pct}%")

    def _on_finished(self, path: str) -> None:
        self._bar.setValue(100)
        if self._load_after:
            self._title.setText("Loading model...")
            self._status.setText("Initializing...")
        else:
            self._title.setText("Downloaded")
            self._status.setText("Done")
        self._success = True
        self.model_ready.emit()
        self.accept()

    def _on_error(self, msg: str) -> None:
        self._title.setText("Download failed")
        self._status.setText(msg)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._cancel_btn.setText("Close")

    def _on_cancel(self) -> None:
        if self._worker:
            self._worker.cancel()
        self.reject()
