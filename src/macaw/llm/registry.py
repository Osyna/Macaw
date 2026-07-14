from __future__ import annotations

from macaw.llm.base import LlmBackend, LlmInfo

_BACKENDS: dict[str, type[LlmBackend]] = {}
_MODELS: dict[str, LlmInfo] = {}


def register(backend_cls: type[LlmBackend]) -> type[LlmBackend]:
    """Class decorator: register an LLM backend by its ``key``."""
    if not backend_cls.key:
        raise ValueError(f"{backend_cls.__name__} must set a non-empty `key`")
    _BACKENDS[backend_cls.key] = backend_cls
    return backend_cls


def register_model(info: LlmInfo) -> None:
    """Register one catalog model against its backend (called by load_catalog)."""
    if info.backend not in _BACKENDS:
        raise ValueError(
            f"llm model {info.id!r}: unknown backend {info.backend!r} "
            f"(registered: {sorted(_BACKENDS)})"
        )
    if info.id in _MODELS:
        raise ValueError(f"Duplicate llm model id: {info.id!r}")
    _MODELS[info.id] = info


def list_models() -> list[LlmInfo]:
    return list(_MODELS.values())


def get_model_info(model_id: str) -> LlmInfo | None:
    """Resolve a model id, or None when unknown (LLM formatting is optional)."""
    return _MODELS.get(model_id)


def create_backend(model_id: str) -> LlmBackend:
    """Instantiate the backend that serves ``model_id``."""
    info = _MODELS[model_id]
    return _BACKENDS[info.backend](info)
