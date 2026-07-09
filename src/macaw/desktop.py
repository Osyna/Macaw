from __future__ import annotations

import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import time

logger = logging.getLogger("macaw")


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _display_server() -> str:
    """Return 'wayland' or 'x11'."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return "wayland"
    return "x11"


def _compositor() -> str | None:
    """Best-effort compositor / DE detection."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    session = os.environ.get("DESKTOP_SESSION", "").lower()
    combined = f"{desktop} {session}"
    if "hyprland" in combined:
        return "hyprland"
    if "sway" in combined:
        return "sway"
    if "kde" in combined or "plasma" in combined:
        return "kde"
    if "gnome" in combined:
        return "gnome"
    if "xfce" in combined:
        return "xfce"
    if "i3" in combined:
        return "i3"
    return None


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _ydotoold_running() -> bool:
    try:
        r = subprocess.run(
            ["pgrep", "-x", "ydotoold"],
            capture_output=True,
            timeout=1,
        )
        return r.returncode == 0
    except Exception:
        return False


def _ensure_ydotoold() -> bool:
    """Start ydotoold if ydotool is available and daemon isn't running."""
    if _ydotoold_running():
        return True
    if not _has("ydotool"):
        return False
    try:
        subprocess.Popen(
            ["ydotoold"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give daemon time to start
        for _ in range(10):
            time.sleep(0.1)
            if _ydotoold_running():
                return True
    except Exception:
        pass
    return False


def _is_xwayland_window(window_address: str) -> bool | None:
    """Check if a Hyprland window is running under XWayland.

    Returns True/False, or None if detection failed.
    """
    try:
        r = subprocess.run(
            ["hyprctl", "clients", "-j"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if r.returncode != 0:
            return None
        for client in json.loads(r.stdout):
            if client.get("address") == window_address:
                return client.get("xwayland", False)
    except Exception:
        pass
    return None


def auto_type_available() -> bool:
    """True if a keystroke-injection tool for auto-type is installed."""
    if sys.platform == "win32":
        return True  # SendInput is part of the OS
    return any(_has(t) for t in ("ydotool", "wtype", "xdotool"))


def auto_type_package(display: str | None = None) -> str:
    """Recommended system package that provides auto-type for this session.

    ydotool on Wayland (it also drives XWayland apps), xdotool on X11.
    Windows needs nothing — SendInput is built in.
    """
    if display is None and sys.platform == "win32":
        return ""
    if (display or _display_server()) == "wayland":
        return "ydotool"
    return "xdotool"


# ---------------------------------------------------------------------------
# Public: DesktopHelper
# ---------------------------------------------------------------------------


class DesktopHelper:
    """One-stop helper for clipboard ops, paste simulation, and window focus.

    Instantiate once at startup — it caches the detection results.
    """

    # Window classes that use Ctrl+Shift+V for paste instead of Ctrl+V.
    _TERMINAL_CLASSES = frozenset(
        {
            "kitty",
            "alacritty",
            "foot",
            "wezterm",
            "st",
            "urxvt",
            "xterm",
            "gnome-terminal",
            "konsole",
            "terminator",
            "tilix",
            "sakura",
            "termite",
            "guake",
            "tilda",
            "xfce4-terminal",
            "lxterminal",
            "mate-terminal",
            "rio",
            "ghostty",
            "contour",
        }
    )

    def __init__(self) -> None:
        self.display = _display_server()
        self.compositor = _compositor()
        self._window_class: str = ""

        # Clipboard read/write commands ---------------------------------
        if self.display == "wayland":
            self._clip_copy = ["wl-copy", "--"] if _has("wl-copy") else None
            self._clip_paste = (
                ["wl-paste", "--no-newline"] if _has("wl-paste") else None
            )
            self._clip_clear = ["wl-copy", "--clear"] if _has("wl-copy") else None
        else:
            if _has("xclip"):
                self._clip_copy = ["xclip", "-selection", "clipboard"]
                self._clip_paste = ["xclip", "-selection", "clipboard", "-o"]
                self._clip_clear = None  # xclip has no clear; we copy empty
            elif _has("xsel"):
                self._clip_copy = ["xsel", "--clipboard", "--input"]
                self._clip_paste = ["xsel", "--clipboard", "--output"]
                self._clip_clear = ["xsel", "--clipboard", "--delete"]
            else:
                self._clip_copy = None
                self._clip_paste = None
                self._clip_clear = None

        # Keystroke simulation tools ------------------------------------
        # Build ordered list of available paste tools.
        # ydotool (evdev) is preferred — it works with both native Wayland
        # and XWayland apps.  wtype only works with native Wayland.
        self._paste_tools = self._detect_paste_tools()

        # Window focus strategy -----------------------------------------
        self._focus_strategy = self._pick_focus_strategy()

        logger.info(
            "Desktop: %s, compositor=%s, paste=%s, focus=%s",
            self.display,
            self.compositor,
            self._paste_tools,
            self._focus_strategy,
        )

        if not self._paste_tools:
            logger.warning(
                "No paste tool available — install ydotool, wtype, or xdotool. "
                "Auto-type mode will not work."
            )

    # -- tool selection -------------------------------------------------

    def _detect_paste_tools(self) -> list[str]:
        """Return ordered list of available paste tools (best first)."""
        tools: list[str] = []
        if self.display == "wayland":
            # ydotool works with ALL windows (native + XWayland)
            if _has("ydotool") and _ensure_ydotoold():
                tools.append("ydotool")
            # wtype is pure Wayland — works with native Wayland apps only
            if _has("wtype"):
                tools.append("wtype")
            # xdotool works with XWayland windows
            if _has("xdotool"):
                tools.append("xdotool")
        else:
            # X11
            if _has("xdotool"):
                tools.append("xdotool")
            if _has("ydotool") and _ensure_ydotoold():
                tools.append("ydotool")
        return tools

    def _pick_focus_strategy(self) -> str | None:
        if self.compositor == "hyprland" and _has("hyprctl"):
            return "hyprctl"
        if self.compositor == "sway" and _has("swaymsg"):
            return "swaymsg"
        if self.display == "x11" and _has("xdotool"):
            return "xdotool"
        # KDE, GNOME, etc. — no reliable CLI focus tool; rely on the
        # window still being focused after our overlay hides.
        return None

    def _best_paste_tool(self, window_id: str | None = None) -> str | None:
        """Pick the best paste tool for the target window."""
        if not self._paste_tools:
            return None

        # On Hyprland, check if target is XWayland — wtype won't work there
        if window_id and self.compositor == "hyprland" and len(self._paste_tools) > 1:
            xwl = _is_xwayland_window(window_id)
            if xwl is True:
                # Need evdev (ydotool) or X11 (xdotool) for XWayland
                for t in self._paste_tools:
                    if t in ("ydotool", "xdotool"):
                        return t
                # wtype as last resort (might not work)
                return self._paste_tools[0]

        return self._paste_tools[0]

    # -- clipboard operations -------------------------------------------

    def clipboard_read(self) -> bytes | None:
        """Read current clipboard contents (binary). Returns None on failure."""
        if not self._clip_paste:
            return None
        try:
            r = subprocess.run(self._clip_paste, capture_output=True, timeout=1)
            return r.stdout if r.returncode == 0 else None
        except Exception:
            return None

    def clipboard_write(self, text: str) -> bool:
        """Write text to the clipboard. Returns True on success."""
        if not self._clip_copy:
            return False
        try:
            proc = subprocess.Popen(self._clip_copy, stdin=subprocess.PIPE)
            proc.communicate(input=text.encode("utf-8"), timeout=2)
            return proc.returncode == 0
        except Exception:
            return False

    def clipboard_restore(self, data: bytes | None) -> None:
        """Restore clipboard to previous state."""
        if data is not None and self._clip_copy:
            try:
                proc = subprocess.Popen(self._clip_copy, stdin=subprocess.PIPE)
                proc.communicate(input=data, timeout=2)
            except Exception:
                pass
        elif self._clip_clear:
            try:
                subprocess.run(self._clip_clear, capture_output=True, timeout=1)
            except Exception:
                pass

    # -- window focus ---------------------------------------------------

    def capture_active_window(self) -> str | None:
        """Capture an identifier for the currently focused window.

        Also stores the window class in self._window_class for paste-key
        selection (terminals need Ctrl+Shift+V instead of Ctrl+V).
        """
        self._window_class = ""
        try:
            if self._focus_strategy == "hyprctl":
                r = subprocess.run(
                    ["hyprctl", "activewindow", "-j"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                if r.returncode == 0:
                    info = json.loads(r.stdout)
                    self._window_class = (info.get("class") or "").lower()
                    return info.get("address")

            elif self._focus_strategy == "swaymsg":
                r = subprocess.run(
                    ["swaymsg", "-t", "get_tree"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                if r.returncode == 0:
                    tree = json.loads(r.stdout)
                    focused = _sway_find_focused(tree)
                    if focused is not None:
                        return str(focused)

            elif self._focus_strategy == "xdotool":
                r = subprocess.run(
                    ["xdotool", "getactivewindow"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                if r.returncode == 0:
                    return r.stdout.strip()
        except Exception:
            pass
        return None

    def focus_window(self, window_id: str | None) -> None:
        """Restore focus to a previously captured window."""
        if not window_id or not self._focus_strategy:
            return
        try:
            if self._focus_strategy == "hyprctl":
                subprocess.run(
                    ["hyprctl", "dispatch", "focuswindow", f"address:{window_id}"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
            elif self._focus_strategy == "swaymsg":
                subprocess.run(
                    ["swaymsg", f"[con_id={window_id}] focus"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
            elif self._focus_strategy == "xdotool":
                subprocess.run(
                    ["xdotool", "windowactivate", window_id],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
        except Exception:
            pass

    # -- paste simulation -----------------------------------------------

    def _send_keystroke(self, tool: str, keys: str) -> bool:
        """Send a keystroke combo. keys is a symbolic name like 'ctrl+v'."""
        try:
            if tool == "wtype":
                # Build wtype args from symbolic key combo
                args = ["wtype", "-d", "8"]
                parts = keys.split("+")
                key = parts[-1]
                mods = parts[:-1]
                for m in mods:
                    args += ["-M", m]
                args += ["-k", key]
                for m in reversed(mods):
                    args += ["-m", m]
                r = subprocess.run(args, capture_output=True, timeout=2)
                return r.returncode == 0
            elif tool == "ydotool":
                # Map symbolic names to evdev keycodes
                keymap = {
                    "ctrl": 29,
                    "shift": 42,
                    "alt": 56,
                    "super": 125,
                    "v": 47,
                    "Insert": 110,
                    "c": 46,
                    "a": 30,
                }
                parts = keys.split("+")
                evseq = []
                for p in parts:
                    code = keymap.get(p)
                    if code is None:
                        return False
                    evseq.append(f"{code}:1")
                for p in reversed(parts):
                    code = keymap.get(p)
                    evseq.append(f"{code}:0")
                r = subprocess.run(
                    ["ydotool", "key"] + evseq,
                    capture_output=True,
                    timeout=2,
                )
                return r.returncode == 0
            elif tool == "xdotool":
                r = subprocess.run(
                    ["xdotool", "key", keys],
                    capture_output=True,
                    timeout=2,
                )
                return r.returncode == 0
        except Exception:
            pass
        return False

    def _is_terminal(self) -> bool:
        """Check if the captured window is a terminal emulator."""
        return self._window_class in self._TERMINAL_CLASSES

    def simulate_paste(self, window_id: str | None = None) -> bool:
        """Send a paste keystroke to the focused window. Returns True on success.

        Picks the right paste combo depending on the tool and target app:
        - Terminals need Ctrl+Shift+V (Ctrl+V is "verbatim insert" in most)
        - wtype uses Shift+Insert (Electron/XWayland compat)
        - evdev-based tools (ydotool) use Ctrl+V
        """
        tool = self._best_paste_tool(window_id)
        if not tool:
            return False

        is_term = self._is_terminal()

        if tool == "wtype":
            # Shift+Insert is universally understood as paste and avoids
            # issues with Electron apps mishandling virtual Ctrl+V.
            # Terminals also accept Shift+Insert.
            combo = "shift+Insert"
        elif is_term:
            # Terminals: Ctrl+Shift+V
            combo = "ctrl+shift+v"
        else:
            combo = "ctrl+v"

        logger.debug("Desktop: paste %s via %s (terminal=%s)", combo, tool, is_term)
        return self._send_keystroke(tool, combo)

    # -- direct text typing ---------------------------------------------

    def _type_directly(self, text: str, window_id: str | None = None) -> bool:
        """Type text directly via input tools, bypassing the clipboard.

        Returns True on success.
        """
        tool = self._best_paste_tool(window_id)
        if not tool:
            return False
        try:
            if tool == "wtype":
                r = subprocess.run(
                    ["wtype", "--", text],
                    capture_output=True,
                    timeout=10,
                )
                return r.returncode == 0
            elif tool == "ydotool":
                r = subprocess.run(
                    ["ydotool", "type", "--", text],
                    capture_output=True,
                    timeout=10,
                )
                return r.returncode == 0
            elif tool == "xdotool":
                r = subprocess.run(
                    ["xdotool", "type", "--clearmodifiers", "--", text],
                    capture_output=True,
                    timeout=10,
                )
                return r.returncode == 0
        except Exception as exc:
            logger.debug("Desktop: direct typing via %s failed: %s", tool, exc)
        return False

    # -- high-level: type text into the focused window ------------------

    def type_into_window(self, text: str, window_id: str | None = None) -> None:
        """Type text into the focused window.

        Uses clipboard-write + paste keystroke for speed (instant regardless
        of text length).  Falls back to direct character-by-character typing
        only when clipboard paste fails.
        """
        if window_id and self._focus_strategy:
            self.focus_window(window_id)
            time.sleep(0.15)  # give compositor time to process focus change

        # Fast path: clipboard write → paste keystroke → restore
        if self._clip_copy:
            saved = self.clipboard_read()
            try:
                if self.clipboard_write(text):
                    time.sleep(0.03)
                    if self.simulate_paste(window_id):
                        logger.debug("Desktop: pasted via clipboard")
                        return
            finally:
                time.sleep(0.15)
                self.clipboard_restore(saved)

        # Slow fallback: direct keystroke-by-keystroke typing
        if self._type_directly(text, window_id):
            logger.debug("Desktop: typed directly (slow path)")
            return

        logger.warning("Desktop: all typing methods failed")


# ---------------------------------------------------------------------------
# Sway tree walker
# ---------------------------------------------------------------------------


def _sway_find_focused(node: dict) -> int | None:
    """Recursively find the focused container id in sway's tree."""
    if node.get("focused"):
        return node.get("id")
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        result = _sway_find_focused(child)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Windows backend (SendInput + pyperclip). Same public surface as DesktopHelper.
# ---------------------------------------------------------------------------

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_VK_CONTROL = 0x11
_VK_RETURN = 0x0D
_VK_V = 0x56


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        # MOUSEINPUT (32 bytes on x64) is the largest union member; pad to it so
        # sizeof(_INPUT) matches the Win32 INPUT struct SendInput validates.
        _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_ubyte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def _key_event(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    ev = _INPUT()
    ev.type = _INPUT_KEYBOARD
    ev.ki = _KEYBDINPUT(vk, scan, flags, 0, None)
    return ev


def _send_events(events: list[_INPUT]) -> bool:
    """Feed events to SendInput in one atomic batch. True if all were injected."""
    if not events:
        return True
    arr = (_INPUT * len(events))(*events)
    sent = ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))
    return sent == len(events)


def _text_events(text: str) -> list[_INPUT]:
    """KEYEVENTF_UNICODE press/release pairs for `text` (newline -> Enter)."""
    events: list[_INPUT] = []
    for ch in text:
        if ch == "\r":
            continue
        if ch == "\n":
            events.append(_key_event(vk=_VK_RETURN))
            events.append(_key_event(vk=_VK_RETURN, flags=_KEYEVENTF_KEYUP))
            continue
        # UTF-16 code units — surrogate pairs handle emoji and friends.
        raw = ch.encode("utf-16-le")
        for i in range(0, len(raw), 2):
            unit = raw[i] | (raw[i + 1] << 8)
            events.append(_key_event(scan=unit, flags=_KEYEVENTF_UNICODE))
            events.append(
                _key_event(scan=unit, flags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP)
            )
    return events


class _WinDesktop:
    """DesktopHelper for Windows: clipboard via pyperclip (native Win32 inside),
    window focus via GetForegroundWindow, keystrokes via SendInput."""

    def __init__(self) -> None:
        self.display = "windows"
        self.compositor = None
        self._window_class = ""
        logger.info("Desktop: windows (SendInput + pyperclip)")

    # -- clipboard operations -------------------------------------------

    def clipboard_read(self) -> bytes | None:
        try:
            import pyperclip

            return pyperclip.paste().encode("utf-8")
        except Exception:  # noqa: BLE001
            return None

    def clipboard_write(self, text: str) -> bool:
        try:
            import pyperclip

            pyperclip.copy(text)
            return True
        except Exception:  # noqa: BLE001
            return False

    def clipboard_restore(self, data: bytes | None) -> None:
        try:
            import pyperclip

            pyperclip.copy((data or b"").decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            pass

    # -- window focus ---------------------------------------------------

    def capture_active_window(self) -> str | None:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        return str(hwnd) if hwnd else None

    def focus_window(self, window_id: str | None) -> None:
        # ponytail: SetForegroundWindow is best-effort — Windows refuses it for
        # background processes sometimes; the target usually still has focus.
        if not window_id:
            return
        try:
            ctypes.windll.user32.SetForegroundWindow(int(window_id))
        except Exception:  # noqa: BLE001
            pass

    # -- paste / typing ---------------------------------------------------

    def simulate_paste(self, window_id: str | None = None) -> bool:
        return _send_events(
            [
                _key_event(vk=_VK_CONTROL),
                _key_event(vk=_VK_V),
                _key_event(vk=_VK_V, flags=_KEYEVENTF_KEYUP),
                _key_event(vk=_VK_CONTROL, flags=_KEYEVENTF_KEYUP),
            ]
        )

    def _type_directly(self, text: str, window_id: str | None = None) -> bool:
        events = _text_events(text)
        ok = True
        for i in range(0, len(events), 512):  # keep SendInput batches sane
            ok = _send_events(events[i : i + 512]) and ok
        return ok

    # -- high-level: type text into the focused window ------------------

    def type_into_window(self, text: str, window_id: str | None = None) -> None:
        if window_id:
            self.focus_window(window_id)
            time.sleep(0.05)
        saved = self.clipboard_read()
        try:
            if self.clipboard_write(text):
                time.sleep(0.03)
                if self.simulate_paste():
                    logger.debug("Desktop: pasted via clipboard")
                    return
        finally:
            time.sleep(0.15)
            self.clipboard_restore(saved)
        if self._type_directly(text):
            logger.debug("Desktop: typed directly (slow path)")
            return
        logger.warning("Desktop: all typing methods failed")


if sys.platform == "win32":  # pragma: no cover — exercised on Windows only
    DesktopHelper = _WinDesktop  # noqa: F811 — deliberate platform override
