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
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from macaw.audio.capture import AudioCapture
from macaw.audio.transcriber import Transcriber
from macaw.config import Config
from macaw.gui.download import ModelDownloadDialog
from macaw.gui.equalizer import EqualizerWidget
from macaw.gui.icon import logo_icon, logo_pixmap
from macaw.gui.install import DependencyInstallDialog
from macaw.gui.settings import (
    _COMBO_STYLE,
    BG,
    BORDER,
    CONTROL_BG,
    FG,
    MUTED,
    ToggleSwitch,
    ValueStepper,
    _close_button,
    _ColorSwatch,
    _field_label,
    _hint_label,
    _section_label,
    _separator,
    _StyledComboBox,
)
from macaw.gui.theme import THEMES, active_theme
from macaw.gui.window import RecordingPreview
from macaw.stt import create_backend, list_models
from macaw.stt.base import hf_cache_sizes

logger = logging.getLogger("macaw")

_T = active_theme()
OK = _T.ok
WARN = _T.warn
DANGER = _T.danger
CARD_BG = _T.surface
ACCENT = _T.accent
ACCENT_FG = _T.accent_fg


def retheme() -> None:
    """Re-resolve every GUI module's palette from the active theme, so a rebuilt
    window picks up the new colors without a restart (live theme switching)."""
    global BG, FG, MUTED, BORDER, CONTROL_BG, _COMBO_STYLE
    global OK, WARN, DANGER, CARD_BG, ACCENT, ACCENT_FG, _T
    from macaw.gui import download, equalizer, install, settings, window

    settings.refresh_theme()
    download.refresh_theme()
    install.refresh_theme()
    # main_window imported these by value (`from settings import BG…`), so its
    # own copies must be re-pointed at the refreshed ones.
    BG, FG, MUTED, BORDER, CONTROL_BG, _COMBO_STYLE = (
        settings.BG,
        settings.FG,
        settings.MUTED,
        settings.BORDER,
        settings.CONTROL_BG,
        settings._COMBO_STYLE,
    )
    _T = active_theme()
    OK, WARN, DANGER = _T.ok, _T.warn, _T.danger
    CARD_BG, ACCENT, ACCENT_FG = _T.surface, _T.accent, _T.accent_fg
    equalizer.refresh_theme()
    window.refresh_theme()


def _fmt_size(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def _eq_stops(colors) -> list[str]:
    """Three representative gradient stops (start, middle, end) from a palette."""
    cols = list(colors) or ["#4CAF7D"]
    if len(cols) >= 3:
        return [cols[0], cols[len(cols) // 2], cols[-1]]
    if len(cols) == 2:
        return [cols[0], cols[0], cols[1]]
    return [cols[0], cols[0], cols[0]]


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)  # detach immediately so it can't ghost-render
            w.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _action_button(text: str, color: str) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFixedHeight(30)
    b.setStyleSheet(f"""
        QPushButton {{
            background: transparent; color: {color};
            border: 1px solid {color}; padding: 2px 14px; font-size: 12px;
        }}
        QPushButton:hover {{ background: {CONTROL_BG}; }}
        QPushButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
    """)
    return b


# ── Models tab: master list + detail/params ──────────────────────────


class ModelsTab(QWidget):
    config_saved = pyqtSignal(object)

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config_path = config_path

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- left: model list --
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        left.addWidget(_section_label("Choose a model"))
        self.list = QListWidget()
        self.list.setFixedWidth(260)
        self.list.setStyleSheet(f"""
            QListWidget {{
                background: {CONTROL_BG}; border: 1px solid {BORDER};
                outline: none; padding: 4px;
            }}
            QListWidget::item {{ padding: 9px 8px; color: {FG}; }}
            QListWidget::item:selected {{ background: {BORDER}; }}
            QListWidget::item:hover {{ background: {CARD_BG}; }}
        """)
        self.list.currentItemChanged.connect(self._on_select)
        left.addWidget(self.list, 1)
        self.install_all_btn = _action_button("Install all optional backends ↓", OK)
        self.install_all_btn.clicked.connect(self._install_all)
        left.addWidget(self.install_all_btn)
        root.addLayout(left)

        root.addSpacing(24)

        # -- right: detail panel (rebuilt per selection) --
        self._detail = QWidget()
        self.detail = QVBoxLayout(self._detail)
        self.detail.setContentsMargins(0, 0, 0, 0)
        self.detail.setSpacing(10)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.addWidget(self._detail, 1)

        self._populate_list()

    # -- list --------------------------------------------------------

    def _active_model(self) -> str:
        return Config.load(self.config_path).model

    def _populate_list(self) -> None:
        cache = hf_cache_sizes()
        active = self._active_model()
        self.list.blockSignals(True)
        self.list.clear()
        any_missing = False
        for info in list_models():
            backend = create_backend(info.id)
            ready = backend.is_ready(cache)
            is_active = info.id == active
            if info.extra and not backend.available():
                any_missing = True
            dot = "●  " if is_active else ""
            item = QListWidgetItem(f"{dot}{info.label}")
            item.setData(Qt.ItemDataRole.UserRole, info.id)
            item.setForeground(_qcolor(FG if ready else MUTED))
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.install_all_btn.setVisible(any_missing)
        if self.list.count():
            self.list.setCurrentRow(_index_of(self.list, active))

    def refresh(self) -> None:
        current = self._current_id()
        self._populate_list()
        if current:
            self.list.setCurrentRow(_index_of(self.list, current))

    def _current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_select(self, *_a) -> None:
        model_id = self._current_id()
        if model_id:
            self._show_detail(model_id)

    # -- detail ------------------------------------------------------

    def _show_detail(self, model_id: str) -> None:
        _clear(self.detail)
        info = next((m for m in list_models() if m.id == model_id), None)
        if info is None:
            return
        backend = create_backend(model_id)
        available = backend.available()
        size = backend.disk_size() if available else 0
        ready = backend.is_ready()
        is_active = self._active_model() == model_id

        name = QLabel(info.label)
        name.setStyleSheet(f"color: {FG}; font-size: 20px; font-weight: 600;")
        self.detail.addWidget(name)

        stream = " · streaming" if info.streaming else ""
        meta = QLabel(
            f"{info.backend} · {info.size} · {info.speed} · {info.languages}{stream}"
        )
        meta.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.detail.addWidget(meta)

        # recommendation row (horizontal)
        rec = QHBoxLayout()
        rec.setSpacing(24)
        rec.addWidget(_kv("Hardware", info.hardware))
        rec.addWidget(_kv("VRAM", info.vram))
        rec.addStretch()
        self.detail.addLayout(rec)
        if info.notes:
            note = QLabel(info.notes)
            note.setStyleSheet(f"color: {MUTED}; font-size: 11px; font-style: italic;")
            self.detail.addWidget(note)

        # source library + download page (clickable)
        links = QHBoxLayout()
        links.setSpacing(28)
        if backend.source_url:
            links.addWidget(_kv_link("Library", backend.source_url))
        model_url = backend.model_url()
        if model_url:
            links.addWidget(_kv_link("Download", model_url))
        links.addStretch()
        if links.count() > 1:  # at least one link besides the stretch
            self.detail.addSpacing(6)
            self.detail.addLayout(links)

        # status
        if not available:
            status = (f"Needs  macaw[{info.extra}]", WARN)
        elif is_active:
            status = ("● Active model", OK)
        elif size > 0:
            status = (f"Downloaded · {_fmt_size(size)}", OK)
        elif not backend.hf_repos():
            status = ("Ready · downloads on first use", OK)
        else:
            status = ("Not downloaded", MUTED)
        st = QLabel(status[0])
        st.setStyleSheet(f"color: {status[1]}; font-size: 12px;")
        self.detail.addWidget(st)

        # actions
        actions = QHBoxLayout()
        actions.setSpacing(8)
        if not available and info.extra:
            btn = _action_button("Install ↓", OK)
            btn.clicked.connect(lambda: self._install([info.extra], info.label))
            actions.addWidget(btn)
        if available and backend.hf_repos() and size == 0:
            btn = _action_button("Download ⤓", FG)
            btn.clicked.connect(lambda: self._download(model_id))
            actions.addWidget(btn)
        set_btn = _action_button("Set as active", FG)
        set_btn.setEnabled(ready and not is_active)
        set_btn.clicked.connect(lambda: self._set_active(model_id))
        actions.addWidget(set_btn)
        if size > 0:
            del_btn = _action_button("Delete 🗑", DANGER)
            del_btn.clicked.connect(lambda: self._delete(model_id))
            actions.addWidget(del_btn)
        actions.addStretch()
        self.detail.addLayout(actions)

        self.detail.addSpacing(6)
        self.detail.addWidget(_separator())
        self.detail.addSpacing(6)
        self.detail.addWidget(_section_label("Parameters"))
        self._add_params(info, backend)

    def _add_params(self, info, backend) -> None:
        if not backend.params:
            lbl = _hint_label("This model has no adjustable parameters.")
            self.detail.addWidget(lbl)
            return
        values = Config.load(self.config_path).model_params.get(info.id, {})
        for p in backend.params:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(_field_label(p.label))
            row.addStretch()
            current = values.get(p.key, p.default)
            row.addWidget(self._param_control(info.id, p, current))
            self.detail.addLayout(row)
            if p.hint:
                self.detail.addWidget(_hint_label(p.hint))
            self.detail.addSpacing(6)

    def _param_control(self, model_id: str, p, value) -> QWidget:
        if p.kind == "bool":
            sw = ToggleSwitch()
            sw.setChecked(bool(value))
            sw.toggled.connect(lambda on: self._save_param(model_id, p.key, on))
            return sw
        decimals = 0 if p.kind == "int" else 1
        step = ValueStepper(
            value=float(value),
            minimum=p.minimum,
            maximum=p.maximum,
            step=p.step,
            suffix="",
            decimals=decimals,
        )
        cast = int if p.kind == "int" else float
        step.valueChanged.connect(lambda v: self._save_param(model_id, p.key, cast(v)))
        return step

    # -- actions -----------------------------------------------------

    def _save_param(self, model_id: str, key: str, value) -> None:
        cfg = Config.load(self.config_path)
        cfg.model_params.setdefault(model_id, {})[key] = value
        cfg.save(self.config_path)
        self.config_saved.emit(cfg)

    def _set_active(self, model_id: str) -> None:
        cfg = Config.load(self.config_path)
        cfg.model = model_id
        cfg.save(self.config_path)
        self.config_saved.emit(cfg)
        self.refresh()

    def _download(self, model_id: str) -> None:
        dlg = ModelDownloadDialog(
            Transcriber(model_size=model_id), parent=self, load_after=False
        )
        dlg.start()
        self.refresh()

    def _delete(self, model_id: str) -> None:
        info = next((m for m in list_models() if m.id == model_id), None)
        reply = QMessageBox.question(
            self,
            "Macaw",
            f"Delete {info.label} from disk?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            create_backend(model_id).delete()
            self.refresh()

    def _install(self, extras: list[str], label: str) -> None:
        wanted = [e for e in dict.fromkeys(e for e in extras if e)]
        if not wanted:
            return
        DependencyInstallDialog(label, wanted, parent=self).start()
        self.refresh()

    def _install_all(self) -> None:
        missing = sorted(
            {
                m.extra
                for m in list_models()
                if m.extra and not create_backend(m.id).available()
            }
        )
        if missing:
            self._install(missing, "all optional backends")


# ── Settings tab: two columns of general settings ────────────────────


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

        f_rec, rec = self._card("Recognition")
        self.lang_combo = _combo(150)
        self._populate_languages()
        rec.addLayout(_row("Language", self.lang_combo))
        col.addWidget(f_rec)

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
        out.addSpacing(14)
        self.position_combo = _combo(160)
        for label, value in [
            ("Bottom Center", "bottom_center"),
            ("Bottom Left", "bottom_left"),
            ("Bottom Right", "bottom_right"),
            ("Top Center", "top_center"),
            ("Top Left", "top_left"),
            ("Top Right", "top_right"),
            ("Center", "center"),
        ]:
            self.position_combo.addItem(label, value)
        out.addLayout(_row("Overlay position", self.position_combo))
        col.addWidget(f_out)

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

        f_app, app = self._card("Appearance")
        self.theme_combo = _combo(180)
        for name, th in THEMES.items():
            self.theme_combo.addItem(th.label, name)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_preset)
        app.addLayout(_row("Theme", self.theme_combo))
        app.addSpacing(14)

        self.opacity_stepper = ValueStepper(94, 50, 100, 5, "%", decimals=0)
        self.opacity_stepper.valueChanged.connect(lambda _v: self._update_preview())
        app.addLayout(_row("Indicator opacity", self.opacity_stepper))
        app.addSpacing(14)

        # bar gradient — three stops the equaliser sweeps through
        self.bar_swatches = [_ColorSwatch(), _ColorSwatch(), _ColorSwatch()]
        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(0, 0, 0, 0)
        bar_row.addWidget(_field_label("Bar colours"))
        bar_row.addStretch()
        for sw in self.bar_swatches:
            sw.picked.connect(lambda _c: self._update_preview())
            bar_row.addWidget(sw)
            bar_row.addSpacing(6)
        app.addLayout(bar_row)
        app.addSpacing(14)

        self.icon_swatch = _ColorSwatch()
        self.icon_swatch.picked.connect(lambda _c: self._update_preview())
        app.addLayout(_row("Icon colour", self.icon_swatch))
        app.addSpacing(14)

        self.border_stepper = ValueStepper(0, 0, 6, 1, "px", decimals=0)
        self.border_stepper.valueChanged.connect(lambda _v: self._update_preview())
        app.addLayout(_row("Border width", self.border_stepper))
        app.addSpacing(14)

        self.border_swatch = _ColorSwatch()
        self.border_swatch.picked.connect(lambda _c: self._update_preview())
        app.addLayout(_row("Border colour", self.border_swatch))
        app.addSpacing(14)

        # Corner radius: overrides the theme's shape with a uniform radius, but
        # only once the user actually touches it (so the speech-bubble et al.
        # stay intact by default).
        self._radius_touched = False
        self.radius_stepper = ValueStepper(0, 0, 28, 1, "px", decimals=0)
        self.radius_stepper.valueChanged.connect(self._on_radius_changed)
        app.addLayout(_row("Corner radius", self.radius_stepper))
        app.addSpacing(16)

        # Bar spacing: gap between equaliser bars (-1 auto until the user touches it).
        self._spacing_touched = False
        self.spacing_stepper = ValueStepper(4, 0, 16, 1, "px", decimals=0)
        self.spacing_stepper.valueChanged.connect(self._on_spacing_changed)
        app.addLayout(_row("Bar spacing", self.spacing_stepper))
        app.addSpacing(14)

        # Bar width: thickness (-1 auto until the user touches it).
        self._width_touched = False
        self.bar_width_stepper = ValueStepper(6, 2, 24, 1, "px", decimals=0)
        self.bar_width_stepper.valueChanged.connect(self._on_bar_width_changed)
        app.addLayout(_row("Bar width", self.bar_width_stepper))
        app.addSpacing(14)

        # Bar roundness: corner radius of each bar (0 = sharp).
        self.bar_radius_stepper = ValueStepper(0, 0, 12, 1, "px", decimals=0)
        self.bar_radius_stepper.valueChanged.connect(lambda _v: self._update_preview())
        app.addLayout(_row("Bar roundness", self.bar_radius_stepper))
        app.addSpacing(14)

        # Bar fade: quiet bars fade to transparent; off = solid bars.
        self.fade_toggle = ToggleSwitch()
        self.fade_toggle.setChecked(True)
        self.fade_toggle.toggled.connect(lambda _v: self._update_preview())
        app.addLayout(_toggle_row("Bar fade", None, self.fade_toggle))
        app.addSpacing(16)

        # live preview of the record indicator with the pending look
        app.addWidget(_field_label("Preview"))
        app.addSpacing(6)
        self.preview = RecordingPreview(active_theme())
        app.addWidget(self.preview)
        app.addSpacing(6)
        app.addWidget(_hint_label("Changing the look restarts Macaw"))
        col.addWidget(f_app)

        col.addStretch()
        return wrap

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
        # picking a theme means "use its shape" — setValue doesn't emit, so the
        # touched flag stays clear and the theme's corners are kept.
        self.radius_stepper.setValue(max(th.corners))
        self._radius_touched = False
        # no theme carries a custom spacing; seed a neutral default and treat it
        # as untouched (-1 auto) so the theme's proportional look is kept.
        self.spacing_stepper.setValue(4)
        self._spacing_touched = False
        self.bar_width_stepper.setValue(6)
        self._width_touched = False
        self.bar_radius_stepper.setValue(th.bar_radius)
        self.fade_toggle.setChecked(th.bar_fade)
        self._update_preview()

    def _on_radius_changed(self, _v: float) -> None:
        self._radius_touched = True
        self._update_preview()

    def _on_spacing_changed(self, _v: float) -> None:
        self._spacing_touched = True
        self._update_preview()

    def _on_bar_width_changed(self, _v: float) -> None:
        self._width_touched = True
        self._update_preview()

    def _update_preview(self) -> None:
        """Render the preview with the pending (unsaved) look from the controls."""
        from dataclasses import replace

        base = THEMES.get(self.theme_combo.currentData())
        if base is None:
            return
        r = round(self.radius_stepper.value())
        corners = (r, r, r, r) if self._radius_touched else base.corners
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

    # -- populate / load / save --

    def _populate_devices(self) -> None:
        self.device_combo.clear()
        self.device_combo.addItem("System Default", None)
        for i, dev in enumerate(AudioCapture.list_devices()):
            if dev["max_input_channels"] > 0:
                self.device_combo.addItem(dev["name"], i)

    def _populate_languages(self) -> None:
        for name, code in {
            "English": "en",
            "French": "fr",
            "German": "de",
            "Spanish": "es",
            "Italian": "it",
            "Portuguese": "pt",
            "Dutch": "nl",
            "Polish": "pl",
            "Russian": "ru",
            "Japanese": "ja",
            "Chinese": "zh",
        }.items():
            self.lang_combo.addItem(name, code)

    def _on_output_toggle(self, on: bool) -> None:
        self.output_label.setText("Type into window" if on else "Copy to clipboard")
        self.streaming_toggle.setEnabled(on)
        if not on:
            self.streaming_toggle.setChecked(False)

    def _load_current(self) -> None:
        cfg = Config.load(self.config_path)
        idx = self.lang_combo.findData(cfg.language)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.device_combo.setCurrentIndex(
            0
            if cfg.device_index is None
            else max(0, self.device_combo.findData(cfg.device_index))
        )
        self.output_toggle.setChecked(cfg.output_mode == "type")
        idx = self.position_combo.findData(cfg.window_position)
        if idx >= 0:
            self.position_combo.setCurrentIndex(idx)
        self.silence_stepper.setValue(cfg.silence_timeout)
        self.sound_toggle.setChecked(cfg.sound_enabled)
        self.streaming_toggle.setChecked(cfg.streaming)
        self.streaming_toggle.setEnabled(cfg.output_mode == "type")
        self.punctuation_toggle.setChecked(cfg.punctuation_hints)
        # Appearance: seed the style controls from the theme, then apply the
        # user's saved overrides on top.
        idx = self.theme_combo.findData(cfg.theme)
        if idx >= 0:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(idx)
            self.theme_combo.blockSignals(False)
        self._on_theme_preset(0)
        self.opacity_stepper.setValue(round(cfg.overlay_opacity * 100))
        if cfg.eq_colors:
            for sw, hexc in zip(self.bar_swatches, _eq_stops(tuple(cfg.eq_colors))):
                sw.setColor(hexc)
        if cfg.accent_color:
            self.icon_swatch.setColor(cfg.accent_color)
        self.border_stepper.setValue(cfg.border_width)
        if cfg.border_color:
            self.border_swatch.setColor(cfg.border_color)
        if cfg.corner_radius >= 0:
            self.radius_stepper.setValue(cfg.corner_radius)
            self._radius_touched = True
        if cfg.bar_spacing >= 0:
            self.spacing_stepper.setValue(cfg.bar_spacing)
            self._spacing_touched = True
        if cfg.bar_width >= 0:
            self.bar_width_stepper.setValue(cfg.bar_width)
            self._width_touched = True
        self.bar_radius_stepper.setValue(cfg.bar_radius)
        self.fade_toggle.setChecked(cfg.bar_fade)
        self._update_preview()

    def _save(self) -> None:
        cfg = Config.load(self.config_path)  # preserve model + model_params
        cfg.device_index = self.device_combo.currentData()
        cfg.language = self.lang_combo.currentData()
        cfg.output_mode = "type" if self.output_toggle.isChecked() else "clipboard"
        cfg.window_position = self.position_combo.currentData()
        cfg.silence_timeout = self.silence_stepper.value()
        cfg.sound_enabled = self.sound_toggle.isChecked()
        cfg.streaming = self.streaming_toggle.isChecked()
        cfg.punctuation_hints = self.punctuation_toggle.isChecked()
        cfg.theme = self.theme_combo.currentData()
        cfg.overlay_opacity = round(self.opacity_stepper.value()) / 100.0
        cfg.eq_colors = [sw.color() for sw in self.bar_swatches]
        cfg.accent_color = self.icon_swatch.color()
        cfg.border_width = round(self.border_stepper.value())
        cfg.border_color = self.border_swatch.color()
        cfg.corner_radius = (
            round(self.radius_stepper.value()) if self._radius_touched else -1
        )
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


class MainWindow(QWidget):
    config_saved = pyqtSignal(object)

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.setWindowTitle("Macaw")
        self.setWindowIcon(logo_icon())
        # Resizable with a floor: tiling WMs / small screens shrink the window,
        # and the settings cards scroll rather than overlap. Comfortable default.
        self.setMinimumSize(820, 560)
        self.resize(960, 860)
        self.setStyleSheet(f"""
            QWidget {{
                background: {BG}; color: {FG};
                font-family: system-ui, -apple-system, sans-serif;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(0)

        # header: logo + wordmark · tabs · close
        header = QHBoxLayout()
        header.setSpacing(0)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(30))
        logo.setFixedSize(30, 30)
        header.addWidget(logo)
        wordmark = QLabel("Macaw")
        wordmark.setStyleSheet(
            f"color: {FG}; font-size: 17px; font-weight: 600; margin-left: 9px;"
        )
        header.addWidget(wordmark)
        header.addSpacing(28)

        self._tab_btns = {}
        for name in ("Models", "Settings"):
            b = QPushButton(name.upper())
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setCheckable(True)
            b.clicked.connect(lambda _c, n=name: self.show_tab(n))
            header.addWidget(b)
            self._tab_btns[name] = b
        header.addStretch()
        header.addWidget(_close_button(self.close))
        root.addLayout(header)
        root.addSpacing(16)
        root.addWidget(_separator())
        root.addSpacing(20)

        self.models = ModelsTab(config_path)
        self.settings = SettingsTab(config_path)
        self.models.config_saved.connect(self._relay)
        self.settings.config_saved.connect(self._relay)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.models)
        self.stack.addWidget(self.settings)
        root.addWidget(self.stack, 1)

        self.show_tab("Models")

    def _relay(self, cfg) -> None:
        self.config_saved.emit(cfg)

    def show_tab(self, name: str) -> None:
        self.stack.setCurrentWidget(self.models if name == "Models" else self.settings)
        for n, b in self._tab_btns.items():
            b.setChecked(n == name)
            b.setStyleSheet(_tab_style(n == name))
        if name == "Settings":
            self.settings.start_preview()
        else:
            self.settings.stop_preview()
        if name == "Models":
            self.models.refresh()

    def open_models(self) -> None:
        self._present()
        self.show_tab("Models")

    def open_settings(self) -> None:
        self._present()
        self.show_tab("Settings")

    def _present(self) -> None:
        self.show()
        self.activateWindow()
        self.raise_()

    def hideEvent(self, event: object) -> None:
        super().hideEvent(event)
        self.settings.stop_preview()

    def closeEvent(self, event: object) -> None:
        self.settings.stop_preview()
        super().closeEvent(event)


# ── small shared helpers ─────────────────────────────────────────────


def _tab_style(active: bool) -> str:
    color = FG if active else MUTED
    border = FG if active else "transparent"
    return (
        f"QPushButton {{ background: transparent; color: {color}; border: none; "
        f"border-bottom: 2px solid {border}; padding: 6px 4px; margin-right: 20px; "
        f"font-size: 12px; letter-spacing: 3px; font-weight: 600; }}"
        f"QPushButton:hover {{ color: {FG}; }}"
    )


def _combo(width: int) -> _StyledComboBox:
    c = _StyledComboBox()
    c.setStyleSheet(_COMBO_STYLE)
    c.setFixedWidth(width)
    return c


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


def _kv(key: str, value: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    k = QLabel(key.upper())
    k.setStyleSheet(f"color: {MUTED}; font-size: 9px; letter-spacing: 2px;")
    v = QLabel(value)
    v.setStyleSheet(f"color: {FG}; font-size: 13px;")
    lay.addWidget(k)
    lay.addWidget(v)
    return w


def _kv_link(key: str, url: str) -> QWidget:
    """Small KEY / clickable-URL pair (opens in the default browser)."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    k = QLabel(key.upper())
    k.setStyleSheet(f"color: {MUTED}; font-size: 9px; letter-spacing: 2px;")
    disp = url.split("://", 1)[-1]
    v = QLabel(f'<a href="{url}" style="color: {OK};">{disp}</a>')
    v.setTextFormat(Qt.TextFormat.RichText)
    v.setOpenExternalLinks(True)
    v.setCursor(Qt.CursorShape.PointingHandCursor)
    v.setToolTip(url)
    v.setStyleSheet("font-size: 12px;")
    lay.addWidget(k)
    lay.addWidget(v)
    return w


def _index_of(list_widget: QListWidget, model_id: str) -> int:
    for i in range(list_widget.count()):
        if list_widget.item(i).data(Qt.ItemDataRole.UserRole) == model_id:
            return i
    return 0


def _qcolor(hex_color: str) -> QColor:
    return QColor(hex_color)
