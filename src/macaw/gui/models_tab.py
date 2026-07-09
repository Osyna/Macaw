from __future__ import annotations

import logging
import random
from pathlib import Path

from PyQt6.QtCore import (
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from macaw.audio.transcriber import Transcriber
from macaw.config import Config
from macaw.gui.download import _LOADING_QUOTES, _DownloadWorker
from macaw.gui.install import DependencyInstallDialog, _InstallWorker
from macaw.gui.widgets import (
    ACCENT,
    BORDER,
    CARD_BG,
    CONTROL_BG,
    DANGER,
    FG,
    MUTED,
    OK,
    WARN,
    ToggleSwitch,
    ValueStepper,
    _combo,
    _field_label,
    _hint_label,
    _section_label,
    _separator,
)
from macaw.stt import create_backend, list_models
from macaw.stt.base import hf_cache_sizes

logger = logging.getLogger("macaw")


def _fmt_size(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


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


_LANGUAGES = [
    ("English", "en"),
    ("French", "fr"),
    ("German", "de"),
    ("Spanish", "es"),
    ("Italian", "it"),
    ("Portuguese", "pt"),
    ("Dutch", "nl"),
    ("Polish", "pl"),
    ("Russian", "ru"),
    ("Japanese", "ja"),
    ("Chinese", "zh"),
]


class _StarRating(QWidget):
    """Read-only five-star display of a model's curated rating (from the catalog)."""

    def __init__(self, rating: int, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        n = max(0, min(5, int(rating or 0)))
        for i in range(1, 6):
            s = QLabel("★" if i <= n else "☆")
            color = "#F5B400" if i <= n else MUTED
            s.setStyleSheet(f"color: {color}; font-size: 18px;")
            lay.addWidget(s)
        lbl = QLabel("Rating")
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; margin-left: 8px;")
        lay.addWidget(lbl)
        lay.addStretch()


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


def _notes(text: str) -> QWidget:
    """Multi-line model description: pros (+) in green, cons (−) in red, and
    plain context lines in the body colour — each word-wrapped for readability."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(5)
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[0] == "+":
            color = OK
        elif line[0] in ("−", "-"):
            color = DANGER
        else:
            color = FG
        lbl = QLabel(line)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {color}; font-size: 13px;")
        lay.addWidget(lbl)
    return w


def _index_of(list_widget: QListWidget, model_id: str) -> int:
    for i in range(list_widget.count()):
        if list_widget.item(i).data(Qt.ItemDataRole.UserRole) == model_id:
            return i
    return 0


def _qcolor(hex_color: str) -> QColor:
    return QColor(hex_color)


class ModelsTab(QWidget):
    config_saved = pyqtSignal(object)
    cancel_load = pyqtSignal()  # user cancelled the in-flight model load

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config_path = config_path
        self._loading = False  # True while the service loads a model in the background
        self._load_state = "ready"  # last async load outcome (drives the Retry button)
        self._op = None  # active download/activation, shown inline in the card
        self._op_timer = QTimer(self)
        self._op_timer.setInterval(2500)
        self._op_timer.timeout.connect(self._rotate_quote)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.welcome = QLabel(
            "Thanks for downloading Macaw!  Pick a model below, then Download "
            "it and Set as active to get started."
        )
        self.welcome.setWordWrap(True)
        self.welcome.setStyleSheet(
            f"background: {CARD_BG}; color: {FG};"
            f" border: 1px solid {ACCENT}; padding: 12px 14px; font-size: 13px;"
        )
        self.welcome.setVisible(False)
        outer.addWidget(self.welcome)
        outer.addSpacing(16)
        root = QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- left: model list --
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        left.addWidget(_section_label(f"Choose a model  ({len(list_models())})"))
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
        self.load_status = QLabel("")
        self.load_status.setWordWrap(True)
        self.load_status.setVisible(False)
        left.addWidget(self.load_status)
        root.addLayout(left)

        root.addSpacing(24)

        # -- right: detail panel (rebuilt per selection) --
        self._detail = QWidget()
        self.detail = QVBoxLayout(self._detail)
        self.detail.setContentsMargins(0, 0, 0, 0)
        self.detail.setSpacing(10)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.addWidget(self._detail, 1)
        outer.addLayout(root, 1)

        self._populate_list()

    # -- list --------------------------------------------------------

    def _active_model(self) -> str:
        return Config.load(self.config_path).model

    def _populate_list(self) -> None:
        cache = hf_cache_sizes()
        cfg = Config.load(self.config_path)
        active = cfg.model
        self.list.blockSignals(True)
        self.list.clear()
        any_missing = False
        any_ready = False
        for info in sorted(list_models(), key=lambda m: (m.cloud, -m.rating)):
            backend = create_backend(info.id)
            ready = backend.is_ready(cache)
            any_ready = any_ready or ready
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
        self.welcome.setVisible(not any_ready)  # first-run / nothing usable yet
        if self.list.count():
            idx = _index_of(self.list, active)
            self.list.setCurrentRow(idx if idx >= 0 else 0)

    def refresh(self) -> None:
        current = self._current_id()
        self._populate_list()
        if current:
            self.list.setCurrentRow(_index_of(self.list, current))
            self._show_detail(current)  # re-render even when the row is unchanged

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

        kind = "cloud" if info.cloud else ("streaming" if info.streaming else "offline")
        badges = " · ".join([kind] + (["recommended"] if info.recommended else []))
        size_part = "" if info.cloud else f"{info.size} · "
        meta = QLabel(
            f"{info.backend} · {size_part}{info.speed} · {info.languages} · {badges}"
        )
        meta.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.detail.addWidget(meta)
        self.detail.addWidget(_StarRating(info.rating))

        # recommendation row (horizontal)
        rec = QHBoxLayout()
        rec.setSpacing(24)
        rec.addWidget(_kv("Hardware", info.hardware))
        rec.addWidget(_kv("VRAM", info.vram))
        rec.addStretch()
        self.detail.addLayout(rec)
        if info.min_specs or info.rec_specs:
            sysrow = QHBoxLayout()
            sysrow.setSpacing(24)
            if info.min_specs:
                sysrow.addWidget(_kv("Minimal", info.min_specs))
            if info.rec_specs:
                sysrow.addWidget(_kv("Recommended", info.rec_specs))
            sysrow.addStretch()
            self.detail.addLayout(sysrow)
        if info.notes:
            self.detail.addWidget(_notes(info.notes))

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
        if info.cloud:
            if not available:
                status = ("Needs  macaw[openai]", WARN)
            elif not backend.api_key():
                status = ("Needs an OpenAI API key", WARN)
            elif is_active:
                status = ("● Active model", OK)
            else:
                status = ("Ready · cloud (uses your API key)", OK)
        elif not available:
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
        if info.cloud:
            self._add_api_key_field()

        op = self._op
        if op is not None and op["model_id"] == model_id:
            self._render_activity(op)
        else:
            self._render_actions(model_id, info, backend, ready, size, is_active)

        if info.lang_select:
            self.detail.addSpacing(6)
            self.detail.addWidget(_section_label("Language"))
            lang = _combo(180)
            for name, code in _LANGUAGES:
                lang.addItem(name, code)
            cur = Config.load(self.config_path).model_languages.get(info.id) or "en"
            li = lang.findData(cur)
            lang.setCurrentIndex(li if li >= 0 else 0)
            lang.currentIndexChanged.connect(
                lambda _i, mid=info.id, c=lang: self._save_language(
                    mid, c.currentData()
                )
            )
            self.detail.addWidget(lang)

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

    def _add_api_key_field(self) -> None:
        self.detail.addSpacing(6)
        self.detail.addWidget(_section_label("OpenAI API key"))
        edit = QLineEdit(Config.load(self.config_path).openai_api_key)
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setPlaceholderText("sk-…")
        edit.setStyleSheet(
            f"QLineEdit {{ background: {CONTROL_BG}; color: {FG};"
            f" border: 1px solid {BORDER}; padding: 6px 8px; }}"
        )
        edit.editingFinished.connect(lambda: self._save_api_key(edit.text().strip()))
        self.detail.addWidget(edit)
        self.detail.addWidget(
            _hint_label("Stored in config.yaml, or set $OPENAI_API_KEY.")
        )

    def _save_api_key(self, key: str) -> None:
        cfg = Config.load(self.config_path)
        if cfg.openai_api_key == key:
            return
        cfg.openai_api_key = key
        cfg.save(self.config_path)
        self.config_saved.emit(cfg)
        self.refresh()

    # -- actions -----------------------------------------------------

    def _save_param(self, model_id: str, key: str, value) -> None:
        cfg = Config.load(self.config_path)
        cfg.model_params.setdefault(model_id, {})[key] = value
        cfg.save(self.config_path)
        self.config_saved.emit(cfg)

    def _save_language(self, model_id: str, code: str) -> None:
        cfg = Config.load(self.config_path)
        cfg.model_languages[model_id] = code
        cfg.save(self.config_path)
        self.config_saved.emit(cfg)

    def show_load_status(self, state: str, label: str, detail: str = "") -> None:
        """Reflect the service's async model load in the inline card zone."""
        self._loading = state == "loading"
        self._load_state = state
        op = self._op
        if op is not None and op["kind"] == "load":
            if state in ("ready", "cancelled"):
                self._op_timer.stop()
                self._op = None
            elif state == "error":
                self._op_timer.stop()
                op["state"] = "error"
                op["detail"] = detail or "Load failed."
        text = {
            "loading": f"⏳  Loading {label}…",
            "ready": f"●  {label} is active",
            "error": f"⚠  Couldn't load {label}: {detail}",
            "cancelled": f"Cancelled loading {label}",
        }.get(state, "")
        color = {"loading": FG, "ready": OK, "error": DANGER}.get(state, MUTED)
        self.load_status.setText(text)
        self.load_status.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.load_status.setVisible(bool(text))
        self.refresh()

    def _set_active(self, model_id: str) -> None:
        cfg = Config.load(self.config_path)
        cfg.model = model_id
        cfg.save(self.config_path)
        label = next((m.label for m in list_models() if m.id == model_id), model_id)
        quotes = random.sample(_LOADING_QUOTES, k=len(_LOADING_QUOTES))
        self._op = {
            "model_id": model_id,
            "kind": "load",
            "state": "running",
            "label": label,
            "quotes": quotes,
            "qi": 0,
            "quote": quotes[0],
        }
        self._loading = True
        self._op_timer.start()
        self.config_saved.emit(cfg)  # service loads async → show_load_status drives it
        self.refresh()

    def _download(self, model_id: str) -> None:
        worker = _DownloadWorker(Transcriber(model_size=model_id), load_after=False)
        self._op = {
            "model_id": model_id,
            "kind": "download",
            "pct": 0,
            "state": "running",
            "worker": worker,
        }
        worker.progress.connect(self._on_download_progress)
        worker.finished.connect(lambda _p: self._finish_download(True, ""))
        worker.error.connect(lambda m: self._finish_download(False, m))
        worker.start()
        self.refresh()

    def _on_download_progress(self, pct: int) -> None:
        op = self._op
        if op is None or op["kind"] != "download":
            return
        op["pct"] = pct
        if self._current_id() == op["model_id"] and getattr(self, "_op_bar", None):
            self._op_bar.setValue(pct)
            self._op_msg.setText(f"Downloading… {pct}%")

    def _finish_download(self, ok: bool, msg: str) -> None:
        op = self._op
        if op is None or op["kind"] != "download":
            return
        if ok:
            self._op = None
        else:
            op["state"] = "error"
            op["detail"] = msg or "Download failed."
        self.refresh()

    def _on_install_line(self, line: str) -> None:
        op = self._op
        if op is None or op["kind"] != "install":
            return
        op["quote"] = line
        if self._current_id() == op["model_id"] and getattr(self, "_op_msg", None):
            self._op_msg.setText(line)

    def _on_install_done(self, ok: bool, msg: str) -> None:
        op = self._op
        if op is None or op["kind"] != "install":
            return
        if ok:
            self._op = None
        else:
            op["state"] = "error"
            op["detail"] = msg or "Install failed."
        self.refresh()

    def _rotate_quote(self) -> None:
        op = self._op
        if op is None or op["kind"] != "load" or op["state"] != "running":
            return
        op["qi"] = (op["qi"] + 1) % len(op["quotes"])
        op["quote"] = op["quotes"][op["qi"]]
        if self._current_id() == op["model_id"] and getattr(self, "_op_msg", None):
            self._op_msg.setText(op["quote"])

    def _cancel_op(self) -> None:
        op = self._op
        if op is None:
            return
        self._op_timer.stop()
        if op["kind"] in ("download", "install"):
            w = op.get("worker")
            if w is not None:
                w.cancel()
        else:
            self.cancel_load.emit()  # tell the service to kill the load
            self._loading = False
        self._op = None
        self.refresh()

    def _render_activity(self, op: dict) -> None:
        """Inline progress zone below the rating: bar + live/funny status + Cancel."""
        self.detail.addSpacing(6)
        bar = QProgressBar()
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setStyleSheet(
            f"QProgressBar {{ background: {BORDER}; border: none; }}"
            f" QProgressBar::chunk {{ background: {ACCENT}; }}"
        )
        running = op["state"] == "running"
        if op["kind"] == "download" and running:
            bar.setRange(0, 100)
            bar.setValue(op.get("pct", 0))
        elif running:
            bar.setRange(0, 0)  # indeterminate — a load has no percentage
        else:
            bar.setRange(0, 100)
            bar.setValue(0)
        self.detail.addWidget(bar)
        self._op_bar = bar

        if op["state"] == "error":
            text, color = op.get("detail", "Failed."), DANGER
        elif op["kind"] == "download":
            text, color = f"Downloading… {op.get('pct', 0)}%", MUTED
        else:
            text, color = op.get("quote", "Loading…"), MUTED
        msg = QLabel(text)
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {color}; font-size: 12px; font-style: italic;")
        self.detail.addWidget(msg)
        self._op_msg = msg

        btn = _action_button("Close" if op["state"] == "error" else "Cancel", DANGER)
        btn.clicked.connect(self._cancel_op)
        self.detail.addSpacing(4)
        row = QHBoxLayout()
        row.addWidget(btn)
        row.addStretch()
        self.detail.addLayout(row)

    def _render_actions(self, model_id, info, backend, ready, size, is_active) -> None:
        actions = QHBoxLayout()
        actions.setSpacing(8)
        if not backend.available() and info.extra:
            btn = _action_button("Install ↓", OK)
            btn.clicked.connect(lambda: self._install([info.extra], info.label))
            actions.addWidget(btn)
        if backend.available() and backend.hf_repos() and size == 0:
            btn = _action_button("Download ⤓", FG)
            btn.clicked.connect(lambda: self._download(model_id))
            actions.addWidget(btn)
        failed_active = is_active and self._load_state == "error"
        set_btn = _action_button("Retry" if failed_active else "Set as active", FG)
        can_set = ready and not self._loading and (not is_active or failed_active)
        set_btn.setEnabled(can_set)
        set_btn.clicked.connect(lambda: self._set_active(model_id))
        actions.addWidget(set_btn)
        if size > 0:
            del_btn = _action_button("Delete 🗑", DANGER)
            del_btn.setEnabled(not self._loading)  # don't rmtree a venv mid-load
            del_btn.clicked.connect(lambda: self._delete(model_id))
            actions.addWidget(del_btn)
        actions.addStretch()
        self.detail.addLayout(actions)

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
        wanted = list(dict.fromkeys(e for e in extras if e))
        if not wanted:
            return
        worker = _InstallWorker(wanted)
        self._op = {
            "model_id": self._current_id(),
            "kind": "install",
            "state": "running",
            "label": label,
            "quote": f"Installing {label}…",
            "worker": worker,
        }
        worker.line.connect(self._on_install_line)
        worker.done.connect(self._on_install_done)
        worker.start()
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
            DependencyInstallDialog(
                "all optional backends", missing, parent=self
            ).start()
            self.refresh()


# ── Settings tab: two columns of general settings ────────────────────
