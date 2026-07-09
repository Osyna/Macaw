"""Failed-but-active model stays retryable in place — regression guard.

Regression ("the dance"): when a model's async load FAILED, the model stayed
"active", so `_show_detail` disabled its "Set as active" button. The user could
not retry without switching to another model and back. Fixed: a failed active
model (`_load_state == "error"`) now renders an enabled "Retry" button, and a
later successful load ("ready") reverts it to the normal "Set as active".
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton

from macaw.config import Config
from macaw.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def _buttons(layout) -> list[QPushButton]:
    out: list[QPushButton] = []
    for i in range(layout.count()):
        it = layout.itemAt(i)
        w = it.widget()
        if isinstance(w, QPushButton):
            out.append(w)
        if it.layout() is not None:
            out += _buttons(it.layout())
    return out


def test_failed_active_model_shows_retry_button():
    p = Path(tempfile.mkdtemp(prefix="macaw_test_")) / "config.yaml"
    Config(model="large-v3-turbo").save(p)
    m = MainWindow(p).models

    # Async load of the active model failed.
    m.show_load_status("error", "Whisper Large v3 Turbo", "boom")
    assert m._load_state == "error"
    assert m._loading is False

    # The active-but-failed model must offer an in-place Retry (no dance).
    m._show_detail("large-v3-turbo")
    texts = [b.text() for b in _buttons(m.detail)]
    assert "Retry" in texts, texts

    # A subsequent successful load clears the error and reverts the button.
    m.show_load_status("ready", "Whisper Large v3 Turbo")
    m._show_detail("large-v3-turbo")
    texts = [b.text() for b in _buttons(m.detail)]
    assert "Retry" not in texts, texts
    assert "Set as active" in texts, texts


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
