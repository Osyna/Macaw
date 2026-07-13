from __future__ import annotations

import importlib.metadata
import os
import shutil
import sys
from pathlib import Path


def _data_bin() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "macaw" / "bin"


def _find_uv() -> str | None:
    """Locate the uv binary. The systemd user service runs with a minimal PATH
    that omits ~/.local/bin, so shutil.which alone isn't enough. The Windows
    zip ships uv.exe next to the app executable; frozen Linux installs get a
    bootstrapped copy under the macaw data dir (see ensure_uv)."""
    found = shutil.which("uv")
    if found:
        return found
    exe = "uv.exe" if os.name == "nt" else "uv"
    for candidate in (
        Path(sys.executable).parent / exe,  # bundled (PyInstaller dist / venv bin)
        _data_bin() / exe,  # bootstrapped by ensure_uv on first install
        Path.home() / ".local/bin" / exe,
        Path.home() / ".cargo/bin" / exe,
        Path("/usr/bin/uv"),
    ):
        if candidate.exists():
            return str(candidate)
    return None


def _uv_download_url() -> tuple[str, str]:
    """(url, archive member) for this platform's latest uv build."""
    base = "https://github.com/astral-sh/uv/releases/latest/download"
    if os.name == "nt":
        return f"{base}/uv-x86_64-pc-windows-msvc.zip", "uv.exe"
    machine = os.uname().machine
    triple = {
        "x86_64": "x86_64-unknown-linux-gnu",
        "aarch64": "aarch64-unknown-linux-gnu",
    }.get(machine, "x86_64-unknown-linux-gnu")
    return f"{base}/uv-{triple}.tar.gz", f"uv-{triple}/uv"


def ensure_uv(progress=None) -> str:
    """A usable uv binary, bootstrapping a private copy if none is installed.

    The AppImage/zip installs don't ship uv (it would add ~40 MB to every
    artifact), but every backend venv install needs it — so the first install
    fetches one into ``<data>/macaw/bin`` and reuses it forever after.
    Raises on download failure (the caller surfaces it as install progress).
    """
    found = _find_uv()
    if found:
        return found
    import io
    import tarfile
    import urllib.request
    import zipfile

    url, member = _uv_download_url()
    if progress:
        progress("Fetching uv (first backend install)…")
    dest = _data_bin() / ("uv.exe" if os.name == "nt" else "uv")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as resp:
        blob = resp.read()
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            dest.write_bytes(zf.read(member))
    else:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
            f = tf.extractfile(member)
            if f is None:
                raise RuntimeError(f"uv archive is missing {member}")
            dest.write_bytes(f.read())
    dest.chmod(0o755)
    return str(dest)


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
