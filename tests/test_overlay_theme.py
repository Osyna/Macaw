"""Theme split + overlay placement + config round-trip — regression guards.

- The app chrome (`active_theme`) is driven by `app_theme`; the recording
  indicator (`active_indicator`) is driven by `theme`. They must stay
  decoupled (a past regression re-coupled the indicator to the chrome).
- `RecordingWindow._target_xy()` returns exact custom coords, and derives
  presets from the screen + padding.
- `Config` persists the overlay coords / placement / app_theme across a
  save→load cycle.

Run: python tests/test_overlay_theme.py   (or `uv run pytest`)
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import macaw.config as config_mod
import macaw.service as service_mod
from macaw.config import Config
from macaw.gui import theme
from macaw.gui.window import RecordingWindow

app = QApplication.instance() or QApplication([])


@contextmanager
def _default_config(cfg: Config):
    """Point `active_theme`/`active_indicator` (which call `Config.load()` with
    no path) at a temp config, then restore. Order-independent."""
    p = Path(tempfile.mkdtemp(prefix="macaw_test_")) / "config.yaml"
    cfg.save(p)
    orig = config_mod._DEFAULT_CONFIG_PATH
    config_mod._DEFAULT_CONFIG_PATH = p
    try:
        yield
    finally:
        config_mod._DEFAULT_CONFIG_PATH = orig


def test_active_theme_is_chrome_independent_of_indicator():
    # Chrome follows app_theme only: a "light" *indicator* theme must not drag
    # the chrome to light, and app_theme="light" must select the light chrome.
    with _default_config(Config(theme="light", app_theme="dark")):
        assert theme.active_theme().name == "oled"
    with _default_config(Config(theme="oled", app_theme="light")):
        assert theme.active_theme().name == "light"


def test_active_indicator_follows_theme_not_app_theme():
    # Indicator follows `theme` only: app_theme must not steer it.
    with _default_config(Config(theme="light", app_theme="dark")):
        assert theme.active_indicator().name == "light"
    with _default_config(Config(theme="oled", app_theme="light")):
        assert theme.active_indicator().name == "oled"


def test_custom_overlay_position_exact():
    w = RecordingWindow(210, 52)
    w.position("custom", custom=(1234, 567))
    assert w._target_xy() == (1234, 567)


def test_preset_position_derives_from_screen():
    w = RecordingWindow(210, 52)
    w.position("top_left", padding=20)
    assert w._target_xy() == (20, 20)


def test_config_roundtrips_overlay_and_app_theme():
    p = Path(tempfile.mkdtemp(prefix="macaw_test_")) / "config.yaml"
    Config(
        app_theme="light", overlay_x=77, overlay_y=88, window_position="custom"
    ).save(p)
    c = Config.load(p)
    assert c.app_theme == "light"
    assert c.overlay_x == 77
    assert c.overlay_y == 88
    assert c.window_position == "custom"


def test_apply_look_updates_theme_and_size_live():
    # No restart: apply_look() mutates the existing widget in place — the new
    # indicator theme and size take effect on the live window.
    w = RecordingWindow(210, 52)
    w.apply_look(theme.THEMES["light"], 400, 120)
    assert w._theme.name == "light"
    assert (w.width(), w.height()) == (400, 120)


def test_indicator_fields_apply_live_and_chrome_restarts():
    # Only app chrome restarts; every indicator field applies live. Assert on
    # the class attrs (MacawService needs tray/audio — never instantiate it).
    svc = service_mod.MacawService
    chrome, indicator, look = set(svc._CHROME), set(svc._INDICATOR), set(svc._LOOK)
    assert svc._CHROME == ("app_theme",)
    assert indicator == {
        "theme",
        "overlay_opacity",
        "overlay_width",
        "overlay_height",
        "eq_colors",
        "accent_color",
        "border_width",
        "border_color",
        "corner_radius",
        "corners",
        "corner_link",
        "bar_spacing",
        "bar_width",
        "bar_radius",
        "bar_fade",
    }
    assert chrome.isdisjoint(indicator)
    assert look == chrome | indicator
    # Teeth: app_theme is chrome-only (a restart field), the indicator `theme`
    # is indicator-only (applies live) — neither may drift into the other set.
    assert "app_theme" in chrome and "app_theme" not in indicator
    assert "theme" in indicator and "theme" not in chrome


def test_per_corner_change_applies_live():
    # Regression: in UNLINKED mode a per-corner edit keeps corner_radius=-1, so
    # unless `corners`/`corner_link` are in _INDICATOR the live overlay never
    # repaints (only the Settings preview did). Lock both into the live set.
    svc = service_mod.MacawService
    assert "corners" in svc._INDICATOR and "corner_link" in svc._INDICATOR
    # Teeth: two configs differing ONLY in corners (same corner_radius=-1) must
    # register as an indicator change — the shape edit is detected, not ignored.
    a = Config(model="", corners=[], corner_link=True)
    b = Config(model="", corners=[28, 0, 28, 3], corner_link=False)
    assert a.corner_radius == b.corner_radius == -1
    assert any(getattr(a, f) != getattr(b, f) for f in svc._INDICATOR)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
