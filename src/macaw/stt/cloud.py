from __future__ import annotations

import importlib.util
import io
import wave

import numpy as np

from macaw.stt.base import Backend, MissingDependency
from macaw.stt.registry import register

# Cloud speech-to-text over any OpenAI-compatible transcription endpoint
# (/v1/audio/transcriptions). One backend serves every configured provider: the
# model id encodes the provider + model as ``cloud:<provider>:<model>`` (e.g.
# ``cloud:groq:whisper-large-v3-turbo``). No local weights or venv — it runs
# in-process via the `openai` SDK (pip install 'macaw[openai]') against the
# provider's base URL + key. Keys live in the encrypted provider secret store;
# the models a provider offers live in llm/providers.py (`stt_models`).

_ENDPOINT_DOCS = "https://platform.openai.com/docs/guides/speech-to-text"


def _to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    """Encode mono float32 [-1, 1] audio as 16-bit PCM WAV bytes (stdlib only)."""
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


def split_id(model_id: str) -> tuple[str, str]:
    """``cloud:<provider>:<model>`` -> (provider_id, model). A bare id maps to
    the OpenAI provider (legacy / direct)."""
    if model_id.startswith("cloud:"):
        _, pid, model = model_id.split(":", 2)
        return pid, model
    return "openai", model_id


@register
class OpenAICloudBackend(Backend):
    """Cloud speech-to-text over an OpenAI-compatible provider. No download;
    billed per use via your provider key."""

    key = "cloud"

    def _resolved(self) -> dict:
        from macaw.config import Config
        from macaw.llm import providers

        pid, _ = split_id(self.model.id)
        return providers.resolve(pid, Config.load().providers.get(pid))

    def api_key(self) -> str:
        return self._resolved().get("key", "")

    # -- capability / weight management --------------------------------

    def available(self) -> bool:
        return importlib.util.find_spec("openai") is not None

    def hf_repos(self) -> list[str]:
        return []  # cloud model — nothing to download or cache

    def is_ready(self, cache: dict[str, int] | None = None) -> bool:
        r = self._resolved()
        return self.available() and (bool(r.get("key")) or not r.get("needs_key"))

    def model_url(self) -> str:
        return self._resolved().get("docs_url") or _ENDPOINT_DOCS

    # -- inference ------------------------------------------------------

    def load(self, model_path: str | None = None) -> None:
        if not self.available():
            raise MissingDependency(
                "openai package not installed — run: pip install 'macaw[openai]'"
            )
        if not self.is_ready():
            raise MissingDependency(
                "cloud provider not configured — enable it and set a key in the "
                "Providers window"
            )

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> str:
        from openai import OpenAI

        _, model = split_id(self.model.id)
        r = self._resolved()
        kwargs: dict = {
            "model": model,
            "file": ("audio.wav", _to_wav_bytes(audio, sample_rate)),
            "response_format": "json",  # the format these models reliably support
        }
        if self.language:
            kwargs["language"] = self.language
        resp = OpenAI(**self._client_args(r)).audio.transcriptions.create(**kwargs)
        return (resp.text or "").strip()

    def _client_args(self, resolved: dict) -> dict:
        """OpenAI client kwargs: the provider's base URL + key. The proxy is read
        from the environment (set by macaw.net); disabling SSL verification needs
        an explicit httpx client."""
        from macaw.config import Config

        args: dict = {"api_key": resolved.get("key") or "none"}
        if resolved.get("base_url"):
            args["base_url"] = resolved["base_url"]
        if not Config.load().ssl_verify:
            import httpx

            args["http_client"] = httpx.Client(verify=False)
        return args
