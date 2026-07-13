"""Backend registry + Transcriber facade checks. Run: uv run pytest tests/test_stt.py"""

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


class _EchoBackend:
    """Records what audio reaches the backend; returns a fixed string."""

    def __init__(self):
        self.seen: np.ndarray | None = None

    def transcribe(self, audio, sample_rate=16_000):
        self.seen = audio
        return "ok"

    def transcribe_partial(self, audio, sample_rate=16_000):
        return None  # mirrors Backend's default: no native streaming


def _gated(monkeypatch, chunks, vad_gate=True):
    """Transcriber with a stubbed VAD + echo backend; returns (t, backend).
    Deterministic: Silero itself isn't under test, the gating logic is."""
    import faster_whisper.vad as fwv

    from macaw.stt.base import ModelInfo

    monkeypatch.setattr(fwv, "get_speech_timestamps", lambda audio, opts: chunks)
    t = Transcriber(model_size="large-v3-turbo", vad_gate=vad_gate)
    echo = _EchoBackend()
    echo.model = ModelInfo(
        id="x", backend="whisper", label="x", size="", speed="", languages=""
    )
    t._backend = echo
    monkeypatch.setattr(t, "_ensure_backend", lambda: echo)
    return t, echo


def test_vad_gate_trims_silence(monkeypatch):
    # 10 s of audio, VAD says speech is 1 s in the middle -> backend sees 1 s.
    t, echo = _gated(monkeypatch, [{"start": 16_000, "end": 32_000}])
    audio = np.full(160_000, 0.1, dtype=np.float32)
    assert t.transcribe(audio) == "ok"
    assert echo.seen.size == 16_000


def test_vad_gate_no_speech_short_circuits(monkeypatch):
    # VAD finds nothing -> '' without ever touching the backend.
    t, echo = _gated(monkeypatch, [])
    assert t.transcribe(np.full(160_000, 0.1, dtype=np.float32)) == ""
    assert echo.seen is None


def test_vad_gate_mostly_speech_passes_through(monkeypatch):
    # >=90% speech -> exact original audio, no copy/concat.
    t, echo = _gated(monkeypatch, [{"start": 0, "end": 158_000}])
    audio = np.full(160_000, 0.1, dtype=np.float32)
    t.transcribe(audio)
    assert echo.seen is audio


def test_vad_gate_off_passes_everything(monkeypatch):
    t, echo = _gated(monkeypatch, [{"start": 16_000, "end": 32_000}], vad_gate=False)
    audio = np.full(160_000, 0.1, dtype=np.float32)
    t.transcribe(audio)
    assert echo.seen is audio


def test_vad_gate_failure_never_loses_audio(monkeypatch):
    # A crashing VAD must degrade to unfiltered transcription, not data loss.
    import faster_whisper.vad as fwv

    t, echo = _gated(monkeypatch, [])

    def boom(audio, opts):
        raise RuntimeError("onnx exploded")

    monkeypatch.setattr(fwv, "get_speech_timestamps", boom)
    audio = np.full(160_000, 0.1, dtype=np.float32)
    assert t.transcribe(audio) == "ok"
    assert echo.seen is audio


class _StreamBackend(_EchoBackend):
    """Echo backend with native incremental support; records each delta."""

    def __init__(self):
        super().__init__()
        self.deltas: list[int] = []

    def transcribe_partial(self, audio, sample_rate=16_000):
        self.deltas.append(audio.size)
        return f"partial after {sum(self.deltas)}"


def _streaming(monkeypatch, backend):
    from macaw.stt.base import ModelInfo

    t = Transcriber(model_size="large-v3-turbo")
    backend.model = ModelInfo(
        id="x", backend="whisper", label="x", size="", speed="", languages=""
    )
    t._backend = backend
    monkeypatch.setattr(t, "_ensure_backend", lambda: backend)
    return t


def test_streaming_feeds_only_new_samples(monkeypatch):
    # Native streamers get the delta each tick, not the whole buffer again.
    b = _StreamBackend()
    t = _streaming(monkeypatch, b)
    _, full1 = t.transcribe_streaming(np.full(16_000, 0.1, dtype=np.float32))
    t.transcribe_streaming(np.full(48_000, 0.1, dtype=np.float32), prev_text=full1)
    assert b.deltas == [16_000, 32_000]  # second call fed only the new 2s
    assert b.seen is None  # batch transcribe never touched


def test_streaming_reset_starts_fresh(monkeypatch):
    b = _StreamBackend()
    t = _streaming(monkeypatch, b)
    t.transcribe_streaming(np.full(16_000, 0.1, dtype=np.float32))
    t.reset_stream()
    t.transcribe_streaming(np.full(16_000, 0.1, dtype=np.float32))
    assert b.deltas == [16_000, 16_000]  # full feed again after reset


def test_streaming_batch_pass_resets_native_stream(monkeypatch):
    # The utterance-final batch pass supersedes the live stream: the next
    # streaming call must feed from sample zero again.
    b = _StreamBackend()
    t = _streaming(monkeypatch, b)
    t.vad_gate = False  # keep the batch pass out of the VAD stub's way
    t.transcribe_streaming(np.full(16_000, 0.1, dtype=np.float32))
    t.transcribe(np.full(16_000, 0.1, dtype=np.float32))  # final pass
    t.transcribe_streaming(np.full(16_000, 0.1, dtype=np.float32))
    assert b.deltas == [16_000, 16_000]


def test_streaming_falls_back_without_native_support(monkeypatch):
    # transcribe_partial -> None means re-transcribe the full buffer (with the
    # VAD gate applied there, so stub timestamps keep everything).
    import faster_whisper.vad as fwv

    monkeypatch.setattr(
        fwv,
        "get_speech_timestamps",
        lambda audio, opts: [{"start": 0, "end": audio.size}],
    )
    b = _EchoBackend()
    t = _streaming(monkeypatch, b)
    _, full = t.transcribe_streaming(np.full(32_000, 0.1, dtype=np.float32))
    assert full == "ok"
    assert b.seen.size == 32_000  # whole buffer, every tick


def test_empty_model_is_not_ready():
    # No model selected yet → never ready; the engine blocks recording on this.
    assert Transcriber(model_size="").is_ready() is False


def test_lang_select_is_a_per_model_capability():
    # Multilingual models opt in; English-only variants opt out — a flipped
    # flag would wrongly show/hide the per-model language chooser in the UI.
    cases = {
        "large-v3-turbo": True,  # whisper multilingual
        "distil-large-v3": False,  # whisper EN-only override
        "nvidia/parakeet-tdt-0.6b-v3": True,  # parakeet 25-lang
        "nvidia/parakeet-tdt-0.6b-v2": False,  # parakeet EN-only
    }
    for model_id, expected in cases.items():
        assert stt.get_model_info(model_id).lang_select is expected, model_id


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


def test_optional_backend_reports_unavailable_without_venv(monkeypatch, tmp_path):
    # Isolated backends are available exactly when their venv exists; a fresh
    # XDG_DATA_HOME has none — available() must be False and must not raise.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
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
    # weights live in the SHARED HF cache (keyed by repo), not the isolated venv
    assert b.hf_repos() == ["UsefulSensors/moonshine"]


def test_isolated_delete_removes_weights_and_shared_venv():
    from macaw.stt import isolated as iso

    # Parakeet v2/v3 and Canary all share the 'nemo' venv; each has a distinct repo.
    b = stt.create_backend("nvidia/parakeet-tdt-0.6b-v2")
    assert b.hf_repos() == ["nvidia/parakeet-tdt-0.6b-v2"]

    orig = (iso.hf_repo_delete, iso.remove, iso.hf_cache_sizes)
    deleted_repos: list[str] = []
    removed_extras: list[str] = []
    WEIGHTS, VENV = 2_500_000_000, 6_000_000_000
    iso.hf_repo_delete = lambda repos: (deleted_repos.extend(repos), WEIGHTS)[1]
    iso.remove = lambda extra: (removed_extras.append(extra), VENV)[1]
    try:
        # Sibling (Canary) still has weights → shared 'nemo' venv must survive.
        iso.hf_cache_sizes = lambda: {"nvidia/canary-qwen-2.5b": 5_000_000_000}
        freed = b.delete()
        assert deleted_repos == ["nvidia/parakeet-tdt-0.6b-v2"]
        assert removed_extras == []  # venv kept for the sibling
        assert freed == WEIGHTS

        # Nothing else cached → free the weights AND the now-unused venv.
        deleted_repos.clear()
        iso.hf_cache_sizes = lambda: {}
        freed = b.delete()
        assert deleted_repos == ["nvidia/parakeet-tdt-0.6b-v2"]
        assert removed_extras == ["nemo"]
        assert freed == WEIGHTS + VENV
    finally:
        iso.hf_repo_delete, iso.remove, iso.hf_cache_sizes = orig


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
