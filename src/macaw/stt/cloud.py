from __future__ import annotations

import importlib.util
import io
import os
import wave

import numpy as np

from macaw.stt.base import Backend, MissingDependency
from macaw.stt.registry import register

# OpenAI cloud transcription (gpt-4o-transcribe / gpt-4o-mini-transcribe).
# Runs in-process over HTTPS — no local weights, no isolated venv. Needs the
# `openai` SDK in the MAIN env (pip install 'macaw[openai]') and an API key.
# Models/provenance live in stt/models/cloud.yaml.

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


@register
class OpenAICloudBackend(Backend):
    """OpenAI cloud speech-to-text. No download; billed per use via your key."""

    key = "openai"

    def api_key(self) -> str:
        """API key: the encrypted OpenAI provider secret, else the legacy config
        field (pre-encryption configs), else the OPENAI_API_KEY env var."""
        from macaw import secrets
        from macaw.config import Config
        from macaw.llm.providers import secret_name

        return (
            secrets.get(secret_name("openai"))
            or Config.load().openai_api_key
            or os.environ.get("OPENAI_API_KEY", "")
        )

    # -- capability / weight management --------------------------------

    def available(self) -> bool:
        return importlib.util.find_spec("openai") is not None

    def hf_repos(self) -> list[str]:
        return []  # cloud model — nothing to download or cache

    def is_ready(self, cache: dict[str, int] | None = None) -> bool:
        return self.available() and bool(self.api_key())

    def model_url(self) -> str:
        return _ENDPOINT_DOCS

    # -- inference ------------------------------------------------------

    def load(self, model_path: str | None = None) -> None:
        if not self.available():
            raise MissingDependency(
                "openai package not installed — run: pip install 'macaw[openai]'"
            )
        if not self.api_key():
            raise MissingDependency(
                "no OpenAI API key — set it in Settings or export OPENAI_API_KEY"
            )

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> str:
        from openai import OpenAI

        key = self.api_key()
        if not key:
            raise MissingDependency(
                "no OpenAI API key — set it in Settings or export OPENAI_API_KEY"
            )
        kwargs: dict = {
            "model": self.model.id,
            "file": ("audio.wav", _to_wav_bytes(audio, sample_rate)),
            "response_format": "json",  # only format these models support
        }
        if self.language:
            kwargs["language"] = self.language
        resp = OpenAI(**self._client_args(key)).audio.transcriptions.create(**kwargs)
        return (resp.text or "").strip()

    def _client_args(self, key: str) -> dict:
        """OpenAI client kwargs. The proxy is read from the environment (set by
        macaw.net); disabling SSL verification needs an explicit httpx client."""
        from macaw.config import Config

        args: dict = {"api_key": key}
        if not Config.load().ssl_verify:
            import httpx

            args["http_client"] = httpx.Client(verify=False)
        return args
