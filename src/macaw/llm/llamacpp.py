from __future__ import annotations

import typing

from macaw.llm.isolated import LlmSubprocessBackend
from macaw.llm.registry import register


@register
class LlamaCppBackend(LlmSubprocessBackend):
    """llama.cpp (GGUF) in the isolated 'llm' venv — lightning-fast local
    formatting on CPU, GPU-offloaded when a CUDA build is installed.

    Inference lives in worker.py; models and provenance live in
    ``llm/models/local.yaml``. The heavy runtime stays out of the base env,
    which keeps the frozen engine binary small.
    """

    key = "llamacpp"

    def download(self, progress_callback: typing.Callable | None = None) -> str:
        if not (self.model.repo and self.model.filename):
            return ""
        import huggingface_hub
        from tqdm.auto import tqdm as _tqdm_base

        class _tqdm(_tqdm_base):
            def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
                kwargs["disable"] = progress_callback is None
                super().__init__(*args, **kwargs)

            def update(self, n: int = 1) -> None:
                super().update(n)
                if progress_callback and self.total:
                    progress_callback(int(100 * self.n / self.total))

        return huggingface_hub.hf_hub_download(
            self.model.repo, self.model.filename, tqdm_class=_tqdm
        )
