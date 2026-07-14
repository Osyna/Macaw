"""NLP punctuation + true-casing backend (punctuators, ONNX on CPU).

A real transformer that restores punctuation and capitalization from raw
speech-to-text — no LLM, no GPU. It runs in the isolated 'nlp' venv (CPU torch
+ onnxruntime + punctuators); the heavy deps never touch the base environment.
Inference lives in ``nlp_worker.py``; models/provenance in ``models/nlp.yaml``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from macaw.llm.base import MissingDependency
from macaw.llm.isolated import LlmSubprocessBackend
from macaw.llm.registry import register
from macaw.stt.isolated import _worker_env, venv_python

_NLP_WORKER = str(Path(__file__).parent / "nlp_worker.py")


@register
class NlpBackend(LlmSubprocessBackend):
    key = "nlp"
    worker_script = _NLP_WORKER

    def _worker_cmd(self, py: Path) -> list[str]:
        # `filename` carries the punctuators key (e.g. "pcs_en"); `repo` is the
        # canonical HF id, used only for download-size / cache accounting.
        return [str(py), self.worker_script, "--model", self.model.filename]

    def download(self, progress_callback=None) -> str:
        """Fetch the ONNX weights into the shared HF cache by loading the model
        once inside the venv — punctuators pulls exactly the files it needs."""
        py = venv_python(self.model.extra)
        if not py.exists():
            raise MissingDependency(f"{self.model.extra} backend is not installed")
        code = (
            "from punctuators.models import PunctCapSegModelONNX as M; "
            f"M.from_pretrained({self.model.filename!r}, "
            "ort_providers=['CPUExecutionProvider'])"
        )
        subprocess.run([str(py), "-c", code], check=True, env=_worker_env())
        return ""
