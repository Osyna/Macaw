"""Config YAML + network wiring contracts (engine-level, no UI).

- The hand-rolled YAML render/parse must round-trip every field the frontend
  edits — a dropped key in `_render` or a mis-parse in `load` silently loses
  user settings (past regressions: corners, model_languages).
- `net.apply` is what makes the proxy / ssl_verify config fields real: it
  wires them into the process environment for downloads + cloud calls.
- The CLI `--proxy` / `--no-ssl-verify` flags persist into the config file.
"""

from __future__ import annotations

import os
from pathlib import Path

from macaw import net
from macaw.config import Config


def test_config_round_trips_every_ui_edited_field(tmp_path: Path):
    # Explicit non-default value for each regression-prone field; save → load
    # must reproduce it exactly.
    values = {
        "device_index": 3,
        "language": "de",
        "output_mode": "type",
        "silence_timeout": 7.5,
        "window_position": "custom",
        "overlay_x": 77,
        "overlay_y": 88,
        "app_theme": "light",
        "theme": "oled",
        "corners": [10, 12, 4, 8],
        "corner_link": False,
        "corner_radius": 9,
        "eq_colors": ["#101010", "#202020"],
        "accent_color": "#abcdef",
        "hotkey_enabled": True,
        "hotkey": "ctrl+alt+space",
        "model": "whisper-x",
        "model_params": {"whisper-x": {"beam_size": 3}},
        "model_languages": {"whisper-x": "fr"},
        "proxy": "http://x:8080",
        "ssl_verify": False,
        "star_prompted": True,
    }
    p = tmp_path / "config.yaml"
    Config(**values).save(p)
    loaded = Config.load(p)
    for field_name, expected in values.items():
        assert getattr(loaded, field_name) == expected, field_name


def test_net_apply_sets_and_clears_proxy_and_ssl():
    keys = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "MACAW_SSL_VERIFY",
    )
    proxy_keys = keys[:4]
    saved = {k: os.environ.get(k) for k in keys}
    try:
        net.apply("http://p:3128", False)
        for k in proxy_keys:
            assert os.environ[k] == "http://p:3128"
        assert os.environ["MACAW_SSL_VERIFY"] == "0"

        net.apply("", True)
        for k in proxy_keys:
            assert k not in os.environ
        assert os.environ["MACAW_SSL_VERIFY"] == "1"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_cli_proxy_and_no_ssl_verify_persist(tmp_path: Path, monkeypatch):
    import macaw.config as config
    from macaw.cli import _apply_net_args, _parser

    args = _parser().parse_args(["--proxy", "http://cli:3128", "--no-ssl-verify"])
    assert args.proxy == "http://cli:3128"
    assert args.no_ssl_verify is True

    monkeypatch.setattr(config, "_DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    _apply_net_args(args)
    loaded = Config.load(tmp_path / "config.yaml")
    assert loaded.proxy == "http://cli:3128"
    assert loaded.ssl_verify is False
