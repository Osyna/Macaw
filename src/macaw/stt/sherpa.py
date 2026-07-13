from __future__ import annotations

from macaw.stt.isolated import SubprocessBackend
from macaw.stt.registry import register


@register
class SherpaOnnxBackend(SubprocessBackend):
    """sherpa-onnx (ONNX) — lightweight CPU ASR in an isolated venv (extra: sherpa).

    Serves streaming Zipformer/Paraformer and offline Parakeet TDT models. Models
    and provenance live in ``stt/models/sherpa.yaml``; weights download on first
    use inside the worker. Loading/inference run in worker.py so its bundled
    onnxruntime never touches the main environment.
    """

    key = "sherpa"
