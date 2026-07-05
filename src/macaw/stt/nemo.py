from __future__ import annotations

from macaw.stt.isolated import SubprocessBackend
from macaw.stt.registry import register

# Parakeet TDT + Canary-Qwen (NVIDIA NeMo). They share the isolated 'nemo' venv
# and want a CUDA GPU. Models/provenance live in stt/models/nemo.yaml;
# loading/inference live in worker.py.


@register
class ParakeetBackend(SubprocessBackend):
    """NVIDIA Parakeet TDT (NeMo) — very fast, native streaming."""

    key = "parakeet"


@register
class CanaryQwenBackend(SubprocessBackend):
    """NVIDIA Canary-Qwen 2.5B (NeMo SALM) — top ASR-leaderboard accuracy."""

    key = "canary-qwen"
