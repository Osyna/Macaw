#!/usr/bin/env python
"""Isolated speech-to-text worker.

Run by a backend's OWN venv python — it must NOT import macaw (that package
isn't installed in the isolated venv). It only imports numpy + the one backend
library, so conflicting dependency stacks stay isolated per backend.

Protocol (line-based JSON on the *real* stdout; all library chatter is
redirected to stderr so it can't corrupt the stream):
    <- {"status": "ready"}            once the model is loaded
    -> /path/to/audio.npy\n           a request: mono float32 16 kHz array
    <- {"text": "..."}   |  {"error": "..."}
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np

# Keep a clean channel for the protocol; send everything else to stderr.
_PROTO = sys.stdout
sys.stdout = sys.stderr


def _emit(obj: dict) -> None:
    _PROTO.write(json.dumps(obj) + "\n")
    _PROTO.flush()


# -- per-backend loaders: return a fn(audio: np.ndarray) -> str --------------


def _load_moonshine(model: str, language: str):
    import moonshine_onnx

    def run(audio):
        # transcribe() loads (and internally caches) the model on first call.
        r = moonshine_onnx.transcribe(audio, model)
        if isinstance(r, (list, tuple)):
            return " ".join(str(x) for x in r).strip()
        return str(r).strip()

    return run


def _load_parakeet(model: str, language: str):
    import nemo.collections.asr as nemo_asr

    m = nemo_asr.models.ASRModel.from_pretrained(model)

    def run(audio):
        out = m.transcribe([audio], batch_size=1)
        return _nemo_text(out)

    return run


def _load_canary(model: str, language: str):
    from nemo.collections.speechlm2.models import SALM

    m = SALM.from_pretrained(model)

    def run(audio):
        return _nemo_text(m.transcribe([audio]))

    return run


def _load_voxtral(model: str, language: str):
    import torch
    from transformers import AutoProcessor, VoxtralForConditionalGeneration

    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoProcessor.from_pretrained(model)
    net = VoxtralForConditionalGeneration.from_pretrained(
        model,
        torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32,
        device_map=device,
    )

    def run(audio):
        inputs = processor.apply_transcription_request(
            language=language, audio=audio, sampling_rate=16_000
        ).to(device)
        with torch.no_grad():
            ids = net.generate(**inputs, max_new_tokens=512)
        text = processor.batch_decode(
            ids[:, inputs.input_ids.shape[1] :], skip_special_tokens=True
        )
        return text[0].strip() if text else ""

    return run


def _nemo_text(out) -> str:
    if not out:
        return ""
    item = out[0]
    return str(getattr(item, "text", item)).strip()


LOADERS = {
    "moonshine": _load_moonshine,
    "parakeet": _load_parakeet,
    "canary-qwen": _load_canary,
    "voxtral": _load_voxtral,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--language", default="en")
    args = ap.parse_args()

    try:
        transcribe = LOADERS[args.backend](args.model, args.language)
    except Exception as exc:  # noqa: BLE001
        _emit({"status": "error", "error": repr(exc)})
        return
    _emit({"status": "ready"})

    for line in sys.stdin:
        path = line.strip()
        if not path:
            continue
        try:
            audio = np.load(path)
            _emit({"text": transcribe(audio)})
        except Exception as exc:  # noqa: BLE001
            _emit({"error": repr(exc)})


if __name__ == "__main__":
    main()
