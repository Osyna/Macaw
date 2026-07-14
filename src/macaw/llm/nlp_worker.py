#!/usr/bin/env python
"""Isolated NLP formatting worker.

Runs in the 'nlp' venv (punctuators + CPU torch + onnxruntime), holding one
ONNX punctuation / true-casing model warm. Protocol on stdin/stdout, one JSON
object per line — the same shape the llm worker speaks:

    ->  {"text": "..."}
    <-  {"text": "Punctuated, true-cased."}   or   {"error": "..."}

Launched by macaw.llm.nlp.NlpBackend with --model <punctuators key or HF id>.
The clean stdout channel carries only protocol JSON; model chatter -> stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# Drop this file's dir from sys.path so `import punctuators` never resolves a
# sibling module and never reaches back into macaw.
_selfdir = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if p and os.path.abspath(p) != _selfdir]

_PROTO = sys.stdout
sys.stdout = sys.stderr


def _emit(obj: dict) -> None:
    _PROTO.write(json.dumps(obj) + "\n")
    _PROTO.flush()


def _load(model: str):
    from punctuators.models import PunctCapSegModelONNX

    # Force CPU: onnxruntime-gpu isn't installed, and CPU is plenty fast here.
    return PunctCapSegModelONNX.from_pretrained(
        model, ort_providers=["CPUExecutionProvider"]
    )


def _format(model, text: str) -> dict:
    t0 = time.monotonic()
    # apply_sbd=False -> one punctuated + true-cased string per input.
    out = model.infer(texts=[text], apply_sbd=False)
    secs = time.monotonic() - t0
    result = (out[0] if out else text) or text
    return {"text": result.strip(), "tps": 0.0, "secs": round(secs, 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    args = ap.parse_args()

    try:
        model = _load(args.model)
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
            text = (req.get("text") or "").strip()
            if not text:
                _emit({"text": ""})
                continue
            _emit(_format(model, text))
        except Exception as exc:  # noqa: BLE001
            _emit({"error": repr(exc)})


if __name__ == "__main__":
    main()
