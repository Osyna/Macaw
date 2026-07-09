"""Key-capture widget for the global shortcut.

Click it and press your combo. Capture goes through evdev (the same layer the
listener uses), NOT Qt key events — so Super and other keys a Wayland compositor
grabs are still captured, and what you set is exactly what will fire.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from macaw.gui.theme import active_theme
from macaw.gui.widgets import BORDER, CONTROL_BG, FG, MUTED
from macaw.hotkey import HotkeyCapture, check_access, pretty

_ACCENT = active_theme().accent
_PROMPT = "Press your shortcut…   ·   Esc to cancel"


class ShortcutCapture(QWidget):
    """Field that captures a key combo via evdev. Emits `changed(spec)`."""

    changed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._spec = ""
        self._capture: HotkeyCapture | None = None
        self._result: tuple[str, str] | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._field = QPushButton()
        self._field.setCursor(Qt.CursorShape.PointingHandCursor)
        self._field.setFixedHeight(32)
        self._field.setMinimumWidth(200)
        self._field.clicked.connect(self._toggle)
        lay.addWidget(self._field, 1)
        self._clear = QPushButton("Clear")
        self._clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear.setFixedHeight(32)
        self._clear.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {MUTED};"
            f" border: 1px solid {BORDER}; padding: 4px 10px; font-size: 11px; }}"
            f" QPushButton:hover {{ color: {FG}; }}"
        )
        self._clear.clicked.connect(self._do_clear)
        lay.addWidget(self._clear)
        self._render()

    # -- public API --
    def spec(self) -> str:
        return self._spec

    def set_spec(self, spec: str) -> None:
        self._cancel()
        self._spec = spec or ""
        self._render()

    # -- capture lifecycle --
    def _toggle(self) -> None:
        if self._capture is not None:
            self._cancel()
            self._render()
        else:
            self._start_capture()

    def _start_capture(self) -> None:
        ok, reason = check_access()
        if not ok:
            self._flash(reason)
            return
        self._result = None
        self._capture = HotkeyCapture()
        self._capture.captured.connect(self._on_captured)
        self._capture.preview.connect(self._on_preview)
        self._capture.failed.connect(self._on_failed)
        self._capture.finished.connect(self._on_finished)
        self._clear.setEnabled(False)
        self._field.setText(_PROMPT)
        self._field.setStyleSheet(self._css(_ACCENT, FG))
        self._capture.start()

    def _cancel(self) -> None:
        if self._capture is not None:
            self._capture.stop()
            self._capture.wait(1500)

    def _do_clear(self) -> None:
        self._cancel()
        if self._spec:
            self._spec = ""
            self.changed.emit("")
        self._render()

    # -- capture signals (delivered on the UI thread) --
    def _on_captured(self, spec: str) -> None:
        self._result = ("ok", spec)

    def _on_preview(self, mods: str) -> None:
        label = pretty(mods)
        self._field.setText(f"{label} + …" if label else _PROMPT)

    def _on_failed(self, reason: str) -> None:
        self._result = ("fail", reason)

    def _on_finished(self) -> None:
        self._capture = None
        self._clear.setEnabled(True)
        result, self._result = self._result, None
        if result and result[0] == "ok":
            self._spec = result[1]
            self._render()
            self.changed.emit(self._spec)
        elif result and result[0] == "fail":
            self._render()
            self._flash(result[1])
        else:
            self._render()

    # -- rendering --
    def _render(self) -> None:
        has = bool(self._spec)
        self._field.setText(pretty(self._spec) if has else "Click to set a shortcut")
        self._field.setStyleSheet(self._css(BORDER, FG if has else MUTED))
        self._clear.setVisible(has)

    def _flash(self, msg: str) -> None:
        self._field.setText(msg)
        self._field.setStyleSheet(self._css(BORDER, MUTED))

    def _css(self, border: str, fg: str) -> str:
        return (
            f"QPushButton {{ background: {CONTROL_BG}; color: {fg};"
            f" border: 1px solid {border}; padding: 4px 12px; font-size: 12px;"
            f" text-align: left; }}"
            f" QPushButton:hover {{ border-color: {MUTED}; }}"
        )
