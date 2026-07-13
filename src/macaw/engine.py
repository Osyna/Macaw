"""Headless Macaw engine.

Everything except UI: audio capture -> STT -> text delivery, global hotkey,
sounds, config live-apply, zmq REP IPC for the CLI, and a WebSocket JSON API
that the Tauri app drives. Windows/overlays are the UI's job — this process
only broadcasts events (state/level/text/progress/config/models/toast/show).

Protocol (ws://127.0.0.1:<port>): client's first message must be
{"auth": "<token>"}; then {"id", "method", "params"} -> {"id", "result"|"error"},
plus broadcast {"event", "data"} frames. Exits on stdin EOF, SIGTERM or `quit`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import queue
import secrets
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, fields

import numpy as np
import pyperclip
import zmq

from macaw import hardware, net
from macaw.audio import sounds
from macaw.audio.capture import AudioCapture
from macaw.audio.transcriber import Transcriber
from macaw.config import Config, config_path
from macaw.desktop import DesktopHelper, auto_type_available
from macaw.stt import create_backend, get_model_info, list_models
from macaw.stt.base import hf_cache_sizes
from macaw.stt.deps import ensure_uv, packages_for_extra
from macaw.stt.isolated import install_commands, mark_installed, remove
from macaw.trigger import _ipc_address

logger = logging.getLogger("macaw")

_CONFIG_FIELDS = {f.name for f in fields(Config)}


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("macaw")
    except PackageNotFoundError:
        return "0.0.0+dev"


def _lang_for(cfg: Config) -> str:
    """The active model's language: its per-model choice, else the default."""
    return cfg.model_languages.get(cfg.model) or cfg.language or "en"


def _split_notes(text: str) -> tuple[list[str], list[str], str]:
    """Catalog notes encode pros as '+' lines and cons as '−'/'-' lines."""
    pros: list[str] = []
    cons: list[str] = []
    plain: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[0] == "+":
            pros.append(line[1:].strip())
        elif line[0] in ("−", "-"):
            cons.append(line[1:].strip())
        else:
            plain.append(line)
    return pros, cons, "\n".join(plain)


# ── background jobs (install / download) ─────────────────────────────


class _InstallJob(threading.Thread):
    """Builds an isolated venv for one extra and installs the backend into it.

    Port of gui/install._InstallWorker: streamed subprocess output becomes
    `progress` events (op="install", key=<extra>, pct=null — indeterminate).
    """

    def __init__(self, engine: Engine, extra: str) -> None:
        super().__init__(daemon=True)
        self._engine = engine
        self._extra = extra
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self._last = ""

    def _progress(self, msg: str, done: bool = False, ok: bool | None = None) -> None:
        self._engine.emit(
            "progress",
            {
                "op": "install",
                "key": self._extra,
                "msg": msg,
                "pct": None,
                "done": done,
                "ok": ok,
            },
        )

    def run(self) -> None:
        extra = self._extra
        try:
            packages = packages_for_extra(extra)
            if not packages:
                self._progress(f"No packages found for '{extra}'", True, False)
                return
            if extra == "whisper" and hardware.probe().get("gpu") == "nvidia":
                # CTranslate2 dlopens cublas/cudnn; the worker preloads them
                # from the venv's own nvidia wheels (pyproject's `cuda` extra).
                packages += packages_for_extra("cuda")
            ensure_uv(self._progress)  # frozen installs: fetch a private uv once
            self._progress(f"Creating isolated environment for {extra}…")
            for cmd in install_commands(extra, packages):
                code = self._stream(cmd)
                if self._cancelled:
                    self._progress("Cancelled", True, False)
                    return
                if code != 0:
                    remove(extra)  # drop the partial venv, leave nothing half-built
                    detail = self._last or f"exit {code}"
                    logger.error("'%s' install failed: %s", extra, detail)
                    self._progress(f"'{extra}' failed — {detail}", True, False)
                    return
            mark_installed(extra)
            self._progress("Installed", True, True)
        except Exception as exc:  # noqa: BLE001
            if not self._cancelled:
                logger.error("Install error: %s", exc)
                self._progress(str(exc), True, False)
        finally:
            self._engine._op_finished(self)
            self._engine.emit("models", {})

    def _stream(self, cmd: list[str]) -> int:
        logger.info("Install step: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            ),  # win: no console flash
        )
        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            if self._cancelled:
                break
            text = raw.rstrip()
            if text:
                self._last = text
                self._progress(text)
        return self._proc.wait()

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


class _DownloadJob(threading.Thread):
    """Downloads one model's weights (port of gui/download._DownloadWorker).

    Progress percentages become `progress` events (op="download", pct 0..100).
    Cancel just abandons the result — hf downloads aren't interruptible.
    """

    def __init__(self, engine: Engine, model_id: str) -> None:
        super().__init__(daemon=True)
        self._engine = engine
        self._model_id = model_id
        self._cancelled = False

    def _progress(
        self, msg: str, pct: float | None, done: bool = False, ok: bool | None = None
    ) -> None:
        self._engine.emit(
            "progress",
            {
                "op": "download",
                "key": self._model_id,
                "msg": msg,
                "pct": pct,
                "done": done,
                "ok": ok,
            },
        )

    def run(self) -> None:
        try:
            create_backend(self._model_id).download(progress_callback=self._on_pct)
            if not self._cancelled:
                self._progress("Downloaded", 100.0, True, True)
        except Exception as exc:  # noqa: BLE001
            if not self._cancelled:
                logger.error("Model download failed: %s", exc)
                self._progress(str(exc), None, True, False)
        finally:
            self._engine._op_finished(self)
            self._engine.emit("models", {})

    def _on_pct(self, pct: int) -> None:
        if not self._cancelled:
            self._progress(f"Downloading… {pct}%", float(pct))

    def cancel(self) -> None:
        self._cancelled = True


# ── engine ───────────────────────────────────────────────────────────


class Engine:
    def __init__(self, token: str) -> None:
        self.token = token
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set = set()

        self.cfg = Config.load()
        net.apply(self.cfg.proxy, self.cfg.ssl_verify)  # proxy/SSL: downloads + cloud
        self.desktop = DesktopHelper()
        self._hw = hardware.probe()  # once; drives the model-fit suggestions
        logger.info("Hardware: %s", hardware.summary(self._hw))
        self.capture = AudioCapture(device=self.cfg.device_index)
        self._apply_silence_level()
        self.transcriber = Transcriber(
            model_size=self.cfg.model,
            language=_lang_for(self.cfg),
            punctuation_hints=self.cfg.punctuation_hints,
            vad_gate=self.cfg.vad_gate,
        )
        self.transcriber.model_params = self.cfg.model_params.get(self.cfg.model, {})

        self.state = "idle"
        self.state_detail = ""
        self.is_recording = False
        self._saved_window_id: str | None = None
        self._stop_event: asyncio.Event | None = None

        # async model load
        self._load_thread: threading.Thread | None = None
        self._model_loaded = False  # True only after a model loads successfully
        self._load_cancelled = False
        self._pending_model: str | None = None  # switch queued behind a cancel

        # streaming live-typing
        self._stream_buffer: list[np.ndarray] = []
        self._stream_prev_text = ""
        self._stream_confirmed_len = 0  # chars already typed
        self._streaming_active = False
        self._stream_busy = False  # a live-typing tick is decoding right now

        self._monitor_task: asyncio.Task | None = None  # 30 Hz level + silence
        self._stream_task: asyncio.Task | None = None  # 1 Hz streaming tick
        self._error_reset: asyncio.Task | None = None  # transient error -> idle
        self._mic_task: asyncio.Task | None = None  # idle mic meter (Settings)

        self._op: dict | None = None  # one long op at a time: load/install/download
        self._hotkey = None
        self._hotkey_capture = None

    # ── events ───────────────────────────────────────────────────────

    def emit(self, event: str, data: dict) -> None:
        """Broadcast to all authed clients. Thread-safe (workers call this)."""
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        msg = json.dumps({"event": event, "data": data})
        loop.call_soon_threadsafe(self._broadcast, msg)

    def _broadcast(self, msg: str) -> None:
        for ws in list(self.clients):
            asyncio.ensure_future(self._send(ws, msg))

    async def _send(self, ws, msg: str) -> None:
        try:
            await ws.send(msg)
        except Exception:  # noqa: BLE001 — dead client; reaped on its recv loop
            self.clients.discard(ws)

    def set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        self.state_detail = detail
        data: dict = {"state": state, "model": self.cfg.model}
        if detail:
            data["detail"] = detail
        self.emit("state", data)

    def toast(self, level: str, msg: str) -> None:
        self.emit("toast", {"level": level, "msg": msg})

    def _flash_error(self, detail: str) -> None:
        """Transient error state (overlay feedback), auto-reset to idle."""
        self.set_state("error", detail)
        if self._error_reset is not None:
            self._error_reset.cancel()

        async def _reset() -> None:
            await asyncio.sleep(2.5)
            if self.state == "error":
                self.set_state("idle")

        self._error_reset = asyncio.ensure_future(_reset())

    # ── hotkey ───────────────────────────────────────────────────────

    def _start_hotkey(self) -> None:
        """(Re)start the global-shortcut listener to match the current config."""
        from macaw.hotkey import HotkeyListener, is_valid

        self._stop_hotkey()
        if not self.cfg.hotkey_enabled or not is_valid(self.cfg.hotkey):
            return
        self._hotkey = HotkeyListener(self.cfg.hotkey)
        # signal fires on the listener thread — bridge into the loop
        self._hotkey.triggered.connect(
            lambda: self.loop.call_soon_threadsafe(self.toggle)
        )
        self._hotkey.start()

    def _stop_hotkey(self) -> None:
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey.wait(1500)
            self._hotkey = None

    def _capture_start(self) -> dict:
        from macaw.hotkey import HotkeyCapture

        self._capture_cancel()
        cap = HotkeyCapture()
        cap.captured.connect(lambda spec: self.emit("hotkey_captured", {"spec": spec}))
        cap.failed.connect(lambda reason: self.toast("error", reason))
        cap.start()
        self._hotkey_capture = cap
        return {"ok": True}

    def _capture_cancel(self) -> dict:
        if self._hotkey_capture is not None:
            self._hotkey_capture.stop()
            self._hotkey_capture = None
        return {"ok": True}

    # ── model lifecycle ──────────────────────────────────────────────

    def _preload_model(self) -> None:
        """Load the active model if it's downloaded. Never auto-download."""
        if self.transcriber.is_ready():
            self._load_model_async()
        else:
            logger.warning(
                "No usable model (%s not downloaded). Open Models to download one.",
                self.cfg.model,
            )
            self.emit("show", {"window": "models"})

    def _switch_model(self, model_name: str) -> None:
        """(Re)load a newly-selected model in the background."""
        self._model_loaded = False
        if self._load_thread is not None and self._load_thread.is_alive():
            # A load is in flight: cancel it quietly and queue this switch —
            # _on_model_loaded picks it up. Killing the worker without the
            # cancel flag would surface as a bogus "worker exited" error.
            self._load_cancelled = True
            self._pending_model = model_name
            self.transcriber.unload_model()  # unblocks the in-flight load
            return
        self.transcriber.unload_model()
        self.transcriber.model_size = model_name
        if self.transcriber.is_ready():
            self._load_model_async()
        else:
            logger.warning("Model %s not downloaded — not loaded", model_name)
            self.set_state("error", "not downloaded")

    def _load_model_async(self) -> None:
        if self._load_thread is not None and self._load_thread.is_alive():
            return  # a load is already in flight
        label = get_model_info(self.transcriber.model_size).label
        logger.info("Loading model (%s)...", self.transcriber.model_size)
        self.set_state("loading", label)
        self._op = {"kind": "load"}
        self._load_thread = threading.Thread(target=self._load_model, daemon=True)
        self._load_thread.start()

    def _load_model(self) -> None:  # worker thread
        try:
            self.transcriber.load_model()
            ok, error = True, ""
        except Exception as exc:  # noqa: BLE001 — report, never crash the engine
            ok, error = False, str(exc)
        self.loop.call_soon_threadsafe(self._on_model_loaded, ok, error)

    def _on_model_loaded(self, ok: bool, error: str) -> None:
        self._model_loaded = ok
        if self._op is not None and self._op.get("kind") == "load":
            self._op = None
        if self._load_cancelled:
            self._load_cancelled = False
            self._model_loaded = False
            self.transcriber.unload_model()  # reap any worker that raced the cancel
            logger.info("Model load cancelled (%s)", self.transcriber.model_size)
            pending, self._pending_model = self._pending_model, None
            if pending is not None:
                self._switch_model(pending)  # load thread is dead — safe to recurse
            else:
                self.set_state("idle")
            self.emit("models", {})
            return
        if ok:
            logger.info("Model ready (%s).", self.transcriber.model_size)
            self.set_state("idle")
        else:
            logger.error(
                "Model load failed (%s): %s", self.transcriber.model_size, error
            )
            self.set_state("error", error)
        self.emit("models", {})

    def _model_reload(self) -> dict:
        """Tear the backend down (isolated worker included — terminate, then
        kill) and load the active model again from scratch."""
        if self.is_recording:
            self.cancel_recording()
        if self._op is not None and self._op.get("kind") == "load":
            # a load is in flight: mark it cancelled so the reload wins
            self._load_cancelled = True
        self.transcriber.unload_model()
        self._model_loaded = False
        logger.info("Model reload requested (%s)", self.transcriber.model_size)
        if self.transcriber.is_ready():
            self._load_model_async()
        else:
            self.set_state("error", "not downloaded")
        self.emit("models", {})
        return {"ok": True}

    def _op_finished(self, job) -> None:  # called from job threads
        loop = self.loop

        def _clear() -> None:
            if self._op is not None and self._op.get("job") is job:
                self._op = None

        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(_clear)

    # ── recording lifecycle (loop thread only) ───────────────────────

    def toggle(self) -> None:
        logger.info("Toggle received (recording=%s)", self.is_recording)
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        try:
            self._start_recording_inner()
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to start recording: %s", exc, exc_info=True)
            self.is_recording = False
            self._stop_tasks()
            self.toast("error", f"Couldn't start recording: {exc}")
            self._flash_error(str(exc))

    def _start_recording_inner(self) -> None:
        if self.is_recording:
            return
        if not self.transcriber.is_ready():
            logger.warning(
                "Recording blocked: no model downloaded (%s).", self.cfg.model
            )
            self.toast(
                "warn",
                "No speech-to-text model is downloaded. "
                "Download one in the Model Manager.",
            )
            self.emit("show", {"window": "models"})
            self._flash_error("No model selected")
            return
        if self.cfg.sound_enabled:
            sounds.play_start()
        if self.cfg.output_mode == "type":
            self._saved_window_id = self.desktop.capture_active_window()
        self.is_recording = True
        if self._error_reset is not None:
            self._error_reset.cancel()
            self._error_reset = None
        self.set_state("recording")
        self.capture.start()
        self.capture.last_sound_time = time.time()
        self.capture.speech_detected = False
        self._monitor_task = asyncio.ensure_future(self._monitor())
        if self.cfg.streaming and self.cfg.output_mode == "type":
            self._streaming_active = True
            self._stream_buffer = []
            self._stream_prev_text = ""
            self._stream_confirmed_len = 0
            self._stream_busy = False
            self.transcriber.reset_stream()  # fresh native-stream state
            self._stream_task = asyncio.ensure_future(self._stream_loop())
            logger.info("Streaming transcription active")

    async def _monitor(self) -> None:
        """~30 Hz while recording: `level` events + the silence timeout."""
        while self.is_recording:
            self.emit("level", {"rms": self._vis_level()})
            timeout = self.cfg.silence_timeout
            if not self.capture.speech_detected:
                if time.time() - self.capture.last_sound_time >= timeout:
                    self.cancel_recording()
                    return
            if self.capture.speech_detected and self.capture.last_sound_time:
                if time.time() - self.capture.last_sound_time >= timeout:
                    self.stop_recording()
                    return
            await asyncio.sleep(1 / 30)

    # -- idle mic meter (Settings "is my microphone working?") ----------

    def _mic_monitor_set(self, params: dict) -> dict:
        on = bool(params.get("on"))
        if on == (self._mic_task is not None):
            return {"on": on}
        if on:
            self._mic_task = asyncio.ensure_future(self._mic_monitor())
        else:
            task, self._mic_task = self._mic_task, None
            if task is not None:
                task.cancel()
            if not self.is_recording:
                self.capture.stop()
        return {"on": on}

    def _gain(self) -> float:
        g = self.cfg.level_gain
        return min(4.0, max(0.5, 1.0 if g is None else float(g)))

    def _vis_level(self) -> float:
        """Log-scaled 0..1 level with the user's visual boost applied —
        quiet mics still fill the animation; the clamp caps screaming."""
        raw = self.capture.current_energy
        vis = (math.log10(raw) + 4) / 3.0 if raw > 1e-10 else 0.0
        return min(1.0, max(0.0, vis * self._gain()))

    def _apply_silence_level(self) -> None:
        """The user drags a marker on the SAME meter `_vis_level` fills, so
        invert that mapping (gain included) to get the raw energy threshold
        the capture layer compares against. Default 0.33 ≈ the old 1e-3."""
        lvl = min(1.0, max(0.0, float(self.cfg.silence_level or 0.0)))
        self.capture.silence_threshold = 10 ** (3 * (lvl / self._gain()) - 4)

    async def _mic_monitor(self) -> None:
        """~30 Hz level events while idle. Recording's own monitor takes over
        seamlessly (this loop goes quiet while `is_recording`), and the
        capture stream is restarted after a recording or device switch."""
        try:
            while True:
                if not self.is_recording:
                    if not self.capture.running:
                        self.capture.start()
                    self.emit("level", {"rms": self._vis_level()})
                await asyncio.sleep(1 / 30)
        except asyncio.CancelledError:
            pass

    def _stop_tasks(self) -> None:
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            self._monitor_task = None
        if self._stream_task is not None:
            self._stream_task.cancel()
            self._stream_task = None
        self._streaming_active = False

    def cancel_recording(self) -> None:
        if not self.is_recording:
            return
        logger.info("Recording cancelled (no speech detected).")
        self.is_recording = False
        self._stop_tasks()
        self.capture.stop()
        self.capture.read()  # drain
        self.set_state("idle")

    def stop_recording(self) -> None:
        if not self.is_recording:
            return
        self.is_recording = False
        self._stop_tasks()
        self.capture.stop()
        if self.cfg.sound_enabled:
            sounds.play_analysing()
        self.set_state("transcribing")
        threading.Thread(target=self._process_audio, daemon=True).start()

    def _process_audio(self) -> None:  # worker thread
        try:
            self._process_audio_inner()
        except Exception as exc:  # noqa: BLE001 — daemon-thread guard
            logger.error("Transcription failed: %s", exc, exc_info=True)
            self.toast("error", f"Transcription failed: {exc}")
            self.set_state("error", detail="Transcription failed")
            self._state_later(1.8, "error", "idle")
            return
        finally:
            if not self.is_recording and self.state not in ("idle", "error", "done"):
                self.set_state("idle")

    def _state_later(self, delay: float, from_state: str, to_state: str) -> None:
        """Schedule a state transition (worker-thread safe); no-op if the
        state changed again in the meantime."""

        def flip() -> None:
            if self.state == from_state:
                self.set_state(to_state)

        self.loop.call_soon_threadsafe(lambda: self.loop.call_later(delay, flip))

    def _process_audio_inner(self) -> None:
        chunks = self.capture.read()

        # In streaming mode, merge remaining capture chunks into stream buffer
        if self._stream_buffer:
            chunks = self._stream_buffer + chunks
            self._stream_buffer = []

        text = ""
        if chunks:
            audio = np.concatenate(chunks)
            logger.debug(
                "Audio: %d samples @ %dHz (%.1fs)",
                len(audio),
                self.capture.sample_rate,
                len(audio) / self.capture.sample_rate,
            )
            text = self.transcriber.transcribe(
                audio, sample_rate=self.capture.sample_rate
            )
        else:
            logger.debug("No audio chunks captured.")

        if text:
            self.emit("text", {"kind": "final", "text": text})
            if self.cfg.output_mode == "type":
                # Overlay hides on idle; give the compositor a beat before typing
                self.set_state("idle")
                time.sleep(0.08)

            # In streaming mode, only type the remaining unconfirmed text
            if self._stream_confirmed_len > 0:
                remaining = text[self._stream_confirmed_len :]
                self._stream_confirmed_len = 0
                self._stream_prev_text = ""
                if remaining.strip():
                    self._deliver_text(remaining.strip())
            else:
                self._deliver_text(text)

            if self.cfg.output_mode != "type":
                # Delivered to the clipboard: flash the ✓ on the overlay.
                self.set_state("done")
                self._state_later(1.2, "done", "idle")
            if self.cfg.sound_enabled:
                sounds.play_done()

    # ── streaming transcription ──────────────────────────────────────

    async def _stream_loop(self) -> None:
        while self._streaming_active:
            await asyncio.sleep(1.0)
            self._stream_tick()

    def _stream_tick(self) -> None:
        if not self._streaming_active:
            return
        # Drain current audio chunks into the stream buffer
        while True:
            try:
                chunk = self.capture._queue.get_nowait()
                if chunk.ndim > 1:
                    chunk = chunk.flatten()
                self._stream_buffer.append(chunk)
            except queue.Empty:
                break
        if not self._stream_buffer:
            return
        audio = np.concatenate(self._stream_buffer)
        if len(audio) < self.capture.sample_rate:
            return  # need at least 1s of audio
        if self._stream_busy:
            return  # previous tick still decoding — never overlap worker calls
        self._stream_busy = True
        threading.Thread(
            target=self._stream_transcribe,
            args=(audio.copy(), self.capture.sample_rate),
            daemon=True,
        ).start()

    def _stream_transcribe(self, audio: np.ndarray, sample_rate: int) -> None:
        """Worker thread: transcribe and live-type newly confirmed text."""
        try:
            confirmed, full = self.transcriber.transcribe_streaming(
                audio,
                sample_rate=sample_rate,
                prev_text=self._stream_prev_text,
            )
        except Exception as exc:  # noqa: BLE001 — mid-stream tick; final pass reports
            logger.error("Streaming transcription error: %s", exc)
            return
        finally:
            self._stream_busy = False
        self._stream_prev_text = full
        if confirmed and len(confirmed) > self._stream_confirmed_len:
            new_text = confirmed[self._stream_confirmed_len :]
            if not new_text.endswith(" "):
                new_text += " "  # so the next chunk appends cleanly
            self._stream_confirmed_len = len(confirmed)
            logger.debug("Streaming: typing confirmed %r", new_text)
            self.emit("text", {"kind": "partial", "text": new_text})
            self.desktop.type_into_window(new_text, self._saved_window_id)

    def _deliver_text(self, text: str) -> None:
        if self.cfg.output_mode == "type":
            self.desktop.type_into_window(text, self._saved_window_id)
        else:
            try:
                pyperclip.copy(text)
            except Exception as exc:  # noqa: BLE001
                logger.error("Clipboard error: %s", exc)

    # ── config ───────────────────────────────────────────────────────

    def config_dict(self) -> dict:
        return asdict(self.cfg)

    def _config_set(self, params: dict) -> dict:
        patch = params.get("patch") or {}
        cfg = Config.load()  # respect concurrent hand-edits of the YAML
        for key, value in patch.items():
            if key not in _CONFIG_FIELDS:
                raise ValueError(f"unknown config field: {key}")
            setattr(cfg, key, value)
        cfg.save()
        self._apply_config(cfg)
        return {"config": self.config_dict(), "path": str(config_path())}

    def _apply_config(self, cfg: Config) -> None:
        """Live-apply: device/hotkey/model/proxy changes take effect now."""
        old = self.cfg
        self.cfg = cfg
        self.transcriber.language = _lang_for(cfg)
        self.transcriber.punctuation_hints = cfg.punctuation_hints
        self.transcriber.vad_gate = cfg.vad_gate
        self.transcriber.model_params = cfg.model_params.get(cfg.model, {})
        if cfg.device_index != old.device_index and not self.is_recording:
            self.capture.stop()  # the mic meter may hold the old stream open
            self.capture = AudioCapture(device=cfg.device_index)
        self._apply_silence_level()  # threshold follows level/gain/device changes
        if (cfg.proxy, cfg.ssl_verify) != (old.proxy, old.ssl_verify):
            net.apply(cfg.proxy, cfg.ssl_verify)
        if cfg.model != old.model:
            self._switch_model(cfg.model)
        if (cfg.hotkey_enabled, cfg.hotkey) != (old.hotkey_enabled, old.hotkey):
            self._start_hotkey()
        self.emit("config", {"config": self.config_dict()})

    # ── models ───────────────────────────────────────────────────────

    def models_list(self) -> list[dict]:
        cfg = self.cfg
        cache = hf_cache_sizes()
        out: list[dict] = []
        hw = self._hw
        for info in sorted(list_models(), key=lambda m: (m.cloud, -m.rating)):
            backend = create_backend(info.id)
            available = backend.available()
            size = backend.disk_size(cache) if available else 0
            api_key = getattr(backend, "api_key", None)
            pros, cons, notes = _split_notes(info.notes)
            out.append(
                {
                    "id": info.id,
                    "backend": info.backend,
                    "label": info.label,
                    "size": info.size,
                    "speed": info.speed,
                    "languages": info.languages,
                    "streaming": info.streaming,
                    "extra": info.extra,
                    "hardware": info.hardware,
                    "vram": info.vram,
                    "notes": notes,
                    "rating": info.rating,
                    "pros": pros,
                    "cons": cons,
                    "rec_specs": info.rec_specs,
                    "min_specs": info.min_specs,
                    "source_url": info.source_url,
                    "repo": info.repo,
                    "params": [
                        {
                            "key": p.key,
                            "label": p.label,
                            "kind": p.kind,
                            "default": p.default,
                            "min": p.minimum,
                            "max": p.maximum,
                            "step": p.step,
                            "hint": p.hint,
                        }
                        for p in backend.params
                    ],
                    "cloud": info.cloud,
                    "recommended": info.recommended,
                    # resource-friendly: runs on plain CPUs (no GPU requirement)
                    "light": (not info.cloud) and ("CPU" in info.hardware),
                    "available": available,
                    "installed": size > 0,
                    "ready": backend.is_ready(cache),
                    "active": info.id == cfg.model,
                    "disk_size": size,
                    "api_key_set": bool(api_key()) if callable(api_key) else False,
                    "lang_select": info.lang_select,
                    "cur_lang": cfg.model_languages.get(info.id) or "en",
                    "cur_params": cfg.model_params.get(info.id, {}),
                }
            )
        hardware.rank(out, hw)
        return out

    def _require_model(self, model_id: str) -> None:
        if get_model_info(model_id).id != model_id:
            raise ValueError(f"unknown model: {model_id}")

    def _set_active(self, params: dict) -> dict:
        self._require_model(params["id"])
        retry = params["id"] == self.cfg.model  # same model -> failed-load retry
        self._config_set({"patch": {"model": params["id"]}})
        if retry:
            self._switch_model(params["id"])
        self.emit("models", {})
        return {"ok": True}

    def _start_install(self, params: dict) -> dict:
        if self._op is not None:
            raise RuntimeError("another operation is in progress")
        job = _InstallJob(self, params["extra"])
        self._op = {"kind": "install", "job": job}
        job.start()
        return {"started": True}

    def _start_download(self, params: dict) -> dict:
        self._require_model(params["id"])
        if self._op is not None:
            raise RuntimeError("another operation is in progress")
        job = _DownloadJob(self, params["id"])
        self._op = {"kind": "download", "job": job}
        job.start()
        return {"started": True}

    def _cancel_op(self) -> dict:
        op = self._op
        if op is None:
            return {"ok": True}
        if op["kind"] == "load":
            if self._load_thread is not None and self._load_thread.is_alive():
                logger.info("Cancelling model load (%s)", self.transcriber.model_size)
                self._load_cancelled = True
                self.transcriber.unload_model()  # load fails fast, worker reaped
        else:
            op["job"].cancel()
        return {"ok": True}

    # ── RPC ──────────────────────────────────────────────────────────

    def _status(self) -> dict:
        from macaw.hotkey import check_access

        hotkey_ok, _ = check_access()
        return {
            "state": self.state,
            "model": self.cfg.model,
            "model_label": (
                get_model_info(self.cfg.model).label if self.cfg.model else ""
            ),
            "version": _version(),
            "hotkey_ok": hotkey_ok,
            "typing_ok": auto_type_available(),
        }

    def _devices_list(self) -> list[dict]:
        import sounddevice as sd

        try:
            default_in = sd.default.device[0]
        except Exception:  # noqa: BLE001
            default_in = None
        out = []
        for i, dev in enumerate(AudioCapture.list_devices()):
            if dev["max_input_channels"] > 0:
                out.append(
                    {"index": i, "name": dev["name"], "default": i == default_in}
                )
        return out

    async def _dispatch(self, method: str, params: dict):
        if method == "ping":
            return "pong"
        if method == "status":
            return await asyncio.to_thread(self._status)
        if method == "config.get":
            return {"config": self.config_dict(), "path": str(config_path())}
        if method == "system.info":
            return {"summary": hardware.summary(self._hw), "hw": self._hw}
        if method == "config.set":
            return self._config_set(params)
        if method == "devices.list":
            return await asyncio.to_thread(self._devices_list)
        if method == "models.list":
            return await asyncio.to_thread(self.models_list)
        if method == "models.set_active":
            return self._set_active(params)
        if method == "models.install":
            return self._start_install(params)
        if method == "models.download":
            return self._start_download(params)
        if method == "models.delete":
            self._require_model(params["id"])
            freed = await asyncio.to_thread(create_backend(params["id"]).delete)
            self.emit("models", {})
            return {"freed": freed}
        if method == "models.cancel":
            return self._cancel_op()
        if method == "record.toggle":
            self.toggle()
            return {"state": self.state}
        if method == "mic.monitor":
            return self._mic_monitor_set(params)
        if method == "model.reload":
            return self._model_reload()
        if method == "record.stop":
            self.stop_recording()
            return {"state": self.state}
        if method == "hotkey.capture_start":
            return self._capture_start()
        if method == "hotkey.capture_cancel":
            return self._capture_cancel()
        if method == "quit":
            self.loop.call_later(0.1, self._request_stop)  # let the reply flush
            return {"ok": True}
        raise ValueError(f"unknown method: {method}")

    async def _client(self, ws) -> None:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
        except Exception:  # noqa: BLE001
            await ws.close()
            return
        if not isinstance(msg, dict) or msg.get("auth") != self.token:
            await ws.close()
            return
        await ws.send(json.dumps({"ok": True}))
        self.clients.add(ws)
        try:
            async for raw in ws:
                await self._handle_rpc(ws, raw)
        except Exception:  # noqa: BLE001 — connection dropped
            pass
        finally:
            self.clients.discard(ws)

    async def _handle_rpc(self, ws, raw) -> None:
        rid = None
        try:
            msg = json.loads(raw)
            rid = msg.get("id")
            result = await self._dispatch(msg.get("method"), msg.get("params") or {})
            await ws.send(json.dumps({"id": rid, "result": result}))
        except Exception as exc:  # noqa: BLE001 — an RPC error must not kill the conn
            logger.error("RPC failed: %s", exc)
            try:
                await ws.send(json.dumps({"id": rid, "error": str(exc)}))
            except Exception:  # noqa: BLE001
                pass

    # ── zmq IPC (CLI compatibility) ──────────────────────────────────

    def _start_ipc(self) -> bool:
        self._ipc_ctx = ctx = zmq.Context()
        sock = ctx.socket(zmq.REP)
        try:
            sock.bind(_ipc_address())
        except zmq.error.ZMQError:
            logger.error("IPC socket already in use — is another instance running?")
            return False
        threading.Thread(target=self._ipc_loop, args=(sock,), daemon=True).start()
        return True

    def _ipc_loop(self, sock) -> None:  # worker thread
        while True:
            try:
                try:
                    msg = sock.recv_string()
                except zmq.ContextTerminated:  # engine shutting down
                    sock.close(0)
                    return
                if msg == "TOGGLE":
                    self.loop.call_soon_threadsafe(self.toggle)
                    sock.send_string("OK")
                elif msg == "SETTINGS":
                    self.emit("show", {"window": "settings"})
                    sock.send_string("OK")
                elif msg == "MODELS":
                    self.emit("show", {"window": "models"})
                    sock.send_string("OK")
                elif msg == "PING":
                    sock.send_string("OK")  # liveness probe for `macaw --status`
                elif msg == "STOP":
                    sock.send_string("OK")
                    self.loop.call_soon_threadsafe(self._request_stop)
                else:
                    sock.send_string("UNKNOWN")
            except Exception as exc:  # noqa: BLE001
                logger.error("IPC error: %s", exc)

    # ── lifecycle ────────────────────────────────────────────────────

    def _stdin_watchdog(self) -> None:  # worker thread
        try:
            if sys.stdin is None:
                return
            fd = sys.stdin.fileno()
            # os.read: the buffered reader's lock would deadlock py shutdown
            while os.read(fd, 4096):
                pass
        except Exception:  # noqa: BLE001
            pass
        logger.info("stdin closed — shutting down")
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._request_stop)

    def _request_stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    async def run(self, port: int) -> int:
        from websockets.asyncio.server import serve

        self.loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        if not self._start_ipc():
            return 1
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self.loop.add_signal_handler(sig, self._request_stop)
            except NotImplementedError:  # Windows
                signal.signal(
                    sig,
                    lambda *_: self.loop.call_soon_threadsafe(self._request_stop),
                )
        threading.Thread(target=self._stdin_watchdog, daemon=True).start()
        self._start_hotkey()
        self._preload_model()
        async with serve(self._client, "127.0.0.1", port):
            print(f"READY ws={port}", flush=True)
            await self._stop_event.wait()
        self._cancel_op()
        self._stop_hotkey()
        self._capture_cancel()
        if self.is_recording:
            self.is_recording = False
            self._stop_tasks()
            self.capture.stop()
        if getattr(self, "_ipc_ctx", None) is not None:
            # Unblocks the REP thread (ContextTerminated) and joins socket
            # teardown — without this, Context.__del__ hangs interpreter exit.
            self._ipc_ctx.term()
        logger.info("Engine stopped.")
        return 0


def main(argv: list[str] | None = None) -> int:
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        # PyInstaller points LD_LIBRARY_PATH at its bundled (older) libs.
        # ld.so snapshotted that at exec, so dropping it from os.environ only
        # affects CHILD processes — backend venv workers, uv installs, and
        # system tools (wl-copy/ydotool/hyprctl) must NOT load the bundled
        # libstdc++ (GLIBCXX version mismatch kills e.g. the NeMo worker).
        orig = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig:
            os.environ["LD_LIBRARY_PATH"] = orig
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)
    p = argparse.ArgumentParser(
        prog="macaw-engine",
        description="Macaw headless engine — WebSocket API for the UI.",
    )
    p.add_argument(
        "--token",
        default="",
        help="WS auth token (default: random — the WS API stays private)",
    )
    p.add_argument("--ws-port", type=int, default=47540, help="WS port (127.0.0.1)")
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    token = args.token or secrets.token_hex(16)
    return asyncio.run(Engine(token).run(args.ws_port))


if __name__ == "__main__":
    sys.exit(main())
