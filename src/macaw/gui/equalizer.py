from __future__ import annotations

import random

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

from macaw.gui.theme import active_theme

_theme = active_theme()


def _lerp(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


def _palette_at(colors, frac: float) -> QColor:
    """Sample a hex palette at position frac (0..1), interpolating between stops."""
    if len(colors) == 1:
        return QColor(colors[0])
    frac = max(0.0, min(1.0, frac))
    pos = frac * (len(colors) - 1)
    i = min(int(pos), len(colors) - 2)
    return _lerp(QColor(colors[i]), QColor(colors[i + 1]), pos - i)


class EqualizerWidget(QWidget):
    """Live audio-level visualizer: centre-anchored bars that fill the widget
    width and tint green while your voice is actually being heard.

    Feed it a 0..1 level via set_energy(); call start()/stop() to run the
    animation. ponytail: bar math mirrors the recording overlay (window.py) —
    kept separate so the Settings preview doesn't pull in the overlay's state
    machine. Fold together if they drift.
    """

    NUM_BARS = 32
    SMOOTHING = 0.34
    IDLE_COLOR = QColor(_theme.eq_idle)  # quiet bars
    EQ_COLORS = _theme.eq_colors  # heard: swept across the palette
    HEARD_LEVEL = 0.12  # energy above which we count it as speech

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.energy_level = 0.0
        self.bar_heights = [0.0] * self.NUM_BARS
        self.bar_targets = [0.0] * self.NUM_BARS
        self._target_tick = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(30)

    def stop(self) -> None:
        self._timer.stop()
        self.energy_level = 0.0
        self.bar_heights = [0.0] * self.NUM_BARS
        self.bar_targets = [0.0] * self.NUM_BARS
        self.update()

    def set_energy(self, energy: float) -> None:
        self.energy_level = min(1.0, max(0.0, energy))

    def _tick(self) -> None:
        self._update_bars()
        self.update()

    def _update_bars(self) -> None:
        self._target_tick += 1
        if self._target_tick >= 2:
            self._target_tick = 0
            e = self.energy_level
            center = self.NUM_BARS / 2.0
            for i in range(self.NUM_BARS):
                if e > 0.05:
                    dist = abs(i - center) / center  # taper toward edges
                    base = e * (1.0 - dist * 0.55)
                    self.bar_targets[i] = min(1.0, base * random.uniform(0.35, 1.0))
                else:
                    self.bar_targets[i] = random.uniform(0.015, 0.05)  # calm idle line

        for i in range(self.NUM_BARS):
            diff = self.bar_targets[i] - self.bar_heights[i]
            speed = self.SMOOTHING if diff > 0 else self.SMOOTHING * 0.45
            self.bar_heights[i] += diff * speed

    def paintEvent(self, event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        w, h = self.width(), self.height()
        slot = w / self.NUM_BARS
        bw = max(2.0, slot * 0.5)
        mid = h / 2.0
        max_h = h - 12

        # bars are grey when quiet and sweep into the theme's colour palette
        # (left→right) as soon as your voice is heard.
        heard = min(1.0, self.energy_level / self.HEARD_LEVEL)

        for i in range(self.NUM_BARS):
            bh = self.bar_heights[i]
            bar_h = max(2.0, bh * max_h)
            x = i * slot + (slot - bw) / 2.0
            y = mid - bar_h / 2.0  # grow symmetrically from the centre
            frac = i / (self.NUM_BARS - 1)
            c = _lerp(self.IDLE_COLOR, _palette_at(self.EQ_COLORS, frac), heard)
            c.setAlpha(int(120 + 135 * min(1.0, bh)))
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, y, bw, bar_h), bw / 2, bw / 2)

        p.end()
