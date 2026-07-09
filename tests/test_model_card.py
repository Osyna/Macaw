"""Regressions for the inline model-card activity zone and preview sizing.

The Models tab stopped popping a separate progress dialog for downloads and
model loads: the activity now renders *inline* in the detail panel, driven by
`ModelsTab._op`. These tests drive that state directly and assert the widgets
a user would see — a determinate download bar, an indeterminate load bar with
a live quote, an error zone with a Close button, the clean hand-back to the
normal action row once a load finishes, and Cancel routing to the worker.
They never start real workers or loads (no network / downloads / audio).

The preview test pins the backdrop-inset relationship the Appearance panel
relies on: the preview is exactly the overlay size plus a MARGIN border on
every side, so the pill keeps the overlay's true proportions.

Each test keeps its MainWindow in a local (`mw`) for the whole body: Qt owns
the card widgets as children of the window, so letting the window get GC'd
mid-test would delete the layout out from under us.

Run: uv run pytest tests/test_model_card.py -q   (QT_QPA_PLATFORM=offscreen)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QProgressBar, QPushButton

from macaw.config import Config
from macaw.gui.main_window import MainWindow
from macaw.gui.window import RecordingPreview, RecordingWindow

app = QApplication.instance() or QApplication([])

MODEL = "large-v3-turbo"  # a real offline model; drives the card without any I/O


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


def _texts(widgets):
    return [w.text() for w in widgets]


# -- activity zone: download / load / error -----------------------------------


def test_download_zone_shows_determinate_progress_and_cancel():
    # A running download renders a determinate bar reflecting the live percent
    # plus a Cancel button — a flipped range or a dropped pct would break the
    # only feedback the user gets during a multi-GB pull.
    mw = MainWindow(_fresh_config())
    m = mw.models
    m._op = {"model_id": MODEL, "kind": "download", "pct": 42, "state": "running"}
    m._show_detail(MODEL)

    bars = _find(m.detail, QProgressBar)
    assert len(bars) == 1
    assert bars[0].value() == 42
    assert bars[0].maximum() == 100  # determinate, not the indeterminate 0..0
    assert any("Cancel" in t for t in _texts(_find(m.detail, QPushButton)))


def test_load_zone_is_indeterminate_with_live_quote_and_cancel():
    # A model load has no percentage, so the bar must be indeterminate
    # (min == max == 0) and show the current rotating quote. Regression: a
    # load rendered as a determinate 0% bar looks frozen/hung.
    quote = "Bribing the GPU…"
    mw = MainWindow(_fresh_config())
    m = mw.models
    m._op = {
        "model_id": MODEL,
        "kind": "load",
        "state": "running",
        "quotes": [quote],
        "qi": 0,
        "quote": quote,
    }
    m._show_detail(MODEL)

    bars = _find(m.detail, QProgressBar)
    assert len(bars) == 1
    assert bars[0].minimum() == 0 and bars[0].maximum() == 0  # indeterminate
    assert any(quote in t for t in _texts(_find(m.detail, QLabel)))
    assert any("Cancel" in t for t in _texts(_find(m.detail, QPushButton)))


def test_error_zone_shows_detail_and_close_button():
    # A failed op surfaces the failure detail and a Close (not Cancel) button —
    # Close acknowledges/dismisses; mislabelling it Cancel implies it aborts a
    # still-running op that isn't there.
    mw = MainWindow(_fresh_config())
    m = mw.models
    m._op = {"model_id": MODEL, "kind": "load", "state": "error", "detail": "boom"}
    m._show_detail(MODEL)

    btns = _texts(_find(m.detail, QPushButton))
    assert any("Close" in t for t in btns)
    assert not any("Cancel" in t for t in btns)
    assert any("boom" in t for t in _texts(_find(m.detail, QLabel)))


def test_ready_clears_op_restores_action_row_and_status():
    # When the service reports the load is ready, the op must clear so the card
    # drops back to the normal action row (no progress bar, no Cancel/Close),
    # and the left-column status reflects the now-active model.
    mw = MainWindow(_fresh_config())
    m = mw.models
    m._op = {
        "model_id": MODEL,
        "kind": "load",
        "state": "running",
        "quotes": ["…"],
        "qi": 0,
        "quote": "…",
    }
    m.show_load_status("ready", "X")
    assert m._op is None

    m._show_detail(MODEL)
    assert _find(m.detail, QProgressBar) == []  # activity zone gone
    btns = _texts(_find(m.detail, QPushButton))
    assert any("Set as active" in t for t in btns)  # normal actions returned
    assert not any(t in ("Cancel", "Close") for t in btns)
    assert "active" in m.load_status.text()


def test_cancel_op_cancels_download_worker_and_clears_state():
    # Cancelling a download must reach the worker's cancel() and drop the op,
    # not just visually hide the bar while the subprocess keeps pulling.
    class _FakeWorker:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    worker = _FakeWorker()
    mw = MainWindow(_fresh_config())
    m = mw.models
    m._op = {"model_id": "x", "kind": "download", "state": "running", "worker": worker}
    m._cancel_op()

    assert worker.cancelled is True
    assert m._op is None


# -- activity zone: install (inline, isolated backends) ----------------------


def test_install_zone_is_indeterminate_with_live_line_and_cancel():
    # Isolated backends (Parakeet/Voxtral) now install their dep INLINE in the
    # card, not in a modal. A running install has no percentage, so the bar must
    # be indeterminate (min == max == 0); a determinate 0% bar looks frozen. The
    # live pip line then replaces the placeholder quote as install progresses.
    mid = "nvidia/parakeet-tdt-0.6b-v3"
    mw = MainWindow(_fresh_config())
    m = mw.models
    m._op = {
        "model_id": mid,
        "kind": "install",
        "state": "running",
        "label": "Parakeet",
        "quote": "Installing Parakeet…",
        "worker": None,
    }
    m._show_detail(mid)

    bars = _find(m.detail, QProgressBar)
    assert len(bars) == 1
    assert bars[0].minimum() == 0 and bars[0].maximum() == 0  # indeterminate
    assert any("Installing Parakeet" in t for t in _texts(_find(m.detail, QLabel)))
    assert any("Cancel" in t for t in _texts(_find(m.detail, QPushButton)))

    # A live pip line overwrites the placeholder quote so the user sees motion.
    m._on_install_line("Collecting torch...")
    assert m._op["quote"] == "Collecting torch..."


def test_install_error_zone_shows_close_and_detail():
    # A failed install surfaces the pip failure detail and a Close (not Cancel)
    # button — there is no live worker left to abort, so Cancel would mislead.
    mid = "nvidia/parakeet-tdt-0.6b-v3"
    mw = MainWindow(_fresh_config())
    m = mw.models
    m._op = {
        "model_id": mid,
        "kind": "install",
        "state": "error",
        "detail": "pip failed",
    }
    m._show_detail(mid)

    btns = _texts(_find(m.detail, QPushButton))
    assert any("Close" in t for t in btns)
    assert not any("Cancel" in t for t in btns)
    assert any("pip failed" in t for t in _texts(_find(m.detail, QLabel)))


def test_cancel_op_cancels_install_worker():
    # Cancelling an install must reach the worker's cancel() (kill the pip
    # subprocess) and drop the op, mirroring the download-cancel contract — not
    # just hide the bar while pip keeps running.
    class _FakeWorker:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    worker = _FakeWorker()
    mid = "nvidia/parakeet-tdt-0.6b-v3"
    mw = MainWindow(_fresh_config())
    m = mw.models
    m._op = {"model_id": mid, "kind": "install", "state": "running", "worker": worker}
    m._cancel_op()

    assert worker.cancelled is True
    assert m._op is None


def test_on_install_done_clears_or_errors_op():
    # Success clears the op so the card drops back to its action row; failure
    # flips it to the error state carrying the pip message the error zone renders.
    mid = "nvidia/parakeet-tdt-0.6b-v3"
    mw = MainWindow(_fresh_config())
    m = mw.models

    m._op = {"model_id": mid, "kind": "install", "state": "running", "worker": None}
    m._on_install_done(True, "")
    assert m._op is None

    m._op = {"model_id": mid, "kind": "install", "state": "running", "worker": None}
    m._on_install_done(False, "boom")
    assert m._op["state"] == "error"
    assert m._op["detail"] == "boom"


# -- preview backdrop inset ---------------------------------------------------


def test_preview_is_overlay_size_plus_margin_border():
    # The preview must be the overlay dimensions plus MARGIN on every side; a
    # wrong inset would distort the pill and misrepresent the real overlay.
    mw = MainWindow(_fresh_config())
    mw.show()
    s = mw.settings
    s.overlay_w_stepper.setValue(210)
    s.overlay_h_stepper.setValue(52)
    s._update_preview()

    m = RecordingPreview.MARGIN
    assert s.preview.size().width() == 210 + 2 * m
    assert s.preview.size().height() == 52 + 2 * m
    mw.hide()


# -- overlay message paint path -----------------------------------------------


def test_overlay_show_message_enters_message_state():
    # Regression guard for the overlay raise/paint path: showing a status
    # message flips the widget into the "message" state the painter branches on.
    rw = RecordingWindow(210, 52)
    rw.timer.stop()  # inert without an event loop, but don't leak a live timer
    rw.show_message("No Model Selected")
    assert rw.state == "message"
    assert rw.message == "No Model Selected"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
