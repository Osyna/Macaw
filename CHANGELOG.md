# Changelog

Notable changes to Macaw. Older releases live on the [releases page](https://github.com/Osyna/Macaw/releases).

## v0.4.3

- **Fixed: sandboxed models (NeMo Parakeet & co.) crashed in packaged builds.**
  The frozen engine leaked PyInstaller's bundled library path into child
  processes, so backend workers loaded an old `libstdc++` and died
  (`GLIBCXX_3.4.32 not found`). Children now get a clean environment — this
  also protects `uv` installs and system tools (wl-copy, ydotool, hyprctl).
- **The overlay now shows a ✓ when your text lands in the clipboard**, and
  transcription failures flash the overlay red instead of ending silently.
- Switching models mid-load no longer reports a bogus "worker exited" error;
  the new choice loads right after the cancel.
- Backend worker crashes now log their stderr tail — no more blind failures.

## v0.4.2

- **Windows open on your current workspace.** Showing Settings/Models from the
  tray remaps the window instead of yanking you to the workspace it was first
  opened on (Wayland can't move mapped windows — so we unmap + remap).
- **`macaw` CLI is back for AppImage installs** — `install.sh` ships a thin
  wrapper; `macaw --settings | --models | --trigger | --stop` are handled by
  the running app (single-instance argv).
- **`install.sh` offers Reinstall / Uninstall / Quit** when Macaw is already
  installed, and `--uninstall` also removes the CLI wrapper and stops the app.

## v0.4.1

- **AppImage: overlay anchoring fixed on Wayland.** The AppImage runtime hook
  forced X11 (XWayland), which disabled wlr-layer-shell and let tiling
  compositors tile the recording indicator into the layout. Macaw now reclaims
  native Wayland when available, and the AppImage ships its own
  `gtk-layer-shell` copy — anchored overlay out of the box, no system package.

## v0.4.0 — New UI, whole app rebuilt on Tauri

- **New UI, whole app rebuilt on Tauri — PyQt6 is gone.** Macaw is now a native
  Tauri app (tray, Settings/Models window, recording overlay as a web frontend)
  driving a headless Python engine (`macaw-engine`) over a token-authed local
  WebSocket. The engine keeps everything that matters — audio capture, the whole
  model catalog, the evdev/RegisterHotKey global hotkey, typing/clipboard
  delivery, zmq CLI IPC — and dies with the app (stdin watchdog), so no orphan
  processes. Config file and schema are unchanged.
- **Overlay, now compositor-native on Wayland** — anchored bottom-center (or any
  corner, or custom X/Y) via wlr-layer-shell with true click-through; same
  per-corner radii, borders, opacity, and 24-bar equalizer as before, rendered
  on canvas with the same attack/release feel. Falls back to normal window
  positioning on X11 and Windows.
- **Fixes that came out of the rewrite** — NVIDIA + Wayland no longer crashes
  webkit (DMA-BUF renderer disabled); editing unrelated settings before a model
  is picked no longer flashes a sticky error; the engine shuts down cleanly
  (zmq context teardown).
- **Packaging** — Linux ships AppImage/deb/rpm with the engine embedded (no
  system Python needed); Windows gets an NSIS installer. `install.sh` now just
  fetches the AppImage and wires the launcher. The old AUR/PKGBUILD flow is
  retired until a `macaw-bin` package lands. Base install no longer pulls PyQt6.
- **Windows (win64, beta)** — Macaw runs natively on Windows. Global hotkey via
  `RegisterHotKey`, typing via `SendInput`, IPC over loopback TCP. Whisper,
  sherpa-onnx, Moonshine, Voxtral, and GPT-4o cloud all work; NeMo stays
  Linux-only. `uv.exe` ships with the engine so sandboxed model installs work
  out of the box.

## v0.3.0 — Eight new brains, one honest Manager

The model catalog triples down: **24 models across 7 engines**, and the Manager finally tells you which one *you* should run.

### New

- **sherpa-onnx engine** — six featherweight ONNX models that fly on plain CPUs: Parakeet TDT v2/v3 (ONNX), bilingual Chinese-English Zipformer and Paraformer, and two real-time streaming models as small as 26 MB. No GPU, no drama.
- **GPT-4o cloud (opt-in)** — `gpt-4o-transcribe` and its mini sibling for when you want maximum accuracy and don't mind the round-trip. Bring your own OpenAI key; nothing leaves your machine unless you pick them.
- **A Manager that gives opinions** — every model card now carries a curated star rating, plain-word pros & cons, VRAM figures, and minimum vs recommended hardware. The list is sorted best-first, and duplicate names (NeMo vs ONNX Parakeets) are finally told apart.

### Improved

- **Downloads and installs moved into the card** — a progress bar, a rotating status line ("Bribing the GPU…"), and a Cancel button, right where you clicked. No more modal dialogs.
- **Appearance panel, redesigned** — live preview pinned top-right, and the overlay's corner radius went per-corner with a Photoshop-style link toggle. Square one corner, round the rest. Go wild.
- **Friendlier star nudge** — the "enjoying Macaw?" prompt is now a small corner toast instead of a dialog in your face.

### Fixed

- **Delete actually deletes.** Removing a Parakeet/Canary/Voxtral/Moonshine model now frees its downloaded weights too — before, they quietly survived and "re-downloads" were suspiciously instant. Shared runtimes are only removed once the last model using them is gone.
- Per-corner overlay edits now reach the live overlay immediately — no restart, no stale shape.
- The settings mic preview and the recorder no longer fight over your microphone.
- The recording overlay reliably stays above the settings window.
