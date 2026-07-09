from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import (
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from macaw.config import Config
from macaw.gui.icon import logo_icon, logo_pixmap
from macaw.gui.models_tab import ModelsTab
from macaw.gui.settings_tab import SettingsTab
from macaw.gui.widgets import (
    ACCENT,
    ACCENT_FG,
    BG,
    CARD_BG,
    FG,
    MUTED,
    _close_button,
    _separator,
)

logger = logging.getLogger("macaw")

GITHUB_URL = "https://github.com/Osyna/Macaw"


def _open_github() -> None:
    """Open the repo in the default browser; fall back to Firefox if none is set."""
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices

    if QDesktopServices.openUrl(QUrl(GITHUB_URL)):
        return
    import shutil
    import subprocess

    fx = shutil.which("firefox")
    if fx:
        subprocess.Popen(
            [fx, GITHUB_URL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


def _tab_style(active: bool) -> str:
    color = FG if active else MUTED
    border = FG if active else "transparent"
    return (
        f"QPushButton {{ background: transparent; color: {color}; border: none; "
        f"border-bottom: 2px solid {border}; padding: 6px 4px; margin-right: 20px; "
        f"font-size: 12px; letter-spacing: 3px; font-weight: 600; }}"
        f"QPushButton:hover {{ color: {FG}; }}"
    )


class StarToast(QFrame):
    """A small, non-modal corner nudge to star the project on GitHub. Lives in
    the bottom-right of its parent window; the rest of Settings stays usable."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setFixedWidth(288)
        self.setStyleSheet(
            f"#toast {{ background: {CARD_BG}; border: 1px solid {ACCENT};"
            f" border-radius: 10px; }}"
        )
        eff = QGraphicsDropShadowEffect(self)
        eff.setBlurRadius(24)
        eff.setColor(QColor(0, 0, 0, 150))
        eff.setOffset(0, 4)
        self.setGraphicsEffect(eff)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("⭐  Enjoying Macaw?")
        title.setStyleSheet(f"color: {FG}; font-size: 13px; font-weight: 600;")
        close = QPushButton("✕")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFixedSize(18, 18)
        close.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {MUTED}; border: none;"
            f" font-size: 13px; }} QPushButton:hover {{ color: {FG}; }}"
        )
        close.clicked.connect(self.close)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(close)
        lay.addLayout(top)

        msg = QLabel("A GitHub star helps others find it — it takes two seconds. 💚")
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        lay.addWidget(msg)

        star_btn = QPushButton("Star on GitHub")
        star_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        star_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: {ACCENT_FG}; border: none;"
            f" padding: 7px 14px; font-size: 12px; font-weight: 600; }}"
        )
        star_btn.clicked.connect(self._star)
        lay.addWidget(star_btn)

    def _star(self) -> None:
        _open_github()
        p = self.parentWidget()
        if p is not None and hasattr(p, "config_path"):
            cfg = Config.load(p.config_path)
            cfg.star_prompted = True
            cfg.save(p.config_path)
        self.close()

    def reposition(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        self.adjustSize()
        m = 22
        self.move(p.width() - self.width() - m, p.height() - self.height() - m)

    def show_toast(self) -> None:
        self.reposition()
        self.show()
        self.raise_()


class MainWindow(QWidget):
    config_saved = pyqtSignal(object)
    cancel_load = pyqtSignal()

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config_path = config_path
        self._star_scheduled = False  # GitHub-star nudge scheduled this session
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
        self._app_theme_btn = QPushButton()
        self._app_theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._app_theme_btn.setFixedSize(28, 28)
        self._app_theme_btn.setToolTip("Toggle dark / light window theme")
        self._app_theme_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {FG}; border: none;"
            f" font-size: 15px; }} QPushButton:hover {{ color: {ACCENT}; }}"
        )
        self._refresh_app_theme_btn()
        self._app_theme_btn.clicked.connect(self._toggle_app_theme)
        header.addWidget(self._app_theme_btn)
        header.addSpacing(12)
        header.addWidget(_close_button(self.close))
        root.addLayout(header)
        root.addSpacing(16)
        root.addWidget(_separator())
        root.addSpacing(20)

        self.models = ModelsTab(config_path)
        self.settings = SettingsTab(config_path)
        self.models.config_saved.connect(self._relay)
        self.models.cancel_load.connect(self.cancel_load)
        self.settings.config_saved.connect(self._relay)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.models)
        self.stack.addWidget(self.settings)
        root.addWidget(self.stack, 1)

        self.show_tab("Models")

    def _relay(self, cfg) -> None:
        self.config_saved.emit(cfg)

    def _refresh_app_theme_btn(self) -> None:
        light = Config.load(self.config_path).app_theme == "light"
        self._app_theme_btn.setText("☀" if light else "☾")

    def _toggle_app_theme(self) -> None:
        cfg = Config.load(self.config_path)
        cfg.app_theme = "dark" if cfg.app_theme == "light" else "light"
        cfg.save(self.config_path)
        self._refresh_app_theme_btn()
        self.config_saved.emit(cfg)  # app_theme ∈ _LOOK → service restarts + reopens

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
        self._maybe_prompt_star()

    def _maybe_prompt_star(self) -> None:
        if self._star_scheduled or Config.load(self.config_path).star_prompted:
            return
        self._star_scheduled = True  # once per session; the modal persists the flag
        QTimer.singleShot(4000, self._show_star)

    def _show_star(self) -> None:
        cfg = Config.load(self.config_path)
        if cfg.star_prompted or not self.isVisible():
            return
        cfg.star_prompted = True
        cfg.save(self.config_path)
        self._star_toast = StarToast(self)
        self._star_toast.show_toast()

    def hideEvent(self, event: object) -> None:
        super().hideEvent(event)
        self.settings.stop_preview()
        t = getattr(self, "_star_toast", None)
        if t is not None:
            t.close()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        t = getattr(self, "_star_toast", None)
        if t is not None and t.isVisible():
            t.reposition()

    def closeEvent(self, event: object) -> None:
        self.settings.stop_preview()
        super().closeEvent(event)


# ── small shared helpers ─────────────────────────────────────────────
