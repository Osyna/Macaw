from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np

from macaw.stt.base import Backend, MissingDependency, hf_cache_sizes, hf_repo_delete
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
    if os.name == "nt":
        return venv_dir(extra) / "Scripts" / "python.exe"
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


def _worker_env() -> dict:
    """Environment for a backend worker: the parent's env minus the import-path
    knobs, so an isolated venv can NEVER pick up the main env's packages (e.g.
    macaw itself) through a stray PYTHONPATH/PYTHONHOME. Belt-and-braces with the
    sys.path cleanup worker.py does on startup."""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


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
        self._incremental = False  # worker advertises native streaming at ready
        # One request-reply pair at a time: a live-typing tick racing the final
        # utterance pass would otherwise interleave stdin writes / stdout reads.
        self._lock = threading.Lock()

    # -- capability / weight management --------------------------------

    def available(self) -> bool:
        return is_installed(self.model.extra)

    def hf_repos(self) -> list[str]:
        # Weights download to the SHARED HF cache on first load (keyed by the
        # catalog `repo`) — only the pip deps live in the isolated venv. So size
        # and delete must track the HF cache, not just the venv directory.
        return [self.model.repo] if self.model.repo else []

    def disk_size(self, cache: dict[str, int] | None = None) -> int:
        # this model's weights (shared HF cache) + its runtime venv (per extra)
        return super().disk_size(cache) + dir_size(self.model.extra)

    def is_ready(self, cache: dict[str, int] | None = None) -> bool:
        # ready once the backend venv exists; weights fetch lazily on first load
        return self.available()

    def delete(self) -> int:
        self.unload()
        freed = hf_repo_delete(self.hf_repos())  # this model's weights
        if not self._extra_in_use():  # keep the shared venv while a sibling needs it
            freed += remove(self.model.extra)
        return freed

    def _extra_in_use(self) -> bool:
        """True if another model sharing this venv still has weights on disk."""
        from macaw.stt.registry import list_models

        sizes = hf_cache_sizes()
        mine = set(self.hf_repos())
        return any(
            m.extra == self.model.extra
            and m.repo
            and m.repo not in mine
            and sizes.get(m.repo, 0) > 0
            for m in list_models()
        )

    # -- worker lifecycle ----------------------------------------------

    def load(self, model_path: str | None = None) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        py = venv_python(self.model.extra)
        if not py.exists():
            raise MissingDependency(f"{self.model.extra} backend is not installed")
        logger.info("Starting %s worker (%s)...", self.key, self.model.id)
        # Worker stderr goes to a temp file: a pipe would fill up (NeMo logs a
        # lot) and DEVNULL made startup crashes undiagnosable.
        self._stderr_path = tempfile.mkstemp(prefix="macaw-worker-", suffix=".log")[1]
        stderr_f = open(self._stderr_path, "w")  # noqa: SIM115 — handed to Popen
        try:
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
                stderr=stderr_f,
                text=True,
                bufsize=1,
                env=_worker_env(),
                # Windows: never pop a console window for the background worker.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            stderr_f.close()  # child holds its own copy of the fd
        status = self._read_message()
        if status.get("status") != "ready":
            err = status.get("error", "unknown")
            if err in ("unknown", "worker exited"):
                err = f"{err}\n{self._stderr_tail()}"
            self.unload()
            raise RuntimeError(f"{self.key} worker failed to start: {err}")
        self._incremental = bool(status.get("incremental"))
        logger.info("%s worker ready.", self.key)

    def _stderr_tail(self, lines: int = 12) -> str:
        try:
            with open(self._stderr_path, errors="replace") as f:
                return "".join(f.readlines()[-lines:]).strip() or "(no stderr)"
        except OSError:
            return "(stderr unavailable)"

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> str:
        return self._request(audio, prefix="")

    def transcribe_partial(
        self, audio: np.ndarray, sample_rate: int = 16_000
    ) -> str | None:
        if not self._incremental:
            return None
        return self._request(audio, prefix="S ")

    def _request(self, audio: np.ndarray, prefix: str) -> str:
        self.load()
        assert self._proc is not None and self._proc.stdin is not None
        fd, path = tempfile.mkstemp(prefix="macaw-audio-", suffix=".npy")
        os.close(fd)
        try:
            np.save(path, audio)
            with self._lock:
                self._proc.stdin.write(prefix + path + "\n")
                self._proc.stdin.flush()
                reply = self._read_message()
            if "error" in reply:
                raise RuntimeError(reply["error"])
            return reply.get("text", "").strip()
        finally:
            os.unlink(path)

    def unload(self) -> None:
        """Terminate the worker AND reap it (wait), so switching/cancelling a
        model never leaves a zombie subprocess or leaked pipes behind."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        for pipe in (proc.stdin, proc.stdout):
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:  # noqa: BLE001
                pass

    # -- protocol ------------------------------------------------------

    def _read_message(self) -> dict:
        """Read lines until a JSON object appears (tolerates stray output)."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return {"error": "worker exited"}
        while True:
            line = proc.stdout.readline()
            if not line:
                return {"error": "worker exited"}
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
