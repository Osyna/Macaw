"""Cloud LLM providers for text formatting.

Every provider speaks one of two protocols — OpenAI-compatible chat
(``/chat/completions``) or Anthropic messages (``/v1/messages``) — so a tiny
stdlib ``urllib`` client covers all of them. No SDK dependency, and it works in
the frozen engine out of the box. This is deliberately narrow: one blocking
POST that turns (system, text) into formatted text.

A provider's effective config layers three sources:
    preset defaults  ⊕  the user's config.providers[id] overrides  ⊕  the key
(the key comes from the encrypted secret store, or an env var as a fallback).
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass

from macaw import secrets

logger = logging.getLogger("macaw")


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    label: str
    kind: str  # "openai" | "anthropic"
    base_url: str
    docs_url: str
    models: tuple[str, ...]  # suggested model ids (first = default)
    env: str = ""  # env var the key falls back to
    needs_key: bool = True
    note: str = ""
    stt_models: tuple[str, ...] = ()  # transcription model ids (cloud voice)


# Built-in providers. `custom` lets users point at any OpenAI-compatible server.
PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        "openai",
        "OpenAI",
        "openai",
        "https://api.openai.com/v1",
        "https://platform.openai.com/api-keys",
        ("gpt-4o-mini", "gpt-4.1-mini", "gpt-4o"),
        env="OPENAI_API_KEY",
        stt_models=("gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"),
    ),
    ProviderPreset(
        "anthropic",
        "Anthropic (Claude)",
        "anthropic",
        "https://api.anthropic.com",
        "https://console.anthropic.com/settings/keys",
        ("claude-3-5-haiku-latest", "claude-3-5-sonnet-latest"),
        env="ANTHROPIC_API_KEY",
    ),
    ProviderPreset(
        "gemini",
        "Google Gemini",
        "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "https://aistudio.google.com/apikey",
        ("gemini-2.0-flash", "gemini-1.5-flash"),
        env="GEMINI_API_KEY",
    ),
    ProviderPreset(
        "xai",
        "xAI (Grok)",
        "openai",
        "https://api.x.ai/v1",
        "https://console.x.ai",
        ("grok-2-latest", "grok-beta"),
        env="XAI_API_KEY",
    ),
    ProviderPreset(
        "openrouter",
        "OpenRouter",
        "openai",
        "https://openrouter.ai/api/v1",
        "https://openrouter.ai/keys",
        (
            "openai/gpt-4o-mini",
            "anthropic/claude-3.5-haiku",
            "meta-llama/llama-3.1-8b-instruct",
        ),
        env="OPENROUTER_API_KEY",
        note="One key, hundreds of models.",
    ),
    ProviderPreset(
        "groq",
        "Groq",
        "openai",
        "https://api.groq.com/openai/v1",
        "https://console.groq.com/keys",
        ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
        env="GROQ_API_KEY",
        note="Very fast inference.",
        stt_models=(
            "whisper-large-v3-turbo",
            "whisper-large-v3",
            "distil-whisper-large-v3-en",
        ),
    ),
    ProviderPreset(
        "mistral",
        "Mistral",
        "openai",
        "https://api.mistral.ai/v1",
        "https://console.mistral.ai/api-keys",
        ("mistral-small-latest", "open-mistral-nemo"),
        env="MISTRAL_API_KEY",
    ),
    ProviderPreset(
        "deepseek",
        "DeepSeek",
        "openai",
        "https://api.deepseek.com",
        "https://platform.deepseek.com/api_keys",
        ("deepseek-chat",),
        env="DEEPSEEK_API_KEY",
    ),
    ProviderPreset(
        "together",
        "Together AI",
        "openai",
        "https://api.together.xyz/v1",
        "https://api.together.ai/settings/api-keys",
        ("meta-llama/Llama-3.3-70B-Instruct-Turbo",),
        env="TOGETHER_API_KEY",
    ),
    ProviderPreset(
        "ollama",
        "Ollama (local server)",
        "openai",
        "http://localhost:11434/v1",
        "https://ollama.com/library",
        ("llama3.2", "qwen2.5", "phi3.5"),
        needs_key=False,
        note="Run models locally on your own Ollama server.",
    ),
    ProviderPreset(
        "custom",
        "Custom (OpenAI-compatible)",
        "openai",
        "",
        "",
        (),
        env="",
        note="Any OpenAI-compatible endpoint — set the Base URL.",
    ),
)

PRESET_BY_ID = {p.id: p for p in PRESETS}


def secret_name(provider_id: str) -> str:
    return f"provider.{provider_id}"


def resolve(provider_id: str, user: dict | None) -> dict:
    """Effective config for a provider: preset ⊕ user overrides ⊕ key."""
    preset = PRESET_BY_ID.get(provider_id)
    if preset is None:
        raise ValueError(f"unknown provider: {provider_id}")
    user = user or {}
    key = secrets.get(secret_name(provider_id))
    if not key and preset.env:
        key = os.environ.get(preset.env, "")
    model = user.get("model") or (preset.models[0] if preset.models else "")
    # cloud voice: explicit list, else a whisper default for OpenAI-compatible
    # endpoints (Anthropic has no transcription API, so none).
    stt = list(preset.stt_models) or (["whisper-1"] if preset.kind == "openai" else [])
    return {
        "id": provider_id,
        "label": preset.label,
        "kind": preset.kind,
        "base_url": (user.get("base_url") or preset.base_url).strip(),
        "model": model,
        "enabled": bool(user.get("enabled", False)),
        "needs_key": preset.needs_key,
        "key": key,
        "key_set": bool(key),
        "docs_url": preset.docs_url,
        "models": list(preset.models),
        "note": preset.note,
        "env": preset.env,
        "stt_models": stt,
    }


def is_ready(resolved: dict) -> bool:
    """True if this provider can format now: a model, and a key if required."""
    if not resolved.get("base_url") or not resolved.get("model"):
        return False
    return bool(resolved.get("key")) or not resolved.get("needs_key")


# ── the client ───────────────────────────────────────────────────────


def _post(url: str, headers: dict, body: dict, ssl_verify: bool, timeout: int) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    ctx = None if ssl_verify else ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:400]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"connection failed: {exc.reason}") from exc


def chat(
    resolved: dict,
    system: str,
    text: str,
    *,
    ssl_verify: bool = True,
    timeout: int = 120,
) -> str:
    """One formatting round-trip through a resolved provider."""
    base = resolved["base_url"].rstrip("/")
    key = resolved.get("key", "")
    model = resolved["model"]
    if resolved["kind"] == "anthropic":
        body = {
            "model": model,
            "system": system,
            "max_tokens": 2048,
            "temperature": 0,
            "messages": [{"role": "user", "content": text}],
        }
        headers = {
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
        data = _post(f"{base}/v1/messages", headers, body, ssl_verify, timeout)
        parts = data.get("content") or []
        return "".join(p.get("text", "") for p in parts).strip()
    # OpenAI-compatible
    body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
    }
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    data = _post(f"{base}/chat/completions", headers, body, ssl_verify, timeout)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"empty response: {json.dumps(data)[:300]}")
    return (choices[0].get("message", {}).get("content") or "").strip()
