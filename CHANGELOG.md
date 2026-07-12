# Changelog

Notable changes to Macaw. Older releases live on the [releases page](https://github.com/Osyna/Macaw/releases).

## Unreleased

- **Appearance is its own tab.** The theming studio moved out of Settings
  into a dedicated top-level section: an always-visible live preview with
  state chips on the left (plus theme presets and save/update/delete), and
  scrollable Position & Size / Shape & Colors / per-state sections on the
  right. Settings keeps audio, hotkey, cloud and system.

- **Animation galleries.** Animations are now picked from live tiles — every
  tile is the real pill running that animation with your colors:
  - Recording: `bars` (classic), `mirror`, `dots`, `orb`, `wave`
    (oscilloscope), `blocks` (segmented VU), `ripple` (energy rings),
    `meter` (level bar) — all audio-reactive.
  - Transcribing: `waves`, `sweep`, `pulse`, `dots` + new `scan`, `cascade`,
    `shimmer`, `orbit`, `typewriter`, `bounce`, `heartbeat`.
  - Done: a new entrance animation — `pop` (default), `flash`, `rise`,
    `none` — and gallery tiles replay it on a loop so you can compare.
  New config fields `record_anim` / `done_anim` join theme snapshots, so
  custom themes capture them.

- **Visible scrollbars.** Every scrollable pane (Settings, Appearance's
  editor column, the model list and dossier) now shows a slim scrollbar —
  draggable, appears only when content overflows.

- Settings window: fixed size (1180×760, not resizable), a ✕ close button in
  the header, and **hover hints** — rest the cursor on any setting label
  (Microphone, Position, …) for a subtle explanation of what it does.

- **Custom themes.** Editing any indicator property flips the selector to
  "● custom (unsaved)" with a save row: name it and it lands in
  `custom_themes` (config), appears in the selector, and can be Updated or
  Deleted. Built-in themes are never overwritten (their names are reserved);
  picking a built-in resets all overrides to pristine.
- Fixed dropdown text overflowing its box (long microphone names now elide).

- **Horizontal layouts.** Settings is two-column (audio/system left, the
  full appearance editor right); the Model manager is master-detail like the
  original PyQt app — compact list with status dots on the left, one big
  always-visible dossier (specs, links, language, parameters, actions) on
  the right, auto-selecting the active model.
- **State-scoped appearance editing**: the preview chips (Recording /
  Transcribing / Done / Error) now switch the controls underneath. Each
  state owns its options — Recording: bars + gradient; Transcribing:
  animation, speed (0.25–3×), and a "use recording colors" link that can be
  unlinked for its own gradient; Done: check color; Error: flash color.

- **The 10 themes now style the indicator only.** The app window is a
  minimal terminal-like chrome — monochrome, hard 0-radius corners, no
  shadows, monospace — in Dark (real black) or Light, chosen under
  System → App theme (`app_theme`, as in the old app).
- Fixed stale header regions after theme switches (partial-repaint damage
  gap in the software renderer — full-window damage is now forced on
  palette swaps).

- **Complete model manager**: search box + status filters (All / Ready /
  Installed / Cloud / Streaming), and every card expands into a full dossier —
  description, pros & cons, spec table (speed, languages, minimal &
  recommended hardware, VRAM, disk use), Library/Weights links, per-model
  spoken-language choice, and the backend's tunable parameters (temperature,
  beam size, VAD, …) with hints — everything the old manager knew, back.

- Fixed the gradient tail glitch: sampling at the far end returned the
  second-to-last color (clamped-index endpoint bug) — bars and the editor
  strip now land exactly on the last stop, and the editor strip renders the
  real interpolated gradient instead of flat blocks.
- **"Show indicator on screen" toggle** in Appearance: pins the real overlay
  (animated) while you edit — move it with the position controls, resize it,
  recolor it, all live on the actual layer surface.

- **Theme engine v2** — the pill renderer is now a faithful port of the
  original canvas overlay: real continuous quiet-bar fade (alpha follows the
  level, no threshold), smooth gradient interpolation along the bars, and
  bars that blend from idle to your gradient as speech is heard.
- **Per-corner radius** editing (link/unlink, four sliders), **bar count**
  (8–48), bar width/gap/rounding, and a **pill background override**.
- **Selectable transcribing animation**: `waves` (the classic, now the
  default), `sweep`, `pulse`, `dots` — previewable from Settings.

- **The recording indicator is now a wlr-layer-shell surface** (dedicated
  `macaw-overlay` process, Slint software renderer into shm buffers): always
  top-most, positioned by compositor anchors at your configured spot, exact
  size, click-through — on ANY Wayland compositor with layer-shell (Hyprland,
  Sway, KDE, …). No window-manager involvement at all; compositors without
  layer-shell (GNOME) fall back to the previous floating window + Hyprland
  rules path automatically.
- **Theming overhaul**: live overlay preview inside Settings (with
  Recording / Transcribing / Done / Error state chips), HSV color pickers,
  an equalizer gradient editor (add/remove/edit stops), and six new themes —
  Dracula, Nord, Gruvbox, Tokyo Night, Rosé Pine, Solarized.

- **New native frontend built with Slint** (`macaw-slint/`, Linux-first, local
  builds only for now). Replaces the Tauri/WebKit shell with one 11 MB Rust
  binary — ~23 MB RSS (was ~230 MB+ with the webview), pure-CPU software
  renderer (no GL/GPU driver in the UI process), no tokio, no GTK, no
  JavaScript. Feature parity: tray (ksni/StatusNotifierItem), Settings +
  Models manager, recording overlay (equalizer, loader, ✓, error flash),
  single-instance with `--settings/--models/--trigger/--stop` forwarding,
  live theme + overlay customization.
- Overlay positioning on Hyprland now uses runtime window rules
  (new 0.5x `windowrule` syntax, legacy `windowrulev2` fallback) — no
  gtk-layer-shell dependency for the Slint frontend.
- The Tauri app (`src-tauri/` + `ui/`) remains the packaged/release path
  until the Slint frontend ships through CI; it will be removed after that
  cutover.

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
