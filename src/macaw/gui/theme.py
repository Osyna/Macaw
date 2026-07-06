"""Macaw's theming system.

Every colour in the GUI comes from the *active* theme, chosen by the
``theme:`` key in ``~/.config/macaw/config.yaml``. Switching theme is a
one-word config change (or the Appearance picker in Settings).

Adding a new theme is deliberately trivial — copy a block below, tweak the
colours, and add it to ``THEMES``. That's it; the whole app re-skins from
these 15 roles. No stylesheet edits anywhere else.

    THEMES["solarized"] = Theme(
        name="solarized", label="Solarized",
        bg="#002b36", surface="#073642", control="#094552",
        fg="#eee8d5", muted="#93a1a1", border="#0a4b5a",
        accent="#268bd2", accent_fg="#fdf6e3",
        ok="#859900", warn="#b58900", danger="#dc322f",
        eq_idle="#0a4b5a",
        overlay_bg="#002b36",
    )

Role meanings:
    bg          window / page background
    surface     card & panel background (sits on bg)
    control     input / combo / list background (sits on surface)
    fg          primary text
    muted       secondary text, hints, labels
    border      hairlines, dividers, outlines
    accent      brand highlight — Apply button glow, hovers
    accent_fg   text drawn *on* accent (pick for contrast)
    ok/warn/danger   status colours (model states, warnings)
    eq_idle     equaliser bars when quiet
    overlay_bg  floating recording bar background
    corners     per-corner radii (tl, tr, br, bl) — the bar's shape (0 = sharp)
    eq_colors   palette the voice equaliser sweeps through, left→right
    border_width / border_color   optional outline on the recording bar
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from PyQt6.QtGui import QColor


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    bg: str
    surface: str
    control: str
    fg: str
    muted: str
    border: str
    accent: str
    accent_fg: str
    ok: str
    warn: str
    danger: str
    eq_idle: str
    overlay_bg: str
    # per-corner radii of the recording bar: (top-left, top-right, bottom-right,
    # bottom-left). Unequal corners give a shape an identity — e.g. a squared
    # bottom-left corner reads as a speech bubble.
    corners: tuple[int, int, int, int] = (12, 12, 12, 12)
    # palette the live voice equaliser sweeps through, left→right (1+ hex,
    # interpolated across the bars). One colour = solid; several = a gradient.
    eq_colors: tuple[str, ...] = ("#4CAF7D",)
    overlay_opacity: float = 0.94  # record indicator opacity (0.5–1.0)
    border_width: int = 0  # record bar border thickness, px (0 = none)
    border_color: str = "#000000"  # border colour (used when width > 0)
    # equaliser bar layout / feel
    bar_spacing: int = -1  # gap between bars, px (-1 = proportional auto)
    bar_width: int = -1  # bar thickness, px (-1 = fill the slot minus gap)
    bar_radius: int = 0  # bar corner radius, px (0 = sharp)
    bar_fade: bool = True  # quiet bars fade to transparent (False = solid)


def qcolor(hex_str: str, alpha: int = 255) -> QColor:
    """Build a QColor from a theme hex, with optional alpha (for painters)."""
    c = QColor(hex_str)
    c.setAlpha(alpha)
    return c


# ── the built-in themes ──────────────────────────────────────────────
THEMES: dict[str, Theme] = {}


def _register(theme: Theme) -> Theme:
    THEMES[theme.name] = theme
    return theme


# True black — the original look, kind to OLED panels.
_register(
    Theme(
        name="oled",
        label="OLED (black)",
        bg="#0A0A0A",
        surface="#0E0E0E",
        control="#111111",
        fg="#FFFFFF",
        muted="#666666",
        border="#2A2A2A",
        accent="#4CAF7D",
        accent_fg="#0A0A0A",
        ok="#4CAF7D",
        warn="#C9A227",
        danger="#CC4444",
        eq_idle="#3A3A3A",
        overlay_bg="#000000",
        corners=(3, 3, 3, 3),  # crisp, minimal — a terminal slab
        eq_colors=("#4CAF7D", "#2FBF9F", "#38BDF8"),  # green → teal → cyan
        border_color="#2A2A2A",
    )
)

# Warm & tropical — matches the website and the scarlet-macaw logo.
_register(
    Theme(
        name="macaw",
        label="Macaw (tropical)",
        bg="#FDF4EA",
        surface="#FFFFFF",
        control="#FBF1E4",
        fg="#1B1712",
        muted="#8A7C6C",
        border="#EADBC7",
        accent="#E5322B",
        accent_fg="#FFFFFF",
        ok="#2E9E6B",
        warn="#B8860B",
        danger="#C81F1A",
        eq_idle="#E7D8C4",
        overlay_bg="#171310",
        corners=(18, 18, 18, 3),  # speech bubble — squared bottom-left corner
        eq_colors=("#E5322B", "#F7B500", "#2F6FD0"),  # scarlet → gold → azure wings
        border_color="#E5322B",
    )
)

# Clean neutral light theme.
_register(
    Theme(
        name="light",
        label="Light",
        bg="#F5F5F7",
        surface="#FFFFFF",
        control="#FFFFFF",
        fg="#1D1D1F",
        muted="#86868B",
        border="#D2D2D7",
        accent="#2F6FD0",
        accent_fg="#FFFFFF",
        ok="#2E9E6B",
        warn="#B8860B",
        danger="#D14343",
        eq_idle="#D2D2D7",
        overlay_bg="#FFFFFF",
        corners=(16, 16, 16, 16),  # a soft, even pill
        eq_colors=("#2F6FD0", "#00A6A6", "#7C5CFC"),  # blue → teal → violet
        border_color="#D2D2D7",
    )
)

# Catppuccin Mocha.
_register(
    Theme(
        name="catppuccin",
        label="Catppuccin (Mocha)",
        bg="#1E1E2E",
        surface="#181825",
        control="#313244",
        fg="#CDD6F4",
        muted="#A6ADC8",
        border="#45475A",
        accent="#CBA6F7",
        accent_fg="#1E1E2E",
        ok="#A6E3A1",
        warn="#F9E2AF",
        danger="#F38BA8",
        eq_idle="#45475A",
        overlay_bg="#11111B",
        corners=(16, 16, 3, 16),  # mirror bubble — squared bottom-right corner
        eq_colors=("#F38BA8", "#CBA6F7", "#89B4FA", "#A6E3A1"),  # pink→mauve→blue→green
        border_color="#CBA6F7",
    )
)

DEFAULT_THEME = "oled"


def active_theme() -> Theme:
    """The theme selected in config, with the user's look overrides (opacity,
    equaliser palette, accent colour) layered on top. Unknown name → OLED."""
    from macaw.config import Config

    cfg = Config.load()
    base = THEMES.get(cfg.theme, THEMES[DEFAULT_THEME])
    over = {
        "overlay_opacity": cfg.overlay_opacity,
        "border_width": cfg.border_width,
        "bar_spacing": cfg.bar_spacing,
        "bar_width": cfg.bar_width,
        "bar_radius": cfg.bar_radius,
        "bar_fade": cfg.bar_fade,
    }
    if cfg.eq_colors:
        over["eq_colors"] = tuple(cfg.eq_colors)
    if cfg.accent_color:
        over["accent"] = cfg.accent_color
    if cfg.border_color:
        over["border_color"] = cfg.border_color
    if cfg.corner_radius >= 0:
        r = cfg.corner_radius
        over["corners"] = (r, r, r, r)
    return replace(base, **over)
