# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the win64 onedir build.

Bundles as real files (they're read from disk at runtime, not imported):
  - the YAML model catalog        (macaw/stt/models/*.yaml)
  - sound + icon assets           (macaw/assets/*)
  - the isolated-backend worker   (macaw/stt/worker.py — spawned via subprocess)
  - macaw's dist metadata         (packages_for_extra parses Requires-Dist)

Two executables share one dist folder:
  Macaw.exe        — windowed tray app (double-click and go)
  macaw-cli.exe    — console build of the same entry point, for --status,
                     --trigger, --repl and for seeing logs while testing.
"""

import os

from PyInstaller.utils.hooks import copy_metadata

root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))  # noqa: F821 — SPECPATH is injected by PyInstaller
src = os.path.join(root, "src", "macaw")

datas = [
    (os.path.join(src, "assets"), "macaw/assets"),
    (os.path.join(src, "stt", "models"), "macaw/stt/models"),
    (os.path.join(src, "stt", "worker.py"), "macaw/stt"),
]
datas += copy_metadata("macaw")

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],  # noqa: F821
    datas=datas,
    excludes=["evdev"],  # Linux hotkey backend
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Macaw",
    icon=os.path.join(src, "assets", "macaw.png"),  # Pillow converts png -> ico
    console=False,
)
exe_cli = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="macaw-cli",
    icon=os.path.join(src, "assets", "macaw.png"),
    console=True,
)
coll = COLLECT(exe, exe_cli, a.binaries, a.datas, name="Macaw")
