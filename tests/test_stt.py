"""Backend registry + Transcriber facade checks. Run: python tests/test_stt.py"""

from __future__ import annotations

import numpy as np

import macaw.stt as stt
from macaw.audio.transcriber import Transcriber
from macaw.stt.base import Backend, ModelInfo
from macaw.stt.registry import register, register_model


def test_all_backends_registered():
    ids = {m.id for m in stt.list_models()}
    for expected in (
        "large-v3-turbo",  # whisper
        "moonshine/tiny",
        "nvidia/parakeet-tdt-0.6b-v3",
        "nvidia/canary-qwen-2.5b",
        "mistralai/Voxtral-Mini-3B-2507",
    ):
        assert expected in ids, f"{expected} not registered"


def test_unknown_model_falls_back_to_default():
    assert stt.get_model_info("does-not-exist").id == "large-v3-turbo"


def test_create_backend_routes_by_model():
    b = stt.create_backend("large-v3-turbo")
    assert b.key == "whisper"
    assert stt.create_backend("moonshine/base").key == "moonshine"


def test_silence_gate_returns_empty_without_loading():
    # Near-silent audio short-circuits before any model import/load.
    t = Transcriber(model_size="large-v3-turbo")
    assert t.transcribe(np.zeros(16_000, dtype=np.float32)) == ""


def test_switching_model_recreates_backend():
    t = Transcriber(model_size="large-v3-turbo")
    t._ensure_backend()
    assert t._backend.key == "whisper"
    t.model_size = "moonshine/tiny"
    t._ensure_backend()
    assert t._backend.key == "moonshine"


def test_models_declare_recommendations():
    for m in stt.list_models():
        assert m.hardware, f"{m.id} missing hardware"
        assert m.vram, f"{m.id} missing vram"


def test_backend_exposes_management_surface():
    b = stt.create_backend("large-v3-turbo")
    assert isinstance(b.available(), bool)
    assert isinstance(b.disk_size(), int)  # 0 if not downloaded; never raises
    assert b.hf_repos()  # whisper maps id -> repo


def test_optional_backend_reports_unavailable_without_dep():
    # nemo isn't installed in CI → available() is False, and it must not raise.
    assert stt.create_backend("nvidia/parakeet-tdt-0.6b-v3").available() is False


def test_packages_for_extra_reads_metadata():
    from macaw.stt.deps import packages_for_extra

    # Resolved from macaw's own Requires-Dist — no hardcoded duplication.
    assert any("transformers" in p for p in packages_for_extra("voxtral"))
    assert any("nemo" in p for p in packages_for_extra("nemo"))
    assert packages_for_extra("does-not-exist") == []


def test_isolated_install_builds_venv_then_pip():
    from macaw.stt.isolated import install_commands

    steps = install_commands("moonshine", ["useful-moonshine-onnx"])
    assert steps[0][1] == "venv"  # first create the isolated venv
    assert "install" in steps[1] and steps[1][-1] == "useful-moonshine-onnx"


def test_isolated_backend_tracks_venv_state():
    from macaw.stt import isolated

    # Subprocess backends are available exactly when their isolated venv exists.
    b = stt.create_backend("moonshine/tiny")
    assert b.available() == isolated.is_installed("moonshine")
    assert b.is_ready() == b.available()
    assert b.hf_repos() == []  # weights live in the venv, not the shared HF cache


def test_adding_a_backend_is_the_whole_job():
    @register
    class _DummyBackend(Backend):
        key = "_dummy"

        def load(self, model_path=None):
            self._loaded = True

        def transcribe(self, audio, sample_rate=16_000):
            return "dummy output"

    # a backend provides code; its model(s) bind via the catalog/register_model
    register_model(
        ModelInfo("_dummy-model", "_dummy", "Dummy", "0 MB", "instant", "EN")
    )
    b = stt.create_backend("_dummy-model")
    b.load()
    assert b.transcribe(np.zeros(10, dtype=np.float32)) == "dummy output"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
