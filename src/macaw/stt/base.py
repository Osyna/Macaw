from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class ModelInfo:
    """Everything the UI and registry need to know about one model.

    `id` is what gets stored in config.yaml and shown/selected by the user.
    Keep it unique across all backends. The `vram`/`hardware`/`notes` fields
    drive the recommendations shown in the Model Manager — fill them in when
    you add a model and they surface automatically.
    """

    id: str
    backend: str  # Backend.key that provides this model
    label: str  # short human name, e.g. "Parakeet TDT v3"
    size: str  # download size, e.g. "~1.6 GB"
    speed: str  # e.g. "very fast"
    languages: str  # e.g. "99+", "EN", "25"
    streaming: bool = False
    lang_select: bool = False  # show a per-model language chooser in the card
    cloud: bool = False  # cloud API model (no local weights; needs an API key)
    recommended: bool = False  # surfaced as the recommended pick in the Manager
    extra: str | None = None  # pip extra required, e.g. "nemo"; None if in base deps
    # -- hardware recommendations (shown in the Model Manager) --
    hardware: str = "CPU / Any"  # e.g. "NVIDIA / AMD GPU", "CPU / Intel"
    vram: str = "—"  # e.g. "~6 GB", "—" for CPU-light models
    notes: str = ""  # any extra hint
    rating: int = 0  # curated 0–5 stars, read-only, drives the list sort order
    min_specs: str = ""  # minimal system to run it (shown in the Manager)
    rec_specs: str = ""  # recommended system (shown in the Manager)
    # -- provenance (shown in the Model Manager) --
    source_url: str = ""  # GitHub/library page implementing this backend
    repo: str = ""  # HF repo id for the weights → download link + cache size
    params: tuple[Param, ...] = ()  # user-tunable settings rendered as controls


@dataclass(frozen=True)
class Param:
    """A user-tunable model parameter, rendered as a control in the Manager.

    A backend declares `params`; each maps to a value stored per model in config
    and passed to the backend at transcription time. Backends with no tunables
    simply declare none.
    """

    key: str
    label: str
    kind: str  # "bool" | "int" | "float"
    default: bool | int | float
    minimum: float = 0.0
    maximum: float = 1.0
    step: float = 1.0
    hint: str = ""


class MissingDependency(RuntimeError):
    """Raised by a backend when its optional dependency isn't installed."""


# -- shared HuggingFace cache helpers (whisper/nemo/voxtral all use HF hub) --


def hf_cache_sizes() -> dict[str, int]:
    """Map every cached HF repo id to its on-disk size — one scan, reused by
    every card so the Model Manager doesn't scan the cache once per model."""
    try:
        from huggingface_hub import scan_cache_dir

        return {r.repo_id: r.size_on_disk for r in scan_cache_dir().repos}
    except Exception:
        return {}


def hf_repo_size(repos: list[str]) -> int:
    """Total on-disk bytes for the given HF repo ids (0 on any error)."""
    if not repos:
        return 0
    sizes = hf_cache_sizes()
    return sum(sizes.get(r, 0) for r in repos)


def hf_repo_delete(repos: list[str]) -> int:
    """Delete the given HF repo ids from cache. Returns bytes freed (0 on error)."""
    if not repos:
        return 0
    try:
        from huggingface_hub import scan_cache_dir

        wanted = set(repos)
        info = scan_cache_dir()
        sel = [r for r in info.repos if r.repo_id in wanted]
        revs = [rev.commit_hash for r in sel for rev in r.revisions]
        total = sum(r.size_on_disk for r in sel)
        if revs:
            info.delete_revisions(*revs).execute()
        return total
    except Exception:
        return 0


class Backend(abc.ABC):
    """A speech-to-text backend. One instance owns one loaded model.

    Contract: audio handed to `transcribe()` is ALWAYS mono float32 at 16 kHz.
    The Transcriber normalizes channels/rate/dtype and gates silence before
    calling, so backends never deal with resampling or multi-channel input.

    To add a new model, that's the whole job:
        1. Subclass Backend and set a unique `key`.
        2. Implement `load()` and `transcribe()`.
        3. Override `available()` / `hf_repos()` if it has an optional dep or
           its weights aren't under `model.id` on the HF hub.
        4. Decorate the class with `@register`, import it in stt/__init__.py, and
           declare its model(s) in a stt/models/*.yaml catalog file.
    """

    key: str = ""  # unique id; the YAML catalog binds models to this backend

    # -- metadata (comes from the model's YAML entry, not the class) ---
    @property
    def params(self) -> list[Param]:
        """User-tunable settings for the active model (from the catalog)."""
        return list(self.model.params)

    @property
    def source_url(self) -> str:
        """GitHub/library page for this model (from the catalog)."""
        return self.model.source_url

    def __init__(
        self,
        model: ModelInfo,
        language: str = "en",
        punctuation_hints: bool = True,
    ) -> None:
        self.model = model
        self.language = language
        self.punctuation_hints = punctuation_hints
        self._param_values: dict = {}

    def set_params(self, values: dict) -> None:
        """Set user-chosen parameter values (from config) for this model."""
        self._param_values = values or {}

    def param(self, key: str):
        """Resolve a parameter's value, falling back to its declared default."""
        for p in self.params:
            if p.key == key:
                return self._param_values.get(key, p.default)
        return None

    @abc.abstractmethod
    def load(self, model_path: str | None = None) -> None:
        """Load the model into memory. Called before the first transcribe()."""

    @abc.abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> str:
        """Transcribe mono float32 16 kHz audio. Return stripped text ('' if none)."""

    def transcribe_partial(
        self, audio: np.ndarray, sample_rate: int = 16_000
    ) -> str | None:
        """Feed ONLY the new samples of an ongoing utterance to a natively
        streaming decoder; returns the full partial text so far — or None when
        this backend can't decode incrementally (callers then fall back to
        re-transcribing the whole buffer). Default: unsupported."""
        return None

    # -- capability / dependency ---------------------------------------

    def available(self) -> bool:
        """True if this backend's dependencies are importable (no heavy import).

        Override with an `importlib.util.find_spec` check for optional deps.
        """
        return True

    # -- weight management (HF hub by default) -------------------------

    def hf_repos(self) -> list[str]:
        """HF repo ids in the shared cache, for size/download/delete.

        Default: the model's `repo` from the catalog. Return [] if the weights
        aren't in the shared HF cache (e.g. isolated-venv backends).
        """
        return [self.model.repo] if self.model.repo else []

    def model_url(self) -> str:
        """Public page to view/download this model's weights ('' if none).

        Default: the HuggingFace page for the catalog `repo`. This is decoupled
        from hf_repos() so isolated backends can still show a download link.
        """
        return f"https://huggingface.co/{self.model.repo}" if self.model.repo else ""

    def download(self, progress_callback: Callable[[int], None] | None = None) -> str:
        """Fetch weights to disk. Returns a local path ('' if not applicable)."""
        repos = self.hf_repos()
        if not repos:
            return ""
        from huggingface_hub import snapshot_download

        path = ""
        for repo in repos:
            path = snapshot_download(repo)
        return path

    def disk_size(self, cache: dict[str, int] | None = None) -> int:
        """Bytes this model occupies on disk (0 if not downloaded).

        Pass a `hf_cache_sizes()` dict to avoid re-scanning the cache per model.
        """
        repos = self.hf_repos()
        if not repos:
            return 0
        if cache is None:
            return hf_repo_size(repos)
        return sum(cache.get(r, 0) for r in repos)

    def is_ready(self, cache: dict[str, int] | None = None) -> bool:
        """True if this model can transcribe right now: dependency present and
        weights available. Backends that manage their own download (no HF repo)
        are ready as soon as the dependency is installed."""
        if not self.available():
            return False
        if not self.hf_repos():
            return True
        return self.disk_size(cache) > 0

    def delete(self) -> int:
        """Remove this model's weights from disk. Returns bytes freed."""
        return hf_repo_delete(self.hf_repos())

    def unload(self) -> None:
        """Release the model from memory. Default: no-op."""
