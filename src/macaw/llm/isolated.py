from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import threading
from pathlib import Path

from macaw.llm.base import LlmBackend, MissingDependency, hf_repo_delete
from macaw.llm.registry import list_models

# The per-extra isolated-venv machinery is backend-agnostic — reuse the STT
# stack's rather than growing a parallel copy. Only the worker protocol differs.
from macaw.stt.isolated import (
    _worker_env,
    dir_size,
    is_installed,
    remove,
    venv_python,
)

logger = logging.getLogger("macaw")

_WORKER = str(Path(__file__).parent / "worker.py")


class LlmSubprocessBackend(LlmBackend):
    """Runs a local text model in the shared 'llm' isolated venv via a
    persistent worker — the heavy runtime (llama.cpp) never touches the base
    environment, and the loaded model stays warm between requests.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._proc: subprocess.Popen | None = None
        self._lock = threading.RLock()

    # -- capability / weight management --------------------------------

    def available(self) -> bool:
        return is_installed(self.model.extra)

    def disk_size(self, cache=None) -> int:
        # GGUF weights (shared HF cache) + the runtime venv (per extra)
        return super().disk_size(cache) + dir_size(self.model.extra)

    def is_ready(self, cache=None) -> bool:
        # local formatting must never stall a transcription on a surprise
        # download: require both the runtime AND the weights on disk
        return self.available() and super().disk_size(cache) > 0

    def delete(self) -> int:
        self.unload()
        freed = hf_repo_delete(self.hf_repos())
        if not self._extra_in_use():
            freed += remove(self.model.extra)
        return freed

    def _extra_in_use(self) -> bool:
        """True if another model sharing this venv still has weights on disk."""
        from macaw.llm.base import hf_cache_sizes

        sizes = hf_cache_sizes()
        mine = set(self.hf_repos())
        return any(
            m.extra == self.model.extra
            and m.repo
            and m.repo not in mine
            and sizes.get(m.repo, 0) > 0
            for m in list_models()
        )

    def download(self, progress_callback=None) -> str:
        """Fetch the exact GGUF into the shared HF cache."""
        if not (self.model.repo and self.model.filename):
            return ""
        from huggingface_hub import hf_hub_download

        return hf_hub_download(self.model.repo, self.model.filename)

    # -- worker lifecycle ----------------------------------------------

    def load(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        py = venv_python(self.model.extra)
        if not py.exists():
            raise MissingDependency(f"{self.model.extra} backend is not installed")
        logger.info("Starting %s worker (%s)...", self.key, self.model.id)
        self._stderr_path = tempfile.mkstemp(prefix="macaw-llm-", suffix=".log")[1]
        stderr_f = open(self._stderr_path, "w")  # noqa: SIM115 — handed to Popen
        try:
            self._proc = subprocess.Popen(
                [
                    str(py),
                    _WORKER,
                    "--repo",
                    self.model.repo,
                    "--filename",
                    self.model.filename,
                    "--n-ctx",
                    str(self.model.n_ctx),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_f,
                text=True,
                bufsize=1,
                env=_worker_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            stderr_f.close()
        status = self._read_message()
        if status.get("status") != "ready":
            err = status.get("error", "unknown")
            if err in ("unknown", "worker exited"):
                err = f"{err}\n{self._stderr_tail()}"
            self.unload()
            raise RuntimeError(f"{self.key} worker failed to start: {err}")
        logger.info("%s worker ready.", self.key)

    def _stderr_tail(self, lines: int = 12) -> str:
        try:
            with open(self._stderr_path, errors="replace") as f:
                return "".join(f.readlines()[-lines:]).strip() or "(no stderr)"
        except OSError:
            return "(stderr unavailable)"

    def format(self, text: str, system: str) -> str:
        if not text.strip():
            return ""
        # bound generation to the input's shape: formatting rarely grows text,
        # so cap tokens near its length — fast, and stops runaway generation.
        max_tokens = min(2048, max(64, len(text) // 2 + 96))
        req = json.dumps({"system": system, "text": text, "max_tokens": max_tokens})
        # hold the lock across load→write→read so a cold idle-unload (which also
        # takes the lock) can never tear the worker down mid-format.
        with self._lock:
            self.load()
            assert self._proc is not None and self._proc.stdin is not None
            self._proc.stdin.write(req + "\n")
            self._proc.stdin.flush()
            reply = self._read_message()
        if "error" in reply:
            raise RuntimeError(reply["error"])
        self.last_tps = float(reply.get("tps", 0.0) or 0.0)
        self.last_secs = float(reply.get("secs", 0.0) or 0.0)
        return reply.get("text", "").strip()

    def unload(self) -> None:
        with self._lock:
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
