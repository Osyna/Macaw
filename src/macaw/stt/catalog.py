"""YAML model catalog.

Every model's metadata, provenance and tunable parameters live in a YAML file
under ``stt/models/``. This loader reads them into ``ModelInfo`` objects and
registers them against the backend that serves them.

To add a model to an existing backend: append an entry to that backend's YAML —
no Python changes. To add a whole new backend: drop a ``<name>.py`` with a
``@register`` Backend subclass (implementing ``load``/``transcribe``) plus a
``<name>.yaml`` describing its models. That is the entire job.

File schema (see any file in ``models/`` for a worked example)::

    backend: whisper                 # default backend for every model below
    source_url: https://github.com/… # library page (provenance link)
    extra: null                      # pip extra for an optional dep, or null
    repo: org/name                   # default HF repo (models may override)
    params:                          # tunables shared by all models (optional)
      - {key: beam_size, label: Beam size, kind: int, default: 1,
         min: 1, max: 10, step: 1, hint: "…"}
    models:
      - id: large-v3-turbo           # unique id (stored in config)
        label: Whisper large-v3-turbo
        repo: org/name               # per-model override of the download repo
        size: "~1.6 GB"
        speed: fast
        languages: "99+"
        hardware: NVIDIA / AMD GPU
        vram: "~6 GB"
        notes: "Default — best balance"   # optional
        # streaming / extra / backend / source_url / params may be overridden here

Any file-level key (backend, source_url, extra, repo, params) is inherited by
every model in that file unless the model overrides it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import yaml

from macaw.stt.base import ModelInfo, Param

logger = logging.getLogger("macaw")

MODELS_DIR = Path(__file__).parent / "models"
_REQUIRED = ("id", "backend", "label", "size", "speed", "languages")
_INHERITED = (
    "backend",
    "source_url",
    "extra",
    "repo",
    "params",
    "lang_select",
    "min_specs",
    "rec_specs",
)


class CatalogError(RuntimeError):
    """A model YAML file is malformed or references something unknown."""


def _param(d: dict, where: str) -> Param:
    try:
        return Param(
            key=d["key"],
            label=d["label"],
            kind=d["kind"],
            default=d["default"],
            minimum=float(d.get("min", 0.0)),
            maximum=float(d.get("max", 1.0)),
            step=float(d.get("step", 1.0)),
            hint=d.get("hint", ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CatalogError(f"{where}: bad param definition ({exc})") from exc


def _model_info(entry: dict, defaults: dict, source: str) -> ModelInfo:
    m = {**defaults, **entry}  # a model entry overrides its file's defaults
    where = f"{source}: model {entry.get('id', '?')!r}"
    missing = [k for k in _REQUIRED if not m.get(k)]
    if missing:
        raise CatalogError(f"{where}: missing required field(s): {', '.join(missing)}")
    params = tuple(_param(p, where) for p in (m.get("params") or []))
    return ModelInfo(
        id=m["id"],
        backend=m["backend"],
        label=m["label"],
        size=str(m["size"]),
        speed=str(m["speed"]),
        languages=str(m["languages"]),
        streaming=bool(m.get("streaming", False)),
        lang_select=bool(m.get("lang_select", False)),
        cloud=bool(m.get("cloud", False)),
        recommended=bool(m.get("recommended", False)),
        extra=m.get("extra"),
        hardware=str(m.get("hardware", "CPU / Any")),
        vram=str(m.get("vram", "—")),
        notes=str(m.get("notes", "")),
        rating=int(m.get("rating", 0)),
        min_specs=str(m.get("min_specs", "")),
        rec_specs=str(m.get("rec_specs", "")),
        source_url=str(m.get("source_url", "")),
        repo=str(m.get("repo", "")),
        params=params,
    )


def read_models(paths: list[Path]) -> list[ModelInfo]:
    """Parse the given YAML files into ModelInfo objects (no registration)."""
    out: list[ModelInfo] = []
    for path in paths:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise CatalogError(f"{path.name}: invalid YAML ({exc})") from exc
        if not isinstance(data, dict):
            raise CatalogError(f"{path.name}: top level must be a mapping")
        models = data.get("models") or []
        if not models:
            logger.warning("catalog: %s declares no models", path.name)
        defaults = {k: data[k] for k in _INHERITED if k in data}
        for entry in models:
            out.append(_model_info(entry, defaults, path.name))
    return out


def load_catalog(
    register_fn: Callable[[ModelInfo], None], models_dir: Path = MODELS_DIR
) -> int:
    """Read every ``*.yaml`` under ``models_dir`` and register each model.

    Returns the number of models registered. Raises CatalogError on any bad or
    empty catalog so a broken file fails loudly at startup, not mid-transcribe.
    """
    files = sorted(models_dir.glob("*.yaml"))
    if not files:
        raise CatalogError(f"no model YAML files found in {models_dir}")
    infos = read_models(files)
    for info in infos:
        register_fn(info)
    return len(infos)
