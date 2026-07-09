"""Pluggable speech-to-text backends.

Models are data, not code. Metadata, provenance and tunable parameters live in
YAML under ``stt/models/`` (see ``catalog.py`` for the schema).

Add a model to an existing backend
    Append an entry to that backend's YAML file. Nothing else changes.

Add a brand-new backend + its own library
    1. Write ``stt/<name>.py``::

        from macaw.stt.base import Backend
        from macaw.stt.registry import register

        @register
        class MyBackend(Backend):
            key = "mybackend"                       # matches `backend:` in the YAML

            def load(self, model_path=None): ...
            def transcribe(self, audio, sample_rate=16_000) -> str: ...
            # override available()/hf_repos()/download() only for special cases

    2. Write ``stt/models/<name>.yaml`` describing its models (id, label, repo,
       size, source_url, params, …).
    3. Import the module below so it registers on startup.

That is the whole job — the Model Manager UI, download/size/delete, provenance
links and parameter controls are all driven by the YAML.
"""

from __future__ import annotations

# Import backends for their @register side effect. Heavy deps stay lazy —
# these modules only bind a key at import time; weights/inference load later.
from macaw.stt import (  # noqa: E402,F401
    cloud,
    moonshine,
    nemo,
    sherpa,
    voxtral,
    whisper,
)
from macaw.stt.base import Backend, MissingDependency, ModelInfo
from macaw.stt.catalog import CatalogError, load_catalog
from macaw.stt.registry import (
    create_backend,
    get_model_info,
    list_models,
    register,
    register_model,
)

# Bind every YAML-declared model to its backend (fails loudly if a file is bad).
load_catalog(register_model)

__all__ = [
    "Backend",
    "ModelInfo",
    "MissingDependency",
    "CatalogError",
    "register",
    "list_models",
    "get_model_info",
    "create_backend",
]
