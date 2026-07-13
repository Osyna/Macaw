from __future__ import annotations

from macaw.stt.isolated import SubprocessBackend
from macaw.stt.registry import register


@register
class MoonshineBackend(SubprocessBackend):
    """Moonshine (ONNX) — ultra-light English, runs in its own isolated venv.

    Models, provenance and params live in ``stt/models/moonshine.yaml``.
    Loading/inference run in worker.py so its deps never touch the main
    environment.
    """

    key = "moonshine"
