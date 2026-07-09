"""Model-ops regressions: subprocess worker lifecycle + the curated catalog.
Run: uv run pytest tests/test_model_ops.py

Guards, in order:
  1. SubprocessBackend.unload() reaps the worker (no zombie) — the footprint fix;
  2. SubprocessBackend._read_message() tolerates a killed/None proc — cancel safety;
  3. list_models() ratings are curated 1..5 with known picks pinned;
  4. catalog integrity — unique labels (Parakeet dedup) + every model has specs;
  5. worker.py never shadows isolated backends with its own dir on sys.path
     (the "No module named 'macaw'" crash for the parakeet backend).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from macaw.stt import get_model_info, list_models
from macaw.stt.base import ModelInfo
from macaw.stt.isolated import _WORKER, SubprocessBackend


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
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    proc = b._proc
    pid = proc.pid

    b.unload()
    assert b._proc is None

    if os.name == "nt":
        # No zombie concept on Windows — "reaped" means the process exited.
        assert proc.poll() is not None, "worker still running after unload()"
        return
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
    # (4 distinct labels). Every model must carry the specs/notes the UI shows.
    models = list_models()
    labels = [m.label for m in models]
    assert len(labels) == len(set(labels)), "duplicate model labels in catalog"
    for m in models:
        assert m.min_specs, f"{m.id} missing min_specs"
        assert m.rec_specs, f"{m.id} missing rec_specs"
        assert m.notes, f"{m.id} missing notes"


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
