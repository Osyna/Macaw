from __future__ import annotations

from macaw.stt.isolated import SubprocessBackend
from macaw.stt.registry import register


@register
class Moonshine2Backend(SubprocessBackend):
    """Moonshine v2 ("moonshine-voice") — streaming English ASR on CPU.

    Models, provenance and params live in ``stt/models/moonshine2.yaml``.
    Loading/inference run in worker.py inside the ``moonshine2`` venv. The
    .ort weight bundles download from download.moonshine.ai into the
    package's own cache (no HF repo), so `repo` stays empty in the catalog
    and disk accounting tracks the venv directory only.
    """

    key = "moonshine2"
