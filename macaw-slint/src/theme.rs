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

pub fn by_name(name: &str) -> &'static ThemeDef {
    match name {
        "oled" => &OLED,
        "light" => &LIGHT,
        "catppuccin" => &CATPPUCCIN,
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

/// Corner radii: cfg.corners[4] > cfg.corner_radius >= 0 > theme corners.
pub fn corners(theme: &ThemeDef, cfg: &Value) -> [f32; 4] {
    if let Some(list) = cfg["corners"].as_array() {
        if list.len() == 4 {
            let mut out = [0f32; 4];
            for (i, v) in list.iter().enumerate() {
                out[i] = v.as_f64().unwrap_or(0.0) as f32;
            }
            return out;
        }
    }
    let uniform = cfg["corner_radius"].as_i64().unwrap_or(-1);
    if uniform >= 0 {
        return [uniform as f32; 4];
    }
    let c = theme.corners;
    [c[0] as f32, c[1] as f32, c[2] as f32, c[3] as f32]
}
