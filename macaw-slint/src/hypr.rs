//! Hyprland overlay placement.
//!
//! Wayland toplevels cannot self-position; Hyprland can, via window rules.
//! We tag the overlay window by its (unique) title and install rules that
//! float it, pin it across workspaces, strip decorations, keep focus away,
//! and move it to the configured anchor. Rules apply when a window maps, so
//! they are (re)installed before every overlay show.
//!
//! On other compositors this module is inert: the overlay still works as a
//! compositor-placed floating window.

use std::process::Command;

pub const OVERLAY_TITLE: &str = "macaw-overlay";

/// HYPRLAND_INSTANCE_SIGNATURE, derived from the runtime dir when the
/// environment lacks it (dev shells, systemd-launched sessions).
fn signature() -> Option<String> {
    if let Ok(sig) = std::env::var("HYPRLAND_INSTANCE_SIGNATURE") {
        return Some(sig);
    }
    let dir = std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/run/user/1000".into());
    let hypr = std::path::Path::new(&dir).join("hypr");
    let mut best: Option<(std::time::SystemTime, String)> = None;
    for e in std::fs::read_dir(hypr).ok()?.flatten() {
        let t = e.metadata().and_then(|m| m.modified()).ok()?;
        let name = e.file_name().to_string_lossy().into_owned();
        if best.as_ref().map(|(bt, _)| t > *bt).unwrap_or(true) {
            best = Some((t, name));
        }
    }
    best.map(|(_, name)| name)
}

fn hyprctl(args: &[&str]) {
    if let Some(sig) = signature() {
        let _ = Command::new("hyprctl")
            .env("HYPRLAND_INSTANCE_SIGNATURE", &sig)
            .args(args)
            .output();
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Monitor {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
    pub reserved_top: i32,
    pub reserved_bottom: i32,
}

/// Focused monitor logical geometry (hyprctl monitors -j).
pub fn focused_monitor() -> Option<Monitor> {
    let sig = signature()?;
    let out = Command::new("hyprctl")
        .env("HYPRLAND_INSTANCE_SIGNATURE", &sig)
        .args(["monitors", "-j"])
        .output()
        .ok()?;
    let mons: serde_json::Value = serde_json::from_slice(&out.stdout).ok()?;
    let mon = mons
        .as_array()?
        .iter()
        .find(|m| m["focused"].as_bool().unwrap_or(false))
        .or_else(|| mons.as_array()?.first())?;
    let scale = mon["scale"].as_f64().unwrap_or(1.0);
    let res = |k: usize| {
        mon["reserved"]
            .as_array()
            .and_then(|r| r.get(k))
            .and_then(|v| v.as_i64())
            .unwrap_or(0) as i32
    };
    Some(Monitor {
        x: mon["x"].as_i64()? as i32,
        y: mon["y"].as_i64()? as i32,
        w: (mon["width"].as_i64()? as f64 / scale) as i32,
        h: (mon["height"].as_i64()? as f64 / scale) as i32,
        reserved_top: res(1),
        reserved_bottom: res(3),
    })
}

/// Overlay top-left for an anchor spec, 24 px margins (parity with the old UI).
pub fn anchor_xy(position: &str, ow: i32, oh: i32, custom: (i32, i32), m: &Monitor) -> (i32, i32) {
    const MARGIN: i32 = 24;
    let (sx, sy) = (m.x, m.y + m.reserved_top);
    let (sw, sh) = (m.w, m.h - m.reserved_top - m.reserved_bottom);
    match position {
        "top_left" => (sx + MARGIN, sy + MARGIN),
        "top_center" => (sx + (sw - ow) / 2, sy + MARGIN),
        "top_right" => (sx + sw - ow - MARGIN, sy + MARGIN),
        "bottom_left" => (sx + MARGIN, sy + sh - oh - MARGIN),
        "bottom_right" => (sx + sw - ow - MARGIN, sy + sh - oh - MARGIN),
        "custom" => (sx + custom.0, sy + custom.1),
        _ /* bottom_center */ => (sx + (sw - ow) / 2, sy + sh - oh - MARGIN),
    }
}

/// Install/update the overlay window rules (one hyprctl call, deduped).
/// Matched on class (Wayland app_id) — set per-window at creation.
///
/// Hyprland ≥0.5x inline syntax: "prop value, …, match:class regex".
/// Older versions reject it ("invalid field …"), so we fall back to the
/// legacy windowrulev2 batch. Runtime keyword rules accumulate per call —
/// the cache skips reinstalling identical geometry.
pub fn install_rules(x: i32, y: i32, w: i32, h: i32) {
    thread_local! {
        static LAST: std::cell::RefCell<String> = const { std::cell::RefCell::new(String::new()) };
    }
    let rule = format!(
        "float on, pin on, no_focus on, border_size 0, no_shadow on, rounding 0, no_anim on, \
         size {w} {h}, move {x} {y}, match:class ^({OVERLAY_TITLE})$"
    );
    if LAST.with(|l| *l.borrow() == rule) {
        return;
    }
    let Some(sig) = signature() else { return };
    let out = Command::new("hyprctl")
        .env("HYPRLAND_INSTANCE_SIGNATURE", &sig)
        .args(["keyword", "windowrule", &rule])
        .output();
    let ok = out.map(|o| o.stdout.starts_with(b"ok")).unwrap_or(false);
    if !ok {
        // legacy syntax (pre-rename Hyprland)
        let sel = format!("class:^({OVERLAY_TITLE})$");
        let batch = [
            format!("float,{sel}"),
            format!("pin,{sel}"),
            format!("noborder,{sel}"),
            format!("norounding,{sel}"),
            format!("noshadow,{sel}"),
            format!("noblur,{sel}"),
            format!("noanim,{sel}"),
            format!("nofocus,{sel}"),
            format!("size {w} {h},{sel}"),
            format!("move {x} {y},{sel}"),
        ]
        .iter()
        .map(|r| format!("keyword windowrulev2 {r}"))
        .collect::<Vec<_>>()
        .join(" ; ");
        hyprctl(&["--batch", &batch]);
    }
    LAST.with(|l| *l.borrow_mut() = rule);
}

/// Nudge an already-mapped overlay (config change while visible).
pub fn move_mapped(x: i32, y: i32) {
    hyprctl(&[
        "dispatch",
        "movewindowpixel",
        &format!("exact {x} {y},class:^({OVERLAY_TITLE})$"),
    ]);
}

/// Float the settings window at its fixed size — tiled windows ignore size
/// hints and would stretch the surface (dead space around fixed content).
/// Runtime keyword rules apply after config rules, so this wins over any
/// stale user rule for the class.
pub fn install_main_rules() {
    let rule = "float on, size 1180 760, center on, match:class ^(macaw)$";
    let Some(sig) = signature() else { return };
    let out = Command::new("hyprctl")
        .env("HYPRLAND_INSTANCE_SIGNATURE", &sig)
        .args(["keyword", "windowrule", rule])
        .output();
    let ok = out.map(|o| o.stdout.starts_with(b"ok")).unwrap_or(false);
    if !ok {
        // legacy syntax
        hyprctl(&[
            "--batch",
            "keyword windowrulev2 float,class:^(macaw)$ ; keyword windowrulev2 size 1180 760,class:^(macaw)$ ; keyword windowrulev2 center,class:^(macaw)$",
        ]);
    }
}

/// Post-map fixup: if the settings window mapped tiled anyway (rule missed
/// its map, compositor restart, …), float it and restore its exact size.
pub fn enforce_main_geometry() {
    // no centerwindow: it acts on the ACTIVE window, not a selector
    hyprctl(&[
        "--batch",
        "dispatch setfloating class:^(macaw)$ ; \
         dispatch resizewindowpixel exact 1180 760,class:^(macaw)$",
    ]);
}
