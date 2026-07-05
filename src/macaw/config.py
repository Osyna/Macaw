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
    theme: str = "oled"
    # look overrides layered on top of the theme (blank/empty = use the theme)
    overlay_opacity: float = 0.94  # record indicator opacity, 0.5–1.0
    eq_colors: list = field(default_factory=list)  # bar gradient stops, hex
    accent_color: str = ""  # icon / accent colour, hex
    border_width: int = 0  # record bar border thickness, px
    border_color: str = ""  # record bar border colour, hex
    corner_radius: int = -1  # uniform corner radius (-1 = theme shape)
    bar_spacing: int = -1  # gap between eq bars, px (-1 = auto)
    bar_width: int = -1  # eq bar thickness, px (-1 = auto)
    bar_radius: int = 0  # eq bar corner radius, px (0 = sharp)
    bar_fade: bool = True  # quiet bars fade out (False = solid)
    model: str = "large-v3-turbo"
    # Per-model tunables: {model_id: {param_key: value}}
    model_params: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or _DEFAULT_CONFIG_PATH
        if path is _DEFAULT_CONFIG_PATH:
            _migrate_legacy()
        if path.exists():
            with open(path) as f:
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
                theme=data.get("theme", "oled"),
                overlay_opacity=float(data.get("overlay_opacity", 0.94)),
                eq_colors=list(data.get("eq_colors") or []),
                accent_color=data.get("accent_color") or "",
                border_width=int(data.get("border_width", 0)),
                border_color=data.get("border_color") or "",
                corner_radius=int(data.get("corner_radius", -1)),
                bar_spacing=int(data.get("bar_spacing", -1)),
                bar_width=int(data.get("bar_width", -1)),
                bar_radius=int(data.get("bar_radius", 0)),
                bar_fade=bool(data.get("bar_fade", True)),
                model=data.get("model", "large-v3-turbo"),
                model_params=data.get("model_params") or {},
            )
        return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or _DEFAULT_CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._render())

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
        return (
            "# macaw configuration\n"
            "# Edit by hand, via the Settings window, or `macaw config edit`.\n"
            "# See `macaw list` (model ids) and `macaw devices` (mic indices).\n"
            "\n"
            "# ── Model ────────────────────────────────────────────────\n"
            f"model: {_yv(self.model)}\n"
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
            "\n"
            "# ── Appearance ───────────────────────────────────────────\n"
            f"theme: {_yv(self.theme)}"
            "  # oled | macaw | light | catppuccin\n"
            f"overlay_opacity: {_yv(self.overlay_opacity)}"
            "  # record indicator opacity, 0.5–1.0\n"
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
            f"bar_spacing: {_yv(self.bar_spacing)}"
            "  # gap between equaliser bars in px (-1 = auto)\n"
            f"bar_width: {_yv(self.bar_width)}"
            "  # equaliser bar thickness in px (-1 = auto)\n"
            f"bar_radius: {_yv(self.bar_radius)}"
            "  # equaliser bar corner radius in px (0 = sharp)\n"
            f"bar_fade: {_yv(self.bar_fade)}"
            "  # quiet bars fade to transparent (false = solid bars)\n"
            "\n"
            "# ── Per-model tunables (set from the Model Manager) ──────\n"
            f"{params}\n"
        )
