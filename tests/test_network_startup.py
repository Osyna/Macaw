"""Regression guards for the network-settings + startup batch.

One test per externally observable contract touched by the change:
config network fields (defaults + save/load round-trip), ``net.apply``'s
proxy/SSL environment wiring, empty-model readiness, the Model Manager list
ordering by rating, the overlay's status-message state, and the CLI's
``--proxy`` / ``--no-ssl-verify`` persistence.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

import macaw.config as config
from macaw import net
from macaw.audio.transcriber import Transcriber
from macaw.config import Config
from macaw.gui.main_window import MainWindow
from macaw.gui.window import RecordingWindow
from macaw.stt import get_model_info

app = QApplication.instance() or QApplication([])


def _tmp_config() -> Path:
    return Path(tempfile.mkdtemp(prefix="macaw_net_test_")) / "config.yaml"


def test_config_defaults_and_network_roundtrip():
    c = Config()
    assert c.model == ""  # nothing selected until the user picks one
    assert c.ssl_verify is True
    assert c.proxy == ""

    tmp = _tmp_config()
    Config(proxy="http://x:8080", ssl_verify=False, model="whisper-x").save(tmp)
    loaded = Config.load(tmp)
    assert loaded.proxy == "http://x:8080"
    assert loaded.ssl_verify is False
    assert loaded.model == "whisper-x"


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


def test_empty_model_is_not_ready():
    assert Transcriber(model_size="").is_ready() is False


def test_model_list_sorted_by_rating():
    # The Manager sorts rows by (cloud, -rating): cloud models sink to the
    # bottom, and rating is non-increasing within the non-cloud group and
    # within the cloud group. Row 0 must be a 5-star local model.
    tmp = _tmp_config()
    Config(model="").save(tmp)

    win = MainWindow(tmp)
    try:
        lw = win.models.list
        ids = [lw.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lw.count())]
        assert ids, "model list is empty"
        rows = [(get_model_info(i).cloud, get_model_info(i).rating) for i in ids]

        # (a) all cloud models sit at the bottom: no non-cloud row after a cloud row.
        seen_cloud = False
        for cloud, _ in rows:
            if cloud:
                seen_cloud = True
            else:
                assert not seen_cloud, f"non-cloud row after a cloud row: {ids}"

        # (b) ratings non-increasing within the non-cloud prefix and cloud suffix.
        local = [r for c, r in rows if not c]
        cloud = [r for c, r in rows if c]
        assert local == sorted(local, reverse=True), f"local not rating-sorted: {local}"
        assert cloud == sorted(cloud, reverse=True), f"cloud not rating-sorted: {cloud}"

        # (c) top row is a 5-star local model.
        top_cloud, top_rating = rows[0]
        assert not top_cloud, f"top row is a cloud model: {ids[0]}"
        assert top_rating == 5, f"top row not 5-star: {ids[0]} = {top_rating}"
    finally:
        win.close()


def test_overlay_show_message_sets_state():
    w = RecordingWindow(210, 52)
    try:
        w.show_message("No Model Selected")
        assert w.state == "message"
        assert w.message == "No Model Selected"
    finally:
        w.close()


def test_cli_proxy_and_no_ssl_verify_persist():
    from macaw.cli import _apply_net_args, _parser

    args = _parser().parse_args(["--proxy", "http://cli:3128", "--no-ssl-verify"])
    assert args.proxy == "http://cli:3128"
    assert args.no_ssl_verify is True

    tmp = _tmp_config()
    orig = config._DEFAULT_CONFIG_PATH
    config._DEFAULT_CONFIG_PATH = tmp
    try:
        _apply_net_args(args)
        loaded = Config.load(tmp)
        assert loaded.proxy == "http://cli:3128"
        assert loaded.ssl_verify is False
    finally:
        config._DEFAULT_CONFIG_PATH = orig


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
