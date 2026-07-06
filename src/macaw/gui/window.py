import math
import random
import subprocess

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from macaw.gui.theme import active_theme, qcolor

_theme = active_theme()


def _palette_color(theme, frac: float, alpha: int) -> QColor:
    """A colour sampled from a theme's eq palette at position frac
    (0=left … 1=right), linearly interpolated between stops."""
    cols = theme.eq_colors
    frac = min(1.0, max(0.0, frac))
    if len(cols) == 1:
        return qcolor(cols[0], alpha)
    pos = frac * (len(cols) - 1)
    i = min(int(pos), len(cols) - 2)
    t = pos - i
    a, b = QColor(cols[i]), QColor(cols[i + 1])
    c = QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )
    c.setAlpha(alpha)
    return c


def _shape_path(rect: QRectF, corners) -> QPainterPath:
    """A rounded-rect path with independent corner radii (tl, tr, br, bl).
    A corner radius of 0 is drawn sharp — that's what makes a speech bubble."""
    tl, tr, br, bl = corners
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path = QPainterPath()
    path.moveTo(x + tl, y)
    path.lineTo(x + w - tr, y)
    if tr:
        path.arcTo(x + w - 2 * tr, y, 2 * tr, 2 * tr, 90, -90)
    path.lineTo(x + w, y + h - br)
    if br:
        path.arcTo(x + w - 2 * br, y + h - 2 * br, 2 * br, 2 * br, 0, -90)
    path.lineTo(x + bl, y + h)
    if bl:
        path.arcTo(x, y + h - 2 * bl, 2 * bl, 2 * bl, 270, -90)
    path.lineTo(x, y + tl)
    if tl:
        path.arcTo(x, y, 2 * tl, 2 * tl, 180, -90)
    path.closeSubpath()
    return path


# ── shared rect-based painters (used by the overlay AND the Settings preview) ──


def _paint_overlay(p, rect, theme, state, bars, phase, done) -> None:
    """Draw the whole recording bar into `rect` using `theme`."""
    a = int(255 * max(0.3, min(1.0, theme.overlay_opacity)))
    bw = max(0, int(theme.border_width))
    # Inset by half the stroke so the whole border stays inside the widget.
    inset = bw / 2.0
    r = QRectF(rect).adjusted(inset, inset, -inset, -inset)
    path = _shape_path(r, theme.corners)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(qcolor(theme.overlay_bg, a))
    p.drawPath(path)

    if bw > 0:
        pen = QPen(qcolor(theme.border_color, a), bw)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)

    if state in ("recording", "analysing"):
        # clip the wave to the pill so nothing spills past the rounded ends
        p.save()
        p.setClipPath(path)
        if state == "recording":
            _paint_eq(p, r, theme, bars)
        else:
            _paint_loader(p, r, theme, phase, len(bars))
        p.restore()
    elif state == "done":
        _paint_done(p, r, theme, done)


def _bar_layout(rect, theme, n):
    """Shared bar geometry for `n` centred bars: (start_x, pitch, bar_w, bar_r).
    Honours the corner inset + spacing/width/roundness settings so the record
    equaliser and the analysing loader look identical."""
    x0, w = rect.x(), rect.width()
    # inset horizontally by the corner radius so bars clear the rounded ends
    hpad = min(float(max(theme.corners)) if theme.corners else 0.0, w * 0.4)
    avail = max(1.0, w - 2.0 * hpad)
    slot = avail / n
    gap = slot * 0.58 if theme.bar_spacing < 0 else float(theme.bar_spacing)
    bw = (slot - gap) if theme.bar_width < 0 else float(theme.bar_width)
    bw = max(2.0, bw)
    pitch = bw + gap
    row = pitch * n - gap
    if row > avail:  # too wide for the pill — scale to fit
        s = avail / row
        bw *= s
        pitch *= s
        row = avail
    br = min(max(0.0, float(theme.bar_radius)), bw / 2.0)
    start = x0 + hpad + (avail - row) / 2.0
    return start, pitch, bw, br


def _paint_bar(p, x, cy_top, bw, bar_h, br) -> None:
    bar = QRectF(x, cy_top, bw, bar_h)
    if br > 0:
        p.drawRoundedRect(bar, br, br)
    else:
        p.drawRect(bar)


def _paint_eq(p, rect, theme, bars) -> None:
    n = len(bars)
    if not n:
        return
    start, pitch, bw, br = _bar_layout(rect, theme, n)
    y0, h = rect.y(), rect.height()
    vpad = max(8.0, h * 0.18)
    bot, top = y0 + h - vpad, y0 + vpad
    max_h = bot - top
    p.setPen(Qt.PenStyle.NoPen)
    for i, bh in enumerate(bars):
        bar_h = max(2.0, bh * max_h)
        alpha = int(100 + 155 * min(1.0, bh)) if theme.bar_fade else 255
        frac = i / (n - 1) if n > 1 else 0.0
        p.setBrush(_palette_color(theme, frac, alpha))
        _paint_bar(p, start + i * pitch, bot - bar_h, bw, bar_h, br)


def _paint_loader(p, rect, theme, phase, n) -> None:
    if n < 1:
        return
    start, pitch, bw, br = _bar_layout(rect, theme, n)
    mid = rect.y() + rect.height() / 2.0
    max_h = rect.height() * 0.42
    t = phase * 0.16
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(n):
        fx = i / (n - 1) if n > 1 else 0.0
        wave = 0.5 + 0.5 * math.sin(t - fx * 5.0)
        wave2 = 0.5 + 0.5 * math.sin(t * 0.7 - fx * 8.0)
        amp = max(wave, wave2 * 0.6)
        bar_h = 3.0 + amp * max_h
        alpha = int(60 + amp * 175) if theme.bar_fade else 255
        p.setBrush(_palette_color(theme, fx, alpha))
        _paint_bar(p, start + i * pitch, mid - bar_h / 2, bw, bar_h, br)


def _paint_done(p, rect, theme, prog) -> None:
    cx = rect.x() + rect.width() / 2.0
    cy = rect.y() + rect.height() / 2.0
    if prog < 1.0:
        rr = 12.0 + prog * 26.0
        p.setPen(QPen(qcolor(theme.accent, int(150 * (1.0 - prog))), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), rr, rr)
    disc_a = int(45 * min(1.0, prog * 2.0))
    if disc_a > 0:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(theme.accent, disc_a))
        p.drawEllipse(QPointF(cx, cy), 15.0, 15.0)
    alpha = int(255 * min(1.0, prog * 2.5))
    pen = QPen(qcolor(theme.accent, alpha), 3.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    pt1 = QPointF(cx - 8, cy + 1)
    pt2 = QPointF(cx - 2, cy + 7)
    pt3 = QPointF(cx + 9, cy - 6)
    if prog < 0.4:
        t = prog / 0.4
        p.drawLine(
            pt1,
            QPointF(
                pt1.x() + (pt2.x() - pt1.x()) * t, pt1.y() + (pt2.y() - pt1.y()) * t
            ),
        )
    else:
        p.drawLine(pt1, pt2)
        t = min(1.0, (prog - 0.4) / 0.6)
        p.drawLine(
            pt2,
            QPointF(
                pt2.x() + (pt3.x() - pt2.x()) * t, pt2.y() + (pt3.y() - pt2.y()) * t
            ),
        )


class RecordingWindow(QWidget):
    stop_signal = pyqtSignal()

    NUM_BARS = 24
    SMOOTHING = 0.32

    def __init__(self):
        super().__init__()
        self.setWindowTitle("LiveTranscriberOverlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(210, 52)

        self.state = "recording"
        self.pulse_phase = 0
        self.energy_level = 0.0

        # Equalizer
        self.bar_heights = [0.0] * self.NUM_BARS
        self.bar_targets = [0.0] * self.NUM_BARS
        self._target_tick = 0

        # Loader
        self.load_angle = 0.0

        # Done
        self.done_progress = 0.0

        # Position
        self._placement = "bottom_center"
        self._padding = 32

        # Animation
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(30)

    def position(self, placement="bottom_center", padding=32):
        self._placement = placement
        self._padding = padding
        self._apply_position()

    def _apply_position(self):
        screen = QApplication.primaryScreen().geometry()
        sw, sh = screen.width(), screen.height()
        ww, wh = self.width(), self.height()
        p = self._padding

        positions = {
            "top_left": (p, p),
            "top_center": ((sw - ww) // 2, p),
            "top_right": (sw - ww - p, p),
            "center": ((sw - ww) // 2, (sh - wh) // 2),
            "bottom_left": (p, sh - wh - p),
            "bottom_center": ((sw - ww) // 2, sh - wh - p),
            "bottom_right": (sw - ww - p, sh - wh - p),
        }

        x, y = positions.get(self._placement, positions["bottom_center"])
        self.move(x, y)

    def showEvent(self, event):
        super().showEvent(event)
        # Reapply position after window is mapped (Wayland ignores move before show)
        QTimer.singleShot(10, self._apply_position)
        # Hyprland fallback: force position via hyprctl after window appears
        QTimer.singleShot(50, self._hyprctl_move)

    def _hyprctl_move(self):
        screen = QApplication.primaryScreen().geometry()
        sw, sh = screen.width(), screen.height()
        ww, wh = self.width(), self.height()
        p = self._padding

        positions = {
            "top_left": (p, p),
            "top_center": ((sw - ww) // 2, p),
            "top_right": (sw - ww - p, p),
            "center": ((sw - ww) // 2, (sh - wh) // 2),
            "bottom_left": (p, sh - wh - p),
            "bottom_center": ((sw - ww) // 2, sh - wh - p),
            "bottom_right": (sw - ww - p, sh - wh - p),
        }

        x, y = positions.get(self._placement, positions["bottom_center"])
        try:
            subprocess.Popen(
                [
                    "hyprctl",
                    "dispatch",
                    "movewindowpixel",
                    f"exact {x} {y},title:LiveTranscriberOverlay",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass

    @pyqtSlot(str)
    def set_state(self, state):
        self.state = state
        self.pulse_phase = 0
        if state == "done":
            self.done_progress = 0.0
        if state in ("recording", "analysing"):
            self.load_angle = 0.0
        if not self.timer.isActive():
            self.timer.start(30)
        self.update()

    def set_energy(self, energy):
        self.energy_level = min(1.0, max(0.0, energy))

    # ── animation ───────────────────────────────────────────────────

    def _tick(self):
        self.pulse_phase += 1
        if self.state == "recording":
            self._update_bars()
        elif self.state == "analysing":
            self.load_angle = (self.load_angle + 5) % 360
        elif self.state == "done":
            self.done_progress = min(1.0, self.done_progress + 0.07)
            if self.done_progress >= 1.0 and self.pulse_phase > 40:
                self.timer.stop()
        self.update()

    def _update_bars(self):
        self._target_tick += 1
        if self._target_tick >= 2:
            self._target_tick = 0
            e = self.energy_level
            center = self.NUM_BARS / 2.0
            for i in range(self.NUM_BARS):
                if e > 0.05:
                    dist = abs(i - center) / center
                    base = e * (1.0 - dist * 0.5)
                    self.bar_targets[i] = min(1.0, base * random.uniform(0.3, 1.0))
                else:
                    self.bar_targets[i] = random.uniform(0.02, 0.08)

        for i in range(self.NUM_BARS):
            diff = self.bar_targets[i] - self.bar_heights[i]
            speed = self.SMOOTHING if diff > 0 else self.SMOOTHING * 0.45
            self.bar_heights[i] += diff * speed

    # ── painting ────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _paint_overlay(
            p,
            self.rect(),
            _theme,
            self.state,
            self.bar_heights,
            self.pulse_phase,
            self.done_progress,
        )
        p.end()

    # ── input ───────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.stop_signal.emit()


class RecordingPreview(QWidget):
    """A live, animated mini-render of the recording bar for the Settings
    Appearance panel. Cycles recording → analysing → done using a *pending*
    theme so the user sees shape, opacity, colours and accent before applying."""

    NUM_BARS = 20

    def __init__(self, theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setMinimumHeight(78)
        self._phase = 0
        self._state = "recording"
        self._frames = 0
        self._bars = [0.05] * self.NUM_BARS
        self._done = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_theme(self, theme) -> None:
        self._theme = theme
        self.update()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(33)

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._phase += 1
        self._frames += 1
        if self._state == "recording":
            e = 0.30 + 0.45 * (0.5 + 0.5 * math.sin(self._phase * 0.10))
            c = self.NUM_BARS / 2.0
            for i in range(self.NUM_BARS):
                dist = abs(i - c) / c
                tgt = (
                    e
                    * (1.0 - dist * 0.45)
                    * (0.55 + 0.45 * math.sin(self._phase * 0.22 + i * 0.5))
                )
                self._bars[i] += (max(0.05, tgt) - self._bars[i]) * 0.28
            if self._frames > 80:
                self._go("analysing")
        elif self._state == "analysing":
            if self._frames > 42:
                self._go("done")
        elif self._state == "done":
            self._done = min(1.0, self._done + 0.06)
            if self._frames > 48:
                self._go("recording")
        self.update()

    def _go(self, state: str) -> None:
        self._state = state
        self._frames = 0
        if state == "done":
            self._done = 0.0

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        # a soft "desktop" backdrop so the bar's opacity is visible
        grad = QLinearGradient(0.0, 0.0, float(r.width()), float(r.height()))
        grad.setColorAt(0.0, QColor("#454b57"))
        grad.setColorAt(1.0, QColor("#23262e"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(r), 10, 10)
        pill = QRectF(r).adjusted(30, 15, -30, -15)
        _paint_overlay(
            p,
            pill,
            self._theme,
            self._state,
            self._bars,
            self._phase,
            self._done,
        )
        p.end()
