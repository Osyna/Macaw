from __future__ import annotations

import typing

from macaw.stt.isolated import SubprocessBackend
from macaw.stt.registry import register

# Only the CTranslate2 weights — skips the original PyTorch checkpoints that
# share some of these repos, keeping downloads (and the HF cache) small.
_ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]


@register
class WhisperBackend(SubprocessBackend):
    """faster-whisper (CTranslate2) in its own isolated venv (extra: whisper).

    Inference lives in worker.py (`_load_whisper`) — CUDA with automatic CPU
    fallback; tunables and language ride the worker's config line. Models,
    params and provenance live in ``stt/models/whisper.yaml``. The engine
    itself stays free of ctranslate2/av/onnxruntime, which is what keeps the
    frozen binary small.
    """

    key = "whisper"

    def download(self, progress_callback: typing.Callable | None = None) -> str:
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

        return huggingface_hub.snapshot_download(
            self.model.repo, allow_patterns=_ALLOW_PATTERNS, tqdm_class=_tqdm
        )
