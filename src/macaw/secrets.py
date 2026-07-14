"""Encrypted local secret store for API keys.

API keys never sit in ``config.yaml`` (which users share, screen-record and sync
to dotfile repos). They live encrypted in ``secrets.enc`` next to the config,
sealed with a Fernet (AES-128-CBC + HMAC) key held in a separate 0600 file under
the data dir. Copying the config alone therefore leaks nothing.

This is local at-rest encryption: a determined attacker with full read access to
the user's home can still reach the key file — that's inherent to any keyless
local store — but it defeats casual extraction and accidental sharing.

Keys are addressed by name, e.g. ``provider.openai``. Env vars are NOT handled
here; the provider layer falls back to them.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("macaw")


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "macaw"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "macaw"


def _store_path() -> Path:
    return _config_home() / "secrets.enc"


def _key_path() -> Path:
    return _data_home() / "secret.key"


def _fernet():
    """The Fernet cipher, creating the master key file (0600) on first use."""
    from cryptography.fernet import Fernet

    kp = _key_path()
    if kp.exists():
        key = kp.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        kp.parent.mkdir(parents=True, exist_ok=True)
        # write then tighten perms before anything else can read it
        kp.write_bytes(key)
        try:
            os.chmod(kp, 0o600)
        except OSError:
            pass
    return Fernet(key)


def _load_raw() -> dict[str, str]:
    p = _store_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _save_raw(data: dict[str, str]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def get(name: str) -> str:
    """Decrypt and return the secret, or '' if missing / undecryptable."""
    token = _load_raw().get(name)
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:  # noqa: BLE001 — corrupt token / rotated key
        return ""


def set(name: str, value: str) -> None:  # noqa: A001 — matches dict-store vocabulary
    """Store (encrypt) a secret. An empty value deletes it."""
    data = _load_raw()
    if not value:
        data.pop(name, None)
    else:
        data[name] = _fernet().encrypt(value.encode()).decode()
    _save_raw(data)


def delete(name: str) -> None:
    set(name, "")


def has(name: str) -> bool:
    return bool(_load_raw().get(name))


def names() -> list[str]:
    return sorted(_load_raw().keys())
