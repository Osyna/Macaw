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
    output_mode: str = "clipboard"  # clipboard | type | live (live = type as you speak)
    silence_timeout: float = 3.0
    auto_stop: bool = True  # False = record until toggled (hotkey/tray/Record)
    level_gain: float = 1.0  # visual input boost, 0.5-4 (quiet mics; peaks still cap)
    silence_level: float = 0.33  # meter position (0-1): quieter than this = silence
    vad_gate: bool = True  # trim silence before transcription (all models)
    window_position: str = "bottom_center"
    sound_enabled: bool = True
    onboarded: bool = False  # first-launch wizard completed (fresh installs: False)
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
    bar_count: int = 24  # number of equaliser bars (8-48)
    overlay_bg: str = ""  # overlay pill background, hex (blank = theme)
    record_anim: str = "bars"  # recording: bars|mirror|dots|wave|blocks|orb
    transcribe_anim: str = "waves"  # transcribing: waves|sweep|pulse|dots|scan|cascade|shimmer|orbit|typewriter|bounce|heartbeat
    format_anim: str = "shimmer"  # formatting step: loader anim (waves|sweep|…)
    orb_size: float = 0.55  # orb circle size, fraction of the pill (0.2-1.0)
    orb_dynamic: bool = False  # orb swells with the voice (else static)
    anim_speed: float = 1.0  # transcribing animation speed multiplier (0.25-3)
    trans_link: bool = True  # transcribing uses the recording (eq) colors
    trans_colors: list = field(default_factory=list)  # own stops when unlinked
    done_anim: str = "pop"  # done entrance: pop|flash|none
    done_color: str = ""  # done check-mark colour, hex (blank = theme)
    done_ring: str = ""  # circle behind the check, hex (blank = pill colour)
    error_color: str = ""  # error flash colour, hex (blank = theme)
    model: str = ""  # empty = nothing selected yet (pick one in the Model Manager)
    # Per-model tunables: {model_id: {param_key: value}}
    model_params: dict = field(default_factory=dict)
    model_languages: dict = field(default_factory=dict)  # {model_id: lang code}
    # Saved indicator themes: {name: {based_on: str, <override fields...>}}
    custom_themes: dict = field(default_factory=dict)
    openai_api_key: str = ""  # for cloud models; falls back to $OPENAI_API_KEY
    # Network (advanced): route downloads + cloud calls through a proxy, and
    # optionally skip SSL verification (e.g. behind a corporate MITM proxy).
    proxy: str = ""  # HTTP(S) proxy URL, e.g. http://host:port (blank = none)
    ssl_verify: bool = True  # False disables SSL certificate verification
    star_prompted: bool = False  # shown the GitHub-star nudge once
    # LLM post-processing: pass final STT text through a small/fast model that
    # fixes punctuation, trims dictation filler and formats to fit (see llm/).
    llm_enabled: bool = False  # run final text through the formatter (not in live mode)
    llm_model: str = ""  # llm model id, or "provider:<id>" for a cloud provider
    llm_prompt: str = ""  # custom system prompt ("" = built-in smart default)
    llm_api_key: str = (
        ""  # cloud LLM key; falls back to openai_api_key, then $OPENAI_API_KEY
    )
    llm_base_url: str = ""  # OpenAI-compatible endpoint ("" = OpenAI)
    # Local formatter load mode: "hot" keeps the model warm in RAM for instant
    # formatting; "cold" loads it only when needed and frees it after idle.
    llm_load_mode: str = "hot"
    # Cloud LLM providers the user has configured: {provider_id: {base_url?,
    # model?, enabled}}. API keys are NOT here — they live in the encrypted
    # secret store (see macaw/secrets.py + macaw/llm/providers.py).
    providers: dict = field(default_factory=dict)
    # Per-model formatter tunables: {model_id: {key: value}} — rendered as
    # controls in the Formatting tab and passed to the backend at format time.
    llm_params: dict = field(default_factory=dict)

    def nudge_live_defaults(self, old_mode: str, patch: dict) -> None:
        """Switching to live typing: give speakers more breathing room — bump
        the silence timeout to 5 s. Only ever off the stock 3 s, and never
        over an explicit value in the same patch: a deliberate choice wins."""
        if (
            self.output_mode == "live"
            and old_mode != "live"
            and "silence_timeout" not in patch
            and self.silence_timeout == 3.0
        ):
            self.silence_timeout = 5.0

    def migrate_secrets(self) -> bool:
        """One-shot: lift legacy plaintext API keys out of the config into the
        encrypted store, and map an old cloud llm_model onto a provider. Returns
        True if the config changed and should be re-saved."""
        from macaw import secrets
        from macaw.llm.providers import secret_name

        changed = False
        legacy_key = self.openai_api_key or self.llm_api_key
        if legacy_key:
            if not secrets.has(secret_name("openai")):
                secrets.set(secret_name("openai"), legacy_key)
            self.openai_api_key = ""
            self.llm_api_key = ""
            changed = True
        if self.llm_model in ("gpt-4o-mini", "gpt-4.1-mini"):
            prov = self.providers.setdefault("openai", {})
            prov["enabled"] = True
            prov["model"] = self.llm_model
            self.llm_model = "provider:openai"
            changed = True
        if self.llm_base_url:
            self.llm_base_url = ""
            changed = True
        return changed

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or _DEFAULT_CONFIG_PATH
        if path is _DEFAULT_CONFIG_PATH:
            _migrate_legacy()
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            # streaming was its own flag before output_mode grew "live";
            # old configs with it enabled migrate to the merged value.
            mode = data.get("output_mode", "clipboard")
            if bool(data.get("streaming", False)) and mode == "type":
                mode = "live"
            return cls(
                device_index=data.get("device_index"),
                language=data.get("language", "en"),
                output_mode=mode,
                silence_timeout=float(data.get("silence_timeout", 3.0)),
                auto_stop=bool(data.get("auto_stop", True)),
                level_gain=float(data.get("level_gain", 1.0)),
                silence_level=float(data.get("silence_level", 0.33)),
                vad_gate=bool(data.get("vad_gate", True)),
                window_position=data.get("window_position", "bottom_center"),
                sound_enabled=bool(data.get("sound_enabled", True)),
                # a pre-existing config means a working setup — never re-onboard
                onboarded=bool(data.get("onboarded", bool(data))),
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
                bar_count=int(data.get("bar_count", 24)),
                overlay_bg=data.get("overlay_bg") or "",
                record_anim=(data.get("record_anim") or "bars").replace(
                    "ripple", "orb"
                ),
                transcribe_anim=data.get("transcribe_anim") or "waves",
                format_anim=data.get("format_anim") or "shimmer",
                orb_size=float(data.get("orb_size", 0.55)),
                orb_dynamic=bool(data.get("orb_dynamic", False)),
                anim_speed=float(data.get("anim_speed", 1.0)),
                trans_link=bool(data.get("trans_link", True)),
                trans_colors=list(data.get("trans_colors") or []),
                done_anim=data.get("done_anim") or "pop",
                done_color=data.get("done_color") or "",
                done_ring=data.get("done_ring") or "",
                error_color=data.get("error_color") or "",
                model=data.get("model") or "",
                model_params=data.get("model_params") or {},
                model_languages=data.get("model_languages") or {},
                custom_themes=data.get("custom_themes") or {},
                openai_api_key=data.get("openai_api_key") or "",
                proxy=data.get("proxy") or "",
                ssl_verify=bool(data.get("ssl_verify", True)),
                star_prompted=bool(data.get("star_prompted", False)),
                llm_enabled=bool(data.get("llm_enabled", False)),
                llm_model=data.get("llm_model") or "",
                llm_prompt=data.get("llm_prompt") or "",
                llm_api_key=data.get("llm_api_key") or "",
                llm_base_url=data.get("llm_base_url") or "",
                llm_load_mode=data.get("llm_load_mode") or "hot",
                providers=data.get("providers") or {},
                llm_params=data.get("llm_params") or {},
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
        if self.custom_themes:
            themes = yaml.safe_dump(
                {"custom_themes": self.custom_themes},
                default_flow_style=False,
                sort_keys=True,
            ).rstrip()
        else:
            themes = "custom_themes: {}"
        if self.providers:
            providers = yaml.safe_dump(
                {"providers": self.providers},
                default_flow_style=False,
                sort_keys=True,
            ).rstrip()
        else:
            providers = "providers: {}"
        if self.llm_params:
            llm_params = yaml.safe_dump(
                {"llm_params": self.llm_params},
                default_flow_style=False,
                sort_keys=True,
            ).rstrip()
        else:
            llm_params = "llm_params: {}"
        prompt = yaml.safe_dump(
            {"llm_prompt": self.llm_prompt},
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ).rstrip()
        return (
            "# macaw configuration\n"
            "# Edit by hand, via the Settings window, or `macaw --config edit`.\n"
            "# See `macaw --list` (model ids) and `macaw --devices` (mic indices).\n"
            "\n"
            "# ── Model ────────────────────────────────────────────────\n"
            f"model: {_yv(self.model)}\n"
            "# (API keys are stored encrypted in secrets.enc — never in this file)\n"
            "\n"
            "# ── Audio ────────────────────────────────────────────────\n"
            f"device_index: {_yv(self.device_index)}"
            "  # microphone index; null = system default\n"
            f"language: {_yv(self.language)}"
            "  # transcription language code (en, fr, de, …)\n"
            "\n"
            "# ── Output ───────────────────────────────────────────────\n"
            f"output_mode: {_yv(self.output_mode)}"
            "  # 'clipboard', 'type' (into the focused window) or 'live' (type as you speak)\n"
            f"window_position: {_yv(self.window_position)}  # overlay position\n"
            "\n"
            "# ── Behaviour ────────────────────────────────────────────\n"
            f"silence_timeout: {_yv(self.silence_timeout)}"
            "  # seconds of silence before auto-stop\n"
            f"auto_stop: {_yv(self.auto_stop)}"
            "  # false = never stop on silence; the hotkey starts AND stops\n"
            f"level_gain: {_yv(self.level_gain)}"
            "  # visual input boost 0.5-4 (animation/meter only; peaks still cap)\n"
            f"silence_level: {_yv(self.silence_level)}"
            "  # meter position 0-1: anything quieter counts as silence\n"
            f"vad_gate: {_yv(self.vad_gate)}"
            "  # skip silent stretches before transcribing (faster, fewer hallucinations)\n"
            f"sound_enabled: {_yv(self.sound_enabled)}  # play record / done tones\n"
            f"onboarded: {_yv(self.onboarded)}  # first-launch wizard completed\n"
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
            f"bar_count: {_yv(self.bar_count)}"
            "  # number of equaliser bars (8-48)\n"
            f"overlay_bg: {_yv(self.overlay_bg)}"
            "  # overlay pill background colour, hex (blank = theme)\n"
            f"record_anim: {_yv(self.record_anim)}"
            "  # recording: bars | mirror | dots | wave | blocks | orb\n"
            f"transcribe_anim: {_yv(self.transcribe_anim)}"
            "  # transcribing: waves | sweep | pulse | dots | scan | cascade | shimmer | orbit | typewriter | bounce | heartbeat\n"
            f"format_anim: {_yv(self.format_anim)}"
            "  # formatting step: loader anim (waves | sweep | pulse | shimmer | …)\n"
            f"orb_size: {_yv(self.orb_size)}"
            "  # orb circle size, fraction of the pill (0.2-1.0)\n"
            f"orb_dynamic: {_yv(self.orb_dynamic)}"
            "  # orb swells with the voice (false = static)\n"
            f"done_anim: {_yv(self.done_anim)}"
            "  # done entrance animation: pop | flash | none\n"
            f"done_ring: {_yv(self.done_ring)}"
            "  # circle behind the check mark, hex (blank = pill colour)\n"
            f"anim_speed: {_yv(self.anim_speed)}"
            "  # transcribing animation speed multiplier (0.25-3)\n"
            f"trans_link: {_yv(self.trans_link)}"
            "  # transcribing reuses the recording colours (false = own stops)\n"
            f"trans_colors: {_yv(self.trans_colors)}"
            "  # transcribing gradient stops when unlinked\n"
            f"done_color: {_yv(self.done_color)}"
            "  # done check-mark colour, hex (blank = theme)\n"
            f"error_color: {_yv(self.error_color)}"
            "  # error flash colour, hex (blank = theme)\n"
            "\n"
            "# ── LLM formatting (post-processing) ─────────────────────\n"
            f"llm_enabled: {_yv(self.llm_enabled)}"
            "  # pass final text through a formatter model (clipboard / type modes)\n"
            f"llm_model: {_yv(self.llm_model)}"
            "  # formatter model id (set it in the LLM tab; blank = none)\n"
            f"llm_load_mode: {_yv(self.llm_load_mode)}"
            "  # 'hot' keeps the model warm in RAM; 'cold' loads on demand\n"
            "# (cloud provider keys + endpoints are managed in the Providers window)\n"
            "# llm_prompt: system prompt for formatting (blank = built-in smart mode)\n"
            f"{prompt}\n"
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
            f"{params}\n{langs}\n{themes}\n{providers}\n{llm_params}\n"
        )
