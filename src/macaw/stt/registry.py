from __future__ import annotations

from macaw.stt.base import Backend, ModelInfo

_BACKENDS: dict[str, type[Backend]] = {}
_MODELS: dict[str, ModelInfo] = {}

DEFAULT_MODEL = "large-v3-turbo"


def register(backend_cls: type[Backend]) -> type[Backend]:
    """Class decorator: register a backend implementation by its `key`.

    Models are declared in YAML (see stt/catalog.py) and bound to a backend by
    that key, so a backend only provides load()/transcribe() — not metadata.
    """
    if not backend_cls.key:
        raise ValueError(f"{backend_cls.__name__} must set a non-empty `key`")
    _BACKENDS[backend_cls.key] = backend_cls
    return backend_cls


def register_model(info: ModelInfo) -> None:
    """Register one catalog model against its backend (called by load_catalog)."""
    if info.backend not in _BACKENDS:
        raise ValueError(
            f"model {info.id!r}: unknown backend {info.backend!r} "
            f"(registered: {sorted(_BACKENDS)})"
        )
    if info.id in _MODELS:
        raise ValueError(f"Duplicate model id: {info.id!r}")
    _MODELS[info.id] = info


def list_models() -> list[ModelInfo]:
    """All registered models, in registration order (backend, then declared)."""
    return list(_MODELS.values())


def get_model_info(model_id: str) -> ModelInfo:
    """Resolve a model id, falling back to the default if it's unknown."""
    return _MODELS.get(model_id) or _MODELS[DEFAULT_MODEL]


def create_backend(
    model_id: str,
    language: str = "en",
    punctuation_hints: bool = True,
) -> Backend:
    """Instantiate the backend that serves `model_id`."""
    info = get_model_info(model_id)
    return _BACKENDS[info.backend](info, language, punctuation_hints)
