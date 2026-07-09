"""Regressions for two behavioural changes.

Feature A — language stopped being a single global Setting and became a
per-model choice: models declare a `lang_select` capability, the service
resolves the active language from the per-model map (falling back to the
global default, then "en"), the global Language combo was removed from
Settings, and the per-model card shows a language chooser *only* for
`lang_select` models, persisting the pick into `model_languages`.

Feature B — a one-time "star us on GitHub" nudge: `_maybe_prompt_star`
schedules the modal exactly once per session and never re-nags once
`star_prompted` has been persisted.

These test observable contracts, not defaults: e.g. the default-False of
`star_prompted` is exercised through the scheduling behaviour it drives, not
asserted as a literal.

Run: uv run pytest tests/test_language_and_star.py -q   (QT_QPA_PLATFORM=offscreen)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QComboBox, QPushButton

from macaw.config import Config
from macaw.gui.main_window import MainWindow, StarToast
from macaw.service import _lang_for
from macaw.stt import get_model_info

app = QApplication.instance() or QApplication([])


def _fresh_config(**overrides) -> Path:
    p = Path(tempfile.mkdtemp(prefix="macaw_test_")) / "config.yaml"
    Config(**overrides).save(p)
    return p


def _find(layout, kind):
    """Recursively collect widgets of `kind` across nested layouts."""
    out = []
    for i in range(layout.count()):
        it = layout.itemAt(i)
        w = it.widget()
        if isinstance(w, kind):
            out.append(w)
        if it.layout() is not None:
            out += _find(it.layout(), kind)
    return out


# -- Feature A: capability + resolution ---------------------------------------


def test_lang_select_is_a_per_model_capability():
    # Multilingual models opt in; English-only variants opt out — a flipped
    # flag would wrongly show/hide the per-model language chooser.
    cases = {
        "large-v3-turbo": True,  # whisper multilingual
        "distil-large-v3": False,  # whisper EN-only override
        "nvidia/parakeet-tdt-0.6b-v3": True,  # parakeet 25-lang
        "nvidia/parakeet-tdt-0.6b-v2": False,  # parakeet EN-only
    }
    for model_id, expected in cases.items():
        assert get_model_info(model_id).lang_select is expected, model_id


def test_lang_for_prefers_per_model_then_global_then_en():
    # per-model choice wins over the global default
    assert _lang_for(Config(model="m", model_languages={"m": "fr"})) == "fr"
    # no per-model entry -> fall back to the global `language`
    assert _lang_for(Config(model="m", language="de")) == "de"
    # nothing set anywhere -> "en"
    assert _lang_for(Config(model="", language="")) == "en"


# -- Feature A + B: config persistence ----------------------------------------


def test_new_config_fields_round_trip_through_save_load():
    # The per-model language map and the star flag are new fields; if _render
    # or load dropped them the per-model choice would be forgotten and the
    # nudge would re-nag every launch.
    p = _fresh_config(model_languages={"x": "fr"}, star_prompted=True)
    loaded = Config.load(p)
    assert loaded.model_languages == {"x": "fr"}
    assert loaded.star_prompted is True


# -- Feature A: Settings + per-model card -------------------------------------


def test_settings_has_no_global_language_combo():
    # The global "Language" control was removed from Settings; re-adding it
    # (self.lang_combo) would resurrect the abandoned global setting.
    mw = MainWindow(_fresh_config())
    assert not hasattr(mw.settings, "lang_combo")


def test_language_combo_shown_only_for_lang_select_models():
    mw = MainWindow(_fresh_config())

    mw.models._show_detail("large-v3-turbo")
    combos = _find(mw.models.detail, QComboBox)
    assert any(c.findData("fr") >= 0 for c in combos), (
        "multilingual model card is missing its language chooser"
    )

    mw.models._show_detail("distil-large-v3")
    combos = _find(mw.models.detail, QComboBox)
    assert not any(c.findData("fr") >= 0 for c in combos), (
        "EN-only model card should not offer a language chooser"
    )


def test_save_language_persists_per_model_choice():
    p = _fresh_config()
    mw = MainWindow(p)
    mw.models._save_language("large-v3-turbo", "fr")
    assert Config.load(p).model_languages["large-v3-turbo"] == "fr"


# -- Feature B: one-time star nudge -------------------------------------------


def test_star_prompt_scheduled_once_and_respects_prior_prompt():
    # Fresh config: the nudge has not been shown, so the first prompt schedules
    # it (a QTimer we deliberately never run). The flag flip is the observable
    # contract; it also proves star_prompted defaults to un-prompted.
    mw = MainWindow(_fresh_config())
    assert mw._star_scheduled is False
    mw._maybe_prompt_star()
    assert mw._star_scheduled is True

    # Already prompted in a prior session: a fresh window must not re-nag.
    already = MainWindow(_fresh_config(star_prompted=True))
    already._maybe_prompt_star()
    assert already._star_scheduled is False


def test_star_toast_builds_non_modal_with_github_button():
    # The nudge became a corner QFrame, not a blocking QDialog. Two contracts:
    # it carries the "Star on GitHub" call-to-action, and it is NOT modal (a
    # regression to setModal(True)/QDialog would freeze the rest of Settings).
    mw = MainWindow(_fresh_config())
    toast = StarToast(mw)  # a real parent, never .exec() — QFrame has no event loop
    buttons = toast.findChildren(QPushButton)
    assert any("Star on GitHub" in b.text() for b in buttons)
    assert toast.isModal() is False


def test_show_star_toasts_over_visible_window_and_persists_flag():
    # _show_star only fires for a visible window: it parents the toast to the
    # window, shows it, flips star_prompted so it never re-nags, and the toast
    # dies with the window (hideEvent closes it).
    p = _fresh_config()
    mw = MainWindow(p)
    mw.show()
    mw._show_star()
    toast = mw._star_toast
    assert isinstance(toast, StarToast)
    assert toast.parent() is mw
    assert toast.isVisible()
    assert Config.load(p).star_prompted is True
    mw.hide()
    assert not toast.isVisible()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
