"""LLM formatting: catalog, backend routing, readiness, Formatter facade.

Run: uv run pytest tests/test_llm.py

The Formatter is the post-STT contract: enabled + ready + a system prompt →
cleaned text; anything else (disabled, no model, empty input) → the text
unchanged; a backend failure propagates (the engine catches and keeps the raw
transcription).
"""

from __future__ import annotations

import macaw.llm as llm
from macaw.llm.base import LlmBackend, LlmInfo
from macaw.llm.formatter import Formatter
from macaw.llm.prompts import SMART_SYSTEM, resolve_system
from macaw.llm.registry import register, register_model


def test_backends_and_models_registered():
    ids = {m.id for m in llm.list_models()}
    for expected in ("qwen2.5-0.5b-instruct", "gpt-4o-mini"):
        assert expected in ids, f"{expected} not registered"
    backends = {m.backend for m in llm.list_models()}
    assert {"llamacpp", "openai-llm"} <= backends


def test_unknown_model_is_none():
    # Unlike STT there is no default formatter — unknown resolves to None.
    assert llm.get_model_info("nope") is None


def test_create_backend_routes_by_model():
    assert llm.create_backend("qwen2.5-0.5b-instruct").key == "llamacpp"
    assert llm.create_backend("gpt-4o-mini").key == "openai-llm"


def test_local_backend_is_venv_gated(monkeypatch, tmp_path):
    # Local formatters are available only once their isolated venv exists.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    b = llm.create_backend("qwen2.5-0.5b-instruct")
    assert b.available() is False
    assert b.is_ready() is False
    assert b.hf_repos() == ["bartowski/Qwen2.5-0.5B-Instruct-GGUF"]


def test_cloud_readiness_needs_key_and_dep():
    b = llm.create_backend("gpt-4o-mini")
    assert b.cloud is True
    b.configure("", "")
    assert b.is_ready() is False  # no key
    # Even with a key, readiness still requires the 'openai' dependency; the
    # gate must never claim ready when a format() would ImportError.
    b.configure("sk-x", "")
    assert b.is_ready() == b.available()


def test_llm_extra_declares_llama_cpp():
    from macaw.stt.deps import packages_for_extra

    assert any(p.startswith("llama-cpp-python") for p in packages_for_extra("llm"))


def test_install_commands_add_cuda_index_only_when_asked():
    from macaw.stt.isolated import install_commands

    plain = install_commands("llm", ["llama-cpp-python"])
    assert "--extra-index-url" not in plain[1]
    cuda = install_commands("llm", ["llama-cpp-python"], "https://idx/cu124")
    assert "--extra-index-url" in cuda[1]
    assert "https://idx/cu124" in cuda[1]


def test_cloud_models_carry_no_weights():
    for m in llm.list_models():
        assert m.size and m.speed and m.notes, m.id
        if m.cloud:
            assert not m.repo, f"{m.id}: cloud model must not declare a repo"


# -- smart prompt --------------------------------------------------------------


def test_smart_default_used_when_no_custom():
    assert resolve_system("") == SMART_SYSTEM
    assert resolve_system("   ") == SMART_SYSTEM


def test_custom_prompt_overrides_and_is_stripped():
    assert resolve_system("  do X only  ") == "do X only"


# -- Formatter facade (stub backend) -------------------------------------------


@register
class _StubLlm(LlmBackend):
    key = "stub-llm"

    def load(self) -> None:
        self.loaded = True

    def format(self, text: str, system: str) -> str:
        if text == "boom":
            raise RuntimeError("backend blew up")
        # echo enough to prove the system prompt + text both reach the backend
        return f"[sys={len(system)}] {text.strip().upper()}"


def _register_stub(model_id: str = "stub-model") -> None:
    if llm.get_model_info(model_id) is None:
        register_model(
            LlmInfo(id=model_id, backend="stub-llm", label="Stub", size="—", speed="x")
        )


def test_formatter_runs_text_through_backend():
    _register_stub()
    f = Formatter("stub-model")
    assert f.format("hello world") == f"[sys={len(SMART_SYSTEM)}] HELLO WORLD"


def test_formatter_custom_prompt_reaches_backend():
    _register_stub()
    f = Formatter("stub-model", custom_prompt="tiny")
    assert f.format("hi") == "[sys=4] HI"  # len("tiny") == 4


def test_formatter_passthrough_when_no_model_or_empty():
    assert Formatter("").format("keep me") == "keep me"  # no model picked
    _register_stub()
    assert Formatter("stub-model").format("   ") == "   "  # empty in → same out


def test_formatter_failure_propagates():
    # The Formatter never silently swallows a failure — it re-raises so the
    # engine can toast + keep the raw transcription.
    _register_stub()
    f = Formatter("stub-model")
    try:
        f.format("boom")
        assert False, "expected the backend failure to propagate"
    except RuntimeError as exc:
        assert "blew up" in str(exc)


def test_formatter_apply_swaps_model_and_unloads():
    _register_stub()
    _register_stub("stub-model-2")
    f = Formatter("stub-model")
    f.format("x")  # instantiate the backend
    assert f._backend is not None and f._backend.model.id == "stub-model"
    f.apply("stub-model-2", "", "", "")
    assert f._backend is None  # model changed → old backend dropped
    assert f.model_id == "stub-model-2"
