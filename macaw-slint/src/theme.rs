//! Theme table (ported from ui/src/theme.ts) + config-override resolution.

use serde_json::Value;
use slint::Color;

#[derive(Clone, Copy)]
pub struct ThemeDef {
    pub bg: u32,
    pub surface: u32,
    pub control: u32,
    pub fg: u32,
    pub muted: u32,
    pub border: u32,
    pub accent: u32,
    pub accent_fg: u32,
    pub ok: u32,
    pub warn: u32,
    pub danger: u32,
    pub overlay_bg: u32,
    pub eq_idle: u32,
    pub eq_colors: [u32; 4], // 0-terminated when fewer stops
    pub corners: [i32; 4],   // tl, tr, br, bl
    pub border_color: u32,
}

pub const MACAW: ThemeDef = ThemeDef {
    bg: 0xFDF4EA,
    surface: 0xFFFFFF,
    control: 0xFBF1E4,
    fg: 0x1B1712,
    muted: 0x8A7C6C,
    border: 0xEADBC7,
    accent: 0xE5322B,
    accent_fg: 0xFFFFFF,
    ok: 0x2E9E6B,
    warn: 0xB8860B,
    danger: 0xC81F1A,
    overlay_bg: 0x171310,
    eq_idle: 0xE7D8C4,
    eq_colors: [0xE5322B, 0xF7B500, 0x2F6FD0, 0],
    corners: [18, 18, 18, 3],
    border_color: 0xE5322B,
};

pub const OLED: ThemeDef = ThemeDef {
    bg: 0x0A0A0A,
    surface: 0x0E0E0E,
    control: 0x111111,
    fg: 0xFFFFFF,
    muted: 0x666666,
    border: 0x2A2A2A,
    accent: 0x4CAF7D,
    accent_fg: 0x0A0A0A,
    ok: 0x4CAF7D,
    warn: 0xC9A227,
    danger: 0xCC4444,
    overlay_bg: 0x000000,
    eq_idle: 0x3A3A3A,
    eq_colors: [0x4CAF7D, 0x2FBF9F, 0x38BDF8, 0],
    corners: [3, 3, 3, 3],
    border_color: 0x2A2A2A,
};

pub const LIGHT: ThemeDef = ThemeDef {
    bg: 0xF5F5F7,
    surface: 0xFFFFFF,
    control: 0xFFFFFF,
    fg: 0x1D1D1F,
    muted: 0x86868B,
    border: 0xD2D2D7,
    accent: 0x2F6FD0,
    accent_fg: 0xFFFFFF,
    ok: 0x2E9E6B,
    warn: 0xB8860B,
    danger: 0xD14343,
    overlay_bg: 0xFFFFFF,
    eq_idle: 0xD2D2D7,
    eq_colors: [0x2F6FD0, 0x00A6A6, 0x7C5CFC, 0],
    corners: [16, 16, 16, 16],
    border_color: 0xD2D2D7,
};

pub const CATPPUCCIN: ThemeDef = ThemeDef {
    bg: 0x1E1E2E,
    surface: 0x181825,
    control: 0x313244,
    fg: 0xCDD6F4,
    muted: 0xA6ADC8,
    border: 0x45475A,
    accent: 0xCBA6F7,
    accent_fg: 0x1E1E2E,
    ok: 0xA6E3A1,
    warn: 0xF9E2AF,
    danger: 0xF38BA8,
    overlay_bg: 0x11111B,
    eq_idle: 0x45475A,
    eq_colors: [0xF38BA8, 0xCBA6F7, 0x89B4FA, 0xA6E3A1],
    corners: [16, 16, 3, 16],
    border_color: 0xCBA6F7,
};

// ── app chrome (settings/models window) ─────────────────────────────
// The 10 themes above style the INDICATOR only. The app itself is a
// minimal terminal-like chrome: monochrome, hard corners, real black.

pub const CHROME_DARK: ThemeDef = ThemeDef {
    bg: 0x000000,
    surface: 0x050505,
    control: 0x0D0D0D,
    fg: 0xD8D8D8,
    muted: 0x6E6E6E,
    border: 0x222222,
    accent: 0xE8E8E8, // inverted-video "highlight"
    accent_fg: 0x000000,
    ok: 0x3FB950,
    warn: 0xD29922,
    danger: 0xF85149,
    overlay_bg: 0x000000, // unused for chrome
    eq_idle: 0x222222,    // unused for chrome
    eq_colors: [0, 0, 0, 0],
    corners: [0, 0, 0, 0],
    border_color: 0x222222,
};

pub const CHROME_LIGHT: ThemeDef = ThemeDef {
    bg: 0xFFFFFF,
    surface: 0xFFFFFF,
    control: 0xF2F2F2,
    fg: 0x141414,
    muted: 0x6E6E6E,
    border: 0xD0D0D0,
    accent: 0x141414,
    accent_fg: 0xFFFFFF,
    ok: 0x1A7F37,
    warn: 0x9A6700,
    danger: 0xCF222E,
    overlay_bg: 0xFFFFFF,
    eq_idle: 0xD0D0D0,
    eq_colors: [0, 0, 0, 0],
    corners: [0, 0, 0, 0],
    border_color: 0xD0D0D0,
};

pub const DRACULA: ThemeDef = ThemeDef {
    bg: 0x282A36,
    surface: 0x21222C,
    control: 0x343746,
    fg: 0xF8F8F2,
    muted: 0x6272A4,
    border: 0x44475A,
    accent: 0xBD93F9,
    accent_fg: 0x282A36,
    ok: 0x50FA7B,
    warn: 0xF1FA8C,
    danger: 0xFF5555,
    overlay_bg: 0x191A21,
    eq_idle: 0x44475A,
    eq_colors: [0xFF79C6, 0xBD93F9, 0x8BE9FD, 0],
    corners: [12, 12, 12, 12],
    border_color: 0xBD93F9,
};

pub const NORD: ThemeDef = ThemeDef {
    bg: 0x2E3440,
    surface: 0x3B4252,
    control: 0x434C5E,
    fg: 0xECEFF4,
    muted: 0x93A3BC,
    border: 0x4C566A,
    accent: 0x88C0D0,
    accent_fg: 0x2E3440,
    ok: 0xA3BE8C,
    warn: 0xEBCB8B,
    danger: 0xBF616A,
    overlay_bg: 0x242933,
    eq_idle: 0x4C566A,
    eq_colors: [0x88C0D0, 0x81A1C1, 0xB48EAD, 0],
    corners: [10, 10, 10, 10],
    border_color: 0x88C0D0,
};

pub const GRUVBOX: ThemeDef = ThemeDef {
    bg: 0x282828,
    surface: 0x32302F,
    control: 0x3C3836,
    fg: 0xEBDBB2,
    muted: 0x928374,
    border: 0x504945,
    accent: 0xFE8019,
    accent_fg: 0x282828,
    ok: 0xB8BB26,
    warn: 0xFABD2F,
    danger: 0xFB4934,
    overlay_bg: 0x1D2021,
    eq_idle: 0x504945,
    eq_colors: [0xFB4934, 0xFABD2F, 0xB8BB26, 0],
    corners: [6, 6, 6, 6],
    border_color: 0xFE8019,
};

pub const TOKYO_NIGHT: ThemeDef = ThemeDef {
    bg: 0x1A1B26,
    surface: 0x16161E,
    control: 0x292E42,
    fg: 0xC0CAF5,
    muted: 0x565F89,
    border: 0x3B4261,
    accent: 0x7AA2F7,
    accent_fg: 0x1A1B26,
    ok: 0x9ECE6A,
    warn: 0xE0AF68,
    danger: 0xF7768E,
    overlay_bg: 0x16161E,
    eq_idle: 0x3B4261,
    eq_colors: [0xF7768E, 0xBB9AF7, 0x7AA2F7, 0x7DCFFF],
    corners: [14, 14, 14, 14],
    border_color: 0x7AA2F7,
};

pub const ROSE_PINE: ThemeDef = ThemeDef {
    bg: 0x191724,
    surface: 0x1F1D2E,
    control: 0x26233A,
    fg: 0xE0DEF4,
    muted: 0x908CAA,
    border: 0x403D52,
    accent: 0xEBBCBA,
    accent_fg: 0x191724,
    ok: 0x9CCFD8,
    warn: 0xF6C177,
    danger: 0xEB6F92,
    overlay_bg: 0x12101A,
    eq_idle: 0x403D52,
    eq_colors: [0xEB6F92, 0xEBBCBA, 0xC4A7E7, 0x9CCFD8],
    corners: [16, 16, 16, 16],
    border_color: 0xEBBCBA,
};

pub const SOLARIZED: ThemeDef = ThemeDef {
    bg: 0x002B36,
    surface: 0x073642,
    control: 0x0A4552,
    fg: 0xEEE8D5,
    muted: 0x839496,
    border: 0x175A69,
    accent: 0x268BD2,
    accent_fg: 0xFDF6E3,
    ok: 0x859900,
    warn: 0xB58900,
    danger: 0xDC322F,
    overlay_bg: 0x00212B,
    eq_idle: 0x175A69,
    eq_colors: [0x268BD2, 0x2AA198, 0x859900, 0],
    corners: [8, 8, 8, 8],
    border_color: 0x268BD2,
};

/// Selectable themes, in menu order. `by_name`/`index_of` key off this.
pub const NAMES: [&str; 10] = [
    "macaw",
    "oled",
    "light",
    "catppuccin",
    "dracula",
    "nord",
    "gruvbox",
    "tokyo-night",
    "rose-pine",
    "solarized",
];

pub fn index_of(name: &str) -> usize {
    NAMES.iter().position(|n| *n == name).unwrap_or(0)
}

pub fn by_name(name: &str) -> &'static ThemeDef {
    match name {
        "oled" => &OLED,
        "light" => &LIGHT,
        "catppuccin" => &CATPPUCCIN,
        "dracula" => &DRACULA,
        "nord" => &NORD,
        "gruvbox" => &GRUVBOX,
        "tokyo-night" => &TOKYO_NIGHT,
        "rose-pine" => &ROSE_PINE,
        "solarized" => &SOLARIZED,
        _ => &MACAW,
    }
}

pub fn rgb(v: u32) -> Color {
    Color::from_rgb_u8((v >> 16) as u8, (v >> 8) as u8, v as u8)
}

/// "#RRGGBB" -> Color (None on anything else).
pub fn parse_hex(s: &str) -> Option<Color> {
    let s = s.trim().strip_prefix('#')?;
    if s.len() != 6 {
        return None;
    }
    u32::from_str_radix(s, 16).ok().map(rgb)
}

/// Theme eq stops, unless the config supplies its own list.
pub fn eq_colors(theme: &ThemeDef, cfg: &Value) -> Vec<Color> {
    let from_cfg: Vec<Color> = cfg["eq_colors"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().and_then(parse_hex))
                .collect()
        })
        .unwrap_or_default();
    if !from_cfg.is_empty() {
        return from_cfg;
    }
    theme
        .eq_colors
        .iter()
        .take_while(|&&c| c != 0)
        .map(|&c| rgb(c))
        .collect()
}

/// Corner radii — old resolveLook semantics (overlay.ts:137-145):
/// corner_link=false + corners[4] → per-corner; corner_radius >= 0 →
/// uniform; else the theme's shape.
pub fn corners(theme: &ThemeDef, cfg: &Value) -> [f32; 4] {
    let linked = cfg["corner_link"].as_bool().unwrap_or(true);
    if !linked {
        if let Some(list) = cfg["corners"].as_array() {
            if list.len() == 4 {
                let mut out = [0f32; 4];
                for (i, v) in list.iter().enumerate() {
                    out[i] = v.as_f64().unwrap_or(0.0).max(0.0) as f32;
                }
                return out;
            }
        }
    }
    let uniform = cfg["corner_radius"].as_i64().unwrap_or(-1);
    if uniform >= 0 {
        return [uniform as f32; 4];
    }
    let c = theme.corners;
    [c[0] as f32, c[1] as f32, c[2] as f32, c[3] as f32]
}

// ── custom themes ────────────────────────────────────────────────────
// A custom theme = a stock base + the indicator override fields, saved
// under config.custom_themes[name]. Stock themes are never overwritten.

/// Every config field that participates in an indicator theme, with its
/// pristine default. Editing any of these makes the current look "custom".
pub fn override_defaults() -> Vec<(&'static str, Value)> {
    use serde_json::json;
    vec![
        ("overlay_bg", json!("")),
        ("overlay_opacity", json!(0.94)),
        ("eq_colors", json!([])),
        ("accent_color", json!("")),
        ("border_color", json!("")),
        ("border_width", json!(0)),
        ("corner_radius", json!(-1)),
        ("corners", json!([])),
        ("corner_link", json!(true)),
        ("bar_spacing", json!(-1)),
        ("bar_width", json!(-1)),
        ("bar_radius", json!(0)),
        ("bar_fade", json!(true)),
        ("bar_count", json!(24)),
        ("transcribe_anim", json!("waves")),
        ("anim_speed", json!(1.0)),
        ("trans_link", json!(true)),
        ("trans_colors", json!([])),
        ("done_color", json!("")),
        ("error_color", json!("")),
    ]
}

/// The stock theme the current look is based on. `theme` is either a stock
/// name or "custom:<name>" whose entry carries `based_on`.
pub fn base_name(cfg: &Value) -> String {
    let theme = cfg["theme"].as_str().unwrap_or("macaw");
    if let Some(name) = theme.strip_prefix("custom:") {
        return cfg["custom_themes"][name]["based_on"]
            .as_str()
            .unwrap_or("macaw")
            .to_string();
    }
    theme.to_string()
}

/// True when any indicator override differs from its pristine default —
/// numeric comparisons are fuzzy (YAML round-trips ints/floats loosely).
pub fn is_dirty(cfg: &Value) -> bool {
    override_defaults().iter().any(|(key, def)| {
        let v = &cfg[*key];
        if v.is_null() {
            return false;
        }
        match (v.as_f64(), def.as_f64()) {
            (Some(a), Some(b)) => (a - b).abs() > 1e-6,
            _ => v != def,
        }
    })
}
