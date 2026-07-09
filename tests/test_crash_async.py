"""Regressions for three crash/async fixes. Run: python tests/test_crash_async.py

Guards, in order:
  1. worker.py no longer shadows isolated backends with its own dir on sys.path
     (the "No module named 'macaw'" crash for the parakeet backend);
  2. _ModelLoadThread turns a load failure into a signal instead of crashing;
  3. ModelsTab.show_load_status drives the banner state machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from macaw.service import _ModelLoadThread
from macaw.stt.isolated import _WORKER

app = QApplication.instance() or QApplication([])


def test_worker_does_not_shadow_backends_with_macaw():
    # Pre-fix, worker.py's own dir shadowed real backend packages, so importing
    # the nemo backend pulled in `macaw` (absent in the isolated venv) → crash.
    result = subprocess.run(
        [
            sys.executable,
            _WORKER,
            "--backend",
            "parakeet",
            "--model",
            "x",
            "--language",
            "en",
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=90,
    )
    # It fails (nemo isn't in the main interpreter) but must blame nemo, not macaw.
    assert "macaw" not in result.stdout, result.stdout


class _FakeTranscriber:
    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc

    def load_model(self) -> None:
        if self._exc is not None:
            raise self._exc


def _run_load_thread(transcriber) -> tuple[bool, str]:
    loop = QEventLoop()
    captured: dict[str, object] = {}

    def on_done(ok: bool, err: str) -> None:
        captured["ok"] = ok
        captured["err"] = err
        loop.quit()

    thread = _ModelLoadThread(transcriber)
    thread.done.connect(on_done)
    QTimer.singleShot(15_000, loop.quit)  # safety net so a hang can't wedge the suite
    thread.start()
    loop.exec()
    thread.wait()
    assert "ok" in captured, "done signal never fired"
    return captured["ok"], captured["err"]  # type: ignore[return-value]


def test_model_load_thread_reports_success():
    ok, err = _run_load_thread(_FakeTranscriber())
    assert ok is True
    assert err == ""


def test_model_load_thread_reports_failure():
    # A raising load_model must surface as done(False, msg), never escape the thread.
    ok, err = _run_load_thread(_FakeTranscriber(RuntimeError("boom")))
    assert ok is False
    assert "boom" in err


def test_models_tab_load_status_banner():
    from macaw.gui.models_tab import ModelsTab

    tab = ModelsTab(Path(tempfile.mktemp(suffix=".yaml")))

    tab.show_load_status("loading", "Whisper large-v3-turbo")
    assert tab._loading is True
    assert "Loading" in tab.load_status.text()

    tab.show_load_status("error", "Whisper large-v3-turbo", "boom")
    assert tab._loading is False
    assert "boom" in tab.load_status.text()

    tab.show_load_status("ready", "Whisper large-v3-turbo")
    assert tab._loading is False
    assert "active" in tab.load_status.text()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name, "...", end=" ")
            fn()
            print("ok")
    print("\nall passed")
