from __future__ import annotations

from macaw.stt.isolated import SubprocessBackend
from macaw.stt.registry import register


@register
class VoxtralBackend(SubprocessBackend):
    """Mistral Voxtral (HF Transformers) — fast, streaming, 13 languages.

    Runs in its own isolated venv (extra: voxtral); wants a CUDA GPU. Models and
    provenance live in ``stt/models/voxtral.yaml``.
    """

    key = "voxtral"
