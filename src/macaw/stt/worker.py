#!/usr/bin/env python
"""Isolated speech-to-text worker.

Run by a backend's OWN venv python — it must NOT import macaw (that package
isn't installed in the isolated venv). It only imports numpy + the one backend
library, so conflicting dependency stacks stay isolated per backend.

Protocol (line-based JSON on the *real* stdout; all library chatter is
redirected to stderr so it can't corrupt the stream):
    <- {"status": "ready", "incremental": bool}   once the model is loaded
    -> /path/to/audio.npy\n           batch request: mono float32 16 kHz array
                                      (also resets any live stream)
    -> S /path/to/audio.npy\n         stream-feed: ONLY the new samples; the
                                      reply text is the full partial so far
    <- {"text": "..."}   |  {"error": "..."}

Backends that can decode incrementally (the natively-streaming sherpa models)
expose it as a `feed` attribute on the transcribe callable; everything else
is batch-only and `S` lines answer with an error.
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
    # Cache-aware streaming FastConformer transducer (sherpa-onnx >= 1.12.22;
    # routed to the NeMo cache-aware impl automatically via decoder metadata).
    # Chunk/cache geometry is embedded in the encoder ONNX — nothing to pass.
    "sherpa-nemotron-streaming-en": {
        "repo": "csukuangfj/sherpa-onnx-nemotron-speech-streaming-en-0.6b-int8-2026-01-14",
        "kind": "online_transducer",
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


# -- Moonshine v2 ("moonshine-voice"): streaming .ort models, own CDN cache ----

_MOONSHINE2_ARCH = {  # catalog id -> moonshine_voice.ModelArch name
    "moonshine2-tiny-en": "TINY_STREAMING",
    "moonshine2-small-en": "SMALL_STREAMING",
    "moonshine2-medium-en": "MEDIUM_STREAMING",
}


def _load_moonshine2(model: str, language: str):
    from moonshine_voice import ModelArch, Transcriber, get_model_for_language

    arch = getattr(ModelArch, _MOONSHINE2_ARCH[model])
    # Weights (.ort bundle) download from download.moonshine.ai on first use
    # and cache inside the package's own model dir — not the HF cache.
    path, arch = get_model_for_language("en", arch)
    t = Transcriber(model_path=path, model_arch=arch)

    def run(audio):
        r = t.transcribe_without_streaming(audio, sample_rate=16_000)
        return " ".join(ln.text.strip() for ln in r.lines if ln.text).strip()

    return run


_SHERPA_TAIL = np.zeros(int(0.5 * 16_000), dtype=np.float32)  # flush streaming tail


def _load_sherpa(model: str, language: str):
    import sherpa_onnx
    from huggingface_hub import snapshot_download

    cfg = _SHERPA_MODELS[model]
    files = [cfg[k] for k in ("encoder", "decoder", "joiner", "tokens") if k in cfg]
    root = snapshot_download(cfg["repo"], allow_patterns=files)
    # 4 threads: measured 1.4x over 2 on the 0.6B Nemotron encoder; beyond 4
    # the return is marginal and it starts starving the rest of the system.
    threads = min(4, os.cpu_count() or 2)

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
            num_threads=threads,
            model_type=cfg["model_type"],
            provider="cpu",
        )

        def _offline(audio):
            s = rec.create_stream()
            s.accept_waveform(16_000, audio)
            rec.decode_stream(s)
            return _post(s.result.text)

        return _offline

    # online kinds: a persistent stream so live typing can feed only the NEW
    # samples each tick (true streaming — bounded per-tick cost), while batch
    # calls still decode a complete utterance from scratch.
    if cfg["kind"] == "online_paraformer":
        rec = sherpa_onnx.OnlineRecognizer.from_paraformer(
            tokens=path(cfg["tokens"]),
            encoder=path(cfg["encoder"]),
            decoder=path(cfg["decoder"]),
            num_threads=threads,
            provider="cpu",
        )
    else:  # online_transducer (streaming Zipformer)
        rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=path(cfg["tokens"]),
            encoder=path(cfg["encoder"]),
            decoder=path(cfg["decoder"]),
            joiner=path(cfg["joiner"]),
            num_threads=threads,
            provider="cpu",
        )

    live = {"s": None}  # the one persistent live-typing stream

    def _drain(s) -> None:
        while rec.is_ready(s):
            rec.decode_stream(s)

    def _online(audio):
        live["s"] = None  # a batch pass supersedes any live stream
        s = rec.create_stream()
        s.accept_waveform(16_000, audio)
        s.accept_waveform(16_000, _SHERPA_TAIL)  # tail padding emits final frames
        s.input_finished()
        _drain(s)
        return _post(rec.get_result(s))

    def _feed(audio):
        if live["s"] is None:
            live["s"] = rec.create_stream()
        live["s"].accept_waveform(16_000, audio)
        _drain(live["s"])
        return _post(rec.get_result(live["s"]))

    _online.feed = _feed
    return _online


LOADERS = {
    "moonshine": _load_moonshine,
    "moonshine2": _load_moonshine2,
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
    _emit({"status": "ready", "incremental": hasattr(transcribe, "feed")})

    for line in sys.stdin:
        reply = _handle_line(transcribe, line.strip())
        if reply is not None:
            _emit(reply)


def _handle_line(transcribe, line: str) -> dict | None:
    """One protocol request -> one reply dict (None for blank keep-alives)."""
    if not line:
        return None
    try:
        if line.startswith("S "):
            feed = getattr(transcribe, "feed", None)
            if feed is None:
                return {"error": "stream feed unsupported"}
            return {"text": feed(np.load(line[2:]))}
        return {"text": transcribe(np.load(line))}
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


if __name__ == "__main__":
    main()
