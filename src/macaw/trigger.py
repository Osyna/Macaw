from __future__ import annotations

import os
import sys

import zmq


def _ipc_address() -> str:
    if sys.platform == "win32":
        # zmq's ipc:// transport needs AF_UNIX; use a loopback TCP port instead.
        # ponytail: fixed port — make it configurable only if a collision ever shows up.
        return "tcp://127.0.0.1:47539"
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return f"ipc://{runtime}/macaw.ipc"
    return "ipc:///tmp/macaw_service.ipc"


def send_command(msg: str, timeout_ms: int = 2000) -> str | None:
    """Send a command to the running service. Returns its reply, or None on timeout."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.REQ)
    # LINGER=0: if the service isn't running, drop the undelivered message on
    # close instead of blocking ctx.term() forever waiting for a peer.
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(_ipc_address())
    sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
    sock.send_string(msg)
    try:
        return sock.recv_string()
    except zmq.error.Again:
        return None
    finally:
        sock.close()
        ctx.term()
