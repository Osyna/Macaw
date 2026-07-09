from __future__ import annotations

import logging

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from macaw.gui.theme import active_theme

logger = logging.getLogger("macaw")

# ── palette (from the active theme — see macaw/gui/theme.py) ──────
_T = active_theme()
BG = _T.bg
FG = _T.fg
MUTED = _T.muted
BORDER = _T.border
CONTROL_BG = _T.control
OK = _T.ok
WARN = _T.warn
DANGER = _T.danger
CARD_BG = _T.surface
ACCENT = _T.accent
ACCENT_FG = _T.accent_fg


# ── shared stylesheet fragments ─────────────────────────────────


def _build_combo_style() -> str:
    return f"""
QComboBox {{
    background: {CONTROL_BG};
    color: {FG};
    border: 1px solid {BORDER};
    padding: 6px 10px;
    font-size: 13px;
    min-height: 18px;
}}
QComboBox:hover {{
    border-color: {MUTED};
}}
QComboBox:focus {{
    border-color: {FG};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {FG};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {CONTROL_BG};
    color: {FG};
    border: 1px solid {BORDER};
    selection-background-color: {BORDER};
    selection-color: {FG};
    outline: none;
    padding: 0;
    margin: 0;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 10px;
    min-height: 22px;
    background: {CONTROL_BG};
    color: {FG};
}}
QComboBox QAbstractItemView::item:selected {{
    background: {BORDER};
}}
QComboBox QListView {{
    background: {CONTROL_BG};
    border: 1px solid {BORDER};
    padding: 0;
    margin: 0;
}}
QComboBox QScrollBar:vertical {{
    background: {CONTROL_BG};
    width: 6px;
    margin: 0;
    padding: 0;
}}
QComboBox QScrollBar::handle:vertical {{
    background: {BORDER};
    min-height: 20px;
}}
QComboBox QScrollBar::add-line:vertical,
QComboBox QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""


_COMBO_STYLE = _build_combo_style()


class _StyledComboBox(QComboBox):
    """QComboBox subclass that forces the popup frame background to black."""

    def showPopup(self) -> None:
        super().showPopup()
        popup = self.findChild(QWidget, "QComboBoxPrivateContainer")
        _popup_css = (
            f"background: {CONTROL_BG}; border: 1px solid {BORDER};"
            " padding: 0; margin: 0;"
        )
        if popup is not None:
            popup.setStyleSheet(_popup_css)
        frame = self.view().parentWidget()
        if frame is not None:
            frame.setStyleSheet(_popup_css)


class ValueStepper(QWidget):
    """Minimal [ - ]  value  [ + ] stepper widget."""

    valueChanged = pyqtSignal(float)

    def __init__(
        self,
        value: float = 3.0,
        minimum: float = 1.0,
        maximum: float = 10.0,
        step: float = 0.5,
        suffix: str = "s",
        decimals: int = 1,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._value = value
        self._min = minimum
        self._max = maximum
        self._step = step
        self._suffix = suffix
        self._decimals = decimals
        self.setFixedSize(140, 34)

        _btn = (
            f"background: {CONTROL_BG}; color: {FG}; border: 1px solid {BORDER}; "
            f"font-size: 16px; font-weight: 400;"
        )
        _btn_hover = f"background: {BORDER}; border-color: {MUTED};"

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self._minus = QPushButton("\u2212")  # minus sign
        self._minus.setFixedSize(34, 34)
        self._minus.setCursor(Qt.CursorShape.PointingHandCursor)
        self._minus.setStyleSheet(
            f"QPushButton {{ {_btn} }} QPushButton:hover {{ {_btn_hover} }}"
        )
        self._minus.clicked.connect(self._dec)
        row.addWidget(self._minus)

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(
            f"background: {CONTROL_BG}; color: {FG}; "
            f"border-top: 1px solid {BORDER}; border-bottom: 1px solid {BORDER}; "
            f"border-left: none; border-right: none; "
            f"font-size: 13px; padding: 0 4px;"
        )
        row.addWidget(self._label, 1)

        self._plus = QPushButton("+")
        self._plus.setFixedSize(34, 34)
        self._plus.setCursor(Qt.CursorShape.PointingHandCursor)
        self._plus.setStyleSheet(
            f"QPushButton {{ {_btn} }} QPushButton:hover {{ {_btn_hover} }}"
        )
        self._plus.clicked.connect(self._inc)
        row.addWidget(self._plus)

        self._refresh()

    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        self._value = max(self._min, min(self._max, v))
        self._refresh()

    def _inc(self) -> None:
        self.setValue(round(self._value + self._step, 2))
        self.valueChanged.emit(self._value)

    def _dec(self) -> None:
        self.setValue(round(self._value - self._step, 2))
        self.valueChanged.emit(self._value)

    def _refresh(self) -> None:
        self._label.setText(f"{self._value:.{self._decimals}f}{self._suffix}")
        self._minus.setEnabled(self._value > self._min)
        self._plus.setEnabled(self._value < self._max)


# ── custom toggle switch ─────────────────────────────────────────


class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(44, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = False
        self._thumb_x = 2.0

        self._anim = QPropertyAnimation(self, b"thumbX")
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool) -> None:
        if self._checked == on:
            return
        self._checked = on
        end = 24.0 if on else 2.0
        self._anim.stop()
        self._anim.setStartValue(self._thumb_x)
        self._anim.setEndValue(end)
        self._anim.start()
        self.toggled.emit(on)

    @pyqtProperty(float)
    def thumbX(self) -> float:
        return self._thumb_x

    @thumbX.setter
    def thumbX(self, val: float) -> None:
        self._thumb_x = val
        self.update()

    def mousePressEvent(self, ev: object) -> None:
        self.setChecked(not self._checked)

    def paintEvent(self, ev: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Track
        track_color = QColor(FG) if self._checked else QColor(BORDER)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(track_color if self._checked else QColor(CONTROL_BG))
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)

        # Thumb — square
        thumb_color = QColor(BG) if self._checked else QColor(FG)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(thumb_color)
        p.drawRect(int(self._thumb_x), 2, 18, 18)

        p.end()


class _ColorSwatch(QPushButton):
    """A small clickable colour chip that opens the native colour picker."""

    picked = pyqtSignal(str)

    def __init__(self, hex_color: str = "#FFFFFF", parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(38, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hex = hex_color
        self._restyle()
        self.clicked.connect(self._pick)

    def color(self) -> str:
        return self._hex

    def setColor(self, hex_color: str) -> None:
        self._hex = hex_color
        self._restyle()

    def _restyle(self) -> None:
        self.setStyleSheet(
            f"QPushButton {{ background: {self._hex}; border: 1px solid {BORDER}; }}"
            f"QPushButton:hover {{ border: 1px solid {MUTED}; }}"
        )

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self._hex), self, "Pick a colour")
        if c.isValid():
            self.setColor(c.name())
            self.picked.emit(c.name())


# ── helpers ──────────────────────────────────────────────────────


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        f"color: {MUTED}; font-size: 10px; letter-spacing: 3px; "
        f"padding: 0; margin: 0; border: none; background: transparent;"
    )
    return lbl


def _field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {FG}; font-size: 13px; "
        f"padding: 0; margin: 0; border: none; background: transparent;"
    )
    return lbl


def _hint_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {MUTED}; font-size: 11px; "
        f"padding: 0; margin: 0; border: none; background: transparent;"
    )
    return lbl


def _separator() -> QWidget:
    line = QWidget()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {BORDER};")
    return line


def _close_button(on_click) -> QPushButton:
    """A minimal × close button for the top-right corner."""
    btn = QPushButton("×")
    btn.setFixedSize(26, 26)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background: transparent; color: {MUTED}; border: none; "
        f"font-size: 22px; }} QPushButton:hover {{ color: {FG}; }}"
    )
    btn.clicked.connect(on_click)
    return btn


def _combo(width: int) -> _StyledComboBox:
    c = _StyledComboBox()
    c.setStyleSheet(_COMBO_STYLE)
    c.setFixedWidth(width)
    return c
