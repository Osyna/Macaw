"""WS + IPC contract tests against a real headless engine subprocess.

The engine is spawned hermetically: XDG_* point into a per-fixture tmp dir
(fresh config, isolated zmq IPC socket, empty HF cache), a random free WS
port, no model configured — so no audio device is ever opened and the real
user config is never touched. One module-scoped engine serves the RPC tests;
lifecycle tests (quit / stdin-EOF / duplicate instance) spawn their own.

Contract under test: local://tauri-contract.md (WS protocol v1).
"""

from __future__ import annotations

import json
import os
import select
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as ws_connect

TOKEN = "test-token-123"
READY_TIMEOUT = 60.0


# ── unit: language resolution (pure helper in engine.py) ─────────────


def test_lang_for_prefers_per_model_then_global_then_en():
    from macaw.config import Config
    from macaw.engine import _lang_for

    # per-model choice wins over the global default
    assert _lang_for(Config(model="m", model_languages={"m": "fr"})) == "fr"
    # no per-model entry -> fall back to the global `language`
    assert _lang_for(Config(model="m", language="de")) == "de"
    # nothing set anywhere -> "en"
    assert _lang_for(Config(model="", language="")) == "en"


# ── engine subprocess plumbing ────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _xdg_env(tmp: Path) -> dict:
    """Hermetic env: config/data/runtime/caches all under tmp."""
    env = os.environ.copy()
    for var, sub in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_RUNTIME_DIR", "run"),  # → zmq IPC socket lives here
    ):
        d = tmp / sub
        d.mkdir(exist_ok=True)
        env[var] = str(d)
    env["HF_HOME"] = str(tmp / "hf")  # empty model cache, no scan of the real one
    env.pop("OPENAI_API_KEY", None)  # deterministic api_key_set
    env["PYTHONUNBUFFERED"] = "1"
    return env


class EngineProc:
    """A `macaw-engine` child process bound to a hermetic tmp XDG tree."""

    def __init__(self, tmp: Path, port: int | None = None, token: str = TOKEN):
        self.tmp = tmp
        self.port = port or _free_port()
        self.token = token
        self.env = _xdg_env(tmp)
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "macaw.engine",
                "--token",
                token,
                "--ws-port",
                str(self.port),
            ],
            stdin=subprocess.PIPE,  # engine exits on stdin EOF — keep it open
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=self.env,
        )

    def wait_ready(self, timeout: float = READY_TIMEOUT) -> None:
        """Block until `READY ws=<port>` appears on stdout (logs are merged)."""
        buf = b""
        fd = self.proc.stdout.fileno()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r, _, _ = select.select([fd], [], [], 0.2)
            if r:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                buf += chunk
                if f"READY ws={self.port}".encode() in buf:
                    # Drain further output so the pipe can never block the engine.
                    threading.Thread(target=self.proc.stdout.read, daemon=True).start()
                    return
            if self.proc.poll() is not None:
                break
        out = buf.decode(errors="replace")
        raise AssertionError(f"engine not READY (exit={self.proc.poll()}):\n{out}")

    def shutdown(self) -> None:
        if self.proc.poll() is None:
            self.proc.stdin.close()  # parent-death watchdog: EOF → exit
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)


class Client:
    """Authed WS client speaking the id/method/params RPC framing."""

    def __init__(self, port: int, token: str):
        self.ws = ws_connect(f"ws://127.0.0.1:{port}", open_timeout=10)
        self.ws.send(json.dumps({"auth": token}))
        assert json.loads(self.ws.recv(timeout=10)) == {"ok": True}
        self._id = 0
        self._events: list[dict] = []

    def call(self, method: str, params: dict | None = None, timeout: float = 30):
        self._id += 1
        self.ws.send(
            json.dumps({"id": self._id, "method": method, "params": params or {}})
        )
        deadline = time.monotonic() + timeout
        while True:
            msg = json.loads(
                self.ws.recv(timeout=max(0.01, deadline - time.monotonic()))
            )
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]
            if "event" in msg:
                self._events.append(msg)

    def wait_event(self, name: str, where=None, timeout: float = 10) -> dict:
        """Next `name` event's data (predicate-filtered), buffered or fresh."""
        deadline = time.monotonic() + timeout
        while True:
            for i, ev in enumerate(self._events):
                if ev["event"] == name and (where is None or where(ev["data"])):
                    return self._events.pop(i)["data"]
            msg = json.loads(
                self.ws.recv(timeout=max(0.01, deadline - time.monotonic()))
            )
            if "event" in msg:
                self._events.append(msg)

    def close(self) -> None:
        self.ws.close()


@pytest.fixture(scope="module")
def engine(tmp_path_factory) -> EngineProc:
    eng = EngineProc(tmp_path_factory.mktemp("engine"))
    try:
        eng.wait_ready()
    except Exception:
        eng.kill()
        raise
    yield eng
    eng.shutdown()


@pytest.fixture
def client(engine) -> Client:
    c = Client(engine.port, engine.token)
    yield c
    c.close()


# ── auth ──────────────────────────────────────────────────────────────


def test_auth_wrong_token_closes_connection(engine):
    ws = ws_connect(f"ws://127.0.0.1:{engine.port}", open_timeout=10)
    ws.send(json.dumps({"auth": "wrong-token"}))
    with pytest.raises(ConnectionClosed):
        ws.recv(timeout=10)  # engine must close without ever acking


def test_auth_garbage_first_message_closes_connection(engine):
    ws = ws_connect(f"ws://127.0.0.1:{engine.port}", open_timeout=10)
    ws.send("not json at all")
    with pytest.raises(ConnectionClosed):
        ws.recv(timeout=10)


def test_auth_right_token_acks(engine):
    ws = ws_connect(f"ws://127.0.0.1:{engine.port}", open_timeout=10)
    try:
        ws.send(json.dumps({"auth": engine.token}))
        assert json.loads(ws.recv(timeout=10)) == {"ok": True}
    finally:
        ws.close()


# ── basic RPC ─────────────────────────────────────────────────────────


def test_ping(client):
    assert client.call("ping") == "pong"


def test_status_shape(client):
    st = client.call("status")
    assert {"state", "model", "model_label", "version", "hotkey_ok"} <= st.keys()
    assert st["state"] in {"idle", "loading", "recording", "transcribing", "error"}
    assert isinstance(st["model"], str)
    assert isinstance(st["model_label"], str)
    assert isinstance(st["hotkey_ok"], bool)
    assert isinstance(st["version"], str) and st["version"]


def test_unknown_method_is_an_rpc_error_not_a_disconnect(client):
    with pytest.raises(RuntimeError, match="unknown method"):
        client.call("no.such.method")
    assert client.call("ping") == "pong"  # connection survived


# ── config ────────────────────────────────────────────────────────────


def test_config_get_returns_every_field_and_hermetic_path(engine, client):
    from dataclasses import fields

    from macaw.config import Config

    got = client.call("config.get")
    assert set(got["config"]) == {f.name for f in fields(Config)}
    assert engine.tmp in Path(got["path"]).parents  # tmp XDG, not the real one


def test_config_set_round_trip_broadcast_and_yaml(engine, client):
    watcher = Client(engine.port, engine.token)  # a second authed client
    try:
        res = client.call(
            "config.set", {"patch": {"silence_timeout": 5.5, "language": "de"}}
        )
        assert res["config"]["silence_timeout"] == 5.5
        assert res["config"]["language"] == "de"

        # `config` event broadcast to every authed client, caller included
        for c in (watcher, client):
            data = c.wait_event(
                "config", where=lambda d: d["config"].get("silence_timeout") == 5.5
            )
            assert data["config"]["language"] == "de"

        # the YAML actually landed under the tmp XDG dir
        p = Path(res["path"])
        assert engine.tmp in p.parents
        on_disk = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert on_disk["silence_timeout"] == 5.5
        assert on_disk["language"] == "de"

        # and a fresh config.get agrees
        assert client.call("config.get")["config"]["silence_timeout"] == 5.5
    finally:
        watcher.close()


def test_config_set_rejects_unknown_field(client):
    with pytest.raises(RuntimeError, match="unknown config field"):
        client.call("config.set", {"patch": {"not_a_real_field": 1}})
    # nothing half-applied: the engine still answers
    assert client.call("ping") == "pong"


# ── models.list ───────────────────────────────────────────────────────

_MODEL_KEYS = {
    "id", "backend", "label", "size", "speed", "languages", "streaming",
    "extra", "hardware", "vram", "notes", "rating", "pros", "cons",
    "rec_specs", "min_specs", "source_url", "repo", "params", "cloud",
    "recommended", "available", "installed", "ready", "active", "disk_size",
    "api_key_set", "lang_select", "cur_lang", "cur_params",
}  # fmt: skip
_PARAM_KEYS = {"key", "label", "kind", "default", "min", "max", "step", "hint"}


def test_models_list_shape_and_ordering(client):
    models = client.call("models.list", timeout=60)
    assert len(models) >= 20
    ids = [m["id"] for m in models]
    assert len(ids) == len(set(ids)), "duplicate model ids"
    for m in models:
        missing = _MODEL_KEYS - m.keys()
        assert not missing, f"{m['id']}: missing keys {missing}"
        assert isinstance(m["rating"], int) and 1 <= m["rating"] <= 5, m["id"]
        assert isinstance(m["cloud"], bool), m["id"]
        assert isinstance(m["params"], list), m["id"]
        for p in m["params"]:
            missing = _PARAM_KEYS - p.keys()
            assert _PARAM_KEYS <= p.keys(), f"{m['id']}: param missing {missing}"
        assert isinstance(m["pros"], list) and isinstance(m["cons"], list), m["id"]
        assert isinstance(m["api_key_set"], bool), m["id"]
        assert isinstance(m["cur_params"], dict), m["id"]
    # sorted (cloud, -rating): locals first, rating non-increasing per group
    clouds = [m["cloud"] for m in models]
    assert clouds == sorted(clouds), "cloud models must sink to the bottom"
    local = [m["rating"] for m in models if not m["cloud"]]
    cloud = [m["rating"] for m in models if m["cloud"]]
    assert local == sorted(local, reverse=True), "local group not rating-sorted"
    assert cloud == sorted(cloud, reverse=True), "cloud group not rating-sorted"


# ── recording without a model ─────────────────────────────────────────


def test_record_toggle_without_model_flags_error_and_engine_survives(engine, client):
    res = client.call("record.toggle")
    assert res == {"state": "error"}
    data = client.wait_event(
        "state", where=lambda d: d.get("detail") == "No model selected"
    )
    assert data["state"] == "error"
    # the engine must stay up and responsive — no crash, no disconnect
    assert client.call("ping") == "pong"
    assert engine.proc.poll() is None


# ── hotkey capture RPC (no real input devices needed) ─────────────────


def test_hotkey_capture_start_and_cancel_are_graceful(client):
    # With or without /dev/input access the RPCs must succeed; a permission
    # problem surfaces as a `toast` event, never as an RPC error or crash.
    assert client.call("hotkey.capture_start") == {"ok": True}
    assert client.call("hotkey.capture_cancel") == {"ok": True}
    assert client.call("ping") == "pong"


# ── zmq IPC (CLI compatibility) ───────────────────────────────────────


def test_zmq_cli_commands_reach_the_engine(engine, client, monkeypatch):
    from macaw.trigger import send_command

    # point the CLI-side resolver at the engine's hermetic IPC socket
    monkeypatch.setenv("XDG_RUNTIME_DIR", engine.env["XDG_RUNTIME_DIR"])

    assert send_command("PING", timeout_ms=5000) == "OK"  # `macaw --status` probe

    assert send_command("SETTINGS", timeout_ms=5000) == "OK"
    show = client.wait_event("show", where=lambda d: d.get("window") == "settings")
    assert show == {"window": "settings"}

    assert send_command("MODELS", timeout_ms=5000) == "OK"
    assert client.wait_event("show", where=lambda d: d.get("window") == "models")

    assert send_command("BOGUS", timeout_ms=5000) == "UNKNOWN"
    assert client.call("ping") == "pong"  # engine unfazed throughout


# ── process lifecycle (own spawns) ────────────────────────────────────


def test_quit_rpc_exits_zero(tmp_path):
    eng = EngineProc(tmp_path)
    try:
        eng.wait_ready()
        c = Client(eng.port, eng.token)
        assert c.call("quit") == {"ok": True}
        c.close()
        assert eng.proc.wait(timeout=15) == 0
    finally:
        eng.kill()


def test_stdin_eof_exits_zero(tmp_path):
    # The parent-death watchdog: Tauri dying closes the pipe → engine exits.
    eng = EngineProc(tmp_path)
    try:
        eng.wait_ready()
        eng.proc.stdin.close()
        assert eng.proc.wait(timeout=15) == 0
    finally:
        eng.kill()


def test_second_engine_on_same_port_fails_cleanly(engine):
    # Same XDG_RUNTIME_DIR + same WS port as the live module engine: the
    # duplicate must refuse to start (bind conflict) and exit non-zero —
    # never a hang, never a silent takeover.
    dup = EngineProc(engine.tmp, port=engine.port)
    try:
        rc = dup.proc.wait(timeout=30)
        out = dup.proc.stdout.read().decode(errors="replace")
        assert rc == 1, f"expected clean failure exit, got {rc}:\n{out}"
        assert "READY" not in out, "duplicate engine must not report READY"
    finally:
        dup.kill()
    # the original engine is unharmed
    c = Client(engine.port, engine.token)
    try:
        assert c.call("ping") == "pong"
    finally:
        c.close()
