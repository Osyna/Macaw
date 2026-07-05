# Macaw

https://github.com/user-attachments/assets/570ed198-7781-48f0-b12d-82b6aca143fd

![Screenshot](assets/screenshot.png)

Fast speech-to-text dictation for Linux with system tray integration. Powered by faster-whisper running on GPU (CUDA) or CPU.

Press a hotkey, speak, and the transcribed text is either typed into the focused window or copied to your clipboard. Works across Wayland compositors (Hyprland, Sway, KDE, GNOME) and X11.

Webiste : [https://macaw.osyna.com/](https://macaw.osyna.com/)

## Features

- One-hotkey dictation with automatic silence detection
- Auto-type mode: text is pasted directly into the focused window
- Clipboard mode: text is copied to the clipboard
- Punctuation hints: per-language prompts that nudge Whisper toward natural punctuation
- Live typing (alpha): streaming transcription that types words as you speak
- System tray with settings GUI
- Cross-desktop support: Wayland (Hyprland, Sway, KDE, GNOME) and X11
- Smart paste tool selection: ydotool, wtype, xdotool with per-window fallback
- GPU acceleration via CUDA, with automatic CPU fallback
- Sound effects for recording, processing, and completion states


## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- At least one paste tool for auto-type mode: `ydotool`, `wtype`, or `xdotool`
- Clipboard tools: `wl-copy` and `wl-paste` (Wayland) or `xclip`/`xsel` (X11)
- PortAudio (`libportaudio2` or equivalent)
- CUDA toolkit (optional, for GPU acceleration)

### Arch Linux

```sh
pacman -S wl-clipboard wtype ydotool portaudio
```

### Ubuntu / Debian

```sh
apt install wl-clipboard wtype ydotool libportaudio2
```

For auto-type on Wayland, `ydotool` is recommended since it works with both native Wayland and XWayland windows. `wtype` only works with native Wayland apps. On X11, `xdotool` is sufficient.


## Install

Pick whichever fits you — all give you the `macaw` command, an app-menu
launcher (🦜 Macaw icon) and a `macaw-trigger` hotkey binary.

| Method | Best for | Command |
|--------|----------|---------|
| **Install script** | most Linux distros, GPU support | `curl -fsSL https://raw.githubusercontent.com/Osyna/Macaw/main/install.sh \| bash` |
| **AUR** | Arch / Manjaro | `yay -S macaw` |
| **AppImage** | no install, CPU only | download from [Releases](https://github.com/Osyna/Macaw/releases), `chmod +x`, run |
| **uv / pipx** | manual, you manage the service | `uv tool install "macaw @ git+https://github.com/Osyna/Macaw"` |

### Install script (recommended)

```sh
curl -fsSL https://raw.githubusercontent.com/Osyna/Macaw/main/install.sh | bash
# or from a clone:  ./install.sh
```

It installs `macaw` (via `uv tool`), detects CUDA/ROCm for GPU acceleration,
adds the desktop launcher, and enables a systemd user service that starts on
login. Re-run it any time to **uninstall** — it detects an existing install and
offers to remove it.

### AppImage (portable, CPU)

Grab `macaw-<version>-x86_64.AppImage` from the
[latest release](https://github.com/Osyna/Macaw/releases/latest):

```sh
chmod +x macaw-*-x86_64.AppImage
./macaw-*-x86_64.AppImage
```

Self-contained (bundles Python, Qt and the Whisper backend) — no system Python
needed. GPU backends aren't included; use the script or AUR for those.

### GPU acceleration & extra models

```sh
uv tool install "macaw[cuda] @ git+https://github.com/Osyna/Macaw"   # NVIDIA
macaw download large-v3-turbo                                          # fetch a model
```

Extra backends (Parakeet, Voxtral, Moonshine) install on demand from the Model
Manager, or as extras: `macaw[nemo]`, `macaw[voxtral]`, `macaw[moonshine]`.


## Usage

| Command | Description |
|---------|-------------|
| `macaw` | Main service with system tray, listens for IPC toggle commands |
| `macaw-trigger` | Sends a toggle signal to the running service (bind to a hotkey) |
| `macaw-cli` | Standalone CLI with push-to-talk (no service needed) |

### Hotkey setup

Bind `macaw-trigger` to a key in your compositor or desktop environment.

Hyprland (`hyprland.conf`):

```
bind = , F9, exec, ~/.local/bin/macaw-trigger
```

Sway (`config`):

```
bindsym F9 exec ~/.local/bin/macaw-trigger
```

KDE / GNOME: use the system keyboard shortcut settings to bind F9 (or any key) to `macaw-trigger`.


## Configuration

Settings are accessible from the system tray icon. They are stored in `~/.config/macaw/config.yaml` (or `$XDG_CONFIG_HOME/macaw/config.yaml`) and can also be edited directly:

```yaml
device_index: null          # Microphone index (null = system default)
language: en                # Whisper language code
model: large-v3-turbo       # Whisper model (see settings GUI for options)
output_mode: clipboard      # "clipboard" or "type"
silence_timeout: 3.0        # Seconds of silence before auto-stop
window_position: bottom_center  # Overlay position on screen
sound_enabled: true         # Play feedback tones
punctuation_hints: true     # Nudge Whisper to add natural punctuation
streaming: false            # Live typing — text appears as you speak (alpha)
```

### Output modes

- **clipboard** -- Transcribed text is copied to the clipboard. The overlay window shows a checkmark when done.
- **type** -- Text is pasted directly into the previously focused window via clipboard-paste simulation. The overlay hides before pasting to avoid stealing focus.

### Punctuation hints

When enabled, a well-punctuated sentence in the configured language is passed as Whisper's `initial_prompt`. This biases the model toward producing commas, periods, and question marks. Supported languages: English, French, German, Spanish, Italian, Portuguese, Dutch, Polish, Russian, Japanese, Chinese.

### Live typing (alpha)

When enabled (requires auto-type mode), macaw transcribes incrementally every ~1 second while you speak. Words that consecutive transcription passes agree on are typed immediately. When you stop speaking, a final pass flushes the remaining text.

This feature uses more GPU since it runs repeated inference on a growing audio window. It is disabled by default.


## Architecture

```
macaw-trigger  --[ZMQ IPC]--> macaw (service)
                                   |
                                   +-- AudioCapture (sounddevice)
                                   +-- Transcriber (facade) --> macaw.stt backends
                                   +-- DesktopHelper (clipboard, paste, focus)
                                   +-- RecordingWindow (PyQt6 overlay)
                                   +-- SettingsWindow (PyQt6 GUI)
                                   +-- SystemTrayIcon
```

- **IPC**: ZMQ REQ/REP over a Unix socket at `$XDG_RUNTIME_DIR/macaw.ipc`
- **Audio**: captured at 16kHz mono via sounddevice, with energy-based speech detection
- **Transcription**: pluggable backends (see below); default `large-v3-turbo` (faster-whisper), greedy decoding, Silero VAD filter
- **Paste simulation**: ydotool (evdev), wtype (Wayland virtual keyboard), or xdotool (X11), with automatic XWayland detection on Hyprland

### Speech-to-text backends

`Transcriber` is a thin facade — it normalizes audio (mono / float32 / 16 kHz)
and gates silence, then delegates to a **backend** selected by the model id in
config. Backends live in `src/macaw/stt/` and self-register:

| Model | Backend | Extra |
|-------|---------|-------|
| Whisper (tiny…large-v3-turbo) | faster-whisper | *(built in)* |
| Moonshine tiny/base | ONNX | `macaw[moonshine]` |
| Parakeet TDT v2/v3 | NVIDIA NeMo | `macaw[nemo]` |
| Canary-Qwen 2.5B | NVIDIA NeMo | `macaw[nemo]` |
| Voxtral Mini | transformers | `macaw[voxtral]` |

Whisper runs **in-process**. The other backends have native dependency stacks
(e.g. Moonshine → numba/numpy) that are irreconcilable with the CUDA +
faster-whisper environment, so each runs in its **own isolated venv** under
`~/.local/share/macaw/backends/<extra>/`, driven by a persistent sidecar
worker process (`stt/worker.py`) that exchanges audio↔text over a pipe. This is
what lets conflicting backends coexist with Whisper. The Model Manager's
**Install** button builds that venv one-click; nothing ever touches the main
environment. NeMo/Voxtral want a CUDA GPU and multi-GB downloads.

**Model Manager** — open with `macaw --models`, the tray *Models* entry, or
Settings → *Manage models…*. It lists every registered model with its hardware
recommendation (NVIDIA / AMD / Intel / CPU), VRAM needed, and download size,
and lets you download, delete, or set the active model. The recommendation
fields come straight from each model's `ModelInfo`, so a newly added backend
shows up here automatically.

Backends that need an optional dependency (Moonshine, Parakeet, Voxtral, …)
get a one-click **Install** button — no manual `pip`. It resolves the exact
packages from macaw's own metadata, installs them into the running
environment, and the backend becomes usable without a restart. *Install all
optional backends* does the lot in one go.

**Adding a new model** is two small files — a backend and a YAML entry:

```python
# src/macaw/stt/mybackend.py — the code (metadata lives in YAML, not here)
from macaw.stt.base import Backend
from macaw.stt.registry import register

@register
class MyBackend(Backend):
    key = "mybackend"                               # models bind to this key

    def load(self, model_path=None): ...            # load into memory
    def transcribe(self, audio, sample_rate=16_000) -> str: ...  # mono f32 16kHz
```

```yaml
# src/macaw/stt/models/mybackend.yaml — the catalog metadata
backend: mybackend            # matches Backend.key above; inherited by each model
models:
  - id: my-model
    label: My Model
    size: "~1 GB"
    speed: fast
    languages: EN
    hardware: "CPU / Any"
    vram: "—"
```

Import the module in `src/macaw/stt/__init__.py` so the class registers; the
YAML is picked up automatically at import. The model then appears in the Model
Manager with its hardware/VRAM/size — no other file changes. See
`tests/test_stt.py` for the contract.

If the backend's dependencies conflict with the main environment, subclass
`SubprocessBackend` instead (set `key` only) and add a loader to
`stt/worker.py` — it then installs into an isolated venv and runs
out-of-process for free.


## Troubleshooting

Check service logs:

```sh
journalctl --user -u macaw -f
```

### Common issues

**Auto-type not working**: Make sure at least one paste tool is installed (`ydotool`, `wtype`, or `xdotool`). Check the logs for "No paste tool available". For `ydotool`, the `ydotoold` daemon must be running and your user needs access to `/dev/uinput` (typically via the `input` group).

**Text goes to wrong window**: The service captures the active window ID before showing the recording overlay. If you switch windows during recording, the text may go to the original window. This is by design.

**No GPU acceleration**: Verify CUDA is available with `python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())"`. If it returns 0, check that CUDA libraries are in your `LD_LIBRARY_PATH` or install the `[cuda]` extra.

**Duplicate instance**: The service exits immediately if another instance is already bound to the IPC socket. Check `journalctl` for "IPC socket already in use".

**VSCode / Electron apps not receiving paste**: On Wayland, `wtype` sends virtual keyboard events that Electron apps can misinterpret. The service uses Shift+Insert instead of Ctrl+V for `wtype`, and prefers `ydotool` (evdev-level) which works universally.


## Releasing

Releases live on [GitHub Releases](https://github.com/Osyna/Macaw/releases) and
are built by CI (`.github/workflows/release.yml`) whenever a `vX.Y.Z` tag is
pushed. Each release carries the wheel, the source sdist, a self-contained CPU
AppImage, `SHA256SUMS`, and the staged AUR `PKGBUILD` + `macaw.install`.

Cut a release in one step — `make tag` bumps the version in `pyproject.toml`
**and** `packaging/aur/PKGBUILD`, commits, and creates the tag:

```sh
make tag VERSION=0.2.0
git push && git push origin v0.2.0
```

The workflow verifies the tag matches `pyproject.toml`, builds every artifact,
and publishes the GitHub Release with auto-generated notes.

### Publishing to the AUR

The AUR package (`yay -S macaw`) is a separate git repo. After the GitHub
release exists, refresh it from an Arch checkout:

```sh
packaging/aur/bump.sh 0.2.0     # fetches the tag tarball, writes sha256 + .SRCINFO
# then commit PKGBUILD + .SRCINFO to the macaw AUR repo and push
```

## License

MIT
