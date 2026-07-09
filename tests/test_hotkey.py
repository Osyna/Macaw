"""Global-shortcut feature checks. Run: python tests/test_hotkey.py

Headless: no real input devices, no network. evdev is only imported inside the
tests that need it (it lives in the dev group); the capture widget runs on the
offscreen platform and is driven through its handler methods, not real events.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from macaw.hotkey import (
    _CaptureState,
    _Matcher,
    _reverse_maps,
    format_spec,
    is_valid,
    parse_spec,
    pretty,
    resolve_spec,
)

app = QApplication.instance() or QApplication([])


# -- spec helpers ---------------------------------------------------------


def test_parse_spec_is_order_insensitive_and_lowercased():
    mods, key = parse_spec("Alt+Ctrl+space")
    assert mods == frozenset({"ctrl", "alt"})
    assert key == "space"
    # Same tokens, any order → same parse.
    assert parse_spec("space+alt+ctrl") == (frozenset({"ctrl", "alt"}), "space")


def test_format_spec_uses_canonical_modifier_order():
    # Order is ctrl, alt, shift, super regardless of input set order.
    assert format_spec({"super", "ctrl"}, "k") == "ctrl+super+k"
    got = format_spec({"shift", "super", "alt", "ctrl"}, "a")
    assert got == "ctrl+alt+shift+super+a"


def test_parse_then_format_round_trips_to_canonical():
    assert format_spec(*parse_spec("alt+ctrl+space")) == "ctrl+alt+space"


def test_pretty_labels_and_empty():
    assert pretty("ctrl+alt+space") == "Ctrl + Alt + Space"
    assert pretty("") == ""


def test_is_valid_requires_modifier_and_known_key():
    # The safety gate: a bare key would fire mid-typing, so reject it.
    assert is_valid("ctrl+space") is True
    assert is_valid("space") is False  # no modifier
    assert is_valid("ctrl") is False  # no main key
    assert is_valid("") is False  # empty
    assert is_valid("ctrl+zzz") is False  # unknown main key


# -- _Matcher edge-trigger semantics (fake integer codes, no evdev) -------


def test_matcher_does_not_fire_without_modifier():
    m = _Matcher(100, [{10, 11}])
    assert m.feed(100, 1) is False


def test_matcher_fires_when_modifier_then_key_pressed():
    m = _Matcher(100, [{10, 11}])
    assert m.feed(10, 1) is False  # modifier down, not the main key
    assert m.feed(100, 1) is True  # main key press with modifier held


def test_matcher_ignores_autorepeat():
    m = _Matcher(100, [{10, 11}])
    m.feed(10, 1)
    assert m.feed(100, 1) is True  # first press fires
    assert m.feed(100, 2) is False  # autorepeat must not re-fire


def test_matcher_needs_modifier_still_held_on_repress():
    m = _Matcher(100, [{10, 11}])
    m.feed(10, 1)
    assert m.feed(100, 1) is True
    m.feed(10, 0)  # release the modifier
    assert m.feed(100, 1) is False  # re-press with nothing held → no fire


def test_matcher_either_side_arms_the_group():
    m = _Matcher(100, [{10, 11}])
    assert m.feed(11, 1) is False  # right-side member of the same group
    assert m.feed(100, 1) is True


def test_matcher_requires_every_group_held():
    # Two independent groups → AND: firing needs a member of each.
    m = _Matcher(100, [{10}, {20}])
    m.feed(10, 1)
    assert m.feed(100, 1) is False  # second group not satisfied
    m.feed(20, 1)
    assert m.feed(100, 1) is True  # both groups now held


# -- resolve_spec (evdev token → ecode mapping) ---------------------------


def test_resolve_spec_maps_tokens_to_ecodes():
    from evdev import ecodes

    key_code, groups = resolve_spec("ctrl+space")
    assert key_code == ecodes.KEY_SPACE
    assert groups == [{ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL}]


def test_resolve_spec_without_modifier_has_empty_groups():
    from evdev import ecodes

    assert resolve_spec("space") == (ecodes.KEY_SPACE, [])


def test_resolve_spec_rejects_unknown_key():
    assert resolve_spec("ctrl+zzz") is None


# -- _CaptureState edge semantics (fake integer codes, no evdev) ----------


def test_capture_state_ignores_bare_key_but_captures_super_combo():
    # The Wayland fix: a bare main key must be ignored (it would otherwise
    # commit mid-typing), while Super — a real modifier here — then a key must
    # commit. Codes are arbitrary fakes; no evdev needed.
    st = _CaptureState({50: "k"}, {100: "super", 101: "super"}, 1)
    assert st.feed(50, 1) is None  # bare key: ignored
    assert st.feed(100, 1) == ("preview", "super")  # modifier down
    assert st.feed(50, 1) == ("capture", "super+k")  # combo commits


def test_capture_state_emits_modifiers_in_canonical_order():
    st = _CaptureState({50: "k"}, {100: "super", 200: "ctrl"}, 1)
    st.feed(100, 1)  # super down first...
    st.feed(200, 1)  # ...then ctrl
    assert st.feed(50, 1) == ("capture", "ctrl+super+k")


def test_capture_state_escape_alone_cancels():
    # esc code = 1; pressed with nothing held -> cancel.
    st = _CaptureState({50: "k"}, {100: "super"}, 1)
    assert st.feed(1, 1) == ("cancel", "")


def test_capture_state_modifier_release_previews_remainder():
    st = _CaptureState({50: "k"}, {100: "super", 200: "ctrl"}, 1)
    st.feed(200, 1)
    st.feed(100, 1)
    assert st.feed(100, 0) == ("preview", "ctrl")  # super released


# -- _reverse_maps (evdev ecode int -> our token) -------------------------


def test_reverse_maps_covers_meta_space_and_ctrl():
    from evdev import ecodes

    key_by_code, mod_by_code = _reverse_maps()
    assert key_by_code[ecodes.KEY_SPACE] == "space"
    assert mod_by_code[ecodes.KEY_LEFTMETA] == "super"  # Super -> super
    assert mod_by_code[ecodes.KEY_RIGHTMETA] == "super"
    assert mod_by_code[ecodes.KEY_LEFTCTRL] == "ctrl"


# -- ShortcutCapture widget, driven through its handlers (offscreen) -------


def test_widget_set_spec_renders_pretty_super_label():
    from macaw.gui.shortcut import ShortcutCapture

    w = ShortcutCapture()
    w.set_spec("ctrl+super+space")
    assert w.spec() == "ctrl+super+space"
    # Field shows the pretty form; capital 'Super' proves pretty() was applied
    # (the raw spec only carries lowercase 'super').
    assert "Super" in w._field.text()


def test_widget_preview_shows_held_modifiers():
    from macaw.gui.shortcut import ShortcutCapture

    w = ShortcutCapture()
    w._on_preview("ctrl+super")
    assert "Ctrl + Super" in w._field.text()


def test_widget_captured_then_finished_commits_and_emits():
    from macaw.gui.shortcut import ShortcutCapture

    w = ShortcutCapture()
    emitted: list[str] = []
    w.changed.connect(emitted.append)

    w._on_captured("super+k")
    w._on_finished()

    assert w.spec() == "super+k"
    assert emitted == ["super+k"]


def test_capture_clear_empties_spec_and_emits():
    from macaw.gui.shortcut import ShortcutCapture

    w = ShortcutCapture()
    w.set_spec("ctrl+alt+space")
    emitted: list[str] = []
    w.changed.connect(emitted.append)

    w._do_clear()

    assert w.spec() == ""
    assert emitted == [""]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
