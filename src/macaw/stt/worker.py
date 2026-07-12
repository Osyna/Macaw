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
import os
import sys

import numpy as np

# Running as `python .../macaw/stt/worker.py` puts THIS file's directory on
# sys.path[0], where sibling modules (nemo.py, whisper.py, …) shadow the real
# backend packages of the same name; importing one then pulls in `macaw`, which
# isn't in the isolated venv. Drop that entry so `import nemo` finds the real
# package, not macaw's backend module.
_selfdir = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if p and os.path.abspath(p) != _selfdir]

# Keep a clean channel for the protocol; send everything else to stderr.
_PROTO = sys.stdout
sys.stdout = sys.stderr


def _emit(obj: dict) -> None:
    _PROTO.write(json.dumps(obj) + "\n")
    _PROTO.flush()


# -- per-backend loaders: return a fn(audio: np.ndarray) -> str --------------


def _amp():
    """bf16 autocast on CUDA — free 2x-class speedup on Ampere+; NVIDIA's own
    NeMo acceleration recipe. No-op on CPU or pre-bf16 GPUs."""
    import contextlib

    try:
        import torch

        if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
            return torch.autocast("cuda", dtype=torch.bfloat16)
    except Exception:  # noqa: BLE001 — autocast is an optimization, never a gate
        pass
    return contextlib.nullcontext()


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
        with _amp():
            out = m.transcribe([audio], batch_size=1)
        return _nemo_text(out)

    return run


def _load_canary(model: str, language: str):
    import torch
    from nemo.collections.speechlm2.models import SALM

    # SALM is an LLM-style speech model: no .transcribe(); ASR is done via
    # generate() with an audio-locator prompt (see NeMo speechlm2/models/salm.py).
    m = SALM.from_pretrained(model).eval()
    if torch.cuda.is_available():
        m = m.to("cuda")

    def run(audio):
        audio_t = torch.as_tensor(
            audio, dtype=torch.float32, device=m.device
        ).unsqueeze(0)
        audio_lens = torch.tensor([audio_t.shape[1]], dtype=torch.long, device=m.device)
        with _amp():
            ids = m.generate(
                prompts=[
                    [
                        {
                            "role": "user",
                            "content": f"Transcribe the following: {m.audio_locator_tag}",
                        }
                    ]
                ],
                audios=audio_t,
                audio_lens=audio_lens,
                max_new_tokens=256,
            )
        return m.tokenizer.ids_to_text(ids[0].cpu()).strip()

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
            audio=audio,
            model_id=model,  # required positional in transformers 5.x
            language=language,
            sampling_rate=16_000,
            format=["wav"],  # required for ndarray; len must match n_audio (1)
        ).to(device, dtype=net.dtype)
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


# -- sherpa-onnx: ONNX Zipformer / Paraformer / Parakeet in one CPU venv -------
# Construction details (repo, files, recognizer kind) live here because the
# isolated worker can't import macaw; sherpa.yaml carries only the user-facing
# metadata + repo link. ponytail: the repo id is the sole intentional duplication.

_SHERPA_MODELS = {
    "sherpa-parakeet-tdt-v3": {
        "repo": "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        "kind": "offline_transducer",
        "model_type": "nemo_transducer",
        "encoder": "encoder.int8.onnx",
        "decoder": "decoder.int8.onnx",
        "joiner": "joiner.int8.onnx",
        "tokens": "tokens.txt",
    },
    "sherpa-parakeet-tdt-v2": {
        "repo": "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
        "kind": "offline_transducer",
        "model_type": "nemo_transducer",
        "encoder": "encoder.int8.onnx",
        "decoder": "decoder.int8.onnx",
        "joiner": "joiner.int8.onnx",
        "tokens": "tokens.txt",
    },
    "sherpa-zipformer-bilingual-zh-en": {
        "repo": "csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
        "kind": "online_transducer",
        "encoder": "encoder-epoch-99-avg-1.int8.onnx",
        "decoder": "decoder-epoch-99-avg-1.int8.onnx",
        "joiner": "joiner-epoch-99-avg-1.int8.onnx",
        "tokens": "tokens.txt",
        "lowercase": True,  # LibriSpeech English tokens are all-caps
    },
    "sherpa-paraformer-bilingual-zh-en": {
        "repo": "csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en",
        "kind": "online_paraformer",
        "encoder": "encoder.int8.onnx",
        "decoder": "decoder.int8.onnx",
        "tokens": "tokens.txt",
    },
    "sherpa-zipformer-en-20m": {
        "repo": "csukuangfj/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17",
        "kind": "online_transducer",
        "encoder": "encoder-epoch-99-avg-1.int8.onnx",
        "decoder": "decoder-epoch-99-avg-1.int8.onnx",
        "joiner": "joiner-epoch-99-avg-1.int8.onnx",
        "tokens": "tokens.txt",
        "lowercase": True,  # English zipformer emits all-caps
    },
    "sherpa-zipformer-zh-14m": {
        "repo": "csukuangfj/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23",
        "kind": "online_transducer",
        "encoder": "encoder-epoch-99-avg-1.int8.onnx",
        "decoder": "decoder-epoch-99-avg-1.int8.onnx",
        "joiner": "joiner-epoch-99-avg-1.int8.onnx",
        "tokens": "tokens.txt",
    },
}

_SHERPA_TAIL = np.zeros(int(0.5 * 16_000), dtype=np.float32)  # flush streaming tail


def _load_sherpa(model: str, language: str):
    import sherpa_onnx
    from huggingface_hub import snapshot_download

    cfg = _SHERPA_MODELS[model]
    files = [cfg[k] for k in ("encoder", "decoder", "joiner", "tokens") if k in cfg]
    root = snapshot_download(cfg["repo"], allow_patterns=files)

    def path(name: str) -> str:
        return os.path.join(root, name)

    lower = cfg.get("lowercase", False)

    def _post(text: str) -> str:
        return text.strip().lower() if lower else text.strip()

    if cfg["kind"] == "offline_transducer":
        rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=path(cfg["encoder"]),
            decoder=path(cfg["decoder"]),
            joiner=path(cfg["joiner"]),
            tokens=path(cfg["tokens"]),
            num_threads=2,
            model_type=cfg["model_type"],
            provider="cpu",
        )

        def _offline(audio):
            s = rec.create_stream()
            s.accept_waveform(16_000, audio)
            rec.decode_stream(s)
            return _post(s.result.text)

        return _offline

    if cfg["kind"] == "online_paraformer":
        rec = sherpa_onnx.OnlineRecognizer.from_paraformer(
            tokens=path(cfg["tokens"]),
            encoder=path(cfg["encoder"]),
            decoder=path(cfg["decoder"]),
            num_threads=2,
            provider="cpu",
        )
    else:  # online_transducer (streaming Zipformer)
        rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=path(cfg["tokens"]),
            encoder=path(cfg["encoder"]),
            decoder=path(cfg["decoder"]),
            joiner=path(cfg["joiner"]),
            num_threads=2,
            provider="cpu",
        )

    def _online(audio):
        s = rec.create_stream()
        s.accept_waveform(16_000, audio)
        s.accept_waveform(16_000, _SHERPA_TAIL)  # tail padding emits final frames
        s.input_finished()
        while rec.is_ready(s):
            rec.decode_stream(s)
        return _post(rec.get_result(s))

    return _online


LOADERS = {
    "moonshine": _load_moonshine,
    "parakeet": _load_parakeet,
    "canary-qwen": _load_canary,
    "voxtral": _load_voxtral,
    "sherpa": _load_sherpa,
}


def _setup_net() -> None:
    """Honour macaw's proxy + SSL settings for HF downloads. The proxy arrives
    via inherited HTTP(S)_PROXY env; MACAW_SSL_VERIFY=0 disables cert checks."""
    if os.environ.get("MACAW_SSL_VERIFY", "1") != "0":
        return
    try:
        import requests
        import urllib3
        from huggingface_hub import configure_http_backend

        def _session():
            s = requests.Session()
            s.verify = False
            return s

        configure_http_backend(_session)
        urllib3.disable_warnings()
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--language", default="en")
    args = ap.parse_args()
    _setup_net()

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
