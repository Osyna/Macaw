from __future__ import annotations

import abc
from dataclasses import dataclass

# The HF-cache accounting helpers are backend-agnostic (they just size/delete
# repo ids in the shared HuggingFace cache), so the LLM stack reuses them
# rather than growing a second copy.
from macaw.stt.base import (
    MissingDependency,
    hf_cache_sizes,
    hf_repo_delete,
    hf_repo_size,
)

__all__ = ["LlmInfo", "LlmBackend", "MissingDependency", "hf_cache_sizes"]


@dataclass(frozen=True)
class LlmInfo:
    """One text-formatting model, from a YAML catalog entry.

    Mirrors stt.ModelInfo but for text→text formatters: no audio/streaming,
    plus GGUF ``filename`` and context ``n_ctx`` the local backend needs to
    build the model. Cloud models carry no weights (``repo`` empty).
    """

    id: str
    backend: str  # LlmBackend.key that serves this model
    label: str  # short human name, e.g. "Qwen2.5 1.5B"
    size: str  # download size, e.g. "~1.0 GB" ("—" for cloud)
    speed: str  # e.g. "instant", "very fast"
    cloud: bool = False  # cloud API model (no local weights; needs an API key)
    recommended: bool = False  # surfaced as the recommended pick in the LLM tab
    extra: str | None = None  # pip extra required (e.g. "llm"); None if in base deps
    hardware: str = "CPU / Any"
    vram: str = "—"
    notes: str = ""  # pros ('+') / cons ('−') / plain, like the STT catalog
    rating: int = 0  # curated 0–5 stars, drives the list sort order
    min_specs: str = ""
    rec_specs: str = ""
    source_url: str = ""  # library / provider page (provenance link)
    repo: str = ""  # HF repo id for GGUF weights (download link + cache size)
    filename: str = ""  # GGUF file within the repo (glob ok, e.g. "*Q4_K_M.gguf")
    n_ctx: int = 4096  # context window the local runtime allocates


class LlmBackend(abc.ABC):
    """A text-formatting backend. One instance owns one loaded model.

    Contract: ``format(text, system)`` returns the reworked text — corrected,
    punctuated and shaped per the ``system`` instruction — and NOTHING else
    (no preamble, no answering the content). Empty in → empty out.

    To add a backend: subclass, set ``key``, implement ``load``/``format``,
    override ``available``/``hf_repos`` for optional deps or non-HF weights,
    decorate with ``@register``, import it in ``llm/__init__.py`` and declare
    its model(s) in an ``llm/models/*.yaml`` catalog file.
    """

    key: str = ""  # unique id; the YAML catalog binds models to this backend
    cloud: bool = False  # cloud backends skip download/venv and need a key

    def __init__(self, model: LlmInfo) -> None:
        self.model = model
        self._api_key = ""
        self._base_url = ""
        # last format() timing, for the LLM tab's Try-it stat (0 when unknown)
        self.last_tps = 0.0
        self.last_secs = 0.0

    def configure(self, api_key: str = "", base_url: str = "") -> None:
        """Supply cloud credentials/endpoint (no-op for local backends)."""
        self._api_key = api_key or ""
        self._base_url = base_url or ""

    @property
    def source_url(self) -> str:
        return self.model.source_url

    @abc.abstractmethod
    def load(self) -> None:
        """Bring the model up (persistent worker / client). Idempotent."""

    @abc.abstractmethod
    def format(self, text: str, system: str) -> str:
        """Rework ``text`` per the ``system`` instruction. '' → ''."""

    # -- capability / dependency ---------------------------------------

    def available(self) -> bool:
        """True if this backend's dependency is importable/installed."""
        return True

    # -- weight management (HF hub by default) -------------------------

    def hf_repos(self) -> list[str]:
        return [self.model.repo] if self.model.repo else []

    def model_url(self) -> str:
        return f"https://huggingface.co/{self.model.repo}" if self.model.repo else ""

    def download(self, progress_callback=None) -> str:
        """Fetch weights to the shared HF cache. '' if not applicable."""
        return ""

    def disk_size(self, cache: dict[str, int] | None = None) -> int:
        repos = self.hf_repos()
        if not repos:
            return 0
        if cache is None:
            return hf_repo_size(repos)
        return sum(cache.get(r, 0) for r in repos)

    def is_ready(self, cache: dict[str, int] | None = None) -> bool:
        """True if this model can format right now: dependency present and
        weights (or, for cloud, an API key) available."""
        if not self.available():
            return False
        if self.cloud:
            return bool(self._api_key)
        if not self.hf_repos():
            return True
        return self.disk_size(cache) > 0

    def delete(self) -> int:
        return hf_repo_delete(self.hf_repos())

    def unload(self) -> None:
        """Release the model. Default: no-op."""
