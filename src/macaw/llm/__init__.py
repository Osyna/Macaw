"""Pluggable text-formatting backends (post-STT correction / formatting).

After speech-to-text produces final text, Macaw can optionally pass it through a
small, fast LLM that fixes punctuation and capitalization, trims dictation filler
and shapes the text to what it is — an email, a message, a list. "Smart mode" is
just the built-in default prompt (see ``prompts.py``); users can replace it.

Models are data, like the STT catalog: metadata + provenance live in YAML under
``llm/models/``. Backends are code (``llamacpp`` local, ``openai-llm`` cloud).

Add a model to an existing backend
    Append an entry to that backend's YAML. Nothing else changes.

Add a new backend
    Write ``llm/<name>.py`` with a ``@register`` LlmBackend subclass
    (``load``/``format``), import it below, and declare its model(s) in
    ``llm/models/<name>.yaml``.
"""

from __future__ import annotations

# Import for the @register side effect. Heavy deps (llama.cpp, openai) stay
# lazy — these modules only bind a key at import time.
from macaw.llm import llamacpp, openai  # noqa: E402,F401
from macaw.llm.base import LlmBackend, LlmInfo, MissingDependency
from macaw.llm.catalog import CatalogError, load_catalog
from macaw.llm.registry import (
    create_backend,
    get_model_info,
    list_models,
    register,
    register_model,
)

# Bind every YAML-declared model to its backend (fails loudly on a bad file).
load_catalog(register_model)

__all__ = [
    "LlmBackend",
    "LlmInfo",
    "MissingDependency",
    "CatalogError",
    "register",
    "register_model",
    "list_models",
    "get_model_info",
    "create_backend",
]
