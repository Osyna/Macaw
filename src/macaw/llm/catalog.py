"""YAML catalog for text-formatting models (mirrors stt/catalog.py).

Every LLM model's metadata and provenance live in a YAML file under
``llm/models/``. This loader reads them into ``LlmInfo`` objects and registers
each against the backend that serves it. Any file-level key is inherited by
every model in the file unless the model overrides it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import yaml

from macaw.llm.base import LlmInfo

logger = logging.getLogger("macaw")

MODELS_DIR = Path(__file__).parent / "models"
_REQUIRED = ("id", "backend", "label", "size", "speed")
_INHERITED = (
    "backend",
    "source_url",
    "extra",
    "repo",
    "cloud",
    "size",
    "hardware",
    "vram",
    "min_specs",
    "rec_specs",
    "n_ctx",
)
_VALID = set(LlmInfo.__dataclass_fields__)


class CatalogError(RuntimeError):
    """An LLM model YAML file is malformed or references something unknown."""


def _model_info(entry: dict, defaults: dict, source: str) -> LlmInfo:
    m = {**defaults, **entry}  # a model entry overrides its file's defaults
    for key in _REQUIRED:
        if not m.get(key):
            raise CatalogError(f"{source}: model missing required '{key}'")
    unknown = set(m) - _VALID
    if unknown:
        raise CatalogError(f"{source} [{m['id']}]: unknown keys {sorted(unknown)}")
    return LlmInfo(
        id=str(m["id"]),
        backend=str(m["backend"]),
        label=str(m["label"]),
        size=str(m["size"]),
        speed=str(m["speed"]),
        cloud=bool(m.get("cloud", False)),
        recommended=bool(m.get("recommended", False)),
        extra=m.get("extra") or None,
        hardware=str(m.get("hardware", "CPU / Any")),
        vram=str(m.get("vram", "—")),
        notes=str(m.get("notes", "")),
        rating=int(m.get("rating", 0)),
        min_specs=str(m.get("min_specs", "")),
        rec_specs=str(m.get("rec_specs", "")),
        source_url=str(m.get("source_url", "")),
        repo=str(m.get("repo", "")),
        filename=str(m.get("filename", "")),
        n_ctx=int(m.get("n_ctx", 4096)),
    )


def read_models(paths: list[Path]) -> list[LlmInfo]:
    """Parse the given YAML files into LlmInfo objects (no registration)."""
    out: list[LlmInfo] = []
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        defaults = {k: data[k] for k in _INHERITED if k in data}
        models = data.get("models") or []
        if not models:
            raise CatalogError(f"{path.name}: no models declared")
        for entry in models:
            out.append(_model_info(entry, defaults, path.name))
    return out


def load_catalog(
    register_fn: Callable[[LlmInfo], None], models_dir: Path = MODELS_DIR
) -> int:
    """Read every ``*.yaml`` under ``models_dir`` and register each model."""
    paths = sorted(models_dir.glob("*.yaml"))
    infos = read_models(paths)
    for info in infos:
        register_fn(info)
    return len(infos)
