from __future__ import annotations

import getpass
import logging
import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from macaw.gui.theme import active_theme

logger = logging.getLogger("macaw")

_T = active_theme()
BG = _T.bg
FG = _T.fg
MUTED = _T.muted
BORDER = _T.border

# System package managers, keyed by distro family. Values are the argv prefix
# run as root (pkexec supplies the privilege); the package name is appended.
_PKG_INSTALL: dict[str, list[str]] = {
    "arch": ["pacman", "-S", "--needed", "--noconfirm"],
    "debian": ["apt-get", "install", "-y"],
    "fedora": ["dnf", "install", "-y"],
    "suse": ["zypper", "install", "-y"],
    "alpine": ["apk", "add"],
    "void": ["xbps-install", "-y"],
}

_DISTRO_FAMILIES: dict[str, tuple[str, ...]] = {
    "arch": ("arch", "endeavouros", "manjaro", "garuda", "cachyos", "artix"),
    "debian": (
        "debian",
        "ubuntu",
        "pop",
        "mint",
        "elementary",
        "zorin",
        "neon",
        "raspbian",
    ),
    "fedora": ("fedora", "rhel", "centos", "nobara", "ultramarine"),
    "suse": ("suse", "opensuse", "sles"),
    "alpine": ("alpine",),
    "void": ("void",),
}

# ponytail: udev rule + input-group setup mirrors install.sh's
# ensure_ydotool_access; keep the two in sync if that recipe changes.
_UINPUT_RULE = "/etc/udev/rules.d/99-uinput-input-group.rules"


def _distro(release: str | None = None) -> str:
    """Best-effort distro family from /etc/os-release (ID + ID_LIKE).

    `release` overrides the file contents (for testing).
    """
    if release is None:
        try:
            release = Path("/etc/os-release").read_text()
        except OSError:
            return ""
    ident = ""
    for line in release.splitlines():
        key, _, val = line.partition("=")
        if key in ("ID", "ID_LIKE"):
            ident += " " + val.strip().strip('"').lower()
    for family, ids in _DISTRO_FAMILIES.items():
        if any(x in ident for x in ids):
            return family
    return ""


def _base_cmd() -> list[str] | None:
    return _PKG_INSTALL.get(_distro())


def manual_command(package: str) -> str:
    """Human-readable install command for when we can't do it automatically."""
    base = _base_cmd()
    if base:
        return "sudo " + " ".join([*base, package])
    return f"install '{package}' with your system package manager"


def _ydotool_setup(install_cmd: str) -> str:
    """Root shell script: install ydotool, then grant /dev/uinput access.

    Runs via ``pkexec sh -c``; ``$1`` is the invoking username. Mirrors
    install.sh's ensure_ydotool_access so auto-type actually works afterwards.
    """
    rule = (
        'KERNEL=="uinput", SUBSYSTEM=="misc", '
        'GROUP="input", MODE="0660", TAG+="uaccess"'
    )
    return (
        "set -e\n"
        f"{install_cmd}\n"
        "modprobe uinput 2>/dev/null || true\n"
        'id -nG "$1" | grep -qw input || usermod -aG input "$1"\n'
        f"printf '%s\\n' '{rule}' > {_UINPUT_RULE}\n"
        "udevadm control --reload-rules 2>/dev/null || true\n"
        "udevadm trigger /dev/uinput 2>/dev/null || true\n"
        "if command -v setfacl >/dev/null 2>&1 && [ -e /dev/uinput ]; then "
        "setfacl -m g:input:rw /dev/uinput || true; fi\n"
    )


def install_command(package: str) -> list[str] | None:
    """pkexec argv that installs `package`, or None if we can't automate it.

    Returns None when pkexec is missing or the distro is unrecognised — the
    dialog then shows `manual_command()` instead.
    """
    if not shutil.which("pkexec"):
        return None
    base = _base_cmd()
    if not base:
        return None
    if package == "ydotool":
        script = _ydotool_setup(" ".join([*base, package]))
        return ["pkexec", "sh", "-c", script, "macaw", getpass.getuser()]
    return ["pkexec", *base, package]


class _InstallWorker(QThread):
    """Runs the (privileged) install command, streaming its output."""

    line = pyqtSignal(str)
    done = pyqtSignal(bool, str)  # (ok, message)

    def __init__(self, cmd: list[str]) -> None:
        super().__init__()
        self._cmd = cmd
        self._last = ""

    def run(self) -> None:
        try:
            proc = subprocess.Popen(
                self._cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for raw in proc.stdout:
                text = raw.rstrip()
                if text:
                    self._last = text
                    self.line.emit(text)
            code = proc.wait()
        except FileNotFoundError:
            self.done.emit(
                False, "pkexec not found — install polkit, or install manually."
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.error("Input-tool install error: %s", exc)
            self.done.emit(False, str(exc))
            return
        if code == 0:
            self.done.emit(True, "Installed")
        elif code in (126, 127):  # pkexec: authorization dismissed / denied
            self.done.emit(False, "Authorization cancelled.")
        else:
            self.done.emit(False, self._last or f"exit {code}")


_BTN_GHOST = (
    f"QPushButton {{ background: transparent; color: {MUTED};"
    f" border: 1px solid {BORDER}; padding: 6px 14px; font-size: 11px; }}"
    f"QPushButton:hover {{ color: {FG}; border-color: {MUTED}; }}"
    f"QPushButton:disabled {{ color: {BORDER}; }}"
)
_BTN_PRIMARY = (
    f"QPushButton {{ background: {FG}; color: {BG}; border: none;"
    f" padding: 6px 16px; font-size: 11px; font-weight: 600; }}"
    f"QPushButton:hover {{ background: {MUTED}; }}"
    f"QPushButton:disabled {{ background: {BORDER}; color: {MUTED}; }}"
)


class InputToolInstallDialog(QDialog):
    """Offer to install the system tool auto-type needs.

    ``self.installed`` is True after a successful install this session.
    """

    def __init__(self, package: str, parent=None) -> None:
        super().__init__(parent)
        self.installed = False
        self._package = package
        self._cmd = install_command(package)
        self._worker: _InstallWorker | None = None
        srv = "Wayland" if package == "ydotool" else "X11"

        self.setWindowTitle("Macaw — auto-type")
        self.setFixedSize(460, 250)
        self.setModal(True)
        self.setStyleSheet(
            f"QDialog {{ background: {BG}; color: {FG};"
            " font-family: system-ui, -apple-system, sans-serif; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        self._title = QLabel("Auto-type needs a helper")
        self._title.setStyleSheet(f"color: {FG}; font-size: 14px; font-weight: 600;")
        layout.addWidget(self._title)

        self._body = QLabel(
            f"Typing into other windows on {srv} needs <b>{package}</b>, which "
            "isn't installed. Auto-type stays off until it is."
        )
        self._body.setWordWrap(True)
        self._body.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        layout.addWidget(self._body)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)  # indeterminate
        self._bar.setFixedHeight(8)
        self._bar.setTextVisible(False)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {BORDER}; border: none; }}"
            f"QProgressBar::chunk {{ background: {FG}; }}"
        )
        self._bar.hide()
        layout.addWidget(self._bar)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        self._status.setFixedHeight(48)
        layout.addWidget(self._status)

        layout.addStretch()

        row = QHBoxLayout()
        row.addStretch()
        self._cancel = QPushButton("Not now")
        self._cancel.setStyleSheet(_BTN_GHOST)
        self._cancel.clicked.connect(self.reject)
        row.addWidget(self._cancel)
        self._ok = QPushButton("Install")
        self._ok.setStyleSheet(_BTN_PRIMARY)
        self._ok.clicked.connect(self._on_ok)
        row.addWidget(self._ok)
        layout.addLayout(row)

        if self._cmd is None:
            # Unknown distro or no pkexec — show the command to run by hand.
            self._body.setText(
                f"Typing into other windows on {srv} needs <b>{package}</b>. "
                "Install it, then reopen Settings:"
            )
            self._status.setText(manual_command(package))
            self._status.setStyleSheet(
                f"color: {FG}; font-size: 11px; font-family: monospace;"
            )
            self._ok.hide()
            self._cancel.setText("Close")

    def _on_ok(self) -> None:
        self._ok.setEnabled(False)
        self._cancel.setEnabled(False)
        self._bar.show()
        self._status.setText("A password prompt will appear…")
        self._worker = _InstallWorker(self._cmd)  # type: ignore[arg-type]
        self._worker.line.connect(lambda t: self._status.setText(t[-160:]))
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, ok: bool, message: str) -> None:
        self._bar.hide()
        self._cancel.setEnabled(True)
        if ok:
            self.installed = True
            self._title.setText("Installed ✓")
            self._body.setText(
                "Log out and back in once so ydotool can use your keyboard, "
                "then restart Macaw."
                if self._package == "ydotool"
                else "Restart Macaw to enable auto-type."
            )
            self._status.setText("")
            self._ok.hide()
            self._cancel.setText("Done")
        else:
            self._title.setText("Install failed")
            self._status.setText(message)
            self._ok.setEnabled(True)
            self._cancel.setText("Close")
