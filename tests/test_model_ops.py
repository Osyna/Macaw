"""Regressions for model-ops fixes: subprocess worker lifecycle, the curated
catalog ratings/specs, and the read-only star-rating widget.
Run: python tests/test_model_ops.py

Guards, in order:
  1. SubprocessBackend.unload() reaps the worker (no zombie) — the footprint fix;
  2. SubprocessBackend._read_message() tolerates a killed/None proc — cancel safety;
  3. list_models() ratings are curated 1..5 with known picks pinned;
  4. catalog integrity — unique labels (Parakeet dedup) + every model has specs;
  5. _StarRating renders N filled / 5-N empty stars, read-only (no rated signal);
  6. the detail card shows the Minimal/Recommended system rows + a filled star.
  7. Config round-trips per-corner radii + the link flag through save()/load();
  8. active_indicator() corner precedence: unlinked 4-tuple > uniform radius > theme;
  9. the corner link/unlink toggle collapses vs. frees corners and _save() persists
     each mode (uniform radius / explicit tuple), reloading to the same shape;
 10. the Models tab header advertises the live model count — "Choose a model  (N)".
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel

from macaw.config import Config
from macaw.gui.main_window import MainWindow
from macaw.gui.models_tab import _StarRating
from macaw.gui.theme import THEMES, active_indicator
from macaw.stt import get_model_info, list_models
from macaw.stt.base import ModelInfo
from macaw.stt.isolated import SubprocessBackend

app = QApplication.instance() or QApplication([])


class _TestBackend(SubprocessBackend):
    key = "test"


def _backend() -> _TestBackend:
    info = ModelInfo(
        id="x",
        backend="test",
        label="X",
        size="-",
        speed="-",
        languages="-",
        extra="test",
    )
    return _TestBackend(info)


def test_unload_reaps_worker_no_zombie():
    # unload() must terminate AND wait() the worker; a bare terminate() would
    # leave a zombie in the process table (os.kill(pid, 0) would then succeed).
    b = _backend()
    b._proc = subprocess.Popen(
        ["sleep", "30"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    pid = b._proc.pid

    b.unload()
    assert b._proc is None

    time.sleep(0.2)  # give the kernel a beat to drop the reaped entry
    reaped = False
    try:
        os.kill(pid, 0)  # zombie or alive -> succeeds; fully reaped -> raises
    except ProcessLookupError:
        reaped = True
    assert reaped, "worker still in the process table — not reaped (zombie)"


def test_read_message_handles_dead_proc():
    # A cancelled/killed load leaves _proc None; reading must fail fast with the
    # sentinel instead of raising, so the caller can surface a clean error.
    b = _backend()
    b._proc = None
    assert b._read_message() == {"error": "worker exited"}


def test_catalog_ratings():
    # Ratings are curated (read-only) 1..5; a naive default would leave 0s.
    # Known picks are pinned so a rating regression in the YAML is caught.
    models = list_models()
    assert models
    for m in models:
        assert 1 <= m.rating <= 5, f"{m.id} rating {m.rating} out of 1..5"
    assert get_model_info("large-v3-turbo").rating == 5
    assert get_model_info("tiny").rating <= 3


def test_catalog_integrity():
    # Labels must be unique — this defends the Parakeet NeMo/ONNX v2/v3 dedup
    # (4 distinct labels). Every model must carry the specs/notes the card shows.
    models = list_models()
    labels = [m.label for m in models]
    assert len(labels) == len(set(labels)), "duplicate model labels in catalog"
    for m in models:
        assert m.min_specs, f"{m.id} missing min_specs"
        assert m.rec_specs, f"{m.id} missing rec_specs"
        assert m.notes, f"{m.id} missing notes"


def _stars(w: _StarRating) -> list[str]:
    return [x.text() for x in w.findChildren(QLabel) if x.text() in ("★", "☆")]


def test_star_rating_widget():
    # Read-only display: rating N -> N filled ★ and 5-N empty ☆, and none of the
    # old clickable API (a `rated` signal / `_pick`) survives.
    stars = _stars(_StarRating(4))
    assert len(stars) == 5
    assert stars.count("★") == 4
    assert stars.count("☆") == 1

    assert _stars(_StarRating(0)).count("★") == 0
    assert _stars(_StarRating(5)).count("★") == 5

    w = _StarRating(3)
    assert not hasattr(w, "rated")
    assert not hasattr(w, "_pick")


def test_detail_card_shows_system_rows():
    # The card renders the curated min/rec system as MINIMAL/RECOMMENDED rows
    # (_kv upper-cases the key) plus the read-only star row. These labels are
    # nested in child widgets, so findChildren must reach into the whole tree.
    tmp = Path(tempfile.mkdtemp(prefix="macaw_detail_test_")) / "config.yaml"
    mw = MainWindow(tmp)  # bind to a local so Qt doesn't GC the window
    try:
        m = mw.models
        m._show_detail("large-v3-turbo")
        texts = [x.text() for x in m._detail.findChildren(QLabel)]
        low = [t.lower() for t in texts]
        assert "minimal" in low, f"no Minimal row; got {texts}"
        assert "recommended" in low, f"no Recommended row; got {texts}"
        assert "★" in texts, "no filled star in the detail card"
    finally:
        mw.close()


def test_corner_config_roundtrip():
    # The two new fields survive the hand-rolled YAML render + parse round-trip
    # (a dropped key in _render, or a mis-parse in load, loses the overlay shape).
    tmp = Path(tempfile.mkdtemp(prefix="macaw_corner_test_")) / "config.yaml"
    Config(model="", corners=[10, 12, 4, 8], corner_link=False).save(tmp)
    c = Config.load(tmp)
    assert c.corners == [10, 12, 4, 8]
    assert c.corner_link is False


def test_active_indicator_corner_precedence():
    # active_indicator() picks the overlay corners by precedence:
    # unlinked 4-tuple > uniform corner_radius > the theme's own shape.
    # It reads Config.load() (default path) internally, so swap the classmethod
    # for one returning a crafted config, and restore it no matter what.
    orig = Config.__dict__["load"]
    try:

        def _use(cfg: Config) -> None:
            Config.load = classmethod(lambda cls, path=None: cfg)

        # (a) unlinked with four explicit radii wins outright
        _use(Config(model="", theme="oled", corner_link=False, corners=[10, 12, 4, 8]))
        assert active_indicator().corners == (10, 12, 4, 8)

        # (b) linked with a uniform radius fills all four corners
        _use(Config(model="", theme="oled", corner_link=True, corner_radius=9))
        assert active_indicator().corners == (9, 9, 9, 9)

        # (c) neither set -> fall through to the theme's own corners
        _use(Config(model="", theme="oled"))
        assert active_indicator().corners == THEMES["oled"].corners  # (3, 3, 3, 3)
    finally:
        Config.load = orig


def test_corner_link_toggle_and_save():
    # The Photoshop-style link toggle: linked corners move as one; unlinked they
    # are independent; and _save() persists either a uniform radius (linked) or an
    # explicit 4-tuple (unlinked), reloading to the exact same shape.
    tmp = Path(tempfile.mkdtemp(prefix="macaw_corner_test_")) / "config.yaml"
    Config(model="").save(tmp)
    mw = MainWindow(tmp)  # bind to a local so Qt doesn't GC the window
    try:
        st = mw.settings
        # the live preview is mounted inside the framed 'stage' panel
        assert st.preview.parentWidget().objectName() == "stage"

        # link -> the four corners collapse to one value and then track together
        st.corner_link_btn.setChecked(True)
        st._toggle_corner_link()
        tl, tr, br, bl = st._corner_values()
        assert tl == tr == br == bl
        st.corner_tl.setValue(15)
        st._on_corner_changed(15)
        assert st._corner_values() == (15, 15, 15, 15)

        # unlink -> editing one corner leaves the other three untouched
        st.corner_link_btn.setChecked(False)
        st._toggle_corner_link()
        st.corner_tr.setValue(7)
        st._on_corner_changed(7)
        assert st._corner_values() == (15, 7, 15, 15)

        # save unlinked with four distinct radii -> stored as an explicit tuple
        st.corner_tl.setValue(10)
        st.corner_tr.setValue(12)
        st.corner_br.setValue(4)
        st.corner_bl.setValue(8)
        st._corners_touched = True
        st._corner_link = False
        st._save()
        c = Config.load(tmp)
        assert c.corners == [10, 12, 4, 8]
        assert c.corner_radius == -1
        assert c.corner_link is False

        # relink + save -> corners collapse to the top-left value, tuple cleared
        st.corner_link_btn.setChecked(True)
        st._toggle_corner_link()
        st._save()
        c = Config.load(tmp)
        assert c.corner_radius == 10  # the tl value the others collapsed onto
        assert c.corners == []
        assert c.corner_link is True
    finally:
        mw.close()


def test_model_count_in_choose_label():
    # The Models tab header advertises how many models the catalog exposes:
    # "Choose a model  (N)" with N == len(list_models()). A dropped label or a
    # count that drifts from the catalog breaks this.
    tmp = Path(tempfile.mkdtemp(prefix="macaw_count_test_")) / "config.yaml"
    Config(model="").save(tmp)
    mw = MainWindow(tmp)
    try:
        n = len(list_models())
        headers = [
            x.text()
            for x in mw.models.findChildren(QLabel)
            if "choose a model" in x.text().lower()
        ]
        assert headers, "no 'Choose a model' header label found"
        assert f"({n})" in headers[0], f"model count {n} missing from {headers[0]!r}"
    finally:
        mw.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
