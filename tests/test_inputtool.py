"""Pure-logic checks for the auto-type installer (macaw.gui.inputtool)."""

from __future__ import annotations

import subprocess

import pytest

import macaw.gui.inputtool as inputtool
from macaw.desktop import auto_type_package


@pytest.mark.parametrize(
    "release, family",
    [
        ("ID=ubuntu\nID_LIKE=debian", "debian"),
        ("ID=arch", "arch"),
        ("ID=fedora", "fedora"),
        ("ID=alpine", "alpine"),
        ("ID=void", "void"),
        ('ID=opensuse-tumbleweed\nID_LIKE="suse opensuse"', "suse"),
        ("ID=nixos", ""),
        ("", ""),
    ],
)
def test_distro_family_mapping(release, family):
    assert inputtool._distro(release) == family


def test_install_command_shapes(monkeypatch):
    monkeypatch.setattr(inputtool, "_distro", lambda *a, **k: "debian")
    monkeypatch.setattr(inputtool.shutil, "which", lambda _: "/usr/bin/pkexec")

    assert inputtool.install_command("xdotool") == [
        "pkexec",
        "apt-get",
        "install",
        "-y",
        "xdotool",
    ]

    ydo = inputtool.install_command("ydotool")
    assert ydo[:3] == ["pkexec", "sh", "-c"]
    assert ydo[-2] == "macaw"
    assert "apt-get install -y ydotool" in ydo[3]


def test_install_command_none_without_pkexec(monkeypatch):
    monkeypatch.setattr(inputtool, "_distro", lambda *a, **k: "debian")
    monkeypatch.setattr(inputtool.shutil, "which", lambda _: None)
    assert inputtool.install_command("ydotool") is None


def test_install_command_none_for_unknown_distro(monkeypatch):
    monkeypatch.setattr(inputtool, "_distro", lambda *a, **k: "")
    monkeypatch.setattr(inputtool.shutil, "which", lambda _: "/usr/bin/pkexec")
    assert inputtool.install_command("xdotool") is None


def test_ydotool_setup_content_and_syntax(tmp_path):
    script = inputtool._ydotool_setup("apt-get install -y ydotool")
    for needle in (
        "apt-get install -y ydotool",
        'usermod -aG input "$1"',
        "99-uinput-input-group.rules",
        "setfacl",
        "udevadm control",
    ):
        assert needle in script, f"missing: {needle}"

    path = tmp_path / "setup.sh"
    path.write_text(script)
    assert subprocess.run(["sh", "-n", str(path)]).returncode == 0


def test_manual_command(monkeypatch):
    monkeypatch.setattr(inputtool, "_distro", lambda *a, **k: "debian")
    assert inputtool.manual_command("ydotool") == "sudo apt-get install -y ydotool"

    monkeypatch.setattr(inputtool, "_distro", lambda *a, **k: "")
    hint = inputtool.manual_command("ydotool")
    assert "ydotool" in hint
    assert not hint.startswith("sudo")


def test_auto_type_package():
    assert auto_type_package("wayland") == "ydotool"
    assert auto_type_package("x11") == "xdotool"
