from __future__ import annotations

import logging
import queue
import time

import numpy as np
import sounddevice as sd

logger = logging.getLogger("macaw")

_TARGET_RATE = 16_000  # 16kHz default for Whisper


class AudioCapture:
    @staticmethod
    def list_devices() -> list[dict]:
        return sd.query_devices()

    def __init__(
        self,
        device: int | None = None,
        block_duration_ms: int = 30,
        silence_threshold: float = 0.001,
    ) -> None:
        self.device = device
        self.block_duration_ms = block_duration_ms
        self.silence_threshold = silence_threshold

        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self.running = False
        self.sample_rate: int = _TARGET_RATE

        # Silence tracking
        self.current_energy: float = 0.0
        self.last_sound_time: float = 0.0
        self.speech_detected: bool = False

    # -- stream callback (runs in audio thread) -------------------------

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logger.warning("Audio callback: %s", status)
        self._queue.put(indata.copy())

        energy = float(np.mean(indata**2))
        self.current_energy = energy
        if energy > self.silence_threshold:
            self.last_sound_time = time.time()
            self.speech_detected = True

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self.running:
            return
        self.running = True

        rate = _TARGET_RATE
        try:
            sd.check_input_settings(device=self.device, samplerate=rate, channels=1)
        except Exception:
            try:
                if self.device is not None:
                    info = sd.query_devices(self.device, "input")
                else:
                    info = sd.query_devices(kind="input")
                rate = int(info["default_samplerate"])
            except Exception as exc:
                logger.warning("Device query fallback: %s", exc)
                rate = _TARGET_RATE

        self.sample_rate = rate
        block_size = int(rate * self.block_duration_ms / 1000)

        self._stream = sd.InputStream(
            samplerate=rate,
            channels=1,
            dtype="float32",
            blocksize=block_size,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        self.running = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def read(self) -> list[np.ndarray]:
        """Drain the queue and return all buffered chunks."""
        chunks: list[np.ndarray] = []
        while True:
            try:
                chunk = self._queue.get_nowait()
                if chunk.ndim > 1:
                    chunk = chunk.flatten()
                chunks.append(chunk)
            except queue.Empty:
                if not self.running:
                    break
                try:
                    chunk = self._queue.get(timeout=0.02)
                    if chunk.ndim > 1:
                        chunk = chunk.flatten()
                    chunks.append(chunk)
                except queue.Empty:
                    break
        return chunks
