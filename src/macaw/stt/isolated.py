from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from macaw.stt.base import Backend, MissingDependency
from macaw.stt.deps import _find_uv

logger = logging.getLogger("macaw")

_WORKER = str(Path(__file__).parent / "worker.py")
_PY_RANGE = ">=3.10,<3.14"


# -- per-extra isolated venv layout (parakeet + canary share the 'nemo' venv) --


def _root() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "macaw" / "backends"


def venv_dir(extra: str) -> Path:
    return _root() / extra


def venv_python(extra: str) -> Path:
    return venv_dir(extra) / "bin" / "python"


def _marker(extra: str) -> Path:
    return venv_dir(extra) / ".installed"


def is_installed(extra: str) -> bool:
    return bool(extra) and _marker(extra).exists() and venv_python(extra).exists()


def mark_installed(extra: str) -> None:
    _marker(extra).write_text("ok\n")


def dir_size(extra: str) -> int:
    d = venv_dir(extra)
    if not d.exists():
        return 0
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())


def remove(extra: str) -> int:
    size = dir_size(extra)
    shutil.rmtree(venv_dir(extra), ignore_errors=True)
    return size


def install_commands(extra: str, packages: list[str]) -> list[list[str]]:
    """Create the isolated venv and install the backend's packages into it.

    A fresh venv resolves independently, so these packages never conflict with
    the main CUDA + faster-whisper environment.
    """
    uv = _find_uv() or "uv"
    d = str(venv_dir(extra))
    return [
        [uv, "venv", "--python", _PY_RANGE, "--allow-existing", d],
        [uv, "pip", "install", "--python", str(venv_python(extra)), *packages],
    ]


# -- backend base that proxies to a persistent worker in the isolated venv -----


class SubprocessBackend(Backend):
    """Runs its model in a per-extra isolated venv via a persistent worker.

    Subclasses only declare `key` + `models`; all model loading and inference
    live in worker.py, executed by the isolated venv's Python. This is how
    backends with mutually-incompatible native deps coexist with Whisper.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._proc: subprocess.Popen | None = None

    # -- capability / weight management --------------------------------

    def available(self) -> bool:
        return is_installed(self.model.extra)

    def hf_repos(self) -> list[str]:
        return []  # weights live under the isolated venv, not the shared HF cache
        # (model_url still shows a download link via the catalog `repo`)

    def disk_size(self, cache: dict[str, int] | None = None) -> int:
        return dir_size(self.model.extra)

    def is_ready(self, cache: dict[str, int] | None = None) -> bool:
        return self.available()

    def delete(self) -> int:
        self.unload()
        return remove(self.model.extra)

    # -- worker lifecycle ----------------------------------------------

    def load(self, model_path: str | None = None) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        py = venv_python(self.model.extra)
        if not py.exists():
            raise MissingDependency(f"{self.model.extra} backend is not installed")
        logger.info("Starting %s worker (%s)...", self.key, self.model.id)
        self._proc = subprocess.Popen(
            [
                str(py),
                _WORKER,
                "--backend",
                self.key,
                "--model",
                self.model.id,
                "--language",
                self.language,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        status = self._read_message()
        if status.get("status") != "ready":
            self.unload()
            raise RuntimeError(
                f"{self.key} worker failed to start: {status.get('error', 'unknown')}"
            )
        logger.info("%s worker ready.", self.key)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> str:
        self.load()
        assert self._proc is not None and self._proc.stdin is not None
        fd, path = tempfile.mkstemp(prefix="macaw-audio-", suffix=".npy")
        os.close(fd)
        try:
            np.save(path, audio)
            self._proc.stdin.write(path + "\n")
            self._proc.stdin.flush()
            reply = self._read_message()
            if "error" in reply:
                raise RuntimeError(reply["error"])
            return reply.get("text", "").strip()
        finally:
            os.unlink(path)

    def unload(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None

    # -- protocol ------------------------------------------------------

    def _read_message(self) -> dict:
        """Read lines until a JSON object appears (tolerates stray output)."""
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = self._proc.stdout.readline()
            if not line:
                return {"error": "worker exited"}
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
