"""Windows global-shortcut backend.

Mirrors the evdev module's public surface — ``HotkeyListener``, ``HotkeyCapture``,
``check_access`` — so ``macaw.hotkey`` can re-export the right implementation per
platform. The listener uses ``RegisterHotKey`` (the OS does the matching, no
polling); capture uses a ``WH_KEYBOARD_LL`` hook so it sees the Win key and works
regardless of which window has focus — the same guarantee the evdev capture gives.

``ctypes.windll`` is only touched inside ``run()`` bodies, so this module imports
cleanly on any OS (keeps the VK tables unit-testable from Linux).
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

# Circular-safe: these names are all defined before hotkey.py imports us.
from macaw.hotkey import (
    _KEY_ECODE_NAMES,
    QThread,
    _CaptureState,
    parse_spec,
    pyqtSignal,
)

logger = logging.getLogger("macaw")

# -- Win32 constants ---------------------------------------------------------

_WM_QUIT = 0x0012
_WM_HOTKEY = 0x0312
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_SYSKEYDOWN = 0x0104  # Alt-modified keys arrive as SYSKEY events
_WM_SYSKEYUP = 0x0105
_WH_KEYBOARD_LL = 13
_VK_ESC = 0x1B

# RegisterHotKey modifier flags.
_MOD_FLAGS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "super": 0x8}
_MOD_NOREPEAT = 0x4000

# main-key token -> Windows virtual-key code. Mirrors hotkey._KEY_ECODE_NAMES —
# a parity test asserts every token has a VK here.
_KEY_VK: dict[str, int] = {
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "minus": 0xBD,  # VK_OEM_MINUS
    "equal": 0xBB,  # VK_OEM_PLUS
    "comma": 0xBC,  # VK_OEM_COMMA
    "dot": 0xBE,  # VK_OEM_PERIOD
    "slash": 0xBF,  # VK_OEM_2
    "semicolon": 0xBA,  # VK_OEM_1
    "grave": 0xC0,  # VK_OEM_3
}
_KEY_VK.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"})
_KEY_VK.update({d: ord(d) for d in "0123456789"})
_KEY_VK.update({f"f{n}": 0x6F + n for n in range(1, 13)})  # VK_F1 = 0x70

# VK -> our tokens (left/right-specific plus the generic codes).
_MOD_BY_VK = {
    0x10: "shift",
    0xA0: "shift",
    0xA1: "shift",
    0x11: "ctrl",
    0xA2: "ctrl",
    0xA3: "ctrl",
    0x12: "alt",
    0xA4: "alt",
    0xA5: "alt",
    0x5B: "super",  # VK_LWIN
    0x5C: "super",  # VK_RWIN
}
_KEY_BY_VK = {vk: tok for tok, vk in _KEY_VK.items()}

assert set(_KEY_VK) == set(_KEY_ECODE_NAMES), "VK map out of sync with hotkey tokens"


def check_access() -> tuple[bool, str]:
    """RegisterHotKey and LL hooks need no special privileges on Windows."""
    return True, ""


class HotkeyListener(QThread):
    """Registers `spec` as a system-wide hotkey and emits `triggered`.

    RegisterHotKey must run on the thread that pumps its messages, so both
    live in run(). Unlike the evdev listener this *grabs* the combo (the OS
    swallows it) — which is what users expect from a Windows hotkey.
    """

    triggered = pyqtSignal()

    def __init__(self, spec: str, parent=None) -> None:
        super().__init__(parent)
        self._spec = spec
        self._tid = 0
        self._stop = False

    def stop(self) -> None:
        self._stop = True
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, _WM_QUIT, 0, 0)

    def run(self) -> None:
        user32 = ctypes.windll.user32
        mods, key = parse_spec(self._spec)
        vk = _KEY_VK.get(key)
        if vk is None or not mods:
            logger.warning("Hotkey %r unusable; listener not started", self._spec)
            return
        flags = _MOD_NOREPEAT
        for m in mods:
            flags |= _MOD_FLAGS[m]
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, flags, vk):
            logger.warning(
                "Hotkey %r rejected — already registered by another app?", self._spec
            )
            return
        logger.info("Hotkey listening (%s) via RegisterHotKey", self._spec)
        try:
            msg = wintypes.MSG()
            while (
                not self._stop and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0
            ):
                if msg.message == _WM_HOTKEY:
                    self.triggered.emit()
        finally:
            user32.UnregisterHotKey(None, 1)


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class HotkeyCapture(QThread):
    """Captures the next combo via a low-level keyboard hook (sees the Win key,
    fires regardless of focus). Same signal surface as the evdev capture."""

    captured = pyqtSignal(str)  # canonical spec
    preview = pyqtSignal(str)  # modifiers held so far, e.g. "ctrl+super"
    failed = pyqtSignal(str)  # reason

    def __init__(self, timeout_s: float = 8.0, parent=None) -> None:
        super().__init__(parent)
        self._timeout = timeout_s
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        user32 = ctypes.windll.user32
        state = _CaptureState(_KEY_BY_VK, _MOD_BY_VK, _VK_ESC)
        outcome: list[tuple[str, str]] = []

        hookproc_t = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )

        def _proc(n_code: int, w_param: int, l_param: int) -> int:
            if n_code == 0 and not outcome:  # HC_ACTION
                kbd = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                value = 1 if w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN) else 0
                res = state.feed(int(kbd.vkCode), value)
                if res is not None:
                    kind, payload = res
                    if kind in ("capture", "cancel"):
                        outcome.append((kind, payload))
                        return 1  # swallow the deciding keypress
                    self.preview.emit(payload)
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        proc = hookproc_t(_proc)  # keep a reference — ctypes callbacks are GC-able
        hook = user32.SetWindowsHookExW(_WH_KEYBOARD_LL, proc, None, 0)
        if not hook:
            self.failed.emit("Couldn't install the keyboard hook.")
            return
        deadline = time.monotonic() + self._timeout
        msg = wintypes.MSG()
        try:
            # LL hooks are delivered while this thread pumps messages.
            while not self._stop and not outcome:
                if time.monotonic() > deadline:
                    self.failed.emit("Timed out — click and try again.")
                    return
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    pass  # nothing to dispatch — pumping alone drives the hook
                time.sleep(0.02)
        finally:
            user32.UnhookWindowsHookEx(hook)
        if outcome and outcome[0][0] == "capture":
            self.captured.emit(outcome[0][1])
