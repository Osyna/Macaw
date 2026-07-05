from __future__ import annotations

import importlib.metadata
import shutil
from pathlib import Path


def _find_uv() -> str | None:
    """Locate the uv binary. The systemd user service runs with a minimal PATH
    that omits ~/.local/bin, so shutil.which alone isn't enough."""
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (
        Path.home() / ".local/bin/uv",
        Path.home() / ".cargo/bin/uv",
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
