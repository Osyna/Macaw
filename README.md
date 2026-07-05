<!-- trunk-ignore-all(markdownlint/MD033) -->
<!-- trunk-ignore(markdownlint/MD041) -->
<div align="center">
  <img src="assets/Macaw.png" height="180px" width="auto" alt="Macaw logo">

  <h3 style="font-size: 25px;">
    Fast speech-to-text dictation for Linux, one hotkey away.
  </h3>

[![release-badge-img]][release-badge]
[![python-badge-img]][python-badge]
[![platform-badge-img]][platform-badge]
[![website-badge-img]][website-badge]
[![license-badge-img]][license-badge]

  </div>
</div>

# Overview

Macaw turns your voice into text. Press a hotkey, talk, and what you said is either typed into the window you're using or dropped on your clipboard. It runs [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on your GPU (CUDA) or CPU, lives in the system tray, and works on both Wayland (Hyprland, Sway, KDE, GNOME) and X11.

The website is at [macaw.osyna.com](https://macaw.osyna.com/).

## Features

- One hotkey to start and stop. It also stops on its own once you go quiet.
- Type mode pastes straight into the focused window. Clipboard mode just copies.
- Punctuation hints per language, so Whisper adds the commas and periods you'd expect.
- Live typing (alpha): words show up as you speak, once two passes agree on them.
- Swappable speech-to-text models. Whisper is built in; Moonshine, Parakeet/Canary, and Voxtral install on demand.
- Picks a paste tool for you (ydotool, wtype, or xdotool) and falls back per window when one misbehaves.
- Uses the GPU when there is one and drops to CPU when there isn't.
- Small sound cues for recording, processing, and done.
- A tray icon with a settings window and a model manager.

## Install

Every method gives you the `macaw` command, an app-menu launcher (the 🦜 icon), and a `macaw-trigger` binary for your hotkey.

| Method | Best for | Command |
|--------|----------|---------|
| Install script | most distros, GPU support | `curl -fsSL https://raw.githubusercontent.com/Osyna/Macaw/main/install.sh \| bash` |
| AUR | Arch / Manjaro | `yay -S macaw` |
| AppImage | no install, CPU only | grab it from [Releases](https://github.com/Osyna/Macaw/releases/latest), `chmod +x`, run |
| uv / pipx | you manage the service yourself | `uv tool install "macaw @ git+https://github.com/Osyna/Macaw"` |

Before installing, make sure you have the system bits Macaw talks to:

```sh
# Arch
pacman -S wl-clipboard wtype ydotool portaudio

# Ubuntu / Debian
apt install wl-clipboard wtype ydotool libportaudio2
```

On Wayland, `ydotool` is the safest pick for type mode: it works with native Wayland apps and XWayland alike. `wtype` only handles native Wayland windows. On X11, `xdotool` is enough. You'll also need Python 3.10+.

### Install script

```sh
curl -fsSL https://raw.githubusercontent.com/Osyna/Macaw/main/install.sh | bash
# or from a clone:  ./install.sh
```

It installs `macaw` with `uv tool`, detects CUDA or ROCm for GPU acceleration, adds the desktop launcher, and enables a systemd user service that starts on login. Run it again any time to uninstall: it notices an existing install and offers to remove it.

### AppImage

Grab `macaw-<version>-x86_64.AppImage` from the [latest release](https://github.com/Osyna/Macaw/releases/latest):

```sh
chmod +x macaw-*-x86_64.AppImage
./macaw-*-x86_64.AppImage
```

It bundles Python, Qt, and the Whisper backend, so you don't need a system Python. The GPU backends aren't included; use the script or AUR for those.

### GPU and extra models

```sh
uv tool install "macaw[cuda] @ git+https://github.com/Osyna/Macaw"   # NVIDIA
macaw download large-v3-turbo                                        # fetch a model
```

The other backends (Parakeet, Voxtral, Moonshine) install from the Model Manager when you want them, or as extras: `macaw[nemo]`, `macaw[voxtral]`, `macaw[moonshine]`.

## Usage

| Command | What it does |
|---------|--------------|
| `macaw` | The tray service. Listens for toggle commands over IPC. |
| `macaw-trigger` | Toggles the running service. Bind this to a key. |
| `macaw-cli` | Standalone push-to-talk. No service needed. |

### Hotkey setup

Bind `macaw-trigger` to a key in your compositor or desktop settings.

```conf
# Hyprland (hyprland.conf)
bind = , F9, exec, ~/.local/bin/macaw-trigger

# Sway (config)
bindsym F9 exec ~/.local/bin/macaw-trigger
```

On KDE or GNOME, add a custom keyboard shortcut that runs `macaw-trigger`.

## Configuration

Settings live behind the tray icon. They're saved to `~/.config/macaw/config.yaml` (or `$XDG_CONFIG_HOME/macaw/config.yaml`), and you can edit the file by hand:

```yaml
device_index: null          # microphone index (null = system default)
language: en                # Whisper language code
model: large-v3-turbo       # see the Model Manager for the full list
output_mode: clipboard      # "clipboard" or "type"
silence_timeout: 3.0        # seconds of silence before it auto-stops
window_position: bottom_center
sound_enabled: true
punctuation_hints: true     # nudge Whisper toward natural punctuation
streaming: false            # live typing as you speak (alpha)
```

The tray settings also cover the recording overlay's look: opacity, bar colours, accent, border, corner radius, and the equaliser's spacing, width, roundness, and fade.

**Output modes.** In clipboard mode the text is copied and the overlay shows a checkmark. In type mode the overlay hides first (so it doesn't steal focus), then the text is pasted into whatever window was focused when you started.

**Punctuation hints.** When on, Macaw feeds Whisper a well-punctuated sentence in your language as the `initial_prompt`, which biases it toward commas, periods, and question marks. Works for English, French, German, Spanish, Italian, Portuguese, Dutch, Polish, Russian, Japanese, and Chinese.

**Live typing (alpha).** With type mode on, Macaw re-transcribes about once a second while you talk and types the words that two consecutive passes agree on. A final pass flushes the rest when you stop. It runs inference on a growing audio window, so it costs more GPU. Off by default.

## How it works

```
macaw-trigger  --[ZMQ IPC]-->  macaw (service)
                                   |
                                   +-- AudioCapture (sounddevice)
                                   +-- Transcriber (facade) --> macaw.stt backends
                                   +-- DesktopHelper (clipboard, paste, focus)
                                   +-- RecordingWindow (PyQt6 overlay)
                                   +-- SettingsWindow (PyQt6 GUI)
                                   +-- SystemTrayIcon
```

- IPC is ZMQ REQ/REP over a Unix socket at `$XDG_RUNTIME_DIR/macaw.ipc`.
- Audio is captured at 16 kHz mono with energy-based speech detection.
- Transcription runs through pluggable backends; the default is `large-v3-turbo` on faster-whisper with a Silero VAD filter.
- Pasting uses ydotool (evdev), wtype (Wayland virtual keyboard), or xdotool (X11), with XWayland detection on Hyprland.

### Speech-to-text backends

`Transcriber` is a thin facade. It normalizes audio to mono float32 at 16 kHz and gates silence, then hands off to whichever backend the configured model id points at. Backends live in `src/macaw/stt/` and register themselves.

| Model | Backend | Extra |
|-------|---------|-------|
| Whisper (tiny … large-v3-turbo) | faster-whisper | built in |
| Moonshine tiny/base | ONNX | `macaw[moonshine]` |
| Parakeet TDT v2/v3 | NVIDIA NeMo | `macaw[nemo]` |
| Canary-Qwen 2.5B | NVIDIA NeMo | `macaw[nemo]` |
| Voxtral Mini | transformers | `macaw[voxtral]` |

Whisper runs in-process. The others carry native dependency stacks that don't get along with the CUDA + faster-whisper environment, so each one lives in its own isolated venv under `~/.local/share/macaw/backends/<extra>/`, driven by a sidecar worker (`stt/worker.py`) that swaps audio and text over a pipe. That's what lets conflicting backends sit next to Whisper without breaking it. The Model Manager's Install button builds that venv for you, and nothing touches the main environment. NeMo and Voxtral want a CUDA GPU and multi-GB downloads.

Open the Model Manager with `macaw --models`, the tray *Models* entry, or Settings → *Manage models…*. It lists every model with its hardware recommendation, VRAM, and download size, and lets you download, delete, or switch the active one. Those fields come from each model's catalog entry, so a new backend shows up on its own.

### Adding a model

Adding a model is two small files: a backend and a YAML entry.

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

Import the module in `src/macaw/stt/__init__.py` so the class registers; the YAML is read at import. The model then shows up in the Model Manager with its hardware, VRAM, and size. If the backend's dependencies clash with the main environment, subclass `SubprocessBackend` instead (set `key` only) and add a loader to `stt/worker.py`; it installs into an isolated venv and runs out-of-process. See `tests/test_stt.py` for the contract.

## Troubleshooting

Service logs:

```sh
journalctl --user -u macaw -f
```

**Type mode does nothing.** You need at least one paste tool (`ydotool`, `wtype`, or `xdotool`); watch the logs for "No paste tool available". For `ydotool`, the `ydotoold` daemon has to be running and your user needs access to `/dev/uinput`, usually via the `input` group.

**Text lands in the wrong window.** Macaw grabs the active window before it shows the overlay. Switch windows mid-recording and the text follows the original one. That's on purpose.

**No GPU acceleration.** Check CUDA with `python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())"`. A `0` means CUDA libraries aren't on your `LD_LIBRARY_PATH`, or you need the `[cuda]` extra.

**Second instance won't start.** Only one service can hold the IPC socket. If it exits immediately, `journalctl` will show "IPC socket already in use".

**Electron apps miss the paste.** On Wayland, `wtype` sends virtual keyboard events that some Electron apps read wrong. Macaw uses Shift+Insert instead of Ctrl+V for `wtype`, and prefers `ydotool`, which works everywhere.

## Releasing

Releases are built by CI (`.github/workflows/release.yml`) whenever a `vX.Y.Z` tag is pushed. Each one ships the wheel, the source sdist, a self-contained CPU AppImage, `SHA256SUMS`, and the staged AUR `PKGBUILD` plus `macaw.install`.

Cut one in a single step. `make tag` bumps the version in `pyproject.toml` and `packaging/aur/PKGBUILD`, commits, and tags:

```sh
make tag VERSION=0.2.0
git push && git push origin v0.2.0
```

To refresh the AUR package after the release exists, run `packaging/aur/bump.sh 0.2.0` from an Arch checkout (it fetches the tag tarball, writes the `sha256` and `.SRCINFO`), then commit and push those to the macaw AUR repo.

## Support

Found a bug or have an idea? [Open an issue](https://github.com/Osyna/Macaw/issues/new).

## License

MIT

<!-- Badges -->

[release-badge-img]: https://img.shields.io/github/v/release/Osyna/Macaw?style=for-the-badge&color=e5322b
[release-badge]: https://github.com/Osyna/Macaw/releases/latest
[python-badge-img]: https://img.shields.io/badge/python-3.10%2B-3776ab?style=for-the-badge
[python-badge]: https://www.python.org/
[platform-badge-img]: https://img.shields.io/badge/platform-Linux-2f6fd0?style=for-the-badge
[platform-badge]: https://github.com/Osyna/Macaw
[website-badge-img]: https://img.shields.io/badge/website-macaw.osyna.com-f7b500?style=for-the-badge
[website-badge]: https://macaw.osyna.com/
[license-badge-img]: https://img.shields.io/github/license/Osyna/Macaw?style=for-the-badge&color=666666
[license-badge]: LICENSE
