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

a = Analysis(
    [entry],
    datas=datas,
    hiddenimports=[
        "websockets",
        "sounddevice",
        "zmq",
        "yaml",
        "numpy",
        "faster_whisper",
    ],
    excludes=["PyQt6", "tkinter"],  # UI is Tauri now; keep the binary lean
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
