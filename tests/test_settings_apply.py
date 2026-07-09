"""Apply in Settings must not restart the service — regression guard.

Regression: clicking Apply closed the Settings window. `SettingsTab._save`
promoted theme-derived colour fields (eq_colors/accent_color/border_color) to
explicit values even when the user never touched them, so they differed from
the stored empties ([]/""). The service treats any change to a field in
`MacawService._LOOK` as an appearance change and restarts the process
(closing the window). Fixed by persisting those fields only when the user
actually picked a colour.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from macaw.config import Config
from macaw.gui.settings_tab import SettingsTab
from macaw.service import MacawService

app = QApplication.instance() or QApplication([])


def _fresh_default_config() -> Path:
    p = Path(tempfile.mkdtemp(prefix="macaw_test_")) / "config.yaml"
    Config().save(p)
    return p


def test_apply_does_not_touch_look_fields():
    p = _fresh_default_config()
    before = Config.load(p)

    t = SettingsTab(p)
    t._load_current()

    # An Apply that only touches non-appearance controls — the exact case that
    # used to (wrongly) restart the service and close the window.
    t.sound_toggle.setChecked(not t.sound_toggle.isChecked())
    t.hotkey_toggle.setChecked(True)
    t.shortcut_capture.set_spec("ctrl+alt+space")

    t._save()
    after = Config.load(p)

    # Every restart-triggering field must be unchanged, else _on_config_changed
    # would restart the process. Before the fix, eq_colors/accent_color/
    # border_color get promoted from the theme and this assertion fails.
    for f in MacawService._LOOK:
        assert getattr(after, f) == getattr(before, f), (
            f"{f} changed on Apply: {getattr(before, f)!r} -> {getattr(after, f)!r}"
        )

    # The non-appearance change was actually persisted (proves _save ran).
    assert after.sound_enabled == (not before.sound_enabled)
    assert after.hotkey_enabled is True
    assert after.hotkey == "ctrl+alt+space"


def test_user_picked_colour_is_saved():
    p = _fresh_default_config()

    t = SettingsTab(p)
    t._load_current()

    t.icon_swatch.setColor("#abcdef")
    t._on_accent_picked("#abcdef")
    t._save()

    assert Config.load(p).accent_color == "#abcdef"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
