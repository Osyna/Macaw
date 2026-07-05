<!-- trunk-ignore-all(markdownlint/MD033) -->
<!-- trunk-ignore(markdownlint/MD041) -->
<div align="center">
  <img src="assets/Macaw.png" height="190px" width="auto" alt="Macaw logo">

  <h1>Macaw</h1>

  <h3>Talk, and Macaw types it. All on your own machine.</h3>

  <p><b>100% local&nbsp;&nbsp;•&nbsp;&nbsp;No cloud&nbsp;&nbsp;•&nbsp;&nbsp;No subscription&nbsp;&nbsp;•&nbsp;&nbsp;Wayland &amp; X11</b></p>

[![stars-badge-img]][stars-badge]
[![release-badge-img]][release-badge]
[![downloads-badge-img]][downloads-badge]
[![python-badge-img]][python-badge]
[![license-badge-img]][license-badge]

  <p>
    <a href="https://macaw.osyna.com/"><b>Website</b></a>
    &nbsp;•&nbsp;
    <a href="https://github.com/Osyna/Macaw/releases/latest"><b>Download</b></a>
    &nbsp;•&nbsp;
    <a href="https://github.com/Osyna/Macaw/issues/new"><b>Report a bug</b></a>
  </p>
</div>

Press a hotkey, say your sentence, and it drops straight into whatever you're typing in, or onto your clipboard. Macaw runs [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on your GPU or CPU, sits quietly in the tray, and works across Wayland (Hyprland, Sway, KDE, GNOME) and X11. Nothing gets uploaded. Nothing phones home.

<!--
  📸 Drop a short demo GIF here (aim for ~720px wide) — this is the money shot.
  <p align="center"><img src="assets/demo.gif" width="720" alt="Macaw dictating into an editor"></p>
-->

<details>
<summary><b>Table of contents</b></summary>

- [Why Macaw](#why-macaw)
- [Features](#features)
- [Models](#models)
- [Install](#install)
- [Usage](#usage)
- [Configuration](#configuration)
- [How it works](#how-it-works)
- [Adding a model](#adding-a-model)
- [Troubleshooting](#troubleshooting)
- [Releasing](#releasing)
- [Contributing](#contributing)
- [License](#license)

</details>

## Why Macaw

- 🔒 **It stays local.** Your voice and your text never leave the machine. No account, no cloud round-trip.
- 🆓 **Free and open.** MIT-licensed, and the models are open too. No subscription, no per-minute meter.
- 🐧 **Made for Linux.** Wayland or X11, a tray icon, a systemd user service, and one hotkey to fire it.
- 🔁 **Swap the brain.** Start on Whisper, move to Parakeet, Voxtral, or Moonshine whenever you feel like it.
- ⚡ **Uses your GPU.** CUDA when it's there, CPU when it isn't, and nothing to wire up either way.

## Features

- One hotkey to start and stop. It also stops on its own once you go quiet.
- Type mode pastes straight into the focused window. Clipboard mode just copies.
- Punctuation hints per language, so Whisper puts the commas and periods where you'd expect them.
- Live typing (alpha): words appear as you speak, once two passes agree on them.
- A tray icon with a settings window and a full model manager.
- Picks a paste tool for you (ydotool, wtype, or xdotool) and falls back per window when one misbehaves.
- Small sound cues for recording, processing, and done.
- A recording overlay you can actually style: opacity, colours, accent, corners, and the equaliser's spacing, width, roundness, and fade.

## Models

Whisper is built in. The rest install on demand from the Model Manager (`macaw --models`), each in its own sandbox so nothing clobbers your main setup. Pick the one that fits your hardware and languages.

| Model | Project | Languages | Download | Runs on | Install |
|-------|---------|-----------|----------|---------|---------|
| Whisper `tiny` → `large-v3-turbo` | [faster-whisper][faster-whisper] · [OpenAI Whisper][whisper] | 99+ | 75 MB – 3 GB | CPU or GPU | built in |
| Moonshine `tiny` / `base` | [Useful Sensors Moonshine][moonshine] | English | ~30–60 MB | CPU | `macaw[moonshine]` |
| Parakeet TDT `v2` / `v3` | [NVIDIA NeMo][nemo] | English / 25 | ~2.5 GB | NVIDIA GPU | `macaw[nemo]` |
| Canary-Qwen 2.5B | [NVIDIA NeMo][nemo] | English | ~5 GB | NVIDIA GPU | `macaw[nemo]` |
| Voxtral Mini 3B | [Mistral Voxtral][voxtral] · [Transformers][transformers] | 13 | ~6 GB | NVIDIA GPU | `macaw[voxtral]` |

The default is `large-v3-turbo`: 99+ languages, about 1.6 GB, and the best speed-to-accuracy trade-off on a GPU. On a laptop with no GPU, `base` or `distil-small.en` keep things snappy.

## Install

Every method gives you the `macaw` command, an app-menu launcher (the 🦜 icon), and a `macaw-trigger` binary for your hotkey.

| Method | Best for | Command |
|--------|----------|---------|
| Install script | most distros, GPU support | `curl -fsSL https://raw.githubusercontent.com/Osyna/Macaw/main/install.sh \| bash` |
| AUR | Arch / Manjaro | `yay -S macaw` |
| AppImage | no install, CPU only | grab it from [Releases](https://github.com/Osyna/Macaw/releases/latest), `chmod +x`, run |
| uv / pipx | you manage the service yourself | `uv tool install "macaw @ git+https://github.com/Osyna/Macaw"` |

First, the system bits Macaw talks to:

```sh
# Arch
pacman -S wl-clipboard wtype ydotool portaudio

# Ubuntu / Debian
apt install wl-clipboard wtype ydotool libportaudio2
```

On Wayland, `ydotool` is the safest pick for type mode: it handles native Wayland apps and XWayland alike. `wtype` only covers native Wayland windows. On X11, `xdotool` is enough. You'll also need Python 3.10+.

### Install script

```sh
curl -fsSL https://raw.githubusercontent.com/Osyna/Macaw/main/install.sh | bash
# or from a clone:  ./install.sh
```

It installs `macaw` with `uv tool`, detects CUDA or ROCm for GPU acceleration, adds the desktop launcher, and enables a systemd user service that starts on login. Run it again any time to uninstall: it spots an existing install and offers to remove it.

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

<!--
  📸 A screenshot of the settings window fits nicely here.
  <p align="center"><img src="assets/settings.png" width="720" alt="Macaw settings window"></p>
-->

**Output modes.** In clipboard mode the text is copied and the overlay shows a checkmark. In type mode the overlay hides first, so it won't steal focus, then the text is pasted into whatever window was focused when you started.

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

`Transcriber` is a thin facade. It normalizes audio to mono float32 at 16 kHz and gates silence, then hands off to whichever backend the configured model points at. Whisper runs in-process; the others carry native dependency stacks that don't get along with the CUDA + faster-whisper environment, so each lives in its own isolated venv under `~/.local/share/macaw/backends/<extra>/`, driven by a sidecar worker (`stt/worker.py`) that swaps audio and text over a pipe. The Model Manager's Install button builds that venv for you, and nothing touches the main environment.

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

**Second instance won't start.** Only one service can hold the IPC socket. If it exits immediately, `journalctl` shows "IPC socket already in use".

**Electron apps miss the paste.** On Wayland, `wtype` sends virtual keyboard events some Electron apps read wrong. Macaw uses Shift+Insert instead of Ctrl+V for `wtype`, and prefers `ydotool`, which works everywhere.

## Releasing

Releases are built by CI (`.github/workflows/release.yml`) whenever a `vX.Y.Z` tag is pushed. Each one ships the wheel, the source sdist, a self-contained CPU AppImage, `SHA256SUMS`, and the staged AUR `PKGBUILD` plus `macaw.install`.

Cut one in a single step. `make tag` bumps the version in `pyproject.toml` and `packaging/aur/PKGBUILD`, commits, and tags:

```sh
make tag VERSION=0.2.0
git push && git push origin v0.2.0
```

To refresh the AUR package after the release exists, run `packaging/aur/bump.sh 0.2.0` from an Arch checkout (it fetches the tag tarball, writes the `sha256` and `.SRCINFO`), then commit and push those to the macaw AUR repo.

## Contributing

Bug reports, ideas, and pull requests are all welcome. [Open an issue](https://github.com/Osyna/Macaw/issues/new) to start. Run `ruff check` and `python tests/test_stt.py` before you push, and keep changes focused.

## License

MIT. Use it, fork it, ship it.

## Acknowledgments

Macaw stands on a lot of good open-source work:

- [faster-whisper][faster-whisper] and [OpenAI Whisper][whisper] for the default engine
- [NVIDIA NeMo][nemo] for Parakeet and Canary-Qwen
- [Mistral Voxtral][voxtral] and Hugging Face [Transformers][transformers]
- [Useful Sensors Moonshine][moonshine] for the featherweight option
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/), [sounddevice](https://python-sounddevice.readthedocs.io/), and [uv](https://docs.astral.sh/uv/)

## Star history

If Macaw saves you some typing, a ⭐ helps other people find it.

<a href="https://star-history.com/#Osyna/Macaw&Date">
  <img src="https://api.star-history.com/svg?repos=Osyna/Macaw&type=Date" width="600" alt="Star history chart">
</a>

<!-- Badges -->

[stars-badge-img]: https://img.shields.io/github/stars/Osyna/Macaw?style=for-the-badge&color=e5322b
[stars-badge]: https://github.com/Osyna/Macaw/stargazers
[release-badge-img]: https://img.shields.io/github/v/release/Osyna/Macaw?style=for-the-badge&color=e5322b
[release-badge]: https://github.com/Osyna/Macaw/releases/latest
[downloads-badge-img]: https://img.shields.io/github/downloads/Osyna/Macaw/total?style=for-the-badge&color=f7b500
[downloads-badge]: https://github.com/Osyna/Macaw/releases
[python-badge-img]: https://img.shields.io/badge/python-3.10%2B-3776ab?style=for-the-badge
[python-badge]: https://www.python.org/
[license-badge-img]: https://img.shields.io/github/license/Osyna/Macaw?style=for-the-badge&color=666666
[license-badge]: LICENSE

<!-- Model & library links -->

[faster-whisper]: https://github.com/SYSTRAN/faster-whisper
[whisper]: https://github.com/openai/whisper
[moonshine]: https://github.com/usefulsensors/moonshine
[nemo]: https://github.com/NVIDIA/NeMo
[voxtral]: https://huggingface.co/mistralai/Voxtral-Mini-3B-2507
[transformers]: https://github.com/huggingface/transformers
