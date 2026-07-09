# Changelog

Notable changes to Macaw. Older releases live on the [releases page](https://github.com/Osyna/Macaw/releases).

## Unreleased

- **Windows (win64, beta)** — Macaw now runs natively on Windows. Global hotkey via `RegisterHotKey`, typing via `SendInput`, IPC over loopback TCP, and a PyInstaller zip on every release (`Macaw.exe` tray app + `macaw-cli.exe` console twin, `uv.exe` bundled so sandboxed model installs work out of the box). Whisper, sherpa-onnx, Moonshine, Voxtral, and GPT-4o cloud all work; NeMo stays Linux-only.

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
