"""Global shortcut support.

Linux: reading the kernel input layer (``/dev/input/event*``) works identically
on X11 and on every Wayland compositor, so this is the one hotkey mechanism that
is portable across Linux desktops. It needs read access to input devices — i.e.
membership in the ``input`` group (the same access ydotool already requires).
The listener only *monitors*; it never grabs the keys, so the combo still
reaches whatever else is listening.

Windows: ``macaw._hotkey_win`` provides the same classes via RegisterHotKey and
a low-level keyboard hook; the re-export at the bottom picks the platform.

Spec strings are canonical, e.g. ``"ctrl+alt+space"`` — modifiers plus one main
key. Parse/format/validate helpers below are pure and shared by both backends.
"""

from __future__ import annotations

import logging
import sys
import threading


class _BoundSignal:
    """Qt-free signal. Callbacks run synchronously on the EMITTING thread —
    no event-loop marshalling; bridge into your loop if you need affinity."""

    def __init__(self) -> None:
        self._fns: list = []

    def connect(self, fn) -> None:
        self._fns.append(fn)

    def disconnect(self, fn) -> None:
        self._fns.remove(fn)

    def emit(self, *args) -> None:
        for fn in list(self._fns):
            fn(*args)


class pyqtSignal:
    """Descriptor mimicking PyQt's class-level signal declaration (per-instance)."""

    def __init__(self, *_types) -> None:
        self._attr = ""

    def __set_name__(self, owner, name) -> None:
        self._attr = f"_signal_{name}"

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        sig = obj.__dict__.get(self._attr)
        if sig is None:
            sig = _BoundSignal()
            obj.__dict__[self._attr] = sig
        return sig


class QThread(threading.Thread):
    """Minimal stand-in for the QThread surface the listeners use."""

    def __init__(self, parent=None) -> None:
        super().__init__(daemon=True)

    def wait(self, ms: float | None = None) -> None:
        self.join(None if ms is None else ms / 1000)

    def isRunning(self) -> bool:
        return self.is_alive()


logger = logging.getLogger("macaw")

# Modifier tokens in canonical (display + serialisation) order.
_MOD_TOKENS = ("ctrl", "alt", "shift", "super")

# token -> evdev ecode NAMES that satisfy it (either side of the keyboard).
_MOD_ECODE_NAMES = {
    "ctrl": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "alt": ("KEY_LEFTALT", "KEY_RIGHTALT"),
    "shift": ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"),
    "super": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
}

# main-key token -> evdev ecode NAME. Data only, so parse/format/validate work
# without evdev installed; names resolve to integer codes lazily in the listener.
_KEY_ECODE_NAMES: dict[str, str] = {
    "space": "KEY_SPACE",
    "enter": "KEY_ENTER",
    "tab": "KEY_TAB",
    "esc": "KEY_ESC",
    "backspace": "KEY_BACKSPACE",
    "delete": "KEY_DELETE",
    "insert": "KEY_INSERT",
    "home": "KEY_HOME",
    "end": "KEY_END",
    "pageup": "KEY_PAGEUP",
    "pagedown": "KEY_PAGEDOWN",
    "up": "KEY_UP",
    "down": "KEY_DOWN",
    "left": "KEY_LEFT",
    "right": "KEY_RIGHT",
    "minus": "KEY_MINUS",
    "equal": "KEY_EQUAL",
    "comma": "KEY_COMMA",
    "dot": "KEY_DOT",
    "slash": "KEY_SLASH",
    "semicolon": "KEY_SEMICOLON",
    "grave": "KEY_GRAVE",
}
_KEY_ECODE_NAMES.update({c: f"KEY_{c.upper()}" for c in "abcdefghijklmnopqrstuvwxyz"})
_KEY_ECODE_NAMES.update({d: f"KEY_{d}" for d in "0123456789"})
_KEY_ECODE_NAMES.update({f"f{n}": f"KEY_F{n}" for n in range(1, 13)})

_PRETTY = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "super": "Super",
    "space": "Space",
    "enter": "Enter",
    "tab": "Tab",
    "esc": "Esc",
    "backspace": "Backspace",
    "delete": "Delete",
    "insert": "Insert",
    "pageup": "PgUp",
    "pagedown": "PgDn",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
}


def parse_spec(spec: str) -> tuple[frozenset[str], str]:
    """'ctrl+alt+space' -> (frozenset{'ctrl','alt'}, 'space'). Order-insensitive."""
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    mods = frozenset(p for p in parts if p in _MOD_TOKENS)
    keys = [p for p in parts if p not in _MOD_TOKENS]
    return mods, (keys[-1] if keys else "")


def format_spec(mods, key: str) -> str:
    """Canonical string: modifiers in fixed order, then the main key."""
    ordered = [m for m in _MOD_TOKENS if m in mods]
    return "+".join(ordered + ([key] if key else []))


def pretty(spec: str) -> str:
    """Human label, e.g. 'Ctrl + Alt + Space' ('' for an empty/unset spec)."""
    mods, key = parse_spec(spec)
    parts = [_PRETTY[m] for m in _MOD_TOKENS if m in mods]
    if key:
        parts.append(_PRETTY.get(key, key.upper()))
    return " + ".join(parts)


def is_valid(spec: str) -> bool:
    """Usable global shortcut: a known main key plus >=1 modifier (a bare key
    would fire during normal typing)."""
    mods, key = parse_spec(spec)
    return bool(mods) and key in _KEY_ECODE_NAMES


class _Matcher:
    """Edge-triggered combo detector over a (code, value) key-event stream.
    Pure integer logic — no evdev — so it is unit-testable."""

    def __init__(self, key_code: int, mod_groups: list[set[int]]) -> None:
        self._key = key_code
        self._groups = mod_groups
        self._down: set[int] = set()

    def feed(self, code: int, value: int) -> bool:
        if value == 1:  # press (value 2 = autorepeat, ignored → one trigger per press)
            self._down.add(code)
            if code == self._key and all(self._down & g for g in self._groups):
                return True
        elif value == 0:  # release
            self._down.discard(code)
        return False


def resolve_spec(spec: str):
    """(key_code, mod_groups) using evdev ecodes, or None if unusable."""
    from evdev import ecodes

    mods, key = parse_spec(spec)
    name = _KEY_ECODE_NAMES.get(key)
    if not name or name not in ecodes.ecodes:
        return None
    groups: list[set[int]] = []
    for m in mods:
        g = {ecodes.ecodes[n] for n in _MOD_ECODE_NAMES[m] if n in ecodes.ecodes}
        if not g:
            return None
        groups.append(g)
    return ecodes.ecodes[name], groups


def check_access() -> tuple[bool, str]:
    """(ok, reason). ok=True means the listener can run right now."""
    try:
        import evdev
    except Exception:  # noqa: BLE001
        return False, "evdev isn't installed — reinstall macaw with hotkey support."
    try:
        paths = evdev.list_devices()
    except Exception as exc:  # noqa: BLE001
        return False, f"Can't list input devices: {exc}"
    for p in paths:
        try:
            evdev.InputDevice(p).close()
            return True, ""
        except PermissionError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return False, (
        "No access to input devices. Add your user to the 'input' group, "
        "then log out and back in."
    )


def _open_keyboards() -> list:
    """Open every device exposing letter keys (i.e. an actual keyboard)."""
    import evdev
    from evdev import ecodes

    devs = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except Exception:  # noqa: BLE001
            continue
        keys = d.capabilities().get(ecodes.EV_KEY, [])
        if ecodes.KEY_A in keys and ecodes.KEY_Z in keys:
            devs.append(d)
        else:
            d.close()
    return devs


class HotkeyListener(QThread):
    """Watches all keyboards for `spec` via evdev and emits `triggered`.

    Compositor-agnostic (reads the kernel input layer). Monitor-only — it never
    grabs the keys. Runs in its own thread; `triggered` is delivered on the UI
    thread by Qt's queued connection.
    """

    triggered = pyqtSignal()

    def __init__(self, spec: str, parent=None) -> None:
        super().__init__(parent)
        self._spec = spec
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        import select

        from evdev import ecodes

        try:
            resolved = resolve_spec(self._spec)
        except Exception:  # noqa: BLE001
            resolved = None
        if resolved is None:
            logger.warning("Hotkey %r unusable; listener not started", self._spec)
            return
        key_code, mod_groups = resolved
        try:
            devices = _open_keyboards()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Hotkey: cannot open input devices (%s)", exc)
            return
        if not devices:
            logger.warning("Hotkey: no readable keyboard devices")
            return
        matcher = _Matcher(key_code, mod_groups)
        fd_map = {d.fd: d for d in devices}
        logger.info("Hotkey listening (%s) on %d device(s)", self._spec, len(devices))
        try:
            while not self._stop:
                r, _, _ = select.select(list(fd_map), [], [], 0.5)
                for fd in r:
                    try:
                        for ev in fd_map[fd].read():
                            if ev.type == ecodes.EV_KEY and matcher.feed(
                                ev.code, ev.value
                            ):
                                self.triggered.emit()
                    except OSError:
                        fd_map.pop(fd, None)  # device disconnected
                if not fd_map:
                    break
        finally:
            for d in devices:
                try:
                    d.close()
                except Exception:  # noqa: BLE001
                    pass


def _reverse_maps():
    """(key_by_code, mod_by_code): evdev ecode int -> our token. Needs evdev."""
    from evdev import ecodes

    key_by_code: dict[int, str] = {}
    for token, name in _KEY_ECODE_NAMES.items():
        code = ecodes.ecodes.get(name)
        if isinstance(code, int):
            key_by_code[code] = token
    mod_by_code: dict[int, str] = {}
    for token, names in _MOD_ECODE_NAMES.items():
        for name in names:
            code = ecodes.ecodes.get(name)
            if isinstance(code, int):
                mod_by_code[code] = token
    return key_by_code, mod_by_code


class _CaptureState:
    """Turns a raw key-event stream into a spec while capturing. Pure/testable.

    feed(code, value) -> ('capture', spec) | ('preview', mods) | ('cancel', '') | None
    """

    def __init__(self, key_by_code, mod_by_code, esc_code) -> None:
        self._keys = key_by_code
        self._mods = mod_by_code
        self._esc = esc_code
        self._held: set[str] = set()

    def feed(self, code: int, value: int):
        if value == 1:  # press
            if self._esc is not None and code == self._esc and not self._held:
                return ("cancel", "")
            if code in self._mods:
                self._held.add(self._mods[code])
                return ("preview", format_spec(self._held, ""))
            if code in self._keys and self._held:
                return ("capture", format_spec(self._held, self._keys[code]))
        elif value == 0 and code in self._mods:  # release
            self._held.discard(self._mods[code])
            return ("preview", format_spec(self._held, ""))
        return None


class HotkeyCapture(QThread):
    """Captures the next combo straight from evdev, so it sees Super and every
    other key even on Wayland/Hyprland where the compositor grabs them before Qt
    ever gets them. Capturing this way means what you set is exactly what the
    listener matches.
    """

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
        import select
        import time

        try:
            from evdev import ecodes
        except Exception:  # noqa: BLE001
            self.failed.emit("evdev isn't installed.")
            return
        try:
            key_by_code, mod_by_code = _reverse_maps()
            devices = _open_keyboards()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        if not devices:
            self.failed.emit("No input access — add your user to the 'input' group.")
            return
        esc = ecodes.ecodes.get("KEY_ESC")
        esc = esc if isinstance(esc, int) else None
        state = _CaptureState(key_by_code, mod_by_code, esc)
        fd_map = {d.fd: d for d in devices}
        deadline = time.monotonic() + self._timeout
        try:
            while not self._stop:
                if time.monotonic() > deadline:
                    self.failed.emit("Timed out — click and try again.")
                    return
                r, _, _ = select.select(list(fd_map), [], [], 0.3)
                for fd in r:
                    try:
                        events = list(fd_map[fd].read())
                    except OSError:
                        fd_map.pop(fd, None)
                        continue
                    for ev in events:
                        if ev.type != ecodes.EV_KEY:
                            continue
                        res = state.feed(ev.code, ev.value)
                        if res is None:
                            continue
                        if res[0] == "capture":
                            self.captured.emit(res[1])
                            return
                        if res[0] == "cancel":
                            return
                        self.preview.emit(res[1])
        finally:
            for d in devices:
                try:
                    d.close()
                except Exception:  # noqa: BLE001
                    pass


if sys.platform == "win32":  # pragma: no cover — exercised on Windows only
    pass
