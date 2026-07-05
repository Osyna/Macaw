from __future__ import annotations

import os

import zmq


def _ipc_address() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return f"ipc://{runtime}/macaw.ipc"
    return "ipc:///tmp/macaw_service.ipc"


IPC_ADDRESS = _ipc_address()


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


def main() -> None:
    reply = send_command("TOGGLE")
    if reply is None:
        print("Service timed out. Is macaw running?")
    else:
        print(f"Service replied: {reply}")


if __name__ == "__main__":
    main()
