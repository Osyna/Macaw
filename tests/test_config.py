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
        "llm_enabled": True,
        "llm_model": "provider:openai",
        "llm_prompt": "Format as an email.\nKeep it concise.",
        "providers": {"openai": {"enabled": True, "model": "gpt-4o-mini"}},
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


def test_streaming_flag_migrates_to_live_output(tmp_path: Path):
    # Pre-0.8 configs had a separate streaming toggle; enabled + type = the
    # old live-typing combo, which must land on the merged "live" mode.
    p = tmp_path / "config.yaml"
    p.write_text("output_mode: type\nstreaming: true\n")
    assert Config.load(p).output_mode == "live"


def test_streaming_flag_without_type_stays_clipboard(tmp_path: Path):
    # streaming only ever worked with output_mode: type — don't invent "live"
    # for clipboard users.
    p = tmp_path / "config.yaml"
    p.write_text("output_mode: clipboard\nstreaming: true\n")
    assert Config.load(p).output_mode == "clipboard"


def test_onboarded_gates_only_fresh_installs(tmp_path: Path):
    # No config file -> wizard. Existing config without the key -> a working
    # setup, never re-onboard. Explicit false -> wizard again (manual reset).
    assert Config.load(tmp_path / "missing.yaml").onboarded is False
    existing = tmp_path / "old.yaml"
    existing.write_text("language: fr\n")
    assert Config.load(existing).onboarded is True
    reset = tmp_path / "reset.yaml"
    reset.write_text("onboarded: false\n")
    assert Config.load(reset).onboarded is False


def test_live_switch_nudges_silence_timeout_to_5s():
    # Switching to live typing off the stock 3 s bumps to 5 s — speakers need
    # breathing room; the auto-stop would cut them off mid-thought.
    cfg = Config()
    cfg.output_mode = "live"
    cfg.nudge_live_defaults(old_mode="type", patch={"output_mode": "live"})
    assert cfg.silence_timeout == 5.0


def test_live_switch_respects_deliberate_timeouts():
    # A custom value survives the switch…
    cfg = Config(silence_timeout=7.0)
    cfg.output_mode = "live"
    cfg.nudge_live_defaults(old_mode="type", patch={"output_mode": "live"})
    assert cfg.silence_timeout == 7.0
    # …and so does an explicit value in the very same patch.
    cfg2 = Config()
    cfg2.output_mode = "live"
    cfg2.silence_timeout = 3.0
    cfg2.nudge_live_defaults(
        old_mode="type", patch={"output_mode": "live", "silence_timeout": 3.0}
    )
    assert cfg2.silence_timeout == 3.0


def test_live_nudge_only_fires_on_the_switch():
    # Already live -> unrelated patches never touch the timeout.
    cfg = Config()
    cfg.output_mode = "live"
    cfg.nudge_live_defaults(old_mode="live", patch={"sound_enabled": False})
    assert cfg.silence_timeout == 3.0


def test_auto_stop_round_trips(tmp_path: Path):
    p = tmp_path / "config.yaml"
    cfg = Config(auto_stop=False)
    cfg.save(p)
    assert Config.load(p).auto_stop is False
    assert Config.load(tmp_path / "missing.yaml").auto_stop is True
