from __future__ import annotations

import importlib.metadata
import os
import shutil
import sys
from pathlib import Path


def _find_uv() -> str | None:
    """Locate the uv binary. The systemd user service runs with a minimal PATH
    that omits ~/.local/bin, so shutil.which alone isn't enough. The Windows
    zip ships uv.exe next to the app executable."""
    found = shutil.which("uv")
    if found:
        return found
    exe = "uv.exe" if os.name == "nt" else "uv"
    for candidate in (
        Path(sys.executable).parent / exe,  # bundled (PyInstaller dist / venv bin)
        Path.home() / ".local/bin" / exe,
        Path.home() / ".cargo/bin" / exe,
        Path("/usr/bin/uv"),
    ):
        if candidate.exists():
            return str(candidate)
    return None


def packages_for_extra(extra: str) -> list[str]:
    """Pip requirements for an optional extra, read from macaw's own metadata.

    Single source of truth is pyproject.toml's [project.optional-dependencies];
    this parses the installed package's Requires-Dist so nothing is duplicated.
    """
    try:
        reqs = importlib.metadata.metadata("macaw").get_all("Requires-Dist") or []
    except importlib.metadata.PackageNotFoundError:
        return []
    wanted = (f'extra == "{extra}"', f"extra == '{extra}'")
    packages = []
    for req in reqs:
        if ";" not in req:
            continue
        name, marker = req.split(";", 1)
        if any(w in marker for w in wanted):
            packages.append(name.strip())
    return packages
