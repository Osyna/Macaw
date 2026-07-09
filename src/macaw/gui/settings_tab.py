from __future__ import annotations

import logging
import math
import queue
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from macaw.audio.capture import AudioCapture
from macaw.config import Config
from macaw.desktop import auto_type_available, auto_type_package
from macaw.gui.equalizer import EqualizerWidget
from macaw.gui.inputtool import InputToolInstallDialog
from macaw.gui.shortcut import ShortcutCapture
from macaw.gui.theme import THEMES, active_indicator
from macaw.gui.widgets import (
    _COMBO_STYLE,
    ACCENT,
    ACCENT_FG,
    BG,
    BORDER,
    CARD_BG,
    CONTROL_BG,
    FG,
    MUTED,
    ToggleSwitch,
    ValueStepper,
    _ColorSwatch,
    _combo,
    _field_label,
    _hint_label,
    _section_label,
    _StyledComboBox,
)
from macaw.gui.window import RecordingPreview

logger = logging.getLogger("macaw")


def _eq_stops(colors) -> list[str]:
    """Three representative gradient stops (start, middle, end) from a palette."""
    cols = list(colors) or ["#4CAF7D"]
    if len(cols) >= 3:
        return [cols[0], cols[len(cols) // 2], cols[-1]]
    if len(cols) == 2:
        return [cols[0], cols[0], cols[1]]
    return [cols[0], cols[0], cols[0]]


def _combo_expand() -> _StyledComboBox:
    c = _StyledComboBox()
    c.setStyleSheet(_COMBO_STYLE)
    c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    c.setMinimumWidth(220)
    return c


def _column():
    """A card stack column. Returns (wrapper_widget, vbox_layout)."""
    wrap = QWidget()
    col = QVBoxLayout(wrap)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(18)
    return wrap, col


def _row(label: str, widget: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(_field_label(label))
    row.addStretch()
    row.addWidget(widget)
    return row


def _toggle_row(label: str, state_label, toggle) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(_field_label(label))
    row.addStretch()
    if state_label is not None:
        row.addWidget(state_label)
        row.addSpacing(10)
    row.addWidget(toggle)
    return row


class SettingsTab(QWidget):
    config_saved = pyqtSignal(object)

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config_path = config_path

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)

        # Scrollable card area — never lets cards overlap when the window is
        # short (tiling WMs, small screens, large fonts); scrolls instead.
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 10, 0)  # room for the scrollbar
        body_lay.setSpacing(18)
        body_lay.addWidget(self._audio_card())  # prominent mic selector + visualizer
        columns = QHBoxLayout()
        columns.setSpacing(18)
        columns.addWidget(self._left_column(), 1)
        columns.addWidget(self._right_column(), 1)
        body_lay.addLayout(columns)
        body_lay.addWidget(self._appearance_card())  # full-width, two-column
        body_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.viewport().setStyleSheet("background: transparent;")
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: transparent; width: 8px; margin: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {BORDER}; border-radius: 4px; min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
        outer.addWidget(scroll, 1)

        # Apply bar
        self.apply_btn = QPushButton("APPLY")
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.setFixedHeight(40)
        self._apply_rest_css = (
            f"QPushButton {{ background: {FG}; color: {BG}; border: none; "
            f"font-size: 11px; letter-spacing: 4px; font-weight: 600; }} "
            f"QPushButton:hover {{ background: {ACCENT}; color: {ACCENT_FG}; }}"
        )
        self.apply_btn.setStyleSheet(self._apply_rest_css)
        self.apply_btn.clicked.connect(self._save)
        outer.addWidget(self.apply_btn)

        # celebratory feedback on save: green glow pulse + colour settle + ✓ SAVED
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 0)
        self._glow.setColor(QColor(ACCENT))
        self._glow.setBlurRadius(0)
        self.apply_btn.setGraphicsEffect(self._glow)
        self._glow_anim = QPropertyAnimation(self._glow, b"blurRadius", self)
        self._glow_anim.setDuration(2400)
        self._glow_anim.setKeyValueAt(0.0, 0.0)
        self._glow_anim.setKeyValueAt(0.12, 38.0)  # quick bloom
        self._glow_anim.setKeyValueAt(0.30, 16.0)
        self._glow_anim.setKeyValueAt(0.72, 16.0)  # sustained soft glow
        self._glow_anim.setKeyValueAt(1.0, 0.0)  # fade out with the green
        self._color_anim = QVariantAnimation(self)
        self._color_anim.setDuration(2400)
        self._color_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._color_anim.setKeyValueAt(0.0, QColor(ACCENT))
        self._color_anim.setKeyValueAt(0.62, QColor(ACCENT))  # hold accent ~1.5s
        self._color_anim.setKeyValueAt(1.0, QColor(FG))  # then settle to fg
        self._color_anim.valueChanged.connect(
            lambda c: self.apply_btn.setStyleSheet(self._flash_css(c.name()))
        )
        self._color_anim.finished.connect(self._apply_settled)

        # Mic preview plumbing
        self._mic_capture: AudioCapture | None = None
        self._mic_timer = QTimer(self)
        self._mic_timer.setInterval(30)
        self._mic_timer.timeout.connect(self._poll_mic)
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)

        self._load_current()

    # -- cards --

    def _card(self, title: str):
        """A bordered section card. Returns (frame, body_layout)."""
        frame = QWidget()
        frame.setObjectName("card")
        frame.setStyleSheet(
            f"#card {{ background: {CARD_BG}; border: 1px solid {BORDER}; }}"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(0)
        lay.addWidget(_section_label(title))
        lay.addSpacing(14)
        return frame, lay

    def _audio_card(self) -> QWidget:
        frame, lay = self._card("Audio input")

        self.device_combo = _combo_expand()
        self._populate_devices()
        lay.addWidget(_field_label("Microphone"))
        lay.addSpacing(6)
        lay.addWidget(self.device_combo)

        lay.addSpacing(14)
        self.mic_meter = EqualizerWidget()
        self.mic_meter.setFixedHeight(84)
        self.mic_meter.setStyleSheet(f"background: {BG}; border: 1px solid {BORDER};")
        lay.addWidget(self.mic_meter)
        lay.addSpacing(8)
        lay.addWidget(
            _hint_label("Speak — the bars turn green when your microphone is heard")
        )
        return frame

    def _left_column(self) -> QWidget:
        wrap, col = _column()

        f_out, out = self._card("Output")
        self.output_toggle = ToggleSwitch()
        self.output_label = QLabel("Copy to clipboard")
        self.output_label.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.output_toggle.toggled.connect(self._on_output_toggle)
        out.addLayout(_toggle_row("Auto-type", self.output_label, self.output_toggle))
        out.addSpacing(4)
        out.addWidget(_hint_label("When on, text is typed into the focused window"))
        out.addSpacing(14)
        self.streaming_toggle = ToggleSwitch()
        self.streaming_label = QLabel("Off")
        self.streaming_label.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.streaming_toggle.toggled.connect(
            lambda on: self.streaming_label.setText("On" if on else "Off")
        )
        out.addLayout(
            _toggle_row(
                "Live typing (alpha)", self.streaming_label, self.streaming_toggle
            )
        )
        out.addSpacing(4)
        out.addWidget(_hint_label("Text appears as you speak (requires auto-type)"))
        col.addWidget(f_out)

        f_hk, hk = self._card("Global shortcut")
        self.hotkey_toggle = ToggleSwitch()
        self.hotkey_state = QLabel("Off")
        self.hotkey_state.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.hotkey_toggle.toggled.connect(self._on_hotkey_toggle)
        hk.addLayout(
            _toggle_row("Global hotkey", self.hotkey_state, self.hotkey_toggle)
        )
        hk.addSpacing(4)
        hk.addWidget(_hint_label("Press it anywhere to start or stop recording"))
        hk.addSpacing(12)
        self.shortcut_capture = ShortcutCapture()
        self.shortcut_capture.changed.connect(lambda _s: self._refresh_hotkey_hint())
        hk.addLayout(_row("Shortcut", self.shortcut_capture))
        hk.addSpacing(6)
        self.hotkey_hint = _hint_label("")
        hk.addWidget(self.hotkey_hint)
        col.addWidget(f_hk)

        col.addStretch()
        return wrap

    def _right_column(self) -> QWidget:
        wrap, col = _column()

        f_tim, tim = self._card("Timing")
        self.silence_stepper = ValueStepper(3.0, 1.0, 10.0, 0.5, "s")
        tim.addLayout(_row("Silence timeout", self.silence_stepper))
        tim.addSpacing(4)
        tim.addWidget(_hint_label("Stop after this much silence"))
        col.addWidget(f_tim)

        f_fb, fb = self._card("Feedback")
        self.sound_toggle = ToggleSwitch()
        fb.addLayout(_toggle_row("Sound effects", None, self.sound_toggle))
        fb.addSpacing(4)
        fb.addWidget(_hint_label("Play tones on record, process, and done"))
        fb.addSpacing(14)
        self.punctuation_toggle = ToggleSwitch()
        fb.addLayout(_toggle_row("Punctuation hints", None, self.punctuation_toggle))
        fb.addSpacing(4)
        fb.addWidget(_hint_label("Nudge Whisper to add natural punctuation"))
        col.addWidget(f_fb)

        f_net, net_card = self._card("Network (advanced)")
        net_card.addWidget(_field_label("Proxy"))
        net_card.addSpacing(6)
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("http://host:port  (blank = none)")
        self.proxy_edit.setStyleSheet(
            f"QLineEdit {{ background: {CONTROL_BG}; color: {FG};"
            f" border: 1px solid {BORDER}; padding: 6px 8px; }}"
        )
        net_card.addWidget(self.proxy_edit)
        net_card.addSpacing(4)
        net_card.addWidget(_hint_label("For model downloads + cloud calls"))
        net_card.addSpacing(14)
        self.ssl_toggle = ToggleSwitch()
        self.ssl_toggle.setChecked(True)
        net_card.addLayout(_toggle_row("Verify SSL", None, self.ssl_toggle))
        net_card.addSpacing(4)
        net_card.addWidget(_hint_label("Off skips cert checks (e.g. corporate proxy)"))
        col.addWidget(f_net)

        col.addStretch()
        return wrap

    def _appearance_card(self) -> QWidget:
        """Full-width indicator styling: controls on the left, a live preview
        pinned top-right. Everything here applies live (no restart)."""
        f_app, app = self._card("Appearance")

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(14)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(14)

        # -- right column opens with the live preview, pinned top-right --
        right.addWidget(self._preview_stage())

        # -- left: shape & placement --
        self.theme_combo = _combo(180)
        for name, th in THEMES.items():
            self.theme_combo.addItem(th.label, name)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_preset)
        left.addLayout(_row("Theme", self.theme_combo))

        self.opacity_stepper = ValueStepper(94, 50, 100, 5, "%", decimals=0)
        self.opacity_stepper.valueChanged.connect(lambda _v: self._update_preview())
        left.addLayout(_row("Indicator opacity", self.opacity_stepper))

        self.overlay_w_stepper = ValueStepper(210, 120, 600, 10, "px", decimals=0)
        self.overlay_w_stepper.valueChanged.connect(lambda _v: self._update_preview())
        left.addLayout(_row("Overlay width", self.overlay_w_stepper))

        self.overlay_h_stepper = ValueStepper(52, 32, 160, 4, "px", decimals=0)
        self.overlay_h_stepper.valueChanged.connect(lambda _v: self._update_preview())
        left.addLayout(_row("Overlay height", self.overlay_h_stepper))

        self.position_combo = _combo(180)
        for label, value in [
            ("Bottom Center", "bottom_center"),
            ("Bottom Left", "bottom_left"),
            ("Bottom Right", "bottom_right"),
            ("Top Center", "top_center"),
            ("Top Left", "top_left"),
            ("Top Right", "top_right"),
            ("Center", "center"),
            ("Custom (X / Y)", "custom"),
        ]:
            self.position_combo.addItem(label, value)
        self.position_combo.currentIndexChanged.connect(self._on_position_change)
        left.addLayout(_row("Overlay position", self.position_combo))

        self.pos_custom = QWidget()
        _pos = QVBoxLayout(self.pos_custom)
        _pos.setContentsMargins(0, 0, 0, 0)
        _pos.setSpacing(14)
        self.overlay_x_stepper = ValueStepper(0, 0, 10000, 10, "px", decimals=0)
        _pos.addLayout(_row("Custom X", self.overlay_x_stepper))
        self.overlay_y_stepper = ValueStepper(0, 0, 10000, 10, "px", decimals=0)
        _pos.addLayout(_row("Custom Y", self.overlay_y_stepper))
        left.addWidget(self.pos_custom)

        left.addWidget(self._corner_control())
        left.addStretch()

        # -- right: colours & equaliser bars (below the preview) --
        self._accent_touched = False
        self.icon_swatch = _ColorSwatch()
        self.icon_swatch.picked.connect(self._on_accent_picked)
        right.addLayout(_row("Icon colour", self.icon_swatch))

        self._eq_touched = False
        self.bar_swatches = [_ColorSwatch(), _ColorSwatch(), _ColorSwatch()]
        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(0, 0, 0, 0)
        bar_row.addWidget(_field_label("Bar colours"))
        bar_row.addStretch()
        for sw in self.bar_swatches:
            sw.picked.connect(self._on_eq_picked)
            bar_row.addWidget(sw)
            bar_row.addSpacing(6)
        right.addLayout(bar_row)

        self.border_stepper = ValueStepper(0, 0, 6, 1, "px", decimals=0)
        self.border_stepper.valueChanged.connect(lambda _v: self._update_preview())
        right.addLayout(_row("Border width", self.border_stepper))

        self._border_color_touched = False
        self.border_swatch = _ColorSwatch()
        self.border_swatch.picked.connect(self._on_border_picked)
        right.addLayout(_row("Border colour", self.border_swatch))

        self._spacing_touched = False
        self.spacing_stepper = ValueStepper(4, 0, 16, 1, "px", decimals=0)
        self.spacing_stepper.valueChanged.connect(self._on_spacing_changed)
        right.addLayout(_row("Bar spacing", self.spacing_stepper))

        self._width_touched = False
        self.bar_width_stepper = ValueStepper(6, 2, 24, 1, "px", decimals=0)
        self.bar_width_stepper.valueChanged.connect(self._on_bar_width_changed)
        right.addLayout(_row("Bar width", self.bar_width_stepper))

        self.bar_radius_stepper = ValueStepper(0, 0, 12, 1, "px", decimals=0)
        self.bar_radius_stepper.valueChanged.connect(lambda _v: self._update_preview())
        right.addLayout(_row("Bar roundness", self.bar_radius_stepper))

        self.fade_toggle = ToggleSwitch()
        self.fade_toggle.setChecked(True)
        self.fade_toggle.toggled.connect(lambda _v: self._update_preview())
        right.addLayout(_toggle_row("Bar fade", None, self.fade_toggle))
        right.addStretch()

        grid = QHBoxLayout()
        grid.setSpacing(36)
        grid.addLayout(left, 1)
        grid.addLayout(right, 1)
        app.addSpacing(8)
        app.addLayout(grid)
        app.addSpacing(10)
        app.addWidget(_hint_label("Look changes apply on Apply — no restart"))
        return f_app

    def _preview_stage(self) -> QWidget:
        """The live preview, framed as a 'stage' and pinned to the top-right."""
        stage = QFrame()
        stage.setObjectName("stage")
        stage.setStyleSheet(
            f"#stage {{ background: {BG}; border: 1px solid {BORDER};"
            f" border-radius: 12px; }}"
        )
        lay = QVBoxLayout(stage)
        lay.setContentsMargins(16, 12, 16, 16)
        lay.setSpacing(10)
        cap = QLabel("LIVE PREVIEW")
        cap.setStyleSheet(f"color: {MUTED}; font-size: 10px; letter-spacing: 2px;")
        lay.addWidget(cap)
        self.preview = RecordingPreview(active_indicator())
        prow = QHBoxLayout()
        prow.addStretch()
        prow.addWidget(self.preview)
        prow.addStretch()
        lay.addLayout(prow)
        return stage

    def _corner_control(self) -> QWidget:
        """Per-corner radius editor with a Photoshop-style centre link toggle:
        linked = the four corners move together, unlinked = each is independent."""
        self._corners_touched = False
        self._corner_link = True
        self.corner_tl = ValueStepper(0, 0, 28, 1, "px", decimals=0)
        self.corner_tr = ValueStepper(0, 0, 28, 1, "px", decimals=0)
        self.corner_br = ValueStepper(0, 0, 28, 1, "px", decimals=0)
        self.corner_bl = ValueStepper(0, 0, 28, 1, "px", decimals=0)
        for s in (self.corner_tl, self.corner_tr, self.corner_br, self.corner_bl):
            s.valueChanged.connect(self._on_corner_changed)

        self.corner_link_btn = QPushButton("🔗")
        self.corner_link_btn.setCheckable(True)
        self.corner_link_btn.setChecked(True)
        self.corner_link_btn.setFixedSize(34, 34)
        self.corner_link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.corner_link_btn.setToolTip(
            "Corners linked — click to set each corner independently"
        )
        self.corner_link_btn.setStyleSheet(
            f"QPushButton {{ background: {CONTROL_BG}; color: {MUTED};"
            f" border: 1px solid {BORDER}; border-radius: 6px; font-size: 14px; }}"
            f"QPushButton:checked {{ color: {ACCENT}; border-color: {ACCENT}; }}"
            f"QPushButton:hover {{ border-color: {MUTED}; }}"
        )
        self.corner_link_btn.clicked.connect(self._toggle_corner_link)

        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(_field_label("Corner radius"))
        g = QGridLayout()
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        g.addWidget(self.corner_tl, 0, 0)
        g.addWidget(self.corner_link_btn, 0, 1, 2, 1, Qt.AlignmentFlag.AlignCenter)
        g.addWidget(self.corner_tr, 0, 2)
        g.addWidget(self.corner_bl, 1, 0)
        g.addWidget(self.corner_br, 1, 2)
        g.setColumnStretch(3, 1)
        outer.addLayout(g)
        return w

    def _on_theme_preset(self, _i: int) -> None:
        """Selecting a theme reseeds the style controls with its defaults."""
        name = self.theme_combo.currentData()
        th = THEMES.get(name)
        if th is None:
            return
        self.opacity_stepper.setValue(round(th.overlay_opacity * 100))
        for sw, hexc in zip(self.bar_swatches, _eq_stops(th.eq_colors)):
            sw.setColor(hexc)
        self.icon_swatch.setColor(th.accent)
        self.border_stepper.setValue(th.border_width)
        self.border_swatch.setColor(th.border_color)
        self._eq_touched = False
        self._accent_touched = False
        self._border_color_touched = False
        # picking a theme means "use its shape": show its per-corner radii and
        # link the control only when they are uniform. setValue does not emit,
        # so the touched flag stays clear and the theme's corners are kept.
        tl, tr, br, bl = th.corners
        self.corner_tl.setValue(tl)
        self.corner_tr.setValue(tr)
        self.corner_br.setValue(br)
        self.corner_bl.setValue(bl)
        self._corners_touched = False
        self._set_corner_link(len(set(th.corners)) == 1)
        # no theme carries a custom spacing; seed a neutral default and treat it
        # as untouched (-1 auto) so the theme's proportional look is kept.
        self.spacing_stepper.setValue(4)
        self._spacing_touched = False
        self.bar_width_stepper.setValue(6)
        self._width_touched = False
        self.bar_radius_stepper.setValue(th.bar_radius)
        self.fade_toggle.setChecked(th.bar_fade)
        self._update_preview()

    def _on_corner_changed(self, v: float) -> None:
        self._corners_touched = True
        if self._corner_link:
            for s in (self.corner_tl, self.corner_tr, self.corner_br, self.corner_bl):
                if s.value() != v:
                    s.setValue(v)  # setValue does not emit → no recursion
        self._update_preview()

    def _toggle_corner_link(self) -> None:
        linked = self.corner_link_btn.isChecked()
        self._set_corner_link(linked)
        self._corners_touched = True  # a deliberate link toggle is a customization
        if linked:  # collapse the four corners to the top-left value
            v = self.corner_tl.value()
            for s in (self.corner_tr, self.corner_br, self.corner_bl):
                s.setValue(v)
        self._update_preview()

    def _set_corner_link(self, linked: bool) -> None:
        self._corner_link = linked
        self.corner_link_btn.setChecked(linked)
        self.corner_link_btn.setText("🔗" if linked else "🔓")
        self.corner_link_btn.setToolTip(
            "Corners linked — click to set each corner independently"
            if linked
            else "Corners independent — click to link them"
        )

    def _corner_values(self) -> tuple:
        return (
            round(self.corner_tl.value()),
            round(self.corner_tr.value()),
            round(self.corner_br.value()),
            round(self.corner_bl.value()),
        )

    def _on_spacing_changed(self, _v: float) -> None:
        self._spacing_touched = True
        self._update_preview()

    def _on_bar_width_changed(self, _v: float) -> None:
        self._width_touched = True
        self._update_preview()

    def _on_eq_picked(self, _c: str) -> None:
        self._eq_touched = True
        self._update_preview()

    def _on_accent_picked(self, _c: str) -> None:
        self._accent_touched = True
        self._update_preview()

    def _on_border_picked(self, _c: str) -> None:
        self._border_color_touched = True
        self._update_preview()

    def _update_preview(self) -> None:
        """Render the preview with the pending (unsaved) look from the controls."""
        from dataclasses import replace

        base = THEMES.get(self.theme_combo.currentData())
        if base is None:
            return
        corners = self._corner_values() if self._corners_touched else base.corners
        self.preview.set_theme(
            replace(
                base,
                corners=corners,
                eq_colors=tuple(s.color() for s in self.bar_swatches),
                accent=self.icon_swatch.color(),
                overlay_opacity=round(self.opacity_stepper.value()) / 100.0,
                border_width=round(self.border_stepper.value()),
                border_color=self.border_swatch.color(),
                bar_spacing=(
                    round(self.spacing_stepper.value()) if self._spacing_touched else -1
                ),
                bar_fade=self.fade_toggle.isChecked(),
                bar_width=(
                    round(self.bar_width_stepper.value()) if self._width_touched else -1
                ),
                bar_radius=round(self.bar_radius_stepper.value()),
            )
        )
        w = round(self.overlay_w_stepper.value())
        h = round(self.overlay_h_stepper.value())
        scale = min(1.0, 260 / w) if w else 1.0  # fit the settings column
        m = RecordingPreview.MARGIN
        self.preview.setFixedSize(round(w * scale) + 2 * m, round(h * scale) + 2 * m)

    def _on_position_change(self) -> None:
        self.pos_custom.setVisible(self.position_combo.currentData() == "custom")

    # -- populate / load / save --

    def _populate_devices(self) -> None:
        self.device_combo.clear()
        self.device_combo.addItem("System Default", None)
        for i, dev in enumerate(AudioCapture.list_devices()):
            if dev["max_input_channels"] > 0:
                self.device_combo.addItem(dev["name"], i)

    def _on_output_toggle(self, on: bool) -> None:
        if on and not auto_type_available():
            dlg = InputToolInstallDialog(auto_type_package(), parent=self)
            dlg.exec()
            if not (dlg.installed and auto_type_available()):
                # Declined or failed — auto-type can't work, revert to clipboard.
                self.output_toggle.blockSignals(True)
                self.output_toggle.setChecked(False)
                self.output_toggle.blockSignals(False)
                on = False
        self.output_label.setText("Type into window" if on else "Copy to clipboard")
        self.streaming_toggle.setEnabled(on)
        if not on:
            self.streaming_toggle.setChecked(False)

    def _on_hotkey_toggle(self, on: bool) -> None:
        self.hotkey_state.setText("On" if on else "Off")
        self._refresh_hotkey_hint()

    def _refresh_hotkey_hint(self) -> None:
        from macaw.hotkey import check_access, is_valid

        ok, reason = check_access()
        enabled = self.hotkey_toggle.isChecked()
        spec = self.shortcut_capture.spec()
        if not ok:
            self.hotkey_hint.setText(f"⚠  {reason}")
        elif enabled and not is_valid(spec):
            self.hotkey_hint.setText("Set a shortcut above to activate it.")
        else:
            self.hotkey_hint.setText(
                "Works on X11 and Wayland — fires even when Macaw isn't focused."
            )

    def _load_current(self) -> None:
        cfg = Config.load(self.config_path)
        self.device_combo.setCurrentIndex(
            0
            if cfg.device_index is None
            else max(0, self.device_combo.findData(cfg.device_index))
        )
        self.output_toggle.blockSignals(True)
        self.output_toggle.setChecked(cfg.output_mode == "type")
        self.output_toggle.blockSignals(False)
        self.output_label.setText(
            "Type into window" if cfg.output_mode == "type" else "Copy to clipboard"
        )
        idx = self.position_combo.findData(cfg.window_position)
        if idx >= 0:
            self.position_combo.setCurrentIndex(idx)
        self.silence_stepper.setValue(cfg.silence_timeout)
        self.sound_toggle.setChecked(cfg.sound_enabled)
        self.streaming_toggle.setChecked(cfg.streaming)
        self.streaming_toggle.setEnabled(cfg.output_mode == "type")
        self.punctuation_toggle.setChecked(cfg.punctuation_hints)
        self.hotkey_toggle.setChecked(cfg.hotkey_enabled)
        self.hotkey_state.setText("On" if cfg.hotkey_enabled else "Off")
        self.shortcut_capture.set_spec(cfg.hotkey)
        self._refresh_hotkey_hint()
        # Appearance: seed the style controls from the theme, then apply the
        # user's saved overrides on top.
        idx = self.theme_combo.findData(cfg.theme)
        if idx >= 0:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(idx)
            self.theme_combo.blockSignals(False)
        self._on_theme_preset(0)
        self.opacity_stepper.setValue(round(cfg.overlay_opacity * 100))
        self.overlay_w_stepper.setValue(cfg.overlay_width)
        self.overlay_h_stepper.setValue(cfg.overlay_height)
        self.overlay_x_stepper.setValue(cfg.overlay_x)
        self.overlay_y_stepper.setValue(cfg.overlay_y)
        self._on_position_change()
        if cfg.eq_colors:
            for sw, hexc in zip(self.bar_swatches, _eq_stops(tuple(cfg.eq_colors))):
                sw.setColor(hexc)
            self._eq_touched = True
        if cfg.accent_color:
            self.icon_swatch.setColor(cfg.accent_color)
            self._accent_touched = True
        self.border_stepper.setValue(cfg.border_width)
        if cfg.border_color:
            self.border_swatch.setColor(cfg.border_color)
            self._border_color_touched = True
        if not cfg.corner_link and len(cfg.corners) == 4:
            tl, tr, br, bl = cfg.corners
            self.corner_tl.setValue(tl)
            self.corner_tr.setValue(tr)
            self.corner_br.setValue(br)
            self.corner_bl.setValue(bl)
            self._corners_touched = True
            self._set_corner_link(False)
        elif cfg.corner_radius >= 0:
            for s in (self.corner_tl, self.corner_tr, self.corner_br, self.corner_bl):
                s.setValue(cfg.corner_radius)
            self._corners_touched = True
            self._set_corner_link(True)
        if cfg.bar_spacing >= 0:
            self.spacing_stepper.setValue(cfg.bar_spacing)
            self._spacing_touched = True
        if cfg.bar_width >= 0:
            self.bar_width_stepper.setValue(cfg.bar_width)
            self._width_touched = True
        self.bar_radius_stepper.setValue(cfg.bar_radius)
        self.fade_toggle.setChecked(cfg.bar_fade)
        self.proxy_edit.setText(cfg.proxy)
        self.ssl_toggle.setChecked(cfg.ssl_verify)
        self._update_preview()

    def _save(self) -> None:
        cfg = Config.load(self.config_path)  # preserve model + model_params
        cfg.device_index = self.device_combo.currentData()
        cfg.output_mode = "type" if self.output_toggle.isChecked() else "clipboard"
        cfg.window_position = self.position_combo.currentData()
        cfg.silence_timeout = self.silence_stepper.value()
        cfg.sound_enabled = self.sound_toggle.isChecked()
        cfg.streaming = self.streaming_toggle.isChecked()
        cfg.punctuation_hints = self.punctuation_toggle.isChecked()
        cfg.hotkey_enabled = self.hotkey_toggle.isChecked()
        cfg.hotkey = self.shortcut_capture.spec()
        cfg.proxy = self.proxy_edit.text().strip()
        cfg.ssl_verify = self.ssl_toggle.isChecked()
        cfg.theme = self.theme_combo.currentData()
        cfg.overlay_opacity = round(self.opacity_stepper.value()) / 100.0
        cfg.overlay_width = round(self.overlay_w_stepper.value())
        cfg.overlay_height = round(self.overlay_h_stepper.value())
        cfg.overlay_x = round(self.overlay_x_stepper.value())
        cfg.overlay_y = round(self.overlay_y_stepper.value())
        if self._eq_touched:
            cfg.eq_colors = [sw.color() for sw in self.bar_swatches]
        if self._accent_touched:
            cfg.accent_color = self.icon_swatch.color()
        cfg.border_width = round(self.border_stepper.value())
        if self._border_color_touched:
            cfg.border_color = self.border_swatch.color()
        if self._corners_touched and not self._corner_link:
            cfg.corners = list(self._corner_values())
            cfg.corner_radius = -1
            cfg.corner_link = False
        elif self._corners_touched:
            cfg.corner_radius = round(self.corner_tl.value())
            cfg.corners = []
            cfg.corner_link = True
        # else: corners untouched — keep cfg.corners/corner_radius/corner_link as
        # loaded, so an Apply never rewrites the overlay's shape.
        cfg.bar_spacing = (
            round(self.spacing_stepper.value()) if self._spacing_touched else -1
        )
        cfg.bar_width = (
            round(self.bar_width_stepper.value()) if self._width_touched else -1
        )
        cfg.bar_radius = round(self.bar_radius_stepper.value())
        cfg.bar_fade = self.fade_toggle.isChecked()
        try:
            cfg.save(self.config_path)
            self._celebrate_apply()
            self.config_saved.emit(cfg)  # service restarts if the look changed
        except Exception as exc:
            logger.error("Settings save error: %s", exc)
            QMessageBox.critical(self, "Error", f"Failed to save settings:\n{exc}")

    # -- apply feedback --

    def _flash_css(self, bg: str) -> str:
        # no :hover rule so the animated colour shows even under the cursor
        return (
            f"QPushButton {{ background: {bg}; color: {ACCENT_FG}; border: none; "
            f"font-size: 11px; letter-spacing: 4px; font-weight: 600; }}"
        )

    def _celebrate_apply(self) -> None:
        self.apply_btn.setText("✓   SAVED")
        self._glow_anim.stop()
        self._glow_anim.start()
        self._color_anim.stop()
        self._color_anim.start()

    def _apply_settled(self) -> None:
        self.apply_btn.setText("APPLY")
        self.apply_btn.setStyleSheet(self._apply_rest_css)

    # -- mic preview --

    def _on_device_changed(self, _i: int) -> None:
        if self.isVisible():
            self.start_preview()

    def start_preview(self) -> None:
        self.stop_preview()
        self.preview.start()
        try:
            self._mic_capture = AudioCapture(device=self.device_combo.currentData())
            self._mic_capture.start()
            self.mic_meter.start()
            self._mic_timer.start()
        except Exception as exc:
            logger.warning("Mic preview unavailable: %s", exc)
            self._mic_capture = None

    def stop_preview(self) -> None:
        self._mic_timer.stop()
        self.mic_meter.stop()
        self.preview.stop()
        if self._mic_capture is not None:
            try:
                self._mic_capture.stop()
            except Exception:
                pass
            self._mic_capture = None

    def _poll_mic(self) -> None:
        cap = self._mic_capture
        if cap is None:
            return
        try:
            while True:
                cap._queue.get_nowait()
        except queue.Empty:
            pass
        raw = cap.current_energy
        vis = (math.log10(raw) + 4) / 3.0 if raw > 1e-10 else 0.0
        self.mic_meter.set_energy(min(1.0, max(0.0, vis)))


# ── main tabbed window ───────────────────────────────────────────────
