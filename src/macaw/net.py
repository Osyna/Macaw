"""Network settings for model downloads and cloud calls.

One place to route HTTP(S) through a proxy and (optionally) skip SSL
verification. Downloads happen both in-process (faster-whisper) and in isolated
worker subprocesses (nemo/voxtral/sherpa/moonshine), so we push proxy + an SSL
marker into the process environment — which the workers inherit — and configure
huggingface_hub's session here for the in-process side. Cloud (OpenAI) calls
read the same two settings and build their own httpx client.
"""

from __future__ import annotations

import os

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


def apply(proxy: str, ssl_verify: bool) -> None:
    """Route this process (and any worker it later spawns) through ``proxy`` and
    honour ``ssl_verify``. Idempotent — call again whenever the settings change."""
    for var in _PROXY_VARS:
        if proxy:
            os.environ[var] = proxy
        else:
            os.environ.pop(var, None)
    # Workers can't import macaw; they read this to configure their own session.
    os.environ["MACAW_SSL_VERIFY"] = "1" if ssl_verify else "0"
    configure_hf(ssl_verify)


def configure_hf(ssl_verify: bool) -> None:
    """Point huggingface_hub's session at our SSL choice (the proxy is read from
    the environment by requests' trust_env). Best-effort: no hub/requests just
    means the library defaults apply."""
    try:
        import requests
        from huggingface_hub import configure_http_backend
    except Exception:  # noqa: BLE001 — hub/requests optional in some envs
        return

    def _session() -> requests.Session:
        s = requests.Session()
        s.verify = ssl_verify
        return s

    configure_http_backend(_session)
    if not ssl_verify:
        try:
            import urllib3

            urllib3.disable_warnings()
        except Exception:  # noqa: BLE001
            pass
