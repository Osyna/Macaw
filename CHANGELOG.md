# Changelog

Notable changes to Macaw. Older releases live on the [releases page](https://github.com/Osyna/Macaw/releases).

## 0.8.1 — 2026-07-14

- **Live typing defaults to a 5 s silence stop.** Switching Output type
  to Live typing bumps "Stop after silence" from the stock 3 s to 5 s —
  live dictation needs breathing room. Only the stock value is nudged;
  anything you set yourself is respected.
- **Never auto-stop.** New toggle under Stop after silence: silence never
  ends the session — the hotkey (or tray / Record button) starts AND
  stops recording. The timeout slider hides behind an ∞ while it's on.

## 0.8.0 — 2026-07-14

- **Live typing now trusts streaming models.** Native streamers
  (Nemotron, Zipformer/Paraformer online, Moonshine v2) type their own
  committed text verbatim the moment it appears — the word-agreement
  filter (built for re-decoding models) added a full tick of lag and
  held back the last words of every sentence. Live ticks run at 4 Hz on
  native models (they only eat new samples). Batch models keep the
  agreement filter + smart splitting exactly as before.
- Live typing no longer injects spaces between typed chunks (they doubled
  spaces and could split words mid-token); deltas type verbatim.
- New worker protocol line "R": cancelling a live session now clears the
  model's persistent stream, so its text can never replay into the next
  recording.
- **First-launch wizard.** A fresh install now opens a friendly six-step
  setup: microphone (with a live level bar and an Advanced fold for boost
  and silence level), language, output style as three explained cards,
  punctuation/sounds, and the global hotkey — captured right in the
  wizard. Skippable at any point; existing installs never see it.
- **One "Output type" setting.** Clipboard, Type into window, or Live
  typing — the separate "Live typing" checkbox is gone (old configs
  migrate automatically). When Live typing is picked, Settings shows
  whether the active model streams natively (⚡) or uses the fallback.
- **Live typing got faster on non-streaming models.** Smart splitting:
  once the utterance grows past ~12 s, everything before the last long
  pause is decoded once and frozen — each tick (and the final pass) only
  re-decodes the live tail, so long dictations no longer slow down as
  they grow. Natively-streaming models are untouched (already bounded).
- **Sliders that write fractions actually work now.** Input level and
  Input boost were truncated to whole numbers on save — dragging the
  silence marker always snapped back to 0%.
- The input-level marker drags smoothly from anywhere on the track and
  commits once on release.
- **New stock oled theme** — the neon "Oled Custom" look (magenta→cyan
  ramp, 14 thick rounded bars, mirror animation, pill-round corners on
  true black) is now the built-in `oled` preset.

## 0.7.2 — 2026-07-14

- **Windows: fixed the last launch blocker.** The WebSocket auth token was
  read from `/dev/urandom` — a path that doesn't exist on Windows, so the
  UI died before its first window (the new `ui-crash.log` caught it
  exactly). Entropy now comes from the OS RNG via `getrandom`
  (BCryptGenRandom on Windows) on every platform.
- CI now runs `macaw-ui.exe --selftest` on a real Windows runner: the
  pre-window startup surface (entropy, single-instance socket) is
  exercised on every build, and a failure prints the crash log right in
  the job output.

## 0.7.1 — 2026-07-14

- **Windows: the GUI now actually launches.** 0.7.0's window died before
  first paint — Slint's software renderer panics when Windows font
  enumeration comes up empty, and the GL renderer wants a matching OpenGL
  config it won't always get (VMs, RDP, odd drivers). The UI fonts
  (DejaVu Sans + Mono, regular and bold) are now embedded in the binary
  and pinned as the default families, so text rendering never touches
  system-font enumeration on any platform. Verified end-to-end by running
  the released win64 artifact under wine: window, tray, engine link and
  hardware-ranked picks all live.
- If the UI ever dies on Windows again, the panic lands in
  `%LOCALAPPDATA%\Macaw\ui-crash.log` with a backtrace — a silent
  GUI-subsystem death is no longer undiagnosable.
- Bonus: embedded fonts lock in the chip/status glyph coverage that
  system fonts couldn't guarantee.

## 0.7.0 — 2026-07-13

- **Windows gets the real app.** The native Slint UI now builds and ships
  for win64 — the same tray, Models/Appearance tabs, hardware-ranked picks,
  themes and recording overlay as Linux. Installers before this shipped the
  retired pre-cutover WebView frontend; that stack is gone from the repo.
  - Recording overlay: frameless always-on-top window, placed from monitor
    geometry with the same anchor settings as Linux.
  - Tray: Win32 notification icon — left-click opens, menu has the same
    Start/Stop recording, Settings, Models and Quit entries.
  - Single instance + `macaw-ui.exe --trigger/--settings/--models/--stop`
    argv forwarding over loopback TCP.
  - Start-at-login now writes an `HKCU\…\Run` entry; engine and browser
    launches no longer flash console windows.
  - Ships as an NSIS per-user installer (no admin) plus a portable zip.

## 0.6.0 — 2026-07-13

- **3× smaller app.** The engine binary drops from ~130 MB to ~43 MB
  (AppImage/installer shrink to match): faster-whisper and its ffmpeg/
  CTranslate2/onnxruntime payload no longer ship inside the engine.
  Whisper now installs on demand into its own sandboxed venv like every
  other backend — one click in the Model Manager; on NVIDIA machines the
  CUDA wheels (cublas/cudnn) are added to that venv automatically and
  decoding stays on the GPU.

- **First install bootstraps its own `uv`.** Frozen installs (AppImage,
  Windows zip) no longer assume `uv` on PATH: the first backend install
  fetches a private copy under `~/.local/share/macaw/bin` and reuses it.

- The Skip-silence gate is now a pure-numpy adaptive energy gate (Silero
  left with faster-whisper). Same contract — 2 s minimum gap, 400 ms
  padding, and it can only ever trim stretches quieter than −46 dBFS, so
  quiet speech is never cut.

- Worker protocol: a fire-and-forget `C {json}` config line carries
  language, punctuation hints and per-model tunables (temperature, beam
  size, VAD) to backend workers per call — no worker restart on change.

## 0.5.0 — 2026-07-13

- Done “flash”: the ok-tinted wash now follows the pill’s rounded
  corners instead of flashing a square box around them.

- **Silence gate you can see.** The Input level meter doubles as the
  silence control: drag the marker on the live bar — anything quieter
  counts as silence for the auto-stop timer (`silence_level`). The band
  left of the marker is tinted, the live fill goes muted under the gate,
  and the mapping tracks your Input boost so the marker always means
  exactly what the meter shows. Default matches the old behaviour.

- **Filters, reworked.** "For you" leads; nothing is selected by default;
  clicking a chip toggles it off again. "All", "Ready" and "Local" are gone;
  new **☘ Light** (resource-friendly) filter finds the CPU-only models —
  every chip has an icon, and light models wear a dossier badge. The
  For-you ranking now reserves two slots for resource-friendly picks, so
  Moonshine v2 and Nemotron stay visible next to the GPU heavyweights.

- **A real light theme.** Layered cool grays with an azure accent instead
  of flat white-on-white, and toasts now follow the active theme instead
  of always rendering dark.

- **Picks for this machine.** The engine probes your hardware once (GPU
  vendor, VRAM, cores, RAM, ARM/Apple) and ranks the catalog against each
  model's own metadata — top picks wear a "#1 pick" badge, a "For you"
  filter shows just the ranked list, and the dossier explains every pick
  ("uses your 24 GB NVIDIA GPU"). Detected specs shown above the list.
  Cloud models only rank once their API key is set; models that need a
  GPU you don't have (or more VRAM than you've got) are never suggested.

- Model dossier: a new **About** section — description and feature/tradeoff
  bullets — now sits below the Spoken language picker for every model, and
  multi-line pros/cons render correctly (previously dropped entirely).

- Settings: **Default language** is a proper list now (same languages as
  the per-model picker) instead of a free-text ISO field.

- Sherpa models decode with up to 4 ONNX threads instead of 2 (measured
  1.4x on the Nemotron encoder), and Whisper's per-model VAD filter now
  defaults off — the global Skip-silence gate already does that job, so
  Silero no longer runs twice on the Whisper path.

- **True streaming for live typing.** Natively-streaming models (the sherpa
  Zipformer/Paraformer/Nemotron family, and now Moonshine v2) keep one
  persistent decode stream during live typing and receive only the NEW
  audio each tick —
  bounded per-tick cost instead of re-decoding the whole utterance every
  second. Ticks never overlap anymore, and worker protocol calls are
  serialized (a live-typing tick can no longer race the final pass).

- **Nemotron Streaming EN** (0.6B int8, ~660 MB): NVIDIA's cache-aware
  streaming FastConformer — the strongest natively-streaming English model
  in sherpa-onnx, sub-second latency on CPU. Recommended for live typing.

- **Moonshine v2** (tiny/small/medium, 35–250 MB): English streaming ASR
  with bounded time-to-first-token; medium rivals Whisper large-v3 at a
  sixth of the size, on CPU. New `moonshine2` backend + isolated venv.

- **Faster transcription on every model.** A Silero VAD gate now cuts
  silent stretches before audio reaches any backend — dictation is often
  30–60% silence, so this is a straight speed multiplier and it removes
  Whisper's hallucinate-on-silence failure mode. New "Skip silence"
  toggle in Settings (`vad_gate`, on by default; zero new dependencies —
  reuses the Silero model faster-whisper already ships).

- **Faster NeMo models.** Parakeet and Canary-Qwen now run under bf16
  autocast on CUDA (NVIDIA's own acceleration recipe), and `cuda-python`
  is pinned in the `nemo` extra so CUDA-graph decoding can never be
  silently disabled by a dependency shuffle.

- **About**: click the parrot next to the Macaw title — version, GitHub,
  releases and osyna.com links in a small overlay.

- **Input boost**: a gain slider next to the live level meter (0.5–4×) —
  quiet microphones get full-height animations, and the level still caps
  at the maximum when you suddenly scream. Visual only: silence detection
  and transcription are untouched.

- Settings: a live **input level meter** under the microphone picker — it
  runs only while the Settings tab is on screen (speak and the bar moves;
  silent means check your device). New **ENGINE card**: backend status,
  **Reload model** (terminates the model worker — force-killed if stuck —
  and loads it clean) and **Force restart** (kills the whole backend
  process and starts fresh; the UI reconnects automatically).

- Header: tabs reordered — **⬢ Models first** (and the landing tab),
  ◐ Appearance next, ⚙ Settings right-aligned; each tab has an icon.

- Done state: the entrance animation now **loops in the live preview** and
  on the pinned on-screen indicator (real dictation still plays it once),
  the ✓ sits on a **configurable circle** (defaults to the pill color), and
  the `rise` animation is gone — saved configs fall back to `pop`.

- Models: the **active model is unmistakable** — green outline + "active"
  badge on its list row and a green ACTIVE badge on its dossier.
  Scrollbars now live in their own gutter (no more drawing over list cards)
  and follow the app theme in dark and light mode.

- **Lighter and faster.** The 30 Hz preview animation now stops entirely
  when nothing shows it (0% CPU hidden in the tray, was constant background
  work), level bars update in place instead of rebuilding the bar row every
  tick, and the animation galleries run on a fixed 12-bar preview instead of
  animating the full bar count per tile. One shared gradient sampler
  (`Grad`) replaces three duplicated implementations.

- Fixed: reopening the window from the tray left it tiled with dead space
  and no fixed size (the compositor class is lost on re-map) — float/size
  rules are now also keyed on the window title and re-enforced on every
  show.

- **Appearance is its own tab.** The theming studio moved out of Settings
  into a dedicated top-level section: an always-visible live preview with
  state chips on the left (plus theme presets and save/update/delete), and
  scrollable Position & Size / Shape & Colors / per-state sections on the
  right. Settings keeps audio, hotkey, cloud and system.

- **Animation galleries.** Animations are now picked from live tiles — every
  tile is the real pill running that animation with your colors:
  - Recording: `bars` (classic), `mirror`, `dots`, `wave` (oscilloscope),
    `blocks` (segmented VU), `ripple` (energy rings) — all audio-reactive.
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
