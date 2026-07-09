"""Windows (win64) port contracts, all runnable headless on Linux.

Run: uv run pytest tests/test_windows_port.py
Every test monkeypatches the platform switches (`sys.platform`, `os.name`)
that the port reads at call time; nothing here needs a real Windows host.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from macaw import _hotkey_win, desktop, hotkey, trigger
from macaw.stt import deps, isolated

# -- trigger._ipc_address ---------------------------------------------------


def test_ipc_address_win32_uses_loopback_tcp(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    # win32 wins even with XDG_RUNTIME_DIR set — no AF_UNIX on Windows.
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert trigger._ipc_address() == "tcp://127.0.0.1:47539"


def test_ipc_address_linux_prefers_xdg_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert trigger._ipc_address() == f"ipc://{tmp_path}/macaw.ipc"


def test_ipc_address_linux_falls_back_to_tmp(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert trigger._ipc_address() == "ipc:///tmp/macaw_service.ipc"


# -- stt.isolated.venv_python ------------------------------------------------


def _patch_os_name(monkeypatch, name):
    # Patching the global os.name would break pathlib itself (Path() picks
    # WindowsPath from os.name at call time); shim only isolated's binding.
    monkeypatch.setattr(
        isolated, "os", SimpleNamespace(name=name, environ=os.environ)
    )


def test_venv_python_windows_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    _patch_os_name(monkeypatch, "nt")
    expected = tmp_path / "macaw" / "backends" / "nemo" / "Scripts" / "python.exe"
    assert isolated.venv_python("nemo") == expected


def test_venv_python_posix_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    _patch_os_name(monkeypatch, "posix")
    expected = tmp_path / "macaw" / "backends" / "nemo" / "bin" / "python"
    assert isolated.venv_python("nemo") == expected


# -- stt.deps._find_uv --------------------------------------------------------


def test_find_uv_falls_back_to_bundled_next_to_executable(monkeypatch, tmp_path):
    # The frozen/zip layout: PATH lookup fails, uv sits beside the interpreter.
    monkeypatch.setattr(deps.shutil, "which", lambda _: None)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").touch()
    monkeypatch.setattr(deps.sys, "executable", str(fake_bin / "python"))
    assert deps._find_uv() == str(fake_bin / "uv")


# -- _hotkey_win (module imports cleanly on Linux; windll only inside run()) --


def test_win_key_map_has_parity_with_linux_tokens():
    assert set(_hotkey_win._KEY_VK) == set(hotkey._KEY_ECODE_NAMES)
    # No two tokens may share a VK, or the reverse map silently drops one.
    assert len(_hotkey_win._KEY_BY_VK) == len(_hotkey_win._KEY_VK)


def test_win_modifier_maps_cover_all_tokens():
    assert set(_hotkey_win._MOD_FLAGS) == set(hotkey._MOD_TOKENS)
    assert set(_hotkey_win._MOD_BY_VK.values()) == set(hotkey._MOD_TOKENS)


def test_win_check_access_never_blocks():
    # RegisterHotKey needs no privileges; the GUI must not show a perms nag.
    assert _hotkey_win.check_access() == (True, "")


# -- desktop auto-type detection ----------------------------------------------


def test_auto_type_available_short_circuits_on_win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    # SendInput is built in: True without probing for any injection tool.
    monkeypatch.setattr(desktop, "_has", lambda _: False)
    assert desktop.auto_type_available() is True


def test_auto_type_package_win32_needs_nothing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert desktop.auto_type_package() == ""


def test_auto_type_package_explicit_display_beats_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert desktop.auto_type_package("wayland") == "ydotool"
    assert desktop.auto_type_package("x11") == "xdotool"


# -- desktop._text_events (pure ctypes struct building, runs anywhere) --------

_UNICODE = 0x0004
_KEYUP = 0x0002
_VK_RETURN = 0x0D


def test_text_events_ascii_is_unicode_press_release_pair():
    down, up = desktop._text_events("a")
    assert (down.ki.wVk, down.ki.wScan, down.ki.dwFlags) == (0, ord("a"), _UNICODE)
    assert (up.ki.wVk, up.ki.wScan, up.ki.dwFlags) == (0, ord("a"), _UNICODE | _KEYUP)


def test_text_events_newline_is_vk_return():
    down, up = desktop._text_events("\n")
    assert (down.ki.wVk, down.ki.wScan, down.ki.dwFlags) == (_VK_RETURN, 0, 0)
    assert (up.ki.wVk, up.ki.wScan, up.ki.dwFlags) == (_VK_RETURN, 0, _KEYUP)


def test_text_events_carriage_return_dropped():
    assert desktop._text_events("\r") == []
    # CRLF collapses to a single Enter press/release pair.
    assert [e.ki.wVk for e in desktop._text_events("\r\n")] == [_VK_RETURN, _VK_RETURN]


def test_text_events_emoji_sends_surrogate_pair():
    events = desktop._text_events("\U0001f99c")  # 🦜
    assert len(events) == 4
    assert all(e.ki.wVk == 0 for e in events)
    # U+1F99C as UTF-16: high then low surrogate, each press+release.
    assert [e.ki.wScan for e in events] == [0xD83E, 0xD83E, 0xDD9C, 0xDD9C]
    assert [e.ki.dwFlags for e in events] == [
        _UNICODE,
        _UNICODE | _KEYUP,
        _UNICODE,
        _UNICODE | _KEYUP,
    ]
