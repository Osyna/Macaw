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
    for expected in ("qwen2.5-0.5b-instruct", "qwen2.5-1.5b-instruct"):
        assert expected in ids, f"{expected} not registered"
    # local catalog is llama.cpp only; cloud is now the provider system
    assert {m.backend for m in llm.list_models()} == {"llamacpp"}


def test_unknown_model_is_none():
    # Unlike STT there is no default formatter — unknown resolves to None.
    assert llm.get_model_info("nope") is None


def test_create_backend_routes_by_model():
    assert llm.create_backend("qwen2.5-0.5b-instruct").key == "llamacpp"


def test_local_backend_is_venv_gated(monkeypatch, tmp_path):
    # Local formatters are available only once their isolated venv exists.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    b = llm.create_backend("qwen2.5-0.5b-instruct")
    assert b.available() is False
    assert b.is_ready() is False
    assert b.hf_repos() == ["bartowski/Qwen2.5-0.5B-Instruct-GGUF"]


def test_provider_presets_cover_the_majors():
    from macaw.llm import providers as P

    ids = {p.id for p in P.PRESETS}
    for want in ("openai", "anthropic", "gemini", "xai", "openrouter", "ollama"):
        assert want in ids, want
    # two protocols cover everything; anthropic is the only non-openai one
    assert P.PRESET_BY_ID["anthropic"].kind == "anthropic"
    assert P.PRESET_BY_ID["ollama"].kind == "openai"
    assert P.PRESET_BY_ID["ollama"].needs_key is False


def test_provider_resolution_layers_and_gates(monkeypatch, tmp_path):
    from macaw.llm import providers as P

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # preset defaults, no key -> not ready
    r = P.resolve("openai", {"enabled": True})
    assert r["model"] == "gpt-4o-mini" and r["base_url"].endswith("openai.com/v1")
    assert P.is_ready(r) is False
    # user overrides win; a stored key makes it ready
    from macaw import secrets

    secrets.set(P.secret_name("openai"), "sk-test")
    r = P.resolve(
        "openai", {"enabled": True, "model": "gpt-4o", "base_url": "http://x/v1"}
    )
    assert r["model"] == "gpt-4o" and r["base_url"] == "http://x/v1"
    assert r["key"] == "sk-test" and P.is_ready(r) is True
    # ollama needs no key
    assert P.is_ready(P.resolve("ollama", {"enabled": True})) is True


def test_provider_env_key_fallback(monkeypatch, tmp_path):
    from macaw.llm import providers as P

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-env")
    r = P.resolve("anthropic", {"enabled": True})
    assert r["key"] == "ant-env" and P.is_ready(r) is True


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


# -- cloud provider client + dispatch -----------------------------------------


def test_provider_chat_client_openai_and_anthropic():
    # The urllib client must speak both protocols and parse each shape. A tiny
    # local server stands in for the real API (no network, no key).
    import http.server
    import json as _json
    import threading

    from macaw.llm import providers as P

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)) or 0)
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            if self.path.endswith("/messages"):
                out = {"content": [{"type": "text", "text": "ANTHROPIC OK"}]}
            else:
                out = {"choices": [{"message": {"content": "OPENAI OK"}}]}
            self.wfile.write(_json.dumps(out).encode())

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        oa = {"kind": "openai", "base_url": base + "/v1", "key": "k", "model": "m"}
        assert P.chat(oa, "sys", "hi") == "OPENAI OK"
        an = {"kind": "anthropic", "base_url": base, "key": "k", "model": "m"}
        assert P.chat(an, "sys", "hi") == "ANTHROPIC OK"
    finally:
        srv.shutdown()


def test_formatter_dispatches_to_provider(monkeypatch):
    from macaw.llm import formatter as F

    seen = {}

    def fake_chat(resolved, system, text, ssl_verify=True, timeout=60):
        seen["model"] = resolved["model"]
        seen["system"] = system
        return "PROVIDER OUT"

    monkeypatch.setattr(F.providers, "chat", fake_chat)
    prov = {
        "kind": "openai",
        "base_url": "http://x/v1",
        "key": "k",
        "model": "gpt-4o-mini",
        "needs_key": True,
    }
    f = F.Formatter("provider:openai", "", prov, True)
    assert f.is_provider() and f.is_ready()
    assert f.format("hello there") == "PROVIDER OUT"
    assert seen["model"] == "gpt-4o-mini"
    assert "transcription formatter" in seen["system"]  # smart default reached it
