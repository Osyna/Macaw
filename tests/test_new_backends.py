"""OpenAI cloud + sherpa-onnx backend checks. Run: python tests/test_new_backends.py

Runs with neither `openai` nor `sherpa-onnx` installed and no network: the
OpenAI SDK is faked in sys.modules and every backend is exercised through the
public stt surface. Every test is zero-arg so pytest and the __main__ runner
below behave identically.
"""

from __future__ import annotations

import io
import sys
import tempfile
import types
import wave
from pathlib import Path
from unittest import mock

import numpy as np

import macaw.stt as stt
from macaw.stt.cloud import _to_wav_bytes

# -- _to_wav_bytes: the WAV encoder the cloud request depends on --------------


def test_to_wav_bytes_header_and_roundtrip():
    # A non-16k rate + a known [1, 0, -1] ramp: defends header, frame count and
    # the 32767 int16 scaling all at once.
    audio = np.array([1.0, 0.0, -1.0], dtype=np.float32)
    data = _to_wav_bytes(audio, 22_050)

    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"

    with wave.open(io.BytesIO(data)) as r:
        assert r.getnchannels() == 1
        assert r.getsampwidth() == 2
        assert r.getframerate() == 22_050  # not hardcoded to 16k
        assert r.getnframes() == len(audio)
        pcm = np.frombuffer(r.readframes(r.getnframes()), dtype="<i2")

    assert list(pcm) == [32767, 0, -32767]


def test_to_wav_bytes_clips_out_of_range():
    # Without clip(), 2.0*32767 overflows int16 and wraps negative; assert the
    # samples pin to full scale instead of wrapping.
    audio = np.array([2.0, -2.0, 0.5], dtype=np.float32)
    with wave.open(io.BytesIO(_to_wav_bytes(audio, 16_000))) as r:
        pcm = np.frombuffer(r.readframes(r.getnframes()), dtype="<i2")

    assert pcm[0] == 32767
    assert pcm[1] == -32767
    assert pcm.min() >= -32767 and pcm.max() <= 32767


# -- OpenAICloudBackend.transcribe: the HTTPS request contract ----------------


def _fake_openai(captured: dict, text: str = " hi "):
    """A stand-in `openai` module whose OpenAI client records what it's asked."""
    mod = types.ModuleType("openai")

    class OpenAI:
        def __init__(self, api_key=None, **_kw):
            captured["api_key"] = api_key

            def create(**kwargs):
                captured["create_kwargs"] = kwargs
                return types.SimpleNamespace(text=text)

            self.audio = types.SimpleNamespace(
                transcriptions=types.SimpleNamespace(create=create)
            )

    mod.OpenAI = OpenAI
    return mod


def test_openai_transcribe_builds_request():
    from macaw.stt.cloud import OpenAICloudBackend

    for model in ("gpt-4o-transcribe", "gpt-4o-mini-transcribe"):
        captured: dict = {}
        b = stt.create_backend(f"cloud:openai:{model}", language="en")
        with (
            mock.patch.dict(sys.modules, {"openai": _fake_openai(captured)}),
            mock.patch.object(
                OpenAICloudBackend, "_resolved", lambda self: {"key": "sk-test"}
            ),
        ):
            out = b.transcribe(np.zeros(1600, dtype=np.float32), sample_rate=16_000)

        assert out == "hi", model  # resp.text (" hi ") is stripped
        assert captured["api_key"] == "sk-test", model
        kw = captured["create_kwargs"]
        assert kw["model"] == model, model  # provider prefix stripped from the id
        assert kw["response_format"] == "json", model
        assert kw["language"] == "en", model
        f = kw["file"]
        assert isinstance(f, tuple) and f[0].endswith(".wav"), model
        assert isinstance(f[1], (bytes, bytearray)) and f[1][:4] == b"RIFF", model


def test_openai_transcribe_omits_empty_language():
    from macaw.stt.cloud import OpenAICloudBackend

    captured: dict = {}
    b = stt.create_backend("cloud:openai:gpt-4o-transcribe", language="")
    with (
        mock.patch.dict(sys.modules, {"openai": _fake_openai(captured)}),
        mock.patch.object(
            OpenAICloudBackend, "_resolved", lambda self: {"key": "sk-test"}
        ),
    ):
        b.transcribe(np.zeros(10, dtype=np.float32))

    assert "language" not in captured["create_kwargs"]


# -- OpenAICloudBackend: capability / readiness gating ------------------------


def test_openai_hf_repos_empty():
    # Cloud model → nothing to download; a non-empty list would make the Model
    # Manager try to size/cache weights that don't exist.
    assert stt.create_backend("cloud:openai:gpt-4o-transcribe").hf_repos() == []


def test_openai_is_ready_gates_on_key():
    from macaw.stt.cloud import OpenAICloudBackend

    b = stt.create_backend("cloud:openai:gpt-4o-transcribe")
    with mock.patch.object(OpenAICloudBackend, "available", lambda self: True):
        with mock.patch.object(
            OpenAICloudBackend, "_resolved", lambda self: {"key": "", "needs_key": True}
        ):
            assert b.is_ready() is False
        with mock.patch.object(
            OpenAICloudBackend, "_resolved", lambda self: {"key": "sk", "needs_key": True}
        ):
            assert b.is_ready() is True


def test_openai_load_requires_key():
    from macaw.stt.base import MissingDependency
    from macaw.stt.cloud import OpenAICloudBackend

    b = stt.create_backend("cloud:openai:gpt-4o-transcribe")
    # available() forced True so we exercise the no-key branch, not missing-dep.
    with (
        mock.patch.object(OpenAICloudBackend, "available", lambda self: True),
        mock.patch.object(
            OpenAICloudBackend, "_resolved", lambda self: {"key": "", "needs_key": True}
        ),
    ):
        try:
            b.load()
        except MissingDependency:
            pass
        else:
            raise AssertionError("load() must raise MissingDependency without a key")


# -- Config: the new openai_api_key field survives save/load ------------------


def test_config_never_writes_api_keys_to_disk():
    # API keys are encrypted in secrets.enc — never rendered into config.yaml,
    # so sharing/syncing the config file leaks nothing.
    from macaw.config import Config

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.yaml"
        Config(openai_api_key="sk-roundtrip-abc123").save(p)
        assert "sk-roundtrip-abc123" not in p.read_text()
        assert Config.load(p).openai_api_key == ""


# -- Catalog flags exposed through list_models() / create_backend() -----------


def test_cloud_models_come_from_providers_not_catalog():
    # Cloud voice is provider-injected, never in the local catalog.
    from macaw.llm import providers

    assert not any(m.cloud for m in stt.list_models())
    assert "gpt-4o-transcribe" in providers.resolve("openai", None)["stt_models"]
    b = stt.create_backend("cloud:openai:gpt-4o-transcribe")
    assert b.model.cloud is True and b.key == "cloud"


def test_sherpa_streaming_and_recommended_flags():
    by_id = {m.id: m for m in stt.list_models()}
    assert by_id["sherpa-parakeet-tdt-v3"].recommended is True
    for mid in (
        "sherpa-zipformer-bilingual-zh-en",
        "sherpa-paraformer-bilingual-zh-en",
        "sherpa-zipformer-en-20m",
        "sherpa-zipformer-zh-14m",
    ):
        assert by_id[mid].streaming is True, mid
    for mid in ("sherpa-parakeet-tdt-v3", "sherpa-parakeet-tdt-v2"):
        assert by_id[mid].streaming is False, mid


def test_create_backend_routes_cloud_and_sherpa():
    assert stt.create_backend("cloud:openai:gpt-4o-transcribe").key == "cloud"
    assert stt.create_backend("cloud:groq:whisper-large-v3").key == "cloud"
    assert stt.create_backend("sherpa-parakeet-tdt-v3").key == "sherpa"


def test_create_backend_routes_moonshine2_and_nemotron():
    assert stt.create_backend("moonshine2-medium-en").key == "moonshine2"
    assert stt.create_backend("sherpa-nemotron-streaming-en").key == "sherpa"


def test_nemotron_flagged_streaming_and_recommended():
    by_id = {m.id: m for m in stt.list_models()}
    m = by_id["sherpa-nemotron-streaming-en"]
    assert m.streaming is True and m.recommended is True


def test_moonshine2_models_have_no_hf_repo():
    # Weights come from download.moonshine.ai via the package's own cache; a
    # non-empty repo would make the Manager size/delete an HF repo that never
    # materializes.
    for mid in ("moonshine2-tiny-en", "moonshine2-small-en", "moonshine2-medium-en"):
        assert stt.create_backend(mid).hf_repos() == [], mid


def _import_worker():
    # Importing worker.py rebinds sys.stdout -> sys.stderr as a side effect;
    # save/restore around the import so pytest's capture survives.
    saved = sys.stdout
    try:
        import macaw.stt.worker as worker

        return worker
    finally:
        sys.stdout = saved


def test_worker_moonshine2_archs_match_catalog():
    worker = _import_worker()
    catalog_ids = {m.id for m in stt.list_models() if m.backend == "moonshine2"}
    assert set(worker._MOONSHINE2_ARCH) == catalog_ids


# -- worker line protocol: batch, stream-feed, and failure replies ------------


def test_worker_handle_line_batch_and_feed(tmp_path):
    worker = _import_worker()
    calls = {}

    def transcribe(audio):
        calls["batch"] = audio.size
        return "batch text"

    def feed(audio):
        calls["feed"] = audio.size
        return "partial text"

    transcribe.feed = feed
    p = tmp_path / "a.npy"
    np.save(p, np.zeros(160, dtype=np.float32))

    assert worker._handle_line(transcribe, str(p)) == {"text": "batch text"}
    assert calls["batch"] == 160
    assert worker._handle_line(transcribe, f"S {p}") == {"text": "partial text"}
    assert calls["feed"] == 160
    assert worker._handle_line(transcribe, "") is None


def test_worker_handle_line_feed_unsupported(tmp_path):
    worker = _import_worker()
    p = tmp_path / "a.npy"
    np.save(p, np.zeros(16, dtype=np.float32))
    reply = worker._handle_line(lambda audio: "x", f"S {p}")
    assert reply == {"error": "stream feed unsupported"}


def test_worker_handle_line_error_reply():
    worker = _import_worker()
    reply = worker._handle_line(lambda audio: "x", "/nonexistent/audio.npy")
    assert "error" in reply


def test_worker_config_line_is_fire_and_forget():
    # "C {json}" updates the shared CFG and produces NO reply — a reply here
    # would desync the request/reply pairing SubprocessBackend relies on.
    worker = _import_worker()
    line = (
        'C {"language": "fr", "punctuation_hints": false, "params": {"beam_size": 5}}'
    )
    assert worker._handle_line(lambda audio: "x", line) is None
    assert worker.CFG["language"] == "fr"
    assert worker.CFG["params"]["beam_size"] == 5


def test_worker_malformed_config_line_is_ignored():
    worker = _import_worker()
    before = dict(worker.CFG)
    assert worker._handle_line(lambda audio: "x", "C {broken json") is None
    assert worker.CFG == before


def test_worker_reset_line_is_fire_and_forget():
    # "R" drops the loader's persistent live stream without a reply — a
    # reply would desync the request/reply pairing.
    worker = _import_worker()
    resets: list[int] = []

    def transcribe(audio):
        return "x"

    transcribe.reset = lambda: resets.append(1)
    assert worker._handle_line(transcribe, "R") is None
    assert resets == [1]


def test_worker_reset_line_without_reset_hook_is_ignored():
    worker = _import_worker()
    assert worker._handle_line(lambda audio: "x", "R") is None


# -- worker._SHERPA_MODELS stays consistent with the sherpa catalog -----------


def test_worker_sherpa_models_match_catalog():
    # Importing worker.py rebinds sys.stdout -> sys.stderr as a side effect;
    # save/restore around the import so pytest's capture survives.
    saved = sys.stdout
    try:
        import macaw.stt.worker as worker
    finally:
        sys.stdout = saved

    catalog_ids = {m.id for m in stt.list_models() if m.backend == "sherpa"}
    assert set(worker._SHERPA_MODELS) == catalog_ids

    valid_kinds = {"offline_transducer", "online_transducer", "online_paraformer"}
    file_keys = {"encoder", "decoder", "joiner", "tokens"}
    for mid, cfg in worker._SHERPA_MODELS.items():
        assert cfg["kind"] in valid_kinds, mid
        present = {k for k in cfg if k in file_keys}
        if cfg["kind"] == "online_paraformer":
            assert present == {"encoder", "decoder", "tokens"}, mid
            assert "joiner" not in cfg, mid
        else:  # offline/online transducer needs a joiner too
            assert present == {"encoder", "decoder", "joiner", "tokens"}, mid


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
