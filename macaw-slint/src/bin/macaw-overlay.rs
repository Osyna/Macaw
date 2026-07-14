//! Layer-shell recording indicator.
//!
//! A dedicated Wayland client: the pill renders through Slint's software
//! renderer (custom platform, no winit) into wl_shm buffers on a
//! wlr-layer-shell OVERLAY surface — always on top, positioned by
//! anchor+margins, click-through via an empty input region. Window managers
//! never touch it.
//!
//! Driven by the parent (macaw-ui) over stdin, one JSON object per line:
//!   {"cmd":"look", ...}   resolved colors/geometry (see apply_look)
//!   {"cmd":"show","mode":"eq"} | {"cmd":"mode",...} | {"cmd":"hide"}
//!   {"cmd":"level","rms":0.4}
//!   {"cmd":"error","text":"..."}
//! stdin EOF => exit. Exits 2 when zwlr_layer_shell_v1 is missing so the
//! parent can fall back to a plain window.

#[path = "../bars.rs"]
mod bars;

use std::io::BufRead;
use std::rc::Rc;
use std::time::{Duration, Instant};

use bars::BarAnim;
use serde_json::Value;
use slint::platform::software_renderer::{
    MinimalSoftwareWindow, PremultipliedRgbaColor, RepaintBufferType,
};
use slint::platform::{Platform, PlatformError, WindowAdapter};
use slint::Model;
use slint::{ComponentHandle, ModelRc, VecModel};
use smithay_client_toolkit::{
    compositor::{CompositorHandler, CompositorState, Region},
    delegate_compositor, delegate_layer, delegate_output, delegate_registry, delegate_shm,
    output::{OutputHandler, OutputState},
    reexports::{
        calloop::{
            channel::{channel, Event as ChanEvent},
            timer::{TimeoutAction, Timer},
            EventLoop,
        },
        calloop_wayland_source::WaylandSource,
    },
    registry::{ProvidesRegistryState, RegistryState},
    registry_handlers,
    shell::{
        wlr_layer::{
            Anchor, KeyboardInteractivity, Layer, LayerShell, LayerShellHandler, LayerSurface,
            LayerSurfaceConfigure,
        },
        WaylandSurface,
    },
    shm::{slot::SlotPool, Shm, ShmHandler},
};
use wayland_client::{
    globals::registry_queue_init,
    protocol::{wl_output, wl_shm, wl_surface},
    Connection, QueueHandle,
};

slint::include_modules!();

const TICK: Duration = Duration::from_millis(33);

// ── Slint custom platform ────────────────────────────────────────────

struct SwPlatform {
    window: Rc<MinimalSoftwareWindow>,
    start: Instant,
}

impl Platform for SwPlatform {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, PlatformError> {
        Ok(self.window.clone())
    }
    fn duration_since_start(&self) -> Duration {
        self.start.elapsed()
    }
}

// ── Wayland state ────────────────────────────────────────────────────

struct Overlay {
    registry_state: RegistryState,
    output_state: OutputState,
    shm: Shm,
    pool: SlotPool,
    layer: LayerSurface,
    // slint
    window: Rc<MinimalSoftwareWindow>,
    ui: OverlayWindow,
    levels: Rc<VecModel<f32>>,
    anim: BarAnim,
    // state
    w: u32,
    h: u32,
    scale: i32,
    rms: f32,
    visible: bool,
    configured: bool,
    exit: bool,
}

impl Overlay {
    /// Render the pill and push a frame. A hidden overlay commits one fully
    /// transparent buffer (surface stays mapped — no remap dance).
    fn draw(&mut self) {
        if !self.configured {
            return;
        }
        let scale = self.scale.max(1);
        let (pw, ph) = (self.w as i32 * scale, self.h as i32 * scale);
        let stride = pw * 4;
        let Ok((buffer, canvas)) =
            self.pool
                .create_buffer(pw, ph, stride, wl_shm::Format::Argb8888)
        else {
            return;
        };

        if self.visible {
            slint::platform::update_timers_and_animations();
            let mut premul = vec![PremultipliedRgbaColor::default(); (pw * ph) as usize];
            self.window.request_redraw();
            self.window.draw_if_needed(|renderer| {
                renderer.render(&mut premul, pw as usize);
            });
            // PremultipliedRgbaColor is R,G,B,A; wl_shm ARGB8888 is B,G,R,A.
            for (px, out) in premul.iter().zip(canvas.chunks_exact_mut(4)) {
                out[0] = px.blue;
                out[1] = px.green;
                out[2] = px.red;
                out[3] = px.alpha;
            }
        } else {
            canvas.fill(0); // fully transparent
        }

        let _ = self.layer.set_buffer_scale(scale as u32);
        let s = self.layer.wl_surface();
        s.damage_buffer(0, 0, pw, ph);
        let _ = buffer.attach_to(s);
        self.layer.commit();
    }

    fn tick(&mut self) {
        if !self.visible {
            return;
        }
        let rms = self.rms;
        let (bars, heard) = self.anim.step(rms);
        let bars = bars.to_vec();
        self.levels.set_vec(bars);
        self.ui.set_heard(heard);
        self.draw();
    }

    fn set_geometry(&mut self, position: &str, w: u32, h: u32, custom: (i32, i32)) {
        const M: i32 = 24;
        let resized = w != self.w || h != self.h;
        self.w = w;
        self.h = h;
        let (anchor, margins) = match position {
            "bottom_left" => (Anchor::BOTTOM | Anchor::LEFT, (0, 0, M, M)),
            "bottom_right" => (Anchor::BOTTOM | Anchor::RIGHT, (0, M, M, 0)),
            "top_center" => (Anchor::TOP, (M, 0, 0, 0)),
            "top_left" => (Anchor::TOP | Anchor::LEFT, (M, 0, 0, M)),
            "top_right" => (Anchor::TOP | Anchor::RIGHT, (M, M, 0, 0)),
            "custom" => (Anchor::TOP | Anchor::LEFT, (custom.1, 0, 0, custom.0)),
            _ /* bottom_center */ => (Anchor::BOTTOM, (0, 0, M, 0)),
        };
        self.layer.set_size(w, h);
        self.layer.set_anchor(anchor);
        self.layer
            .set_margin(margins.0, margins.1, margins.2, margins.3);
        let scale = self.scale.max(1);
        self.window
            .set_size(slint::PhysicalSize::new(w * scale as u32, h * scale as u32));
        if resized {
            // A buffer of the NEW size must not be attached until the
            // compositor acks with a configure — drawing early glitches or
            // kills the surface (the mid-resize disappearing pill).
            self.configured = false;
        }
        self.layer.commit();
    }

    fn apply_look(&mut self, v: &Value) {
        let c = |k: &str| -> slint::Color {
            v[k].as_str()
                .and_then(parse_hex)
                .unwrap_or(slint::Color::from_argb_u8(0, 0, 0, 0))
        };
        let f = |k: &str, d: f64| v[k].as_f64().unwrap_or(d) as f32;
        let ui = &self.ui;
        ui.set_pill_bg(c("pill_bg"));
        ui.set_pill_opacity(f("opacity", 0.94));
        ui.set_idle_color(c("idle"));
        ui.set_ok_color(c("ok"));
        ui.set_danger_color(c("danger"));
        ui.set_pill_border_color(c("border_color"));
        ui.set_pill_border_width(f("border_width", 0.0));
        ui.set_r_tl(f("r_tl", 18.0));
        ui.set_r_tr(f("r_tr", 18.0));
        ui.set_r_br(f("r_br", 18.0));
        ui.set_r_bl(f("r_bl", 3.0));
        ui.set_bar_width(f("bar_width", -1.0));
        ui.set_bar_spacing(f("bar_spacing", -1.0));
        ui.set_bar_radius(f("bar_radius", 0.0));
        ui.set_bar_fade(v["bar_fade"].as_bool().unwrap_or(true));
        ui.set_orb_size(f("orb_size", 0.55));
        ui.set_orb_dynamic(v["orb_dynamic"].as_bool().unwrap_or(false));
        ui.set_record_anim(v["record_anim"].as_str().unwrap_or("bars").into());
        ui.set_anim(v["anim"].as_str().unwrap_or("waves").into());
        ui.set_done_anim(v["done_anim"].as_str().unwrap_or("pop").into());
        ui.set_ring_color(c("done_ring"));
        ui.set_anim_speed(f("anim_speed", 1.0));
        if let Some(stops) = v["loader"].as_array() {
            let lc: Vec<slint::Color> = stops
                .iter()
                .filter_map(|s| s.as_str().and_then(parse_hex))
                .collect();
            ui.set_loader_colors(ModelRc::from(lc.as_slice()));
        }
        let count = v["bar_count"].as_u64().unwrap_or(24).clamp(8, 48) as usize;
        if self.levels.row_count() != count {
            self.anim.set_count(count);
            self.levels.set_vec(vec![0.0f32; count]);
        }
        if let Some(stops) = v["eq"].as_array() {
            let eq: Vec<slint::Color> = stops
                .iter()
                .filter_map(|s| s.as_str().and_then(parse_hex))
                .collect();
            ui.set_eq_colors(ModelRc::from(eq.as_slice()));
        }
        let w = v["width"].as_u64().unwrap_or(210) as u32;
        let h = v["height"].as_u64().unwrap_or(52) as u32;
        let pos = v["position"]
            .as_str()
            .unwrap_or("bottom_center")
            .to_string();
        let custom = (
            v["x"].as_i64().unwrap_or(0) as i32,
            v["y"].as_i64().unwrap_or(0) as i32,
        );
        self.set_geometry(&pos, w, h, custom);
        if self.visible && self.configured {
            self.draw();
        }
    }

    fn on_msg(&mut self, v: Value) {
        match v["cmd"].as_str().unwrap_or_default() {
            "look" => self.apply_look(&v),
            "show" => {
                self.visible = true;
                self.anim.reset();
                if let Some(m) = v["mode"].as_str() {
                    self.ui.set_mode(m.into());
                }
                // formatting step reuses the loader with its own animation
                if let Some(a) = v["anim"].as_str() {
                    self.ui.set_anim(a.into());
                }
                // preview shows loop the done entrance; real dictation
                // plays it once
                self.ui.set_demo_loop(v["demo"].as_bool().unwrap_or(false));
                self.draw();
            }
            "mode" => {
                if let Some(m) = v["mode"].as_str() {
                    self.ui.set_mode(m.into());
                }
            }
            "hide" => {
                self.visible = false;
                self.rms = 0.0;
                self.anim.reset();
                self.draw();
            }
            "level" => self.rms = v["rms"].as_f64().unwrap_or(0.0) as f32,
            "error" => self
                .ui
                .set_error_text(v["text"].as_str().unwrap_or("Error").into()),
            "quit" => self.exit = true,
            _ => {}
        }
    }
}

fn parse_hex(s: &str) -> Option<slint::Color> {
    let s = s.trim().strip_prefix('#')?;
    if s.len() != 6 {
        return None;
    }
    let v = u32::from_str_radix(s, 16).ok()?;
    Some(slint::Color::from_rgb_u8(
        (v >> 16) as u8,
        (v >> 8) as u8,
        v as u8,
    ))
}

fn main() {
    let Ok(conn) = Connection::connect_to_env() else {
        eprintln!("[overlay] no wayland display");
        std::process::exit(2);
    };
    let (globals, event_queue) = registry_queue_init::<Overlay>(&conn).expect("registry");
    let qh: QueueHandle<Overlay> = event_queue.handle();

    let compositor = CompositorState::bind(&globals, &qh).expect("wl_compositor");
    let Ok(layer_shell) = LayerShell::bind(&globals, &qh) else {
        // No zwlr_layer_shell_v1 (e.g. GNOME): parent falls back to a window.
        eprintln!("[overlay] layer-shell unavailable");
        std::process::exit(2);
    };
    let shm = Shm::bind(&globals, &qh).expect("wl_shm");
    let pool = SlotPool::new(210 * 52 * 4, &shm).expect("slot pool");

    // Slint: software renderer into our buffers, no windowing backend.
    let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    window.set_size(slint::PhysicalSize::new(210, 52));
    slint::platform::set_platform(Box::new(SwPlatform {
        window: window.clone(),
        start: Instant::now(),
    }))
    .expect("set slint platform");
    let ui = OverlayWindow::new().expect("create pill");
    let levels = Rc::new(VecModel::from(vec![0.0f32; 24]));
    ui.set_levels(ModelRc::from(Rc::clone(&levels)));
    ui.show().expect("show pill");

    let surface = compositor.create_surface(&qh);
    let layer =
        layer_shell.create_layer_surface(&qh, surface, Layer::Overlay, Some("macaw-overlay"), None);
    layer.set_anchor(Anchor::BOTTOM);
    layer.set_margin(0, 0, 24, 0);
    layer.set_size(210, 52);
    layer.set_exclusive_zone(-1); // sit over bars/panels, reserve nothing
    layer.set_keyboard_interactivity(KeyboardInteractivity::None);

    // Click-through: empty input region, forever.
    let region = Region::new(&compositor).expect("region");
    layer.set_input_region(Some(region.wl_region()));
    layer.commit(); // initial commit without buffer -> first configure

    let mut overlay = Overlay {
        registry_state: RegistryState::new(&globals),
        output_state: OutputState::new(&globals, &qh),
        shm,
        pool,
        layer,
        window,
        ui,
        levels,
        anim: BarAnim::new(24),
        w: 210,
        h: 52,
        scale: 1,
        rms: 0.0,
        visible: false,
        configured: false,
        exit: false,
    };

    // stdin -> calloop channel (thread does the blocking reads)
    let (tx, rx) = channel::<Value>();
    std::thread::spawn(move || {
        let stdin = std::io::stdin();
        for line in stdin.lock().lines().map_while(Result::ok) {
            if let Ok(v) = serde_json::from_str::<Value>(&line) {
                if tx.send(v).is_err() {
                    return;
                }
            }
        }
        // parent gone: ask the loop to exit
        let _ = tx.send(serde_json::json!({"cmd": "quit"}));
    });

    let mut event_loop: EventLoop<Overlay> = EventLoop::try_new().expect("event loop");
    let handle = event_loop.handle();
    handle
        .insert_source(rx, |ev, _, state| {
            if let ChanEvent::Msg(v) = ev {
                state.on_msg(v);
            } else {
                state.exit = true;
            }
        })
        .expect("stdin source");
    handle
        .insert_source(Timer::from_duration(TICK), |deadline, _, state| {
            state.tick();
            TimeoutAction::ToInstant(deadline + TICK)
        })
        .expect("timer source");
    WaylandSource::new(conn.clone(), event_queue)
        .insert(event_loop.handle())
        .expect("wayland source");

    let _keep_region = region;
    while !overlay.exit {
        // A mid-run wayland error (compositor restart, output reconfigure,
        // protocol hiccup) must not panic-abort: exit cleanly — the parent
        // notices the dead child and respawns or falls back to its window.
        if let Err(e) = event_loop.dispatch(Duration::from_millis(500), &mut overlay) {
            eprintln!("[overlay] wayland dispatch failed: {e}");
            std::process::exit(1);
        }
    }
}

// ── sctk plumbing ────────────────────────────────────────────────────

impl LayerShellHandler for Overlay {
    fn closed(&mut self, _c: &Connection, _q: &QueueHandle<Self>, _l: &LayerSurface) {
        self.exit = true;
    }
    fn configure(
        &mut self,
        _c: &Connection,
        _q: &QueueHandle<Self>,
        _l: &LayerSurface,
        cfg: LayerSurfaceConfigure,
        _serial: u32,
    ) {
        if cfg.new_size.0 != 0 {
            self.w = cfg.new_size.0;
        }
        if cfg.new_size.1 != 0 {
            self.h = cfg.new_size.1;
        }
        // Slint must render at the ACKED size — a mismatch under-sizes the
        // pixel buffer and panics the software renderer.
        let s = self.scale.max(1) as u32;
        self.window
            .set_size(slint::PhysicalSize::new(self.w * s, self.h * s));
        self.configured = true;
        self.draw();
    }
}

impl CompositorHandler for Overlay {
    fn scale_factor_changed(
        &mut self,
        _c: &Connection,
        _q: &QueueHandle<Self>,
        _s: &wl_surface::WlSurface,
        new_factor: i32,
    ) {
        self.scale = new_factor;
        let s = self.scale.max(1) as u32;
        self.window
            .set_size(slint::PhysicalSize::new(self.w * s, self.h * s));
        self.window
            .dispatch_event(slint::platform::WindowEvent::ScaleFactorChanged {
                scale_factor: s as f32,
            });
        self.draw();
    }
    fn transform_changed(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        _: &wl_surface::WlSurface,
        _: wl_output::Transform,
    ) {
    }
    fn frame(&mut self, _: &Connection, _: &QueueHandle<Self>, _: &wl_surface::WlSurface, _t: u32) {
    }
    fn surface_enter(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        _: &wl_surface::WlSurface,
        _: &wl_output::WlOutput,
    ) {
    }
    fn surface_leave(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        _: &wl_surface::WlSurface,
        _: &wl_output::WlOutput,
    ) {
    }
}

impl OutputHandler for Overlay {
    fn output_state(&mut self) -> &mut OutputState {
        &mut self.output_state
    }
    fn new_output(&mut self, _: &Connection, _: &QueueHandle<Self>, _: wl_output::WlOutput) {}
    fn update_output(&mut self, _: &Connection, _: &QueueHandle<Self>, _: wl_output::WlOutput) {}
    fn output_destroyed(&mut self, _: &Connection, _: &QueueHandle<Self>, _: wl_output::WlOutput) {}
}

impl ShmHandler for Overlay {
    fn shm_state(&mut self) -> &mut Shm {
        &mut self.shm
    }
}

impl ProvidesRegistryState for Overlay {
    fn registry(&mut self) -> &mut RegistryState {
        &mut self.registry_state
    }
    registry_handlers![OutputState];
}

delegate_compositor!(Overlay);
delegate_output!(Overlay);
delegate_shm!(Overlay);
delegate_layer!(Overlay);
delegate_registry!(Overlay);
