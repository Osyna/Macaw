# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macaw-engine onefile console build (Linux + Windows).

The engine is the headless Python side of the Tauri app: audio capture, STT,
global hotkey, text injection, WebSocket API. Tauri bundles the resulting
binary as a sidecar (src-tauri/binaries/macaw-engine-<target-triple>).

Bundled as real files (read from disk at runtime, not imported):
  - the YAML model catalog        (macaw/stt/models/*.yaml)
  - sound assets                  (macaw/assets/*)
  - the isolated-backend worker   (macaw/stt/worker.py — spawned via subprocess)
  - macaw's dist metadata         (packages_for_extra parses Requires-Dist)
"""

import os
import re
import subprocess
import sys

from PyInstaller.utils.hooks import copy_metadata

root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))  # noqa: F821 — SPECPATH is injected by PyInstaller
src = os.path.join(root, "src", "macaw")

# Generated entry stub — keeps the spec the only committed file here.
entry = os.path.join(SPECPATH, "entry.py")  # noqa: F821
with open(entry, "w") as f:
    f.write("from macaw.engine import main; import sys; sys.exit(main())\n")

datas = [
    (os.path.join(src, "assets"), "macaw/assets"),
    (os.path.join(src, "stt", "models"), "macaw/stt/models"),
    (os.path.join(src, "stt", "worker.py"), "macaw/stt"),
]
datas += copy_metadata("macaw")

# The Linux sounddevice wheel does NOT bundle libportaudio (the Windows/macOS
# wheels do) — it dlopens the system lib. Bundle it so the frozen engine runs
# on hosts without portaudio installed (AppImage, raw binary).
binaries = []
if sys.platform.startswith("linux"):
    out = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True).stdout
    m = re.search(r"libportaudio\.so\.2\S* .*=> (\S+)", out)
    if not m:
        raise SystemExit(
            "libportaudio.so.2 not found — install portaudio (Arch) / "
            "libportaudio2 (Debian) so it can be bundled into the engine."
        )
    binaries.append((m.group(1), "."))

a = Analysis(
    [entry],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "websockets",
        "sounddevice",
        "zmq",
        "yaml",
        "numpy",
        "huggingface_hub",
    ],
    # The STT backends (faster-whisper included) live in on-demand isolated
    # venvs — none of their heavy deps may leak into the frozen engine.
    excludes=[
        "PyQt6",
        "tkinter",
        "faster_whisper",
        "ctranslate2",
        "av",
        "onnxruntime",
        "tokenizers",
    ],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="macaw-engine",
    console=True,
)
