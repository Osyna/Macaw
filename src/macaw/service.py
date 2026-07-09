from __future__ import annotations

import logging
import math
import os
import queue
import signal
import subprocess
import sys
import threading
import time

import numpy as np
import pyperclip
import zmq
from PyQt6.QtCore import (
    Q_ARG,
    QMetaObject,
    QObject,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from macaw import net
from macaw.audio import sounds
from macaw.audio.capture import AudioCapture
from macaw.audio.transcriber import Transcriber
from macaw.config import _DEFAULT_CONFIG_PATH, Config
from macaw.desktop import DesktopHelper
from macaw.gui.icon import create_tray_icon, logo_icon
from macaw.gui.main_window import MainWindow
from macaw.gui.theme import active_indicator
from macaw.gui.window import RecordingWindow
from macaw.stt import get_model_info
from macaw.trigger import _ipc_address

logger = logging.getLogger("macaw")


def _lang_for(cfg: Config) -> str:
    """The active model's language: its per-model choice, else the default."""
    return cfg.model_languages.get(cfg.model) or cfg.language or "en"


IPC_ADDRESS = _ipc_address()

CONFIG_PATH = _DEFAULT_CONFIG_PATH


class _IPCWorker(QObject):
    toggle_received = pyqtSignal()
    settings_received = pyqtSignal()
    models_received = pyqtSignal()
    quit_received = pyqtSignal()

    def run(self) -> None:
        ctx = zmq.Context()
        sock = ctx.socket(zmq.REP)
        try:
            sock.bind(IPC_ADDRESS)
        except zmq.error.ZMQError:
            logger.error("IPC socket already in use — is another instance running?")
            import os

            os._exit(1)
        while True:
            try:
                msg = sock.recv_string()
                if msg == "TOGGLE":
                    self.toggle_received.emit()
                    sock.send_string("OK")
                elif msg == "SETTINGS":
                    self.settings_received.emit()
                    sock.send_string("OK")
                elif msg == "MODELS":
                    self.models_received.emit()
                    sock.send_string("OK")
                elif msg == "PING":
                    sock.send_string("OK")  # liveness probe for `macaw --status`
                elif msg == "STOP":
                    sock.send_string("OK")
                    self.quit_received.emit()
                else:
                    sock.send_string("UNKNOWN")
            except Exception as exc:
                logger.error("IPC error: %s", exc)


class _ModelLoadThread(QThread):
    """Loads the active model off the UI thread so activating never freezes,
    and turns a load failure into a signal instead of an uncaught crash."""

    done = pyqtSignal(bool, str)  # (ok, error)

    def __init__(self, transcriber) -> None:
        super().__init__()
        self._transcriber = transcriber

    def run(self) -> None:
        try:
            self._transcriber.load_model()
            self.done.emit(True, "")
        except Exception as exc:  # noqa: BLE001 — report it, never crash the UI
            self.done.emit(False, str(exc))


class MacawService:
    def __init__(self) -> None:
        self.app = QApplication(sys.argv)
        # PyQt6 aborts the process on an unhandled exception in a slot; log it
        # and keep running so a transcription/capture hiccup can't kill Macaw.
        sys.excepthook = self._excepthook
        self.app.setQuitOnLastWindowClosed(False)
        # Stable identity → Wayland app_id / X11 class becomes "macaw" (not
        # "python3"), so compositor window rules can target the app reliably.
        self.app.setApplicationName("Macaw")
        self.app.setDesktopFileName("macaw")
        self.app.setWindowIcon(logo_icon())

        self.cfg = Config.load(CONFIG_PATH)
        net.apply(self.cfg.proxy, self.cfg.ssl_verify)  # proxy/SSL: downloads + cloud

        # Desktop integration (clipboard, paste, window focus)
        self.desktop = DesktopHelper()

        # Audio + transcription
        self.capture = AudioCapture(device=self.cfg.device_index)
        self.transcriber = Transcriber(
            model_size=self.cfg.model,
            language=_lang_for(self.cfg),
            punctuation_hints=self.cfg.punctuation_hints,
        )
        self.transcriber.model_params = self.cfg.model_params.get(self.cfg.model, {})

        # GUI
        self.window = RecordingWindow(self.cfg.overlay_width, self.cfg.overlay_height)
        self.window.stop_signal.connect(self._stop_recording)

        self.main_window = MainWindow(CONFIG_PATH)
        self.main_window.config_saved.connect(self._on_config_changed)
        self.main_window.cancel_load.connect(self._cancel_model_load)
        self._load_cancelled = False
        self._model_loaded = False  # True only after a model loads successfully

        self._setup_tray()

        # Preload model (show progress dialog if download needed)
        self._preload_model()

        # State
        self.is_recording = False
        self._saved_window_id: str | None = None

        # Streaming state
        self._stream_buffer: list[np.ndarray] = []
        self._stream_prev_text: str = ""
        self._stream_confirmed_len: int = 0  # chars already typed
        self._stream_timer = QTimer()
        self._stream_timer.setInterval(1000)
        self._stream_timer.timeout.connect(self._stream_tick)
        self._streaming_active = False

        # Silence timer
        self._silence_timer = QTimer()
        self._silence_timer.setInterval(50)
        self._silence_timer.timeout.connect(self._check_silence)

        # IPC
        self._ipc_worker = _IPCWorker()
        self._ipc_thread = QThread()
        self._ipc_worker.moveToThread(self._ipc_thread)
        self._ipc_worker.toggle_received.connect(self._handle_toggle)
        self._ipc_worker.settings_received.connect(self._show_settings)
        self._ipc_worker.models_received.connect(self._show_models)
        self._ipc_worker.quit_received.connect(self.app.quit)
        self._ipc_thread.started.connect(self._ipc_worker.run)
        self._ipc_thread.start()
        self._hotkey = None
        self._start_hotkey()
        self.app.aboutToQuit.connect(self._stop_hotkey)
        if os.path.exists(self._reopen_marker()):
            try:
                os.remove(self._reopen_marker())
            except OSError:
                pass
            # a look change restarted us while Settings was open — bring it back
            QTimer.singleShot(500, self._show_settings)

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self.app)
        self._tray.setIcon(create_tray_icon())
        menu = QMenu()
        # Disable compositor transparency on Wayland
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        menu.setWindowOpacity(1.0)
        menu.setStyleSheet("""
            QMenu {
                background-color: #0A0A0A;
                color: #FFFFFF;
                border: 1px solid #2A2A2A;
                padding: 4px 0;
            }
            QMenu::item {
                background-color: #0A0A0A;
                padding: 8px 24px;
                font-size: 13px;
            }
            QMenu::item:selected {
                background-color: #1A1A1A;
            }
            QMenu::separator {
                height: 1px;
                background: #2A2A2A;
                margin: 4px 0;
            }
        """)
        settings_act = QAction("Settings", self.app)
        settings_act.triggered.connect(self._show_settings)
        menu.addAction(settings_act)
        models_act = QAction("Models", self.app)
        models_act.triggered.connect(self._show_models)
        menu.addAction(models_act)
        menu.addSeparator()
        quit_act = QAction("Quit", self.app)
        quit_act.triggered.connect(self.app.quit)
        menu.addAction(quit_act)
        self._tray.setContextMenu(menu)
        self._tray.show()

    def _preload_model(self) -> None:
        """Load the active model if it's downloaded. Never auto-download —
        the user downloads models explicitly from the Model Manager."""
        if self.transcriber.is_ready():
            self._load_model_async()
        else:
            logger.warning(
                "No usable model (%s not downloaded). Open Models to download one.",
                self.cfg.model,
            )
            # First run / nothing usable — open the Manager so the welcome shows.
            QTimer.singleShot(400, self._show_models)

    def _show_settings(self) -> None:
        self.main_window.open_settings()

    def _reopen_marker(self) -> str:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
        return os.path.join(runtime, "macaw-reopen-settings")

    def _show_models(self) -> None:
        self.main_window.open_models()

    def _excepthook(self, exc_type, exc, tb) -> None:
        logger.error("Unhandled exception", exc_info=(exc_type, exc, tb))

    def _notify_error(self, message: str) -> None:
        logger.error("%s", message)
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.showMessage(
                "Macaw", message, QSystemTrayIcon.MessageIcon.Warning, 5000
            )

    def _notify_no_model(self) -> None:
        """No model is downloaded — tell the user and open the Model Manager."""
        logger.warning("Recording blocked: no model downloaded (%s).", self.cfg.model)
        self.window.show_message("No Model Selected")
        self.window.position(
            self.cfg.window_position,
            custom=(self.cfg.overlay_x, self.cfg.overlay_y),
        )
        self.window.show()
        QTimer.singleShot(2500, self.window.hide)
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.showMessage(
                "Macaw — no model",
                "No speech-to-text model is downloaded. "
                "Download one in the Model Manager.",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )
        self._show_models()

    # Indicator look: the overlay's own palette / size / bars — applied live.
    _INDICATOR = (
        "theme",
        "overlay_opacity",
        "overlay_width",
        "overlay_height",
        "eq_colors",
        "accent_color",
        "border_width",
        "border_color",
        "corner_radius",
        "corners",
        "corner_link",
        "bar_spacing",
        "bar_width",
        "bar_radius",
        "bar_fade",
    )
    # Chrome: settings / model windows' palette — baked at import, needs a restart.
    _CHROME = ("app_theme",)
    _LOOK = _CHROME + _INDICATOR  # any look field (union; used by tests)

    def _on_config_changed(self, cfg: Config) -> None:
        old = self.cfg
        self.cfg = cfg
        self.transcriber.language = _lang_for(cfg)
        self.transcriber.punctuation_hints = cfg.punctuation_hints
        self.transcriber.model_params = cfg.model_params.get(cfg.model, {})
        self.capture = AudioCapture(device=cfg.device_index)
        if (cfg.proxy, cfg.ssl_verify) != (old.proxy, old.ssl_verify):
            net.apply(cfg.proxy, cfg.ssl_verify)

        t = getattr(self, "_load_thread", None)
        loading = t is not None and t.isRunning()
        # Reload on a new model, or retry one whose last load failed (so the user
        # can just re-activate it — no need to bounce through another model).
        if cfg.model != old.model or (not self._model_loaded and not loading):
            self._switch_model(cfg.model)
        if any(getattr(old, f) != getattr(cfg, f) for f in self._INDICATOR):
            self.window.apply_look(
                active_indicator(), cfg.overlay_width, cfg.overlay_height
            )
        if any(getattr(old, f) != getattr(cfg, f) for f in self._CHROME):
            self._restart_for_look()
        if (cfg.hotkey_enabled, cfg.hotkey) != (old.hotkey_enabled, old.hotkey):
            self._start_hotkey()

    def _restart_for_look(self) -> None:
        """Theme/style changed — tell the user and restart to apply it cleanly."""
        QMessageBox.information(
            self.main_window,
            "New look",
            "Macaw will restart automatically to apply the new theme / style.",
        )
        QTimer.singleShot(150, self._restart)

    def _restart(self) -> None:
        logger.info("Restarting to apply appearance change…")
        try:  # a look change always comes from Settings — reopen it after restart
            open(self._reopen_marker(), "w").close()
        except OSError:
            pass
        if os.environ.get("INVOCATION_ID"):
            # Managed by systemd — let it cycle the unit cleanly.
            subprocess.Popen(["systemctl", "--user", "restart", "macaw.service"])
            self.app.quit()
        else:
            # Foreground: drop the IPC socket file, then re-exec a fresh process.
            addr = IPC_ADDRESS
            if addr.startswith("ipc://"):
                try:
                    os.unlink(addr[len("ipc://") :])
                except OSError:
                    pass
            os.execv(sys.executable, [sys.executable, "-m", "macaw"])

    def _start_hotkey(self) -> None:
        """(Re)start the global-shortcut listener to match the current config."""
        from macaw.hotkey import HotkeyListener, is_valid

        self._stop_hotkey()
        if not self.cfg.hotkey_enabled or not is_valid(self.cfg.hotkey):
            return
        self._hotkey = HotkeyListener(self.cfg.hotkey)
        self._hotkey.triggered.connect(self._handle_toggle)
        self._hotkey.start()

    def _stop_hotkey(self) -> None:
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey.wait(1500)
            self._hotkey = None

    def _switch_model(self, model_name: str) -> None:
        """(Re)load a newly-selected model in the background. Only downloaded
        models can be selected (the Model Manager enforces that), so we never
        download here — we just reload, off the UI thread so it can't freeze."""
        self._model_loaded = False
        self.transcriber.unload_model()
        self.transcriber.model_size = model_name
        if self.transcriber.is_ready():
            self._load_model_async()
        else:
            logger.warning("Model %s not downloaded — not loaded", model_name)
            self._set_manager_status(
                "error", get_model_info(model_name).label, "not downloaded"
            )

    def _load_model_async(self) -> None:
        """Load the active model on a background thread; report state to the UI."""
        prev = getattr(self, "_load_thread", None)
        if prev is not None and prev.isRunning():
            return  # a load is already in flight (the UI disables activate)
        label = get_model_info(self.transcriber.model_size).label
        logger.info("Loading model (%s)...", self.transcriber.model_size)
        self._set_manager_status("loading", label)
        self._load_thread = _ModelLoadThread(self.transcriber)
        self._load_thread.done.connect(self._on_model_loaded)
        self._load_thread.start()

    def _on_model_loaded(self, ok: bool, error: str) -> None:
        label = get_model_info(self.transcriber.model_size).label
        self._model_loaded = ok
        if self._load_cancelled:
            self._load_cancelled = False
            self._model_loaded = False
            self.transcriber.unload_model()  # reap any worker that raced the cancel
            logger.info("Model load cancelled (%s)", self.transcriber.model_size)
            self._set_manager_status("cancelled", label)
            return
        if ok:
            logger.info("Model ready (%s).", self.transcriber.model_size)
            self._set_manager_status("ready", label)
        else:
            logger.error(
                "Model load failed (%s): %s", self.transcriber.model_size, error
            )
            self._set_manager_status("error", label, error)

    def _set_manager_status(self, state: str, label: str, detail: str = "") -> None:
        mw = getattr(self, "main_window", None)
        if mw is not None:
            mw.models.show_load_status(state, label, detail)

    def _cancel_model_load(self) -> None:
        """User cancelled: kill the worker the load thread is blocked on. The
        load fails fast ('worker exited') and unload reaps it — no zombie."""
        thread = getattr(self, "_load_thread", None)
        if thread is not None and thread.isRunning():
            logger.info("Cancelling model load (%s)", self.transcriber.model_size)
            self._load_cancelled = True
            self.transcriber.unload_model()

    # -- silence detection ----------------------------------------------

    def _check_silence(self) -> None:
        if not self.is_recording:
            return
        raw = self.capture.current_energy
        vis = (math.log10(raw) + 4) / 3.0 if raw > 1e-10 else 0.0
        self.window.set_energy(min(1.0, max(0.0, vis)))

        if not self.capture.speech_detected:
            if time.time() - self.capture.last_sound_time >= self.cfg.silence_timeout:
                self._cancel_recording()
                return

        if self.capture.speech_detected and self.capture.last_sound_time:
            if time.time() - self.capture.last_sound_time >= self.cfg.silence_timeout:
                self._stop_recording()

    # -- recording lifecycle --------------------------------------------

    def _handle_toggle(self) -> None:
        logger.info("Toggle received (recording=%s)", self.is_recording)
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        try:
            self._start_recording_inner()
        except Exception as exc:  # noqa: BLE001 — a slot exception must not abort
            logger.error("Failed to start recording: %s", exc, exc_info=True)
            self.is_recording = False
            self._silence_timer.stop()
            try:
                self.window.hide()
            except Exception:  # noqa: BLE001
                pass
            self._notify_error(f"Couldn't start recording: {exc}")

    def _start_recording_inner(self) -> None:
        if self.is_recording:
            return
        if not self.transcriber.is_ready():
            self._notify_no_model()
            return
        self._pause_preview()  # free the settings mic so it can't clash with capture
        if self.cfg.sound_enabled:
            sounds.play_start()
        if self.cfg.output_mode == "type":
            self._saved_window_id = self.desktop.capture_active_window()
        self.is_recording = True
        self.window.position(
            self.cfg.window_position,
            custom=(self.cfg.overlay_x, self.cfg.overlay_y),
        )
        self.window.set_state("recording")
        self.window.show()
        if self.cfg.output_mode != "type":
            self.window.activateWindow()
        self.window.raise_()
        self.capture.start()
        self.capture.last_sound_time = time.time()
        self.capture.speech_detected = False
        self._silence_timer.start()

        # Start streaming if enabled
        if self.cfg.streaming and self.cfg.output_mode == "type":
            self._streaming_active = True
            self._stream_buffer = []
            self._stream_prev_text = ""
            self._stream_confirmed_len = 0
            self._stream_timer.start()
            logger.info("Streaming transcription active")

    def _cancel_recording(self) -> None:
        if not self.is_recording:
            return
        logger.info("Recording cancelled (no speech detected).")
        self.is_recording = False
        self._silence_timer.stop()
        if self._streaming_active:
            self._streaming_active = False
            self._stream_timer.stop()
        self.window.set_energy(0.0)
        self.capture.stop()
        self.capture.read()  # drain
        self.window.hide()
        QTimer.singleShot(500, self._resume_preview)

    def _pause_preview(self) -> None:
        """Release the settings mic preview so it can't clash with recording."""
        mw = getattr(self, "main_window", None)
        if mw is not None:
            mw.settings.stop_preview()

    def _resume_preview(self) -> None:
        """Restart the settings mic preview once recording is idle again."""
        if self.is_recording:
            return
        mw = getattr(self, "main_window", None)
        if mw is None or not mw.isVisible():
            return
        if mw.stack.currentWidget() is mw.settings:
            mw.settings.start_preview()

    def _stop_recording(self) -> None:
        if not self.is_recording:
            return
        self.is_recording = False
        self._silence_timer.stop()
        # Stop streaming timer
        if self._streaming_active:
            self._streaming_active = False
            self._stream_timer.stop()
        self.window.set_energy(0.0)
        self.capture.stop()
        if self.cfg.sound_enabled:
            sounds.play_analysing()
        self.window.set_state("analysing")
        threading.Thread(target=self._process_audio, daemon=True).start()
        QTimer.singleShot(500, self._resume_preview)

    def _process_audio(self) -> None:
        try:
            self._process_audio_inner()
        except Exception as exc:  # noqa: BLE001 — daemon-thread guard + clean overlay
            logger.error("Transcription failed: %s", exc, exc_info=True)
            self._notify_error(f"Transcription failed: {exc}")
            QMetaObject.invokeMethod(
                self.window, "hide", Qt.ConnectionType.QueuedConnection
            )

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
            if self.cfg.output_mode == "type":
                # Hide overlay BEFORE typing so it doesn't steal focus
                QMetaObject.invokeMethod(
                    self.window,
                    "hide",
                    Qt.ConnectionType.QueuedConnection,
                )
                time.sleep(0.08)  # let compositor process the unmap

            # In streaming mode, only type the remaining unconfirmed text
            if self._stream_confirmed_len > 0:
                remaining = text[self._stream_confirmed_len :]
                self._stream_confirmed_len = 0
                self._stream_prev_text = ""
                if remaining.strip():
                    self._deliver_text(remaining.strip())
            else:
                self._deliver_text(text)

            if self.cfg.sound_enabled:
                sounds.play_done()
            if self.cfg.output_mode != "type":
                QMetaObject.invokeMethod(
                    self.window,
                    "set_state",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, "done"),
                )
                time.sleep(0.5)
        QMetaObject.invokeMethod(
            self.window,
            "hide",
            Qt.ConnectionType.QueuedConnection,
        )

    # -- streaming transcription ----------------------------------------

    def _stream_tick(self) -> None:
        """Periodic streaming transcription while recording."""
        if not self._streaming_active:
            return

        # Drain current audio chunks into buffer
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

        # Run in thread to not block UI
        threading.Thread(
            target=self._stream_transcribe,
            args=(audio.copy(), self.capture.sample_rate),
            daemon=True,
        ).start()

    def _stream_transcribe(self, audio: np.ndarray, sample_rate: int) -> None:
        """Background thread: transcribe and type confirmed text."""
        confirmed, full = self.transcriber.transcribe_streaming(
            audio,
            sample_rate=sample_rate,
            prev_text=self._stream_prev_text,
        )
        self._stream_prev_text = full

        # Type only the newly confirmed portion
        if confirmed and len(confirmed) > self._stream_confirmed_len:
            new_text = confirmed[self._stream_confirmed_len :]
            # Add trailing space so next chunk appends cleanly
            if not new_text.endswith(" "):
                new_text += " "
            self._stream_confirmed_len = len(confirmed)
            logger.debug("Streaming: typing confirmed %r", new_text)
            self.desktop.type_into_window(new_text, self._saved_window_id)

    # -- text delivery --------------------------------------------------

    def _deliver_text(self, text: str) -> None:
        if self.cfg.output_mode == "type":
            self.desktop.type_into_window(text, self._saved_window_id)
        else:
            try:
                pyperclip.copy(text)
            except Exception as exc:
                logger.error("Clipboard error: %s", exc)

    # -- run ------------------------------------------------------------

    def run(self) -> None:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        sys.exit(self.app.exec())


def main() -> None:
    # The CLI (macaw.cli) is the real entry point; kept here for back-compat
    # with anything still importing macaw.service:main.
    from macaw.cli import main as cli_main

    sys.exit(cli_main())
