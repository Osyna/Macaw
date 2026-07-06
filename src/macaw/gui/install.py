from __future__ import annotations

import logging
import subprocess

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from macaw.gui.theme import active_theme
from macaw.stt.deps import packages_for_extra
from macaw.stt.isolated import install_commands, mark_installed, remove

logger = logging.getLogger("macaw")

_T = active_theme()
BG = _T.bg
FG = _T.fg
MUTED = _T.muted
BORDER = _T.border
DANGER = _T.danger


class _InstallWorker(QThread):
    """Builds an isolated venv per extra and installs the backend into it.

    A dedicated venv resolves independently, so these packages can never change
    or conflict with the main CUDA + faster-whisper environment.
    """

    line = pyqtSignal(str)
    done = pyqtSignal(bool, str)  # (ok, message)

    def __init__(self, extras: list[str]) -> None:
        super().__init__()
        self._extras = extras
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self._last = ""

    def run(self) -> None:
        try:
            for extra in self._extras:
                if not self._install_one(extra):
                    return
            if not self._cancelled:
                self.done.emit(True, "Installed")
        except Exception as exc:  # noqa: BLE001
            if not self._cancelled:
                logger.error("Install error: %s", exc)
                self.done.emit(False, str(exc))

    def _install_one(self, extra: str) -> bool:
        packages = packages_for_extra(extra)
        if not packages:
            self.done.emit(False, f"No packages found for '{extra}'")
            return False
        self.line.emit(f"Creating isolated environment for {extra}…")
        for cmd in install_commands(extra, packages):
            code = self._stream(cmd)
            if self._cancelled:
                return False
            if code != 0:
                remove(extra)  # drop the partial venv, leave nothing half-built
                detail = self._last or f"exit {code}"
                logger.error("'%s' install failed: %s", extra, detail)
                self.done.emit(False, f"'{extra}' failed — {detail}")
                return False
        mark_installed(extra)
        return True

    def _stream(self, cmd: list[str]) -> int:
        logger.info("Install step: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            if self._cancelled:
                break
            text = raw.rstrip()
            if text:
                self._last = text
                self.line.emit(text)
        return self._proc.wait()

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


class DependencyInstallDialog(QDialog):
    """Modal dialog that installs optional backends into isolated venvs."""

    def __init__(self, label: str, extras: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Macaw — install")
        self.setFixedSize(440, 210)
        self.setModal(True)
        self._extras = extras
        self._worker: _InstallWorker | None = None

        self.setStyleSheet(f"""
            QDialog {{
                background: {BG}; color: {FG};
                font-family: system-ui, -apple-system, sans-serif;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        self._title = QLabel(f"Installing {label}…")
        self._title.setStyleSheet(f"color: {FG}; font-size: 13px; font-weight: 500;")
        layout.addWidget(self._title)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)  # indeterminate — download size is unknown
        self._bar.setFixedHeight(8)
        self._bar.setTextVisible(False)
        self._bar.setStyleSheet(f"""
            QProgressBar {{ background: {BORDER}; border: none; }}
            QProgressBar::chunk {{ background: {FG}; }}
        """)
        layout.addWidget(self._bar)

        self._status = QLabel("Starting…")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        self._status.setFixedHeight(60)
        layout.addWidget(self._status)

        layout.addStretch()

        self._btn = QPushButton("Cancel")
        self._btn.setFixedWidth(90)
        self._btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {MUTED};
                border: 1px solid {BORDER}; padding: 6px 12px; font-size: 11px;
            }}
            QPushButton:hover {{ color: {FG}; border-color: {MUTED}; }}
        """)
        self._btn.clicked.connect(self._on_button)
        layout.addWidget(self._btn)

    def start(self) -> None:
        self._worker = _InstallWorker(self._extras)
        self._worker.line.connect(self._on_line)
        self._worker.done.connect(self._on_done)
        self._worker.start()
        self.exec()

    def _on_line(self, text: str) -> None:
        self._status.setText(text[-200:])

    def _on_done(self, ok: bool, message: str) -> None:
        if ok:
            self._title.setText("Installed ✓")
            self._status.setText("Ready to use. You can close this window.")
            self._bar.setRange(0, 100)
            self._bar.setValue(100)
            self.accept()
        else:
            self._title.setText("Install failed")
            self._status.setText(message)
            self._bar.setRange(0, 100)
            self._bar.setValue(0)
            self._btn.setText("Close")

    def _on_button(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
        self.reject()
