from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_SR = 44100

_cache: dict[str, np.ndarray] = {}


def _decode_mp3(path: Path) -> np.ndarray:
    """Decode an MP3 file to a float32 mono numpy array using PyAV."""
    import av

    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        frames = []
        for frame in container.decode(stream):
            arr = frame.to_ndarray().astype(np.float32)
            # av returns int16-range for s16 format; normalise to [-1, 1]
            if frame.format.name in ("s16", "s16p"):
                arr = arr / 32768.0
            elif frame.format.name in ("s32", "s32p"):
                arr = arr / 2147483648.0
            # planar formats have shape (channels, samples) — mix to mono
            if arr.ndim == 2:
                arr = arr.mean(axis=0)
            frames.append(arr)

    if not frames:
        return np.zeros(0, dtype=np.float32)
    pcm = np.concatenate(frames).astype(np.float32)
    np.clip(pcm, -1.0, 1.0, out=pcm)
    return pcm


def _load(name: str) -> np.ndarray | None:
    if name in _cache:
        return _cache[name]
    path = _ASSETS / f"{name}.mp3"
    if not path.exists():
        logger.warning("Sound asset not found: %s", path)
        return None
    try:
        _cache[name] = _decode_mp3(path)
        return _cache[name]
    except Exception as exc:
        logger.warning("Failed to decode %s: %s", path.name, exc)
        return None


def _play(name: str) -> None:
    data = _load(name)
    if data is None or len(data) == 0:
        return
    try:
        sd.play(data, _SR)
    except Exception as exc:
        logger.debug("Sound playback failed: %s", exc)


# -- public API -------------------------------------------------------


def play_start() -> None:
    _play("record")


def play_analysing() -> None:
    _play("wait")


def play_done() -> None:
    _play("done")
