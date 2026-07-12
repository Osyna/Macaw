//! Macaw native frontend (Slint).
//!
//! One process, three background threads + the UI event loop:
//!   ws       — blocking WebSocket client to the engine
//!   single   — unix-socket single-instance server (argv forwarding)
//!   ksni     — tray service (spawned by ksni itself)
//! All UI access happens on the Slint event loop; background threads and RPC
//! replies marshal in via `upgrade_in_event_loop` and reach the app state
//! through a UI-thread-local handle (Rc — deliberately !Send).

mod engine;
mod hypr;
mod single;
mod theme;
mod tray;
mod ws;

slint::include_modules!();

use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::sync::mpsc::{channel, Sender};

use serde_json::{json, Map, Value};
use slint::{ComponentHandle, Model, ModelRc, SharedString, VecModel};

const WS_PORT: u16 = 47540;
const BAR_COUNT: usize = 24;

thread_local! {
    static APP: RefCell<Option<Rc<App>>> = const { RefCell::new(None) };
}

fn with_app(f: impl FnOnce(&Rc<App>)) {
    APP.with(|a| {
        if let Some(app) = a.borrow().as_ref() {
            f(app);
        }
    });
}

fn token() -> String {
    let mut buf = [0u8; 16];
    std::fs::File::open("/dev/urandom")
        .and_then(|mut f| std::io::Read::read_exact(&mut f, &mut buf))
        .expect("urandom");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

fn human_size(bytes: u64) -> String {
    match bytes {
        0 => String::new(),
        b if b >= 1 << 30 => format!("{:.1} GB", b as f64 / (1u64 << 30) as f64),
        b if b >= 1 << 20 => format!("{:.0} MB", b as f64 / (1u64 << 20) as f64),
        b => format!("{:.0} KB", b as f64 / 1024.0),
    }
}

/// {key: value} with a runtime key (json! needs literal keys).
fn kv(key: &str, value: Value) -> Value {
    let mut m = Map::new();
    m.insert(key.to_string(), value);
    Value::Object(m)
}

/// App-internal additions to the engine event stream: RPC results are routed
/// through the same pump so every UI mutation happens in one place.
enum Msg {
    Ws(ws::Event),
    ConfigLoaded(Value),
    DevicesLoaded(Value),
    ModelsLoaded(Vec<Value>),
    Cmd(single::Cmd),
}

// ── app state (UI thread only) ──────────────────────────────────────

struct App {
    ui: MainWindow,
    overlay: OverlayWindow,
    client: ws::Client,
    msg_tx: Sender<Msg>,
    engine: RefCell<engine::Engine>,
    tray: Option<tray::TrayHandle>,
    cfg: RefCell<Value>,
    devices: RefCell<Vec<(Option<i64>, String)>>, // (device_index, label)
    models_raw: RefCell<Vec<Value>>,
    op: RefCell<Option<(String, String, String, f32)>>, // op, key, msg, pct(-1 = indet)
    toasts: Rc<VecModel<Toast>>,
    levels: Rc<VecModel<f32>>,
    bars: RefCell<[f32; BAR_COUNT]>,
    rms: Cell<f32>,
    tick: Cell<u64>,
    level_timer: slint::Timer,
    preview_timer: slint::Timer,
    previewing: Cell<bool>,
}

impl App {
    // ── theme / look ────────────────────────────────────────────────
    fn apply_theme(&self) {
        let cfg = self.cfg.borrow();
        let name = cfg["theme"].as_str().unwrap_or("macaw").to_string();
        let t = theme::by_name(&name);
        self.ui.global::<Theme>().set_pal(Palette {
            bg: theme::rgb(t.bg),
            surface: theme::rgb(t.surface),
            control: theme::rgb(t.control),
            fg: theme::rgb(t.fg),
            muted: theme::rgb(t.muted),
            border: theme::rgb(t.border),
            accent: cfg["accent_color"]
                .as_str()
                .and_then(theme::parse_hex)
                .unwrap_or(theme::rgb(t.accent)),
            accent_fg: theme::rgb(t.accent_fg),
            ok: theme::rgb(t.ok),
            warn: theme::rgb(t.warn),
            danger: theme::rgb(t.danger),
            overlay_bg: theme::rgb(t.overlay_bg),
            eq_idle: theme::rgb(t.eq_idle),
        });

        // overlay look: theme + config overrides, pre-resolved
        let o = &self.overlay;
        o.set_pill_bg(theme::rgb(t.overlay_bg));
        o.set_pill_opacity(cfg["overlay_opacity"].as_f64().unwrap_or(0.94) as f32);
        o.set_idle_color(theme::rgb(t.eq_idle));
        o.set_ok_color(theme::rgb(t.ok));
        o.set_danger_color(theme::rgb(t.danger));
        let eq = theme::eq_colors(t, &cfg);
        o.set_eq_colors(ModelRc::from(eq.as_slice()));
        let c = theme::corners(t, &cfg);
        o.set_r_tl(c[0]);
        o.set_r_tr(c[1]);
        o.set_r_br(c[2]);
        o.set_r_bl(c[3]);
        let bw = cfg["border_width"].as_i64().unwrap_or(0) as f32;
        o.set_pill_border_width(bw);
        o.set_pill_border_color(if bw > 0.0 {
            cfg["border_color"]
                .as_str()
                .and_then(theme::parse_hex)
                .unwrap_or(theme::rgb(t.border_color))
        } else {
            slint::Color::from_argb_u8(0, 0, 0, 0)
        });
        let bar_w = cfg["bar_width"].as_i64().unwrap_or(-1);
        let bar_s = cfg["bar_spacing"].as_i64().unwrap_or(-1);
        o.set_bar_width(if bar_w >= 1 { bar_w as f32 } else { 4.0 });
        o.set_bar_spacing(if bar_s >= 0 { bar_s as f32 } else { 3.0 });
        o.set_bar_radius(cfg["bar_radius"].as_i64().unwrap_or(0) as f32);
        o.set_bar_fade(cfg["bar_fade"].as_bool().unwrap_or(true));
    }

    // ── config → Cfg struct ─────────────────────────────────────────
    fn apply_config(&self) {
        let cfg = self.cfg.borrow();
        let s = |k: &str| SharedString::from(cfg[k].as_str().unwrap_or_default());
        let f = |k: &str, d: f64| cfg[k].as_f64().unwrap_or(d) as f32;
        let b = |k: &str, d: bool| cfg[k].as_bool().unwrap_or(d);
        let eq_join = cfg["eq_colors"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>()
                    .join(", ")
            })
            .unwrap_or_default();
        self.ui.set_cfg(Cfg {
            language: s("language"),
            output_mode: s("output_mode"),
            device_label: SharedString::new(),
            silence_timeout: f("silence_timeout", 3.0),
            sound_enabled: b("sound_enabled", true),
            streaming: b("streaming", false),
            punctuation_hints: b("punctuation_hints", true),
            hotkey_enabled: b("hotkey_enabled", false),
            hotkey: s("hotkey"),
            theme: s("theme"),
            window_position: s("window_position"),
            overlay_opacity: f("overlay_opacity", 0.94),
            overlay_width: f("overlay_width", 210.0),
            overlay_height: f("overlay_height", 52.0),
            overlay_x: SharedString::from(cfg["overlay_x"].as_i64().unwrap_or(0).to_string()),
            overlay_y: SharedString::from(cfg["overlay_y"].as_i64().unwrap_or(0).to_string()),
            corner_radius: f("corner_radius", -1.0),
            bar_spacing: f("bar_spacing", -1.0),
            bar_width: f("bar_width", -1.0),
            bar_radius: f("bar_radius", 0.0),
            bar_fade: b("bar_fade", true),
            eq_colors: SharedString::from(eq_join),
            accent_color: s("accent_color"),
            border_color: s("border_color"),
            border_width: f("border_width", 0.0),
            api_key_set: cfg["openai_api_key"]
                .as_str()
                .map(|k| !k.is_empty())
                .unwrap_or(false),
            proxy: s("proxy"),
            ssl_verify: b("ssl_verify", true),
            autostart: autostart_path().exists(),
        });
        let want = cfg["device_index"].as_i64();
        let idx = self
            .devices
            .borrow()
            .iter()
            .position(|(i, _)| *i == want)
            .unwrap_or(0);
        self.ui.set_device_current(idx as i32);
        drop(cfg);
        self.apply_theme();
    }

    fn patch(&self, patch: Value) {
        self.client
            .call("config.set", json!({ "patch": patch }), None);
    }

    // ── models ──────────────────────────────────────────────────────
    fn refresh_models(&self) {
        let tx = self.msg_tx.clone();
        self.client.call(
            "models.list",
            json!({}),
            Some(Box::new(move |res| {
                if let Ok(Value::Array(list)) = res {
                    let _ = tx.send(Msg::ModelsLoaded(list));
                }
            })),
        );
    }

    fn render_models(&self) {
        let raw = self.models_raw.borrow();
        let op = self.op.borrow();
        let rows: Vec<ModelRow> = raw
            .iter()
            .map(|m| {
                let s = |k: &str| SharedString::from(m[k].as_str().unwrap_or_default());
                let id = m["id"].as_str().unwrap_or_default();
                let extra = m["extra"].as_str().unwrap_or_default();
                let busy = op
                    .as_ref()
                    .map(|(o, key, ..)| {
                        (o == "download" && key == id) || (o == "install" && key == extra)
                    })
                    .unwrap_or(false);
                let (msg, pct) = op
                    .as_ref()
                    .map(|(_, _, m, p)| (m.clone(), *p))
                    .unwrap_or_default();
                ModelRow {
                    id: s("id"),
                    label: s("label"),
                    backend: s("backend"),
                    size: s("size"),
                    speed: s("speed"),
                    languages: s("languages"),
                    hardware: s("hardware"),
                    vram: s("vram"),
                    streaming: m["streaming"].as_bool().unwrap_or(false),
                    cloud: m["cloud"].as_bool().unwrap_or(false),
                    recommended: m["recommended"].as_bool().unwrap_or(false),
                    rating: m["rating"].as_i64().unwrap_or(0) as i32,
                    pros: s("pros"),
                    cons: s("cons"),
                    notes: s("notes"),
                    available: m["available"].as_bool().unwrap_or(false),
                    installed: m["installed"].as_bool().unwrap_or(false),
                    ready: m["ready"].as_bool().unwrap_or(false),
                    active: m["active"].as_bool().unwrap_or(false),
                    extra: SharedString::from(extra),
                    disk_size: SharedString::from(human_size(m["disk_size"].as_u64().unwrap_or(0))),
                    api_key_set: m["api_key_set"].as_bool().unwrap_or(false),
                    busy,
                    progress_pct: if busy { pct } else { -1.0 },
                    progress_msg: SharedString::from(if busy { msg } else { String::new() }),
                }
            })
            .collect();
        self.ui.set_op_running(op.is_some());
        self.ui.set_models(ModelRc::from(rows.as_slice()));
        let label = raw
            .iter()
            .find(|m| m["active"].as_bool().unwrap_or(false))
            .and_then(|m| m["label"].as_str())
            .unwrap_or("");
        self.ui.set_active_model_label(SharedString::from(label));
    }

    // ── overlay ─────────────────────────────────────────────────────
    fn overlay_geometry(&self) -> (i32, i32, i32, i32) {
        let cfg = self.cfg.borrow();
        let w = cfg["overlay_width"].as_i64().unwrap_or(210) as i32;
        let h = cfg["overlay_height"].as_i64().unwrap_or(52) as i32;
        let pos = cfg["window_position"]
            .as_str()
            .unwrap_or("bottom_center")
            .to_string();
        let custom = (
            cfg["overlay_x"].as_i64().unwrap_or(0) as i32,
            cfg["overlay_y"].as_i64().unwrap_or(0) as i32,
        );
        drop(cfg);
        let (x, y) = hypr::focused_monitor()
            .map(|m| hypr::anchor_xy(&pos, w, h, custom, &m))
            .unwrap_or((0, 0));
        (x, y, w, h)
    }

    fn show_overlay(self: &Rc<Self>, mode: &str) {
        let visible = self.overlay.window().is_visible();
        self.overlay.set_mode(mode.into());
        if !visible {
            let (x, y, w, h) = self.overlay_geometry();
            hypr::install_rules(x, y, w, h);
            self.overlay
                .window()
                .set_size(slint::LogicalSize::new(w as f32, h as f32));
            let _ = self.overlay.show();
        }
        if !self.level_timer.running() {
            let weak = Rc::downgrade(self);
            self.level_timer.start(
                slint::TimerMode::Repeated,
                std::time::Duration::from_millis(33),
                move || {
                    if let Some(app) = weak.upgrade() {
                        app.tick_bars();
                    }
                },
            );
        }
    }

    fn hide_overlay(&self) {
        self.level_timer.stop();
        self.previewing.set(false);
        self.preview_timer.stop();
        let _ = self.overlay.hide();
        *self.bars.borrow_mut() = [0.0; BAR_COUNT];
        self.rms.set(0.0);
    }

    /// 30 Hz bar animation: center-weighted bell + per-bar shimmer, with the
    /// old overlay's asymmetric attack/decay smoothing.
    fn tick_bars(&self) {
        let t = self.tick.get().wrapping_add(1);
        self.tick.set(t);
        let rms = if self.previewing.get() {
            (0.55 + 0.4 * ((t as f32) * 0.11).sin()).clamp(0.0, 1.0)
        } else {
            self.rms.get()
        };
        let mut bars = self.bars.borrow_mut();
        let c = (BAR_COUNT as f32 - 1.0) / 2.0;
        for (i, bar) in bars.iter_mut().enumerate() {
            let d = (i as f32 - c) / c; // -1..1
            let bell = 0.35 + 0.65 * (-2.2 * d * d).exp();
            let shimmer = 0.72 + 0.28 * ((1.7 * i as f32) + (t as f32) * 0.43).sin();
            let target = (rms.powf(0.85) * bell * shimmer).clamp(0.0, 1.0);
            let k = if target > *bar { 0.32 } else { 0.18 };
            *bar += (target - *bar) * k;
        }
        self.levels.set_vec(bars.to_vec());
    }

    // ── message handling (UI thread) ────────────────────────────────
    fn on_msg(self: &Rc<Self>, msg: Msg) {
        match msg {
            Msg::Ws(ev) => self.on_event(ev),
            Msg::ConfigLoaded(v) => {
                self.cfg.replace(v["config"].clone());
                self.apply_config();
            }
            Msg::DevicesLoaded(devs) => {
                let mut list = vec![(None, "System default".to_string())];
                if let Some(arr) = devs.as_array() {
                    for d in arr {
                        list.push((
                            d["index"].as_i64(),
                            d["name"].as_str().unwrap_or("?").to_string(),
                        ));
                    }
                }
                let labels: Vec<SharedString> = list
                    .iter()
                    .map(|(_, l)| SharedString::from(l.as_str()))
                    .collect();
                self.devices.replace(list);
                self.ui.set_devices(ModelRc::from(labels.as_slice()));
                self.apply_config();
            }
            Msg::ModelsLoaded(list) => {
                self.models_raw.replace(list);
                self.render_models();
            }
            Msg::Cmd(cmd) => self.on_cmd(cmd),
        }
    }

    fn on_event(self: &Rc<Self>, ev: ws::Event) {
        match ev {
            ws::Event::Connected => {
                self.ui.set_engine_connected(true);
                let tx = self.msg_tx.clone();
                self.client.call(
                    "config.get",
                    json!({}),
                    Some(Box::new(move |res| {
                        if let Ok(v) = res {
                            let _ = tx.send(Msg::ConfigLoaded(v));
                        }
                    })),
                );
                let tx = self.msg_tx.clone();
                self.client.call(
                    "devices.list",
                    json!({}),
                    Some(Box::new(move |res| {
                        if let Ok(v) = res {
                            let _ = tx.send(Msg::DevicesLoaded(v));
                        }
                    })),
                );
                self.refresh_models();
            }
            ws::Event::Disconnected => self.ui.set_engine_connected(false),
            ws::Event::State { state, detail } => {
                self.ui.set_engine_state(SharedString::from(state.as_str()));
                if let Some(t) = &self.tray {
                    let rec = state == "recording";
                    t.update(move |tr| tr.recording = rec);
                }
                match state.as_str() {
                    "recording" => self.show_overlay("eq"),
                    "transcribing" => self.show_overlay("loader"),
                    "done" => self.show_overlay("done"),
                    "error" if !detail.is_empty() => {
                        self.overlay
                            .set_error_text(SharedString::from(detail.as_str()));
                        self.show_overlay("error");
                    }
                    _ => self.hide_overlay(), // idle / loading / detail-less error
                }
            }
            ws::Event::Level { rms } => self.rms.set(rms),
            ws::Event::Config { config } => {
                self.cfg.replace(config);
                self.apply_config();
                if self.overlay.window().is_visible() {
                    let (x, y, w, h) = self.overlay_geometry();
                    hypr::install_rules(x, y, w, h);
                    self.overlay
                        .window()
                        .set_size(slint::LogicalSize::new(w as f32, h as f32));
                    hypr::move_mapped(x, y);
                }
            }
            ws::Event::Models => self.refresh_models(),
            ws::Event::Progress {
                op,
                key,
                msg,
                pct,
                done,
                ok,
            } => {
                if done {
                    self.op.replace(None);
                    let level = if ok == Some(true) { "success" } else { "error" };
                    self.toast(level, &msg);
                } else {
                    self.op.replace(Some((op, key, msg, pct.unwrap_or(-1.0))));
                }
                self.render_models();
            }
            ws::Event::Toast { level, msg } => self.toast(&level, &msg),
            ws::Event::Show { window } => self.present(&window),
            ws::Event::HotkeyCaptured { spec } => {
                self.ui.set_capturing_hotkey(false);
                self.patch(json!({ "hotkey": spec, "hotkey_enabled": true }));
            }
        }
    }

    fn toast(&self, level: &str, msg: &str) {
        self.toasts.push(Toast {
            level: level.into(),
            msg: msg.into(),
        });
        let toasts = Rc::clone(&self.toasts);
        slint::Timer::single_shot(std::time::Duration::from_secs(4), move || {
            if toasts.row_count() > 0 {
                toasts.remove(0);
            }
        });
    }

    fn present(&self, tab: &str) {
        if tab == "settings" || tab == "models" {
            self.ui.set_tab(tab.into());
        }
        let _ = self.ui.show();
    }

    fn on_cmd(self: &Rc<Self>, cmd: single::Cmd) {
        match cmd {
            single::Cmd::Show => self.present(""),
            single::Cmd::Settings => self.present("settings"),
            single::Cmd::Models => self.present("models"),
            single::Cmd::Trigger => self.client.call("record.toggle", json!({}), None),
            single::Cmd::Stop => self.quit(),
        }
    }

    fn quit(&self) {
        self.client.call("quit", json!({}), None);
        self.client.shutdown();
        self.engine.borrow_mut().kill();
        single::release();
        let _ = slint::quit_event_loop();
    }
}

fn autostart_path() -> std::path::PathBuf {
    let base = std::env::var("XDG_CONFIG_HOME")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| {
            std::path::PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".config")
        });
    base.join("autostart/macaw.desktop")
}

fn set_autostart(on: bool) {
    let path = autostart_path();
    if !on {
        let _ = std::fs::remove_file(&path);
        return;
    }
    if let Ok(exe) = std::env::current_exe() {
        let _ = std::fs::create_dir_all(path.parent().unwrap());
        let _ = std::fs::write(
            &path,
            format!(
                "[Desktop Entry]\nType=Application\nName=Macaw\nExec={}\nX-GNOME-Autostart-enabled=true\n",
                exe.display()
            ),
        );
    }
}

fn main() {
    let flag = std::env::args().skip(1).find(|a| a.starts_with("--"));

    // Single instance: forward argv and exit if an instance already runs.
    let (cmd_tx, cmd_rx) = channel::<single::Cmd>();
    let Some(_lock) = single::acquire(flag.as_deref(), cmd_tx.clone()) else {
        return;
    };
    if flag.as_deref() == Some("--stop") {
        single::release(); // nothing was running
        return;
    }

    // Per-window Wayland app_id via creation order: 0 = main, 1 = overlay.
    let counter = Cell::new(0u32);
    slint::BackendSelector::new()
        .backend_name("winit".into())
        .with_winit_window_attributes_hook(move |attrs| {
            use slint::winit_030::winit::platform::wayland::WindowAttributesExtWayland;
            let i = counter.get();
            counter.set(i + 1);
            let app_id = if i == 0 { "macaw" } else { hypr::OVERLAY_TITLE };
            attrs.with_name(app_id, "")
        })
        .select()
        .expect("select winit backend");

    let ui = MainWindow::new().expect("create main window"); // adapter #0
    let overlay = OverlayWindow::new().expect("create overlay"); // adapter #1

    let tok = token();
    let eng = engine::Engine::spawn(&tok, WS_PORT);
    let (msg_tx, msg_rx) = channel::<Msg>();
    let (ev_tx, ev_rx) = channel::<ws::Event>();
    let client = ws::spawn(WS_PORT, tok, ev_tx);

    let toasts = Rc::new(VecModel::from(Vec::<Toast>::new()));
    ui.set_toasts(ModelRc::from(Rc::clone(&toasts)));
    let levels = Rc::new(VecModel::from(vec![0.0f32; BAR_COUNT]));
    overlay.set_levels(ModelRc::from(Rc::clone(&levels)));

    let app = Rc::new(App {
        tray: tray::spawn(cmd_tx),
        ui,
        overlay,
        client,
        msg_tx: msg_tx.clone(),
        engine: RefCell::new(eng),
        cfg: RefCell::new(Value::Null),
        devices: RefCell::new(vec![(None, "System default".into())]),
        models_raw: RefCell::new(vec![]),
        op: RefCell::new(None),
        toasts,
        levels,
        bars: RefCell::new([0.0; BAR_COUNT]),
        rms: Cell::new(0.0),
        tick: Cell::new(0),
        level_timer: slint::Timer::default(),
        preview_timer: slint::Timer::default(),
        previewing: Cell::new(false),
    });
    APP.with(|a| *a.borrow_mut() = Some(Rc::clone(&app)));

    // ── UI callbacks → engine RPCs ──────────────────────────────────
    {
        let a = Rc::clone(&app);
        app.ui.on_set_str(move |k, v| {
            let key = k.to_string();
            let val = v.to_string();
            let patch = match key.as_str() {
                "overlay_x" | "overlay_y" => kv(&key, json!(val.parse::<i64>().unwrap_or(0))),
                "eq_colors" => {
                    let list: Vec<&str> = val
                        .split(',')
                        .map(str::trim)
                        .filter(|s| !s.is_empty())
                        .collect();
                    kv(&key, json!(list))
                }
                _ => kv(&key, json!(val)),
            };
            a.patch(patch);
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_set_num(move |k, v| {
            let key = k.to_string();
            let patch = if ["silence_timeout", "overlay_opacity"].contains(&key.as_str()) {
                kv(&key, json!((v as f64 * 100.0).round() / 100.0))
            } else {
                kv(&key, json!(v as i64))
            };
            a.patch(patch);
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui
            .on_set_bool(move |k, v| a.patch(kv(k.as_str(), json!(v))));
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_pick_device(move |i| {
            let idx = a.devices.borrow().get(i as usize).and_then(|(di, _)| *di);
            a.patch(kv("device_index", json!(idx)));
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_capture_hotkey(move || {
            a.ui.set_capturing_hotkey(true);
            a.client.call("hotkey.capture_start", json!({}), None);
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_preview_overlay(move || {
            a.previewing.set(true);
            a.show_overlay("eq");
            let weak = Rc::downgrade(&a);
            a.preview_timer.start(
                slint::TimerMode::SingleShot,
                std::time::Duration::from_secs(3),
                move || {
                    if let Some(app) = weak.upgrade() {
                        app.hide_overlay();
                    }
                },
            );
        });
    }
    app.ui.on_set_autostart(set_autostart);
    {
        let a = Rc::clone(&app);
        app.ui
            .on_set_api_key(move |v| a.patch(kv("openai_api_key", json!(v.to_string()))));
    }
    {
        let a = Rc::clone(&app);
        app.ui
            .on_toggle_recording(move || a.client.call("record.toggle", json!({}), None));
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_install(move |extra| {
            a.client.call(
                "models.install",
                json!({ "extra": extra.to_string() }),
                None,
            );
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_download(move |id| {
            a.client
                .call("models.download", json!({ "id": id.to_string() }), None);
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_activate(move |id| {
            a.client
                .call("models.set_active", json!({ "id": id.to_string() }), None);
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_delete(move |id| {
            a.client
                .call("models.delete", json!({ "id": id.to_string() }), None);
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui
            .on_cancel_op(move || a.client.call("models.cancel", json!({}), None));
    }

    // Tray app: closing the main window hides it.
    {
        let a = Rc::clone(&app);
        app.ui.window().on_close_requested(move || {
            let _ = a.ui.hide();
            slint::CloseRequestResponse::HideWindow
        });
    }

    // ── pumps: ws events + commands → the one UI-thread handler ────
    {
        let ui = app.ui.as_weak();
        std::thread::spawn(move || {
            for ev in ev_rx {
                let _ = ui.upgrade_in_event_loop(move |_| with_app(|a| a.on_msg(Msg::Ws(ev))));
            }
        });
    }
    {
        let ui = app.ui.as_weak();
        std::thread::spawn(move || {
            for cmd in cmd_rx {
                let _ = ui.upgrade_in_event_loop(move |_| with_app(|a| a.on_msg(Msg::Cmd(cmd))));
            }
        });
    }
    {
        // RPC results posted onto msg_tx by reply closures (ws thread).
        let ui = app.ui.as_weak();
        std::thread::spawn(move || {
            for msg in msg_rx {
                let _ = ui.upgrade_in_event_loop(move |_| with_app(|a| a.on_msg(msg)));
            }
        });
    }

    if flag.as_deref() == Some("--models") {
        app.ui.set_tab("models".into());
    }
    if flag.as_deref() != Some("--trigger") {
        let _ = app.ui.show();
    }

    slint::run_event_loop_until_quit().expect("event loop");
    APP.with(|a| a.borrow_mut().take());
    app.engine.borrow_mut().kill();
    single::release();
}
