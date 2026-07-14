#!/usr/bin/env python
"""Isolated LLM formatting worker.

Runs in the 'llm' venv (llama.cpp), holding one GGUF model warm. Protocol on
stdin/stdout, one JSON object per line:

    ->  {"system": "...", "text": "...", "max_tokens": 256}
    <-  {"text": "formatted..."}   or   {"error": "..."}

Launched by macaw.llm.isolated.LlmSubprocessBackend with --repo/--filename/
--n-ctx. The clean stdout channel carries only protocol JSON; everything else
(llama.cpp's chatter) goes to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# This file's dir is on sys.path[0]; drop it so `import llama_cpp` finds the
# real package, never a sibling module, and never reaches back into macaw.
_selfdir = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if p and os.path.abspath(p) != _selfdir]

_PROTO = sys.stdout
sys.stdout = sys.stderr


def _emit(obj: dict) -> None:
    _PROTO.write(json.dumps(obj) + "\n")
    _PROTO.flush()


def _n_gpu_layers() -> int:
    """Offload everything to the GPU when the installed llama.cpp build
    supports it (tiny models fit easily); plain CPU wheels report no offload
    and run on CPU. Fall back to 0 if the probe isn't available."""
    try:
        import llama_cpp

        return -1 if llama_cpp.llama_supports_gpu_offload() else 0
    except Exception:  # noqa: BLE001
        return 0


def _load(repo: str, filename: str, n_ctx: int):
    from huggingface_hub import hf_hub_download
    from llama_cpp import Llama

    path = hf_hub_download(repo, filename)  # HF-cache hit once downloaded
    return Llama(
        model_path=path,
        n_ctx=n_ctx,
        n_gpu_layers=_n_gpu_layers(),
        n_threads=os.cpu_count() or 4,
        verbose=False,
    )


def _format(llm, system: str, text: str, max_tokens: int) -> str:
    out = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        temperature=0.0,  # deterministic: formatting, not creativity
        top_k=1,
        max_tokens=max_tokens,
    )
    return (out["choices"][0]["message"]["content"] or "").strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--filename", required=True)
    ap.add_argument("--n-ctx", type=int, default=4096)
    args = ap.parse_args()

    try:
        llm = _load(args.repo, args.filename, args.n_ctx)
    except Exception as exc:  # noqa: BLE001 — report, don't crash silently
        _emit({"status": "error", "error": repr(exc)})
        return
    _emit({"status": "ready"})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            text = _format(
                llm,
                req.get("system", ""),
                req.get("text", ""),
                int(req.get("max_tokens", 256)),
            )
            _emit({"text": text})
        except Exception as exc:  # noqa: BLE001
            _emit({"error": repr(exc)})


if __name__ == "__main__":
    main()
