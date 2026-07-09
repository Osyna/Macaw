from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
_DEFAULT_CONFIG_PATH = _CONFIG_HOME / "macaw" / "config.yaml"
# oscribe was this app's previous name; carry old settings over on first run.
_LEGACY_DIR = _CONFIG_HOME / "oscribe"


def _migrate_legacy() -> None:
    """One-shot: adopt ~/.config/oscribe/ if the macaw dir doesn't exist yet."""
    new_dir = _DEFAULT_CONFIG_PATH.parent
    if new_dir.exists() or not _LEGACY_DIR.is_dir():
        return
    try:
        shutil.copytree(_LEGACY_DIR, new_dir)
    except OSError:
        pass  # ponytail: best-effort; a fresh config is written if this fails


def config_path() -> Path:
    """The config file location (respects $XDG_CONFIG_HOME)."""
    return _DEFAULT_CONFIG_PATH


def _yv(v: object) -> str:
    """Render a scalar as a YAML token for the commented template."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v if v and all(c.isalnum() or c in "-_./" for c in v) else repr(v)
    return str(v)


@dataclass
class Config:
    device_index: int | None = None
    language: str = "en"
    output_mode: str = "clipboard"
    silence_timeout: float = 3.0
    window_position: str = "bottom_center"
    sound_enabled: bool = True
    streaming: bool = False
    punctuation_hints: bool = True
    hotkey_enabled: bool = False  # listen for a global shortcut to toggle recording
    hotkey: str = ""  # combo spec, e.g. "ctrl+alt+space" (empty = unset)
    theme: str = "macaw"
    app_theme: str = "dark"  # settings chrome: dark | light (indicator uses `theme`)
    # look overrides layered on top of the theme (blank/empty = use the theme)
    overlay_opacity: float = 0.94  # record indicator opacity, 0.5–1.0
    overlay_width: int = 210  # record overlay width, px
    overlay_height: int = 52  # record overlay height, px
    overlay_x: int = 0  # custom overlay X (used when window_position == "custom")
    overlay_y: int = 0  # custom overlay Y (used when window_position == "custom")
    eq_colors: list = field(default_factory=list)  # bar gradient stops, hex
    accent_color: str = ""  # icon / accent colour, hex
    border_width: int = 0  # record bar border thickness, px
    border_color: str = ""  # record bar border colour, hex
    corner_radius: int = -1  # uniform corner radius (-1 = theme shape)
    corners: list = field(default_factory=list)  # per-corner radii, empty = unused
    corner_link: bool = True  # UI: edit all four corners together (False = independent)
    bar_spacing: int = -1  # gap between eq bars, px (-1 = auto)
    bar_width: int = -1  # eq bar thickness, px (-1 = auto)
    bar_radius: int = 0  # eq bar corner radius, px (0 = sharp)
    bar_fade: bool = True  # quiet bars fade out (False = solid)
    model: str = ""  # empty = nothing selected yet (pick one in the Model Manager)
    # Per-model tunables: {model_id: {param_key: value}}
    model_params: dict = field(default_factory=dict)
    model_languages: dict = field(default_factory=dict)  # {model_id: lang code}
    openai_api_key: str = ""  # for cloud models; falls back to $OPENAI_API_KEY
    # Network (advanced): route downloads + cloud calls through a proxy, and
    # optionally skip SSL verification (e.g. behind a corporate MITM proxy).
    proxy: str = ""  # HTTP(S) proxy URL, e.g. http://host:port (blank = none)
    ssl_verify: bool = True  # False disables SSL certificate verification
    star_prompted: bool = False  # shown the GitHub-star nudge once

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or _DEFAULT_CONFIG_PATH
        if path is _DEFAULT_CONFIG_PATH:
            _migrate_legacy()
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return cls(
                device_index=data.get("device_index"),
                language=data.get("language", "en"),
                output_mode=data.get("output_mode", "clipboard"),
                silence_timeout=float(data.get("silence_timeout", 3.0)),
                window_position=data.get("window_position", "bottom_center"),
                sound_enabled=bool(data.get("sound_enabled", True)),
                streaming=bool(data.get("streaming", False)),
                punctuation_hints=bool(data.get("punctuation_hints", True)),
                hotkey_enabled=bool(data.get("hotkey_enabled", False)),
                hotkey=data.get("hotkey") or "",
                theme=data.get("theme", "macaw"),
                app_theme=data.get("app_theme", "dark"),
                overlay_opacity=float(data.get("overlay_opacity", 0.94)),
                overlay_width=int(data.get("overlay_width", 210)),
                overlay_height=int(data.get("overlay_height", 52)),
                overlay_x=int(data.get("overlay_x", 0)),
                overlay_y=int(data.get("overlay_y", 0)),
                eq_colors=list(data.get("eq_colors") or []),
                accent_color=data.get("accent_color") or "",
                border_width=int(data.get("border_width", 0)),
                border_color=data.get("border_color") or "",
                corner_radius=int(data.get("corner_radius", -1)),
                corners=[int(c) for c in (data.get("corners") or [])],
                corner_link=bool(data.get("corner_link", True)),
                bar_spacing=int(data.get("bar_spacing", -1)),
                bar_width=int(data.get("bar_width", -1)),
                bar_radius=int(data.get("bar_radius", 0)),
                bar_fade=bool(data.get("bar_fade", True)),
                model=data.get("model") or "",
                model_params=data.get("model_params") or {},
                model_languages=data.get("model_languages") or {},
                openai_api_key=data.get("openai_api_key") or "",
                proxy=data.get("proxy") or "",
                ssl_verify=bool(data.get("ssl_verify", True)),
                star_prompted=bool(data.get("star_prompted", False)),
            )
        return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or _DEFAULT_CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._render(), encoding="utf-8")

    def _render(self) -> str:
        """A grouped, commented YAML file — readable and hand-editable."""
        if self.model_params:
            params = yaml.safe_dump(
                {"model_params": self.model_params},
                default_flow_style=False,
                sort_keys=True,
            ).rstrip()
        else:
            params = "model_params: {}"
        if self.model_languages:
            langs = yaml.safe_dump(
                {"model_languages": self.model_languages},
                default_flow_style=False,
                sort_keys=True,
            ).rstrip()
        else:
            langs = "model_languages: {}"
        return (
            "# macaw configuration\n"
            "# Edit by hand, via the Settings window, or `macaw --config edit`.\n"
            "# See `macaw --list` (model ids) and `macaw --devices` (mic indices).\n"
            "\n"
            "# ── Model ────────────────────────────────────────────────\n"
            f"model: {_yv(self.model)}\n"
            f"openai_api_key: {_yv(self.openai_api_key)}"
            "  # for GPT-4o cloud models (or set $OPENAI_API_KEY)\n"
            "\n"
            "# ── Audio ────────────────────────────────────────────────\n"
            f"device_index: {_yv(self.device_index)}"
            "  # microphone index; null = system default\n"
            f"language: {_yv(self.language)}"
            "  # transcription language code (en, fr, de, …)\n"
            "\n"
            "# ── Output ───────────────────────────────────────────────\n"
            f"output_mode: {_yv(self.output_mode)}  # 'clipboard' or 'type'\n"
            f"streaming: {_yv(self.streaming)}"
            "  # live typing as you speak (needs output_mode: type)\n"
            f"window_position: {_yv(self.window_position)}  # overlay position\n"
            "\n"
            "# ── Behaviour ────────────────────────────────────────────\n"
            f"silence_timeout: {_yv(self.silence_timeout)}"
            "  # seconds of silence before auto-stop\n"
            f"sound_enabled: {_yv(self.sound_enabled)}  # play record / done tones\n"
            f"punctuation_hints: {_yv(self.punctuation_hints)}"
            "  # nudge the model toward punctuation\n"
            f"hotkey_enabled: {_yv(self.hotkey_enabled)}"
            "  # global shortcut to start/stop recording (needs the 'input' group)\n"
            f"hotkey: {_yv(self.hotkey)}"
            "  # e.g. 'ctrl+alt+space' — set it in the Settings window\n"
            "\n"
            "# ── Appearance ───────────────────────────────────────────\n"
            f"theme: {_yv(self.theme)}"
            "  # oled | macaw | light | catppuccin\n"
            f"app_theme: {_yv(self.app_theme)}"
            "  # settings window chrome: dark | light\n"
            f"overlay_opacity: {_yv(self.overlay_opacity)}"
            "  # record indicator opacity, 0.5–1.0\n"
            f"overlay_width: {_yv(self.overlay_width)}"
            "  # record overlay width in px\n"
            f"overlay_height: {_yv(self.overlay_height)}"
            "  # record overlay height in px\n"
            f"overlay_x: {_yv(self.overlay_x)}"
            "  # custom overlay X, px (window_position: custom)\n"
            f"overlay_y: {_yv(self.overlay_y)}"
            "  # custom overlay Y, px (window_position: custom)\n"
            "eq_colors: "
            + ("[" + ", ".join(f'"{c}"' for c in self.eq_colors) + "]")
            + "  # bar gradient stops (empty = theme)\n"
            f"accent_color: {_yv(self.accent_color)}"
            "  # icon / accent colour (blank = theme)\n"
            f"border_width: {_yv(self.border_width)}"
            "  # record bar border thickness in px (0 = none)\n"
            f"border_color: {_yv(self.border_color)}"
            "  # record bar border colour (blank = theme)\n"
            f"corner_radius: {_yv(self.corner_radius)}"
            "  # uniform corner radius in px (-1 = keep the theme's shape)\n"
            f"corners: {'[' + ', '.join(str(int(c)) for c in self.corners) + ']'}"
            "  # per-corner radii tl,tr,br,bl (empty = use corner_radius)\n"
            f"corner_link: {_yv(self.corner_link)}"
            "  # edit all four corners together in Settings (false = independent)\n"
            f"bar_spacing: {_yv(self.bar_spacing)}"
            "  # gap between equaliser bars in px (-1 = auto)\n"
            f"bar_width: {_yv(self.bar_width)}"
            "  # equaliser bar thickness in px (-1 = auto)\n"
            f"bar_radius: {_yv(self.bar_radius)}"
            "  # equaliser bar corner radius in px (0 = sharp)\n"
            f"bar_fade: {_yv(self.bar_fade)}"
            "  # quiet bars fade to transparent (false = solid bars)\n"
            "\n"
            "# ── Network (advanced) ───────────────────────────────────\n"
            f"proxy: {_yv(self.proxy)}"
            "  # HTTP(S) proxy for model downloads + cloud calls (blank = none)\n"
            f"ssl_verify: {_yv(self.ssl_verify)}"
            "  # verify SSL certs (false = disable, e.g. corporate MITM proxy)\n"
            "\n"
            "# ── State ───────────────────────────────────────────────\n"
            f"star_prompted: {_yv(self.star_prompted)}"
            "  # whether the GitHub-star nudge has been shown\n"
            "\n"
            "# ── Per-model tunables (set from the Model Manager) ──────\n"
            f"{params}\n{langs}\n"
        )
