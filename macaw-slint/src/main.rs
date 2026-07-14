//! Macaw native frontend (Slint).
//!
//! One process, three background threads + the UI event loop:
//!   ws       — blocking WebSocket client to the engine
//!   single   — single-instance server (unix socket / loopback TCP), argv fwd
//!   tray     — ksni service (unix) / Win32 notification icon (windows)
//! All UI access happens on the Slint event loop; background threads and RPC
//! replies marshal in via `upgrade_in_event_loop` and reach the app state
//! through a UI-thread-local handle (Rc — deliberately !Send).
#![cfg_attr(windows, windows_subsystem = "windows")]

mod bars;
mod engine;
mod hypr;
mod ls;
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
/// Gallery tiles render this fixed downsample instead of the full bar_count.
const DEMO_BARS: usize = 12;
use bars::BarAnim;

/// Per-model spoken-language choices (parity with the old manager).
const LANGS: [(&str, &str); 11] = [
    ("English", "en"),
    ("French", "fr"),
    ("German", "de"),
    ("Spanish", "es"),
    ("Italian", "it"),
    ("Portuguese", "pt"),
    ("Dutch", "nl"),
    ("Polish", "pl"),
    ("Russian", "ru"),
    ("Japanese", "ja"),
    ("Chinese", "zh"),
];

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
    // getrandom: OS entropy on every platform (BCryptGenRandom on Windows —
    // the old /dev/urandom read panicked there before the first window).
    getrandom::fill(&mut buf).expect("OS entropy");
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

fn hex(c: slint::Color) -> String {
    format!("#{:02X}{:02X}{:02X}", c.red(), c.green(), c.blue())
}

/// Update a color model in place when the length matches — wholesale
/// `set_vec` re-instantiates every `for`-item, which tears down any popup
/// (color picker) living near them mid-edit.
fn set_colors(model: &Rc<VecModel<slint::Color>>, new: Vec<slint::Color>) {
    use slint::Model;
    if model.row_count() == new.len() {
        for (i, c) in new.into_iter().enumerate() {
            if model.row_data(i) != Some(c) {
                model.set_row_data(i, c);
            }
        }
    } else {
        model.set_vec(new);
    }
}

/// App-internal additions to the engine event stream: RPC results are routed
/// through the same pump so every UI mutation happens in one place.
enum Msg {
    Ws(ws::Event),
    ConfigLoaded(Value),
    SystemInfo(Value),
    DevicesLoaded(Value),
    ModelsLoaded(Vec<Value>),
    LlmModelsLoaded(Vec<Value>),
    LlmTest(String, String),
    ProvidersLoaded(Vec<Value>),
    ProviderTest(String),
    FormatterStatus(Value),
    Cmd(single::Cmd),
}

// ── app state (UI thread only) ──────────────────────────────────────

struct App {
    ui: MainWindow,
    overlay: OverlayWindow,
    client: ws::Client,
    msg_tx: Sender<Msg>,
    engine: RefCell<engine::Engine>,
    token: String,       // engine auth token — reused when the engine is restarted
    mic_mon: Cell<bool>, // idle mic meter active (Settings tab visible)
    tray: Option<tray::TrayHandle>,
    cfg: RefCell<Value>,
    devices: RefCell<Vec<(Option<i64>, String)>>, // (device_index, label)
    models_raw: RefCell<Vec<Value>>,
    llm_models_raw: RefCell<Vec<Value>>,
    providers_raw: RefCell<Vec<Value>>,
    provider_sel: Cell<i32>,
    op: RefCell<Option<(String, String, String, f32)>>, // op, key, msg, pct(-1 = indet)
    toasts: Rc<VecModel<Toast>>,
    levels: Rc<VecModel<f32>>,
    demo: Rc<VecModel<f32>>, // fixed 12-bar downsample for gallery tiles
    eq: Rc<VecModel<slint::Color>>,
    trans: Rc<VecModel<slint::Color>>, // resolved transcribing stops
    anim: RefCell<BarAnim>,
    rms: Cell<f32>,
    ls: RefCell<Option<ls::LsOverlay>>,
    ls_respawns: Cell<u32>, // layer-shell child revival budget (crash-loop guard)
    level_timer: slint::Timer,
    recording: Cell<bool>,
    pinned: Cell<bool>,            // "show indicator" live-edit toggle in Settings
    preview_mode: RefCell<String>, // state chip selected in Appearance
    expanded: RefCell<String>,     // model id with the open dossier ("" = none)
    search: RefCell<String>,
    filter: Cell<i32>, // -1 none / 0 For you / 1 Light / 2 Cloud / 3 Live
}

impl App {
    // ── theme / look ────────────────────────────────────────────────
    fn apply_theme(&self) {
        let cfg = self.cfg.borrow();
        let name = theme::base_name(&cfg);
        let t = theme::by_name(&name); // INDICATOR look only
                                       // app chrome: minimal terminal, dark (real black) or light
        let ch = if cfg["app_theme"].as_str().unwrap_or("dark") == "light" {
            &theme::CHROME_LIGHT
        } else {
            &theme::CHROME_DARK
        };
        let tg = self.ui.global::<Theme>();
        tg.set_repaint_flip(!tg.get_repaint_flip());
        tg.set_pal(Palette {
            bg: theme::rgb(ch.bg),
            surface: theme::rgb(ch.surface),
            control: theme::rgb(ch.control),
            fg: theme::rgb(ch.fg),
            muted: theme::rgb(ch.muted),
            border: theme::rgb(ch.border),
            accent: theme::rgb(ch.accent),
            accent_fg: theme::rgb(ch.accent_fg),
            ok: theme::rgb(ch.ok),
            warn: theme::rgb(ch.warn),
            danger: theme::rgb(ch.danger),
            overlay_bg: theme::rgb(t.overlay_bg),
            eq_idle: theme::rgb(t.eq_idle),
        });

        // overlay look: theme + config overrides, resolved once, pushed to
        // both the real overlay window and the settings preview (Look global).
        // Unset fields fall back to the BASE THEME's defaults (theme_defaults),
        // not the global pristine values — oled ships its own bar geometry.
        let td: std::collections::HashMap<&str, Value> =
            theme::theme_defaults(&name).into_iter().collect();
        let di = |k: &str| td[k].as_i64().unwrap_or(0);
        let db = |k: &str| td[k].as_bool().unwrap_or(true);
        let ds = |k: &str| td[k].as_str().unwrap_or("").to_string();
        set_colors(&self.eq, theme::eq_colors(t, &cfg));
        let c = theme::corners(t, &cfg);
        let bw = cfg["border_width"].as_i64().unwrap_or(0) as f32;
        let border_color = if bw > 0.0 {
            cfg["border_color"]
                .as_str()
                .and_then(theme::parse_hex)
                .unwrap_or(theme::rgb(t.border_color))
        } else {
            slint::Color::from_argb_u8(0, 0, 0, 0)
        };
        let bar_w = cfg["bar_width"].as_i64().unwrap_or(di("bar_width")) as f32;
        let bar_s = cfg["bar_spacing"].as_i64().unwrap_or(di("bar_spacing")) as f32;
        let opacity = cfg["overlay_opacity"].as_f64().unwrap_or(0.94) as f32;
        let bar_radius = cfg["bar_radius"].as_i64().unwrap_or(di("bar_radius")) as f32;
        let bar_fade = cfg["bar_fade"].as_bool().unwrap_or(db("bar_fade"));
        let anim = cfg["transcribe_anim"]
            .as_str()
            .unwrap_or("waves")
            .to_string();
        let record_anim = cfg["record_anim"]
            .as_str()
            .map(str::to_string)
            .unwrap_or_else(|| ds("record_anim"));
        let done_anim = cfg["done_anim"].as_str().unwrap_or("pop").to_string();
        let pill_bg = cfg["overlay_bg"]
            .as_str()
            .and_then(theme::parse_hex)
            .unwrap_or(theme::rgb(t.overlay_bg));
        let count = cfg["bar_count"]
            .as_u64()
            .unwrap_or(di("bar_count") as u64)
            .clamp(8, 48) as usize;
        if self.levels.row_count() != count {
            self.anim.borrow_mut().set_count(count);
            self.levels.set_vec(vec![0.0f32; count]);
        }
        // per-state colors: transcribing follows recording unless unlinked
        let trans_linked = cfg["trans_link"].as_bool().unwrap_or(true);
        let own: Vec<slint::Color> = cfg["trans_colors"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|v| v.as_str().and_then(theme::parse_hex))
                    .collect()
            })
            .unwrap_or_default();
        let trans: Vec<slint::Color> = if trans_linked || own.is_empty() {
            self.eq.iter().collect()
        } else {
            own
        };
        set_colors(&self.trans, trans);
        let done_color = cfg["done_color"]
            .as_str()
            .and_then(theme::parse_hex)
            .unwrap_or(theme::rgb(t.ok));
        let ring_color = cfg["done_ring"]
            .as_str()
            .and_then(theme::parse_hex)
            .unwrap_or(pill_bg); // default: disk matches the pill
        let error_color = cfg["error_color"]
            .as_str()
            .and_then(theme::parse_hex)
            .unwrap_or(theme::rgb(t.danger));
        let anim_speed = cfg["anim_speed"].as_f64().unwrap_or(1.0).clamp(0.25, 3.0) as f32;

        // the winit-fallback overlay and the settings preview (Look global)
        // receive the exact same resolved look — one list, applied to both
        let o = &self.overlay;
        let look = self.ui.global::<Look>();
        macro_rules! push {
            ($($setter:ident($v:expr);)*) => {
                $( let v = $v; o.$setter(v.clone()); look.$setter(v); )*
            };
        }
        push! {
            set_pill_bg(pill_bg);
            set_pill_opacity(opacity);
            set_idle_color(theme::rgb(t.eq_idle));
            set_ok_color(done_color);
            set_danger_color(error_color);
            set_eq_colors(ModelRc::from(Rc::clone(&self.eq)));
            set_record_anim(SharedString::from(record_anim.as_str()));
            set_anim(SharedString::from(anim.as_str()));
            set_done_anim(SharedString::from(done_anim.as_str()));
            set_ring_color(ring_color);
            set_anim_speed(anim_speed);
            set_loader_colors(ModelRc::from(Rc::clone(&self.trans)));
            set_r_tl(c[0]);
            set_r_tr(c[1]);
            set_r_br(c[2]);
            set_r_bl(c[3]);
            set_pill_border_width(bw);
            set_pill_border_color(border_color);
            set_bar_width(bar_w);
            set_bar_spacing(bar_s);
            set_bar_radius(bar_radius);
            set_bar_fade(bar_fade);
        }
        look.set_pv_w(cfg["overlay_width"].as_f64().unwrap_or(210.0) as f32);
        look.set_pv_h(cfg["overlay_height"].as_f64().unwrap_or(52.0) as f32);
        look.set_levels(ModelRc::from(Rc::clone(&self.levels)));

        // layer-shell overlay process gets the same resolved look + geometry
        let eq_hex: Vec<String> = self.eq.iter().map(hex).collect();
        let trans_hex: Vec<String> = self.trans.iter().map(hex).collect();
        self.ls_send(json!({
            "cmd": "look",
            "pill_bg": hex(pill_bg),
            "opacity": opacity,
            "idle": format!("#{:06X}", t.eq_idle),
            "ok": hex(done_color),
            "danger": hex(error_color),
            "border_color": hex(border_color),
            "border_width": bw,
            "r_tl": c[0], "r_tr": c[1], "r_br": c[2], "r_bl": c[3],
            "bar_width": bar_w, "bar_spacing": bar_s, "bar_radius": bar_radius,
            "bar_fade": bar_fade,
            "record_anim": record_anim,
            "anim": anim,
            "done_anim": done_anim,
            "done_ring": hex(ring_color),
            "anim_speed": anim_speed,
            "bar_count": count,
            "eq": eq_hex,
            "loader": trans_hex,
            "width": cfg["overlay_width"].as_u64().unwrap_or(210),
            "height": cfg["overlay_height"].as_u64().unwrap_or(52),
            "position": cfg["window_position"].as_str().unwrap_or("bottom_center"),
            "x": cfg["overlay_x"].as_i64().unwrap_or(0),
            "y": cfg["overlay_y"].as_i64().unwrap_or(0),
        }));
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
        let t = theme::by_name(&theme::base_name(&cfg));
        let corners = theme::corners(t, &cfg);
        // unset override fields display their base theme's defaults
        let td: std::collections::HashMap<&str, Value> =
            theme::theme_defaults(&theme::base_name(&cfg))
                .into_iter()
                .collect();
        let tdf = |k: &str| td[k].as_f64().unwrap_or(0.0) as f32;
        let lang_idx = LANGS
            .iter()
            .position(|(_, c)| *c == cfg["language"].as_str().unwrap_or("en"))
            .unwrap_or(0) as i32;
        self.ui.set_default_lang_current(lang_idx);
        self.ui.set_cfg(Cfg {
            language: s("language"),
            output_mode: s("output_mode"),
            device_label: SharedString::new(),
            silence_timeout: f("silence_timeout", 3.0),
            auto_stop: b("auto_stop", true),
            vad_gate: b("vad_gate", true),
            level_gain: f("level_gain", 1.0),
            sound_enabled: b("sound_enabled", true),
            onboarded: b("onboarded", true),
            silence_level: f("silence_level", 0.33),
            punctuation_hints: b("punctuation_hints", true),
            hotkey_enabled: b("hotkey_enabled", false),
            hotkey: s("hotkey"),
            theme: s("theme"),
            theme_dirty: theme::is_dirty(&cfg),
            theme_is_custom: cfg["theme"].as_str().unwrap_or("").starts_with("custom:"),
            theme_custom_name: SharedString::from(
                cfg["theme"]
                    .as_str()
                    .unwrap_or("")
                    .strip_prefix("custom:")
                    .unwrap_or(""),
            ),
            app_theme: s("app_theme"),
            window_position: s("window_position"),
            overlay_opacity: f("overlay_opacity", 0.94),
            overlay_width: f("overlay_width", 210.0),
            overlay_height: f("overlay_height", 52.0),
            overlay_x: SharedString::from(cfg["overlay_x"].as_i64().unwrap_or(0).to_string()),
            overlay_y: SharedString::from(cfg["overlay_y"].as_i64().unwrap_or(0).to_string()),
            corner_radius: f("corner_radius", -1.0),
            corner_link: b("corner_link", true),
            c_tl: corners[0],
            c_tr: corners[1],
            c_br: corners[2],
            c_bl: corners[3],
            bar_spacing: f("bar_spacing", tdf("bar_spacing") as f64),
            bar_width: f("bar_width", tdf("bar_width") as f64),
            bar_radius: f("bar_radius", tdf("bar_radius") as f64),
            bar_fade: b("bar_fade", td["bar_fade"].as_bool().unwrap_or(true)),
            bar_count: f("bar_count", tdf("bar_count") as f64),
            record_anim: if s("record_anim").is_empty() {
                SharedString::from(td["record_anim"].as_str().unwrap_or("bars"))
            } else {
                s("record_anim")
            },
            transcribe_anim: s("transcribe_anim"),
            format_anim: s("format_anim"),
            done_anim: if s("done_anim").is_empty() {
                "pop".into()
            } else {
                s("done_anim")
            },
            anim_speed: f("anim_speed", 1.0),
            trans_link: b("trans_link", true),
            done_color: s("done_color"),
            done_value: cfg["done_color"]
                .as_str()
                .and_then(theme::parse_hex)
                .unwrap_or(theme::rgb(t.ok)),
            done_ring: s("done_ring"),
            done_ring_value: cfg["done_ring"]
                .as_str()
                .and_then(theme::parse_hex)
                .unwrap_or(
                    cfg["overlay_bg"]
                        .as_str()
                        .and_then(theme::parse_hex)
                        .unwrap_or(theme::rgb(t.overlay_bg)),
                ),
            error_color: s("error_color"),
            error_value: cfg["error_color"]
                .as_str()
                .and_then(theme::parse_hex)
                .unwrap_or(theme::rgb(t.danger)),
            overlay_bg: s("overlay_bg"),
            overlay_bg_value: cfg["overlay_bg"]
                .as_str()
                .and_then(theme::parse_hex)
                .unwrap_or(theme::rgb(t.overlay_bg)),
            eq_colors: SharedString::from(eq_join),
            accent_color: s("accent_color"),
            accent_value: cfg["accent_color"]
                .as_str()
                .and_then(theme::parse_hex)
                .unwrap_or(theme::rgb(t.accent)),
            border_color: s("border_color"),
            border_value: cfg["border_color"]
                .as_str()
                .and_then(theme::parse_hex)
                .unwrap_or(theme::rgb(t.border_color)),
            border_width: f("border_width", 0.0),
            api_key_set: cfg["openai_api_key"]
                .as_str()
                .map(|k| !k.is_empty())
                .unwrap_or(false),
            proxy: s("proxy"),
            ssl_verify: b("ssl_verify", true),
            autostart: autostart_enabled(),
            llm_enabled: b("llm_enabled", false),
            llm_prompt: s("llm_prompt"),
            llm_base_url: s("llm_base_url"),
            llm_load_mode: s("llm_load_mode"),
        });
        // the wizard only ever appears once a real config said "not onboarded"
        self.ui.set_show_wizard(!b("onboarded", true));
        let want = cfg["device_index"].as_i64();
        let idx = self
            .devices
            .borrow()
            .iter()
            .position(|(i, _)| *i == want)
            .unwrap_or(0);
        self.ui.set_device_current(idx as i32);
        // theme selector: stock + saved customs (+ transient unsaved entry)
        let mut names: Vec<String> = theme::NAMES.iter().map(|s| s.to_string()).collect();
        let mut customs: Vec<String> = cfg["custom_themes"]
            .as_object()
            .map(|o| o.keys().cloned().collect())
            .unwrap_or_default();
        customs.sort();
        names.extend(customs.iter().cloned());
        let theme_str = cfg["theme"].as_str().unwrap_or("macaw").to_string();
        let custom_name = theme_str.strip_prefix("custom:").unwrap_or("").to_string();
        let is_custom = !custom_name.is_empty();
        let dirty = theme::is_dirty(&cfg);
        let mut cur = if is_custom {
            theme::NAMES.len() + customs.iter().position(|n| *n == custom_name).unwrap_or(0)
        } else {
            theme::index_of(&theme_str)
        };
        if dirty && !is_custom {
            names.push("● custom (unsaved)".into());
            cur = names.len() - 1;
        }
        let name_strs: Vec<SharedString> = names
            .iter()
            .map(|n| SharedString::from(n.as_str()))
            .collect();
        self.ui.set_theme_names(ModelRc::from(name_strs.as_slice()));
        self.ui.set_theme_current(cur as i32);
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

    fn refresh_llm_models(&self) {
        let tx = self.msg_tx.clone();
        self.client.call(
            "llm.list",
            json!({}),
            Some(Box::new(move |res| {
                if let Ok(Value::Array(list)) = res {
                    let _ = tx.send(Msg::LlmModelsLoaded(list));
                }
            })),
        );
    }

    fn refresh_formatter_status(&self) {
        let tx = self.msg_tx.clone();
        self.client.call(
            "llm.status",
            json!({}),
            Some(Box::new(move |res| {
                if let Ok(v) = res {
                    let _ = tx.send(Msg::FormatterStatus(v));
                }
            })),
        );
    }

    fn refresh_providers(&self) {
        let tx = self.msg_tx.clone();
        self.client.call(
            "providers.list",
            json!({}),
            Some(Box::new(move |res| {
                if let Ok(Value::Array(list)) = res {
                    let _ = tx.send(Msg::ProvidersLoaded(list));
                }
            })),
        );
    }

    fn render_providers(&self) {
        let raw = self.providers_raw.borrow();
        let s = |m: &Value, k: &str| SharedString::from(m[k].as_str().unwrap_or_default());
        let rows: Vec<ProviderRow> = raw
            .iter()
            .map(|m| {
                let models: Vec<SharedString> = m["models"]
                    .as_array()
                    .map(|a| {
                        a.iter()
                            .filter_map(Value::as_str)
                            .map(SharedString::from)
                            .collect()
                    })
                    .unwrap_or_default();
                ProviderRow {
                    id: s(m, "id"),
                    label: s(m, "label"),
                    kind: s(m, "kind"),
                    base_url: s(m, "base_url"),
                    model: s(m, "model"),
                    note: s(m, "note"),
                    docs_url: s(m, "docs_url"),
                    env: s(m, "env"),
                    models: ModelRc::from(models.as_slice()),
                    needs_key: m["needs_key"].as_bool().unwrap_or(true),
                    key_set: m["key_set"].as_bool().unwrap_or(false),
                    enabled: m["enabled"].as_bool().unwrap_or(false),
                    ready: m["ready"].as_bool().unwrap_or(false),
                    active: m["active"].as_bool().unwrap_or(false),
                }
            })
            .collect();
        let n = rows.len() as i32;
        let idx = self.provider_sel.get().clamp(0, (n - 1).max(0));
        self.provider_sel.set(idx);
        if let Some(r) = rows.get(idx as usize) {
            self.ui.set_provider_sel(r.clone());
        }
        self.ui.set_provider_sel_index(idx);
        self.ui.set_providers(ModelRc::from(rows.as_slice()));
    }

    fn render_llm_models(&self) {
        let raw = self.llm_models_raw.borrow();
        let op = self.op.borrow();
        let s = |m: &Value, k: &str| SharedString::from(m[k].as_str().unwrap_or_default());
        let repo_url = |m: &Value| {
            let repo = m["repo"].as_str().unwrap_or_default();
            if repo.is_empty() {
                String::new()
            } else if repo.starts_with("http") {
                repo.to_string()
            } else {
                format!("https://huggingface.co/{repo}")
            }
        };
        let rows: Vec<LlmModelRow> = raw
            .iter()
            .map(|m| {
                let id = m["id"].as_str().unwrap_or_default();
                // ops share one slot: an llm download keys on the model id, an
                // install on the "llm" extra.
                let busy = op
                    .as_ref()
                    .map(|(o, key, ..)| {
                        (o == "download" && key == id) || (o == "install" && key == "llm")
                    })
                    .unwrap_or(false);
                let (msg, pct) = op
                    .as_ref()
                    .map(|(_, _, m, p)| (m.clone(), *p))
                    .unwrap_or_default();
                LlmModelRow {
                    id: s(m, "id"),
                    label: s(m, "label"),
                    backend: s(m, "backend"),
                    size: s(m, "size"),
                    speed: s(m, "speed"),
                    cloud: m["cloud"].as_bool().unwrap_or(false),
                    recommended: m["recommended"].as_bool().unwrap_or(false),
                    desc: s(m, "desc"),
                    notes: s(m, "notes"),
                    provider: m["provider"].as_bool().unwrap_or(false),
                    rating: m["rating"].as_i64().unwrap_or(0) as i32,
                    source_url: s(m, "source_url"),
                    repo_url: SharedString::from(repo_url(m)),
                    available: m["available"].as_bool().unwrap_or(false),
                    installed: m["installed"].as_bool().unwrap_or(false),
                    removable: m["removable"].as_bool().unwrap_or(false),
                    ready: m["ready"].as_bool().unwrap_or(false),
                    active: m["active"].as_bool().unwrap_or(false),
                    api_key_set: m["api_key_set"].as_bool().unwrap_or(false),
                    uses_prompt: m["uses_prompt"].as_bool().unwrap_or(true),
                    busy,
                    progress_pct: if busy { pct } else { -1.0 },
                    progress_msg: SharedString::from(if busy { msg } else { String::new() }),
                }
            })
            .collect();
        let names: Vec<SharedString> = rows.iter().map(|r| r.label.clone()).collect();
        let current = rows.iter().position(|r| r.active).map(|i| i as i32).unwrap_or(-1);
        let sel = rows.iter().find(|r| r.active).cloned();
        self.ui.set_llm_have_sel(sel.is_some());
        if let Some(r) = sel {
            self.ui.set_llm_sel(r);
        }
        self.ui.set_llm_names(ModelRc::from(names.as_slice()));
        self.ui.set_llm_current(current);
        self.ui.set_llm_models(ModelRc::from(rows.as_slice()));
        // per-model tunables for the active formatter (rendered in its card)
        let dp: Vec<ParamRow> = raw
            .iter()
            .find(|m| m["active"].as_bool() == Some(true))
            .map(|m| {
                let cur = m["cur_params"].clone();
                m["params"]
                    .as_array()
                    .map(|a| {
                        a.iter()
                            .map(|p| {
                                let key = p["key"].as_str().unwrap_or_default();
                                let kind = p["kind"].as_str().unwrap_or("bool");
                                let val = cur[key].clone();
                                ParamRow {
                                    key: key.into(),
                                    label: p["label"].as_str().unwrap_or(key).into(),
                                    kind: kind.into(),
                                    hint: p["hint"].as_str().unwrap_or_default().into(),
                                    value: val.as_f64().or(p["default"].as_f64()).unwrap_or(0.0)
                                        as f32,
                                    bvalue: val.as_bool().or(p["default"].as_bool()).unwrap_or(false),
                                    minimum: p["min"].as_f64().unwrap_or(0.0) as f32,
                                    maximum: p["max"].as_f64().unwrap_or(1.0) as f32,
                                    step: p["step"].as_f64().unwrap_or(1.0) as f32,
                                }
                            })
                            .collect()
                    })
                    .unwrap_or_default()
            })
            .unwrap_or_default();
        self.ui.set_llm_detail_params(ModelRc::from(dp.as_slice()));
    }

    /// Active model's native-streaming capability -> the Settings badge and
    /// the wizard's Live typing card note.
    fn sync_live_native(&self) {
        let active = self.cfg.borrow()["model"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        let native = self
            .models_raw
            .borrow()
            .iter()
            .find(|m| m["id"].as_str() == Some(active.as_str()))
            .map(|m| m["streaming"].as_bool().unwrap_or(false))
            .unwrap_or(false);
        self.ui.set_live_native(native);
    }

    fn render_models(&self) {
        let raw = self.models_raw.borrow();
        let op = self.op.borrow();
        let expanded = self.expanded.borrow().clone();
        let needle = self.search.borrow().to_lowercase();
        let filter = self.filter.get();
        let rows: Vec<ModelRow> = raw
            .iter()
            .filter(|m| {
                let hay = format!(
                    "{} {} {}",
                    m["label"].as_str().unwrap_or(""),
                    m["backend"].as_str().unwrap_or(""),
                    m["id"].as_str().unwrap_or("")
                )
                .to_lowercase();
                if !needle.is_empty() && !hay.contains(&needle) {
                    return false;
                }
                match filter {
                    0 => m["fit_rank"].as_i64().unwrap_or(0) > 0,
                    1 => m["light"].as_bool().unwrap_or(false),
                    2 => m["cloud"].as_bool().unwrap_or(false),
                    3 => m["streaming"].as_bool().unwrap_or(false),
                    _ => true, // -1 = no filter
                }
            })
            .map(|m| {
                let s = |k: &str| SharedString::from(m[k].as_str().unwrap_or_default());
                // catalog notes arrive as arrays of bullet lines
                let lines = |k: &str, sep: &str| {
                    SharedString::from(
                        m[k].as_array()
                            .map(|a| {
                                a.iter()
                                    .filter_map(Value::as_str)
                                    .collect::<Vec<_>>()
                                    .join(sep)
                            })
                            .unwrap_or_default(),
                    )
                };
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
                let repo = m["repo"].as_str().unwrap_or_default();
                let repo_url = if repo.is_empty() {
                    String::new()
                } else if repo.starts_with("http") {
                    repo.to_string()
                } else {
                    format!("https://huggingface.co/{repo}")
                };
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
                    pros: lines("pros", "\n+ "),
                    cons: lines("cons", "\n− "),
                    notes: s("notes"),
                    available: m["available"].as_bool().unwrap_or(false),
                    installed: m["installed"].as_bool().unwrap_or(false),
                    ready: m["ready"].as_bool().unwrap_or(false),
                    active: m["active"].as_bool().unwrap_or(false),
                    extra: SharedString::from(extra),
                    disk_size: SharedString::from(human_size(m["disk_size"].as_u64().unwrap_or(0))),
                    api_key_set: m["api_key_set"].as_bool().unwrap_or(false),
                    min_specs: s("min_specs"),
                    rec_specs: s("rec_specs"),
                    source_url: s("source_url"),
                    repo_url: SharedString::from(repo_url),
                    lang_select: m["lang_select"].as_bool().unwrap_or(false),
                    has_params: m["params"]
                        .as_array()
                        .map(|a| !a.is_empty())
                        .unwrap_or(false),
                    expanded: id == expanded,
                    busy,
                    progress_pct: if busy { pct } else { -1.0 },
                    progress_msg: SharedString::from(if busy { msg } else { String::new() }),
                    fit_rank: m["fit_rank"].as_i64().unwrap_or(0) as i32,
                    fit_why: s("fit_why"),
                    light: m["light"].as_bool().unwrap_or(false),
                }
            })
            .collect();
        let mut rows = rows;
        if filter == 0 {
            rows.sort_by_key(|r| r.fit_rank); // "For you": best pick first
        }
        self.ui.set_op_running(op.is_some());
        // selected dossier (master-detail right pane)
        let sel_row = rows.iter().find(|r| r.expanded).cloned();
        self.ui.set_have_sel(sel_row.is_some());
        if let Some(r) = sel_row {
            self.ui.set_sel(r);
        }
        self.ui.set_models(ModelRc::from(rows.as_slice()));
        let label = raw
            .iter()
            .find(|m| m["active"].as_bool().unwrap_or(false))
            .and_then(|m| m["label"].as_str())
            .unwrap_or("");
        self.ui.set_active_model_label(SharedString::from(label));

        // detail models for the expanded card
        if let Some(m) = raw
            .iter()
            .find(|m| m["id"].as_str() == Some(expanded.as_str()))
        {
            let cur = m["cur_params"].clone();
            let params: Vec<ParamRow> = m["params"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .map(|p| {
                            let key = p["key"].as_str().unwrap_or_default();
                            let kind = p["kind"].as_str().unwrap_or("float");
                            let val = cur[key].clone();
                            ParamRow {
                                key: key.into(),
                                label: p["label"].as_str().unwrap_or(key).into(),
                                kind: kind.into(),
                                hint: p["hint"].as_str().unwrap_or_default().into(),
                                value: val.as_f64().or(p["default"].as_f64()).unwrap_or(0.0) as f32,
                                bvalue: val.as_bool().or(p["default"].as_bool()).unwrap_or(false),
                                minimum: p["min"].as_f64().unwrap_or(0.0) as f32,
                                maximum: p["max"].as_f64().unwrap_or(1.0) as f32,
                                step: p["step"].as_f64().unwrap_or(0.1) as f32,
                            }
                        })
                        .collect()
                })
                .unwrap_or_default();
            self.ui.set_detail_params(ModelRc::from(params.as_slice()));
            let cur_lang = m["cur_lang"].as_str().unwrap_or("en");
            let idx = LANGS.iter().position(|(_, c)| *c == cur_lang).unwrap_or(0);
            self.ui.set_lang_current(idx as i32);
        }
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
            .or_else(|| self.winit_monitor())
            .map(|m| hypr::anchor_xy(&pos, w, h, custom, &m))
            .unwrap_or((0, 0));
        (x, y, w, h)
    }

    /// Monitor geometry via winit when Hyprland isn't around (Windows, GNOME,
    /// …) — same logical-pixel space `anchor_xy` expects.
    fn winit_monitor(&self) -> Option<hypr::Monitor> {
        use slint::winit_030::WinitWindowAccessor;
        self.ui
            .window()
            .with_winit_window(|win| {
                let m = win.current_monitor().or_else(|| win.primary_monitor())?;
                let sf = m.scale_factor();
                let p = m.position();
                let s = m.size();
                Some(hypr::Monitor {
                    x: (p.x as f64 / sf) as i32,
                    y: (p.y as f64 / sf) as i32,
                    w: (s.width as f64 / sf) as i32,
                    h: (s.height as f64 / sf) as i32,
                    reserved_top: 0,
                    reserved_bottom: 0,
                })
            })
            .flatten()
    }

    /// Off-Hyprland (Windows, GNOME fallback) the compositor won't place the
    /// overlay for us — position the winit window directly.
    fn place_overlay_fallback(&self, x: i32, y: i32) {
        if !hypr::available() {
            self.overlay
                .window()
                .set_position(slint::LogicalPosition::new(x as f32, y as f32));
        }
    }

    /// Send to the layer-shell overlay process; false = gone. A dead child
    /// schedules recovery (respawn or window fallback) on the next loop turn
    /// — an active session must never run without its indicator.
    fn ls_send(&self, v: Value) -> bool {
        let mut slot = self.ls.borrow_mut();
        if let Some(proc) = slot.as_mut() {
            if proc.send(&v) {
                return true;
            }
            eprintln!("[shell] layer-shell overlay lost");
            proc.kill();
            *slot = None;
            drop(slot);
            slint::Timer::single_shot(std::time::Duration::from_millis(0), || {
                with_app(|a| a.ls_recover());
            });
        }
        false
    }

    /// The layer-shell child died mid-flight: give it a fresh start (bounded
    /// budget, so a crash-looping child can't spin us), re-push the look and
    /// geometry, and restore whatever the current state should display —
    /// otherwise the winit fallback takes over immediately.
    fn ls_recover(self: &Rc<Self>) {
        if self.ls_respawns.get() < 3 {
            self.ls_respawns.set(self.ls_respawns.get() + 1);
            *self.ls.borrow_mut() = ls::LsOverlay::spawn();
            if self.ls.borrow().is_some() {
                eprintln!("[shell] layer-shell overlay respawned");
                self.apply_theme(); // fresh child knows nothing: look + geometry
            }
        }
        self.sync_overlay_to_state();
    }

    /// Put the indicator into whatever the current engine state demands.
    /// Shared by the State event and layer-shell recovery.
    fn sync_overlay_to_state(self: &Rc<Self>) {
        let state = self.ui.get_engine_state().to_string();
        match state.as_str() {
            "recording" => self.show_overlay("eq", false),
            "transcribing" => self.show_overlay("loader", false),
            "formatting" => self.show_overlay("format", false),
            "done" => self.show_overlay("done", false),
            "error" if !self.overlay.get_error_text().is_empty() => {
                let detail = self.overlay.get_error_text();
                self.ls_send(json!({"cmd": "error", "text": detail.as_str()}));
                self.show_overlay("error", false);
            }
            _ if self.pinned.get() => {
                let mode = self.preview_mode.borrow().clone();
                self.show_overlay(&mode, true); // live-edit pin follows the chip
            }
            _ => self.hide_overlay(), // idle / loading / detail-less error
        }
    }

    /// `demo`: preview shows (pin / state chips) loop the done entrance;
    /// real engine states play it once.
    fn show_overlay(self: &Rc<Self>, mode: &str, demo: bool) {
        // "format" is the transcribe loader look with its own animation: map it
        // to the pill's "loader" mode and swap in the formatting-step anim.
        let anim: Option<String> = match mode {
            "format" => Some(
                self.cfg.borrow()["format_anim"]
                    .as_str()
                    .unwrap_or("shimmer")
                    .to_string(),
            ),
            "loader" => Some(
                self.cfg.borrow()["transcribe_anim"]
                    .as_str()
                    .unwrap_or("waves")
                    .to_string(),
            ),
            _ => None,
        };
        let pill_mode = if mode == "format" { "loader" } else { mode };
        let mut msg = json!({"cmd": "show", "mode": pill_mode, "demo": demo});
        if let Some(a) = &anim {
            msg["anim"] = json!(a);
        }
        if self.ls_send(msg) {
            return;
        }
        let visible = self.overlay.window().is_visible();
        self.overlay.set_demo_loop(demo);
        if let Some(a) = &anim {
            self.overlay.set_anim(a.as_str().into());
        }
        self.overlay.set_mode(pill_mode.into());
        if !visible {
            let (x, y, w, h) = self.overlay_geometry();
            hypr::install_rules(x, y, w, h);
            self.overlay
                .window()
                .set_size(slint::LogicalSize::new(w as f32, h as f32));
            self.place_overlay_fallback(x, y);
            let _ = self.overlay.show();
        }
    }

    fn hide_overlay(&self) {
        self.ls_send(json!({"cmd": "hide"}));
        if self.overlay.window().is_visible() {
            let _ = self.overlay.hide();
        }
        self.anim.borrow_mut().reset();
        self.rms.set(0.0);
    }

    /// Runs for the whole app lifetime (30 Hz): drives the winit-fallback
    /// overlay bars during recording and the settings preview otherwise.
    /// (The layer-shell process animates its own bars from raw rms.)
    fn start_level_timer(self: &Rc<Self>) {
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

    fn tick_bars(&self) {
        use slint::Model;
        let recording = self.recording.get();
        let pinned = self.pinned.get();
        // Who renders these levels? The settings window (preview + galleries)
        // and the winit-fallback overlay. The layer-shell process animates its
        // own bars from raw rms — pinned-idle only needs the level cmd below.
        let ui_visible = self.ui.window().is_visible() || self.overlay.window().is_visible();
        let pinned_idle = pinned && !recording;
        if !ui_visible && !pinned_idle {
            return; // tray-idle: no model churn, no wakeup work
        }
        let rms = if recording {
            self.rms.get()
        } else {
            // settings-preview wave: nothing live to visualize
            let t = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis() as u64)
                .unwrap_or(0) as f32;
            (0.55 + 0.4 * (t * 0.0033).sin()).clamp(0.0, 1.0)
        };
        let mut anim = self.anim.borrow_mut();
        let (bars, heard) = anim.step(rms);
        if ui_visible {
            // in place: set_vec resets the model, re-instantiating every bar
            // element per tick — set_row_data only dirties height bindings
            if self.levels.row_count() == bars.len() {
                for (i, v) in bars.iter().enumerate() {
                    if self.levels.row_data(i) != Some(*v) {
                        self.levels.set_row_data(i, *v);
                    }
                }
            } else {
                self.levels.set_vec(bars.to_vec());
            }
            // gallery tiles run on a fixed 12-bar downsample — 8+ live pills
            // at full bar_count would animate hundreds of extra rectangles
            let n = bars.len().max(1);
            for i in 0..DEMO_BARS {
                let v = bars[i * n / DEMO_BARS];
                if self.demo.row_data(i) != Some(v) {
                    self.demo.set_row_data(i, v);
                }
            }
            self.overlay.set_heard(heard);
            self.ui.global::<Look>().set_heard(heard);
        }
        // pinned live-edit: feed the layer-shell process the synthetic wave
        // so the on-screen indicator moves while idle
        if pinned_idle {
            self.ls_send(json!({"cmd": "level", "rms": rms}));
        }
    }

    // ── message handling (UI thread) ────────────────────────────────
    fn on_msg(self: &Rc<Self>, msg: Msg) {
        match msg {
            Msg::Ws(ev) => self.on_event(ev),
            Msg::ConfigLoaded(v) => {
                self.cfg.replace(v["config"].clone());
                if let Some(p) = v["llm_default_prompt"].as_str() {
                    self.ui.set_llm_default_prompt(SharedString::from(p));
                }
                self.apply_config();
                self.sync_live_native();
                self.sync_mic_monitor(); // wizard visibility follows onboarded
            }
            Msg::SystemInfo(v) => {
                self.ui
                    .set_hw_summary(SharedString::from(v["summary"].as_str().unwrap_or("")));
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
                // default the dossier to the active model
                if self.expanded.borrow().is_empty() {
                    if let Some(active) = list
                        .iter()
                        .find(|m| m["active"].as_bool().unwrap_or(false))
                        .and_then(|m| m["id"].as_str())
                    {
                        *self.expanded.borrow_mut() = active.to_string();
                    }
                }
                self.models_raw.replace(list);
                self.render_models();
                self.sync_live_native();
            }
            Msg::LlmModelsLoaded(list) => {
                self.llm_models_raw.replace(list);
                self.render_llm_models();
            }
            Msg::LlmTest(out, stat) => {
                self.ui.set_llm_test_busy(false);
                self.ui.set_llm_test_output(SharedString::from(out));
                self.ui.set_llm_test_stat(SharedString::from(stat));
            }
            Msg::ProvidersLoaded(list) => {
                self.providers_raw.replace(list);
                self.render_providers();
            }
            Msg::ProviderTest(out) => {
                self.ui.set_provider_testing(false);
                self.ui.set_provider_test_result(SharedString::from(out));
            }
            Msg::FormatterStatus(v) => {
                let enabled = v["enabled"].as_bool().unwrap_or(false);
                let provider = v["provider"].as_bool().unwrap_or(false);
                let ready = v["ready"].as_bool().unwrap_or(false);
                let loaded = v["loaded"].as_bool().unwrap_or(false);
                let mode = v["mode"].as_str().unwrap_or("hot");
                let (status, ok) = if !enabled {
                    ("disabled", false)
                } else if provider {
                    if ready {
                        ("cloud · ready", true)
                    } else {
                        ("cloud · needs key", false)
                    }
                } else if !ready {
                    ("not installed", false)
                } else if loaded {
                    ("warm", true)
                } else if mode == "cold" {
                    ("loaded on demand", true)
                } else {
                    ("ready", true)
                };
                self.ui
                    .set_formatter_label(SharedString::from(v["label"].as_str().unwrap_or("—")));
                self.ui.set_formatter_status(SharedString::from(status));
                self.ui.set_formatter_ok(ok);
            }
            Msg::Cmd(cmd) => self.on_cmd(cmd),
        }
    }

    fn on_event(self: &Rc<Self>, ev: ws::Event) {
        match ev {
            ws::Event::Connected => {
                self.ui.set_engine_connected(true);
                // (re)sync the idle mic meter — fresh engines start without it
                self.mic_mon.set(false);
                self.sync_mic_monitor();
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
                    "system.info",
                    json!({}),
                    Some(Box::new(move |res| {
                        if let Ok(v) = res {
                            let _ = tx.send(Msg::SystemInfo(v));
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
                self.refresh_llm_models();
                self.refresh_formatter_status();
                self.refresh_providers();
            }
            ws::Event::Disconnected => self.ui.set_engine_connected(false),
            ws::Event::State { state, detail } => {
                self.ui.set_engine_state(SharedString::from(state.as_str()));
                self.recording.set(state == "recording");
                if let Some(t) = &self.tray {
                    let rec = state == "recording";
                    t.update(move |tr| tr.recording = rec);
                }
                if state == "error" {
                    // sync reads it back; empty detail = detail-less error
                    self.overlay
                        .set_error_text(SharedString::from(detail.as_str()));
                }
                self.sync_overlay_to_state();
            }
            ws::Event::Level { rms } => {
                self.rms.set(rms);
                self.ui.set_mic_level(rms);
                self.ls_send(json!({"cmd": "level", "rms": rms}));
            }
            ws::Event::Config { config } => {
                self.cfg.replace(config);
                self.apply_config();
                self.refresh_formatter_status();
                self.sync_live_native();
                self.sync_mic_monitor(); // wizard visibility follows onboarded
                if self.ls.borrow().is_none() && self.overlay.window().is_visible() {
                    let (x, y, w, h) = self.overlay_geometry();
                    hypr::install_rules(x, y, w, h);
                    self.overlay
                        .window()
                        .set_size(slint::LogicalSize::new(w as f32, h as f32));
                    hypr::move_mapped(x, y);
                    self.place_overlay_fallback(x, y);
                }
            }
            ws::Event::Models => {
                self.refresh_models();
                self.refresh_llm_models();
                self.refresh_formatter_status();
                self.refresh_providers();
            }
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
                self.render_llm_models();
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
        if tab == "settings" || tab == "models" || tab == "appearance" {
            self.ui.set_tab(tab.into());
        }
        let _ = self.ui.show();
        self.sync_mic_monitor();
        // re-maps lose the Wayland app_id (winit), so the class rule can
        // miss — enforce float + fixed size once the surface is up
        slint::Timer::single_shot(std::time::Duration::from_millis(400), || {
            hypr::enforce_main_geometry();
        });
    }

    /// The idle mic meter runs while the Settings tab is on screen — or the
    /// first-launch wizard (its mic step needs the live bar).
    fn sync_mic_monitor(&self) {
        let wizard = self.ui.get_show_wizard();
        let on = (self.ui.get_tab() == "settings" || wizard) && self.ui.window().is_visible();
        if on != self.mic_mon.get() {
            self.mic_mon.set(on);
            self.client.call("mic.monitor", json!({ "on": on }), None);
            if !on {
                self.ui.set_mic_level(0.0);
            }
        }
    }

    /// Kill the engine process and start a fresh one (same token/port; the
    /// ws client reconnects on its own). The big hammer for a hung backend.
    fn restart_engine(&self) {
        self.toast("info", "Restarting engine…");
        self.ui.set_engine_connected(false);
        self.mic_mon.set(false);
        let mut eng = self.engine.borrow_mut();
        eng.kill();
        *eng = engine::Engine::spawn(&self.token, WS_PORT);
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
        if let Some(proc) = self.ls.borrow_mut().as_mut() {
            proc.kill();
        }
        self.client.call("quit", json!({}), None);
        self.client.shutdown();
        self.engine.borrow_mut().kill();
        single::release();
        let _ = slint::quit_event_loop();
    }
}

#[cfg(not(windows))]
fn autostart_enabled() -> bool {
    autostart_path().exists()
}

/// True when the HKCU Run entry exists (mirrors autostart_path().exists()).
#[cfg(windows)]
fn autostart_enabled() -> bool {
    use std::os::windows::process::CommandExt;
    std::process::Command::new("reg")
        .args([
            "query",
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
            "/v",
            "Macaw",
        ])
        .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

#[cfg(not(windows))]
fn autostart_path() -> std::path::PathBuf {
    let base = std::env::var("XDG_CONFIG_HOME")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| {
            std::path::PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".config")
        });
    base.join("autostart/macaw.desktop")
}

#[cfg(not(windows))]
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

/// HKCU Run entry — the Windows equivalent of the XDG autostart file.
#[cfg(windows)]
fn set_autostart(on: bool) {
    use std::os::windows::process::CommandExt;
    const KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let mut cmd = std::process::Command::new("reg");
    if on {
        let Ok(exe) = std::env::current_exe() else {
            return;
        };
        cmd.args([
            "add",
            KEY,
            "/v",
            "Macaw",
            "/t",
            "REG_SZ",
            "/d",
            &format!("\"{}\"", exe.display()),
            "/f",
        ]);
    } else {
        cmd.args(["delete", KEY, "/v", "Macaw", "/f"]);
    }
    let _ = cmd.creation_flags(CREATE_NO_WINDOW).status();
}

fn main() {
    // GUI subsystem on Windows: panics have no console to land in — persist
    // them so "the app doesn't launch" is always diagnosable.
    #[cfg(windows)]
    if let Ok(base) = std::env::var("LOCALAPPDATA") {
        let dir = std::path::PathBuf::from(base).join("Macaw");
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("ui-crash.log");
        std::panic::set_hook(Box::new(move |info| {
            let _ = std::fs::write(
                &path,
                format!("{info}\n\n{}", std::backtrace::Backtrace::force_capture()),
            );
        }));
    }
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
    if flag.as_deref() == Some("--selftest") {
        // CI runs this on a real Windows box: exercises the pre-window
        // startup surface that differs per OS (entropy, single-instance
        // socket) without needing a desktop session. A panic here aborts
        // with a nonzero exit code and lands in ui-crash.log.
        let tok = token();
        assert_eq!(tok.len(), 32, "token must be 16 random bytes hex-encoded");
        single::release();
        println!("selftest ok");
        return;
    }

    // Per-window Wayland app_id, keyed on the window TITLE — hide()+show()
    // re-creates winit windows, so a creation-order counter drifts (a
    // re-shown main window inherited the overlay app_id: class lost,
    // float rules missed, window tiled). Windows has no app_id concept —
    // attributes pass through untouched there.
    let selector = slint::BackendSelector::new()
        .backend_name("winit".into())
        .with_winit_window_attributes_hook(move |attrs| {
            #[cfg(unix)]
            {
                use slint::winit_030::winit::platform::wayland::WindowAttributesExtWayland;
                // Size hints come from the fixed .slint window size — adding
                // min/max here too raced Slint's own hints and could kill the
                // surface (min > max protocol error) before first map.
                let overlay = attrs.title == hypr::OVERLAY_TITLE;
                let app_id = if overlay {
                    hypr::OVERLAY_TITLE
                } else {
                    "macaw"
                };
                return attrs.with_name(app_id, "");
            }
            #[cfg(not(unix))]
            attrs
        });
    // Software renderer everywhere: no GL dependency (VMs, RDP, wine, weak
    // drivers), small RSS. Text never touches system-font enumeration — the
    // UI fonts are embedded and pinned via default-font-family (app.slint).
    selector
        .renderer_name("software".into())
        .select()
        .expect("select winit backend");

    let ui = MainWindow::new().expect("create main window"); // adapter #0
    let overlay = OverlayWindow::new().expect("create overlay"); // adapter #1

    let tok = token();
    let eng = engine::Engine::spawn(&tok, WS_PORT);
    let (msg_tx, msg_rx) = channel::<Msg>();
    let (ev_tx, ev_rx) = channel::<ws::Event>();
    let client = ws::spawn(WS_PORT, tok.clone(), ev_tx);

    let toasts = Rc::new(VecModel::from(Vec::<Toast>::new()));
    ui.set_toasts(ModelRc::from(Rc::clone(&toasts)));
    let levels = Rc::new(VecModel::from(vec![0.0f32; 24]));
    overlay.set_levels(ModelRc::from(Rc::clone(&levels)));
    let demo = Rc::new(VecModel::from(vec![0.0f32; DEMO_BARS]));
    ui.global::<Look>()
        .set_demo_levels(ModelRc::from(Rc::clone(&demo)));

    let app = Rc::new(App {
        tray: tray::spawn(cmd_tx),
        ui,
        overlay,
        client,
        msg_tx: msg_tx.clone(),
        engine: RefCell::new(eng),
        token: tok,
        mic_mon: Cell::new(false),
        cfg: RefCell::new(Value::Null),
        devices: RefCell::new(vec![(None, "System default".into())]),
        models_raw: RefCell::new(vec![]),
        llm_models_raw: RefCell::new(vec![]),
        providers_raw: RefCell::new(vec![]),
        provider_sel: Cell::new(0),
        op: RefCell::new(None),
        toasts,
        levels,
        demo,
        eq: Rc::new(VecModel::from(Vec::<slint::Color>::new())),
        trans: Rc::new(VecModel::from(Vec::<slint::Color>::new())),
        anim: RefCell::new(BarAnim::new(24)),
        rms: Cell::new(0.0),
        ls: RefCell::new(ls::LsOverlay::spawn()),
        ls_respawns: Cell::new(0),
        level_timer: slint::Timer::default(),
        recording: Cell::new(false),
        pinned: Cell::new(false),
        preview_mode: RefCell::new("eq".into()),
        expanded: RefCell::new(String::new()),
        search: RefCell::new(String::new()),
        filter: Cell::new(-1), // -1 = no filter selected
    });
    APP.with(|a| *a.borrow_mut() = Some(Rc::clone(&app)));
    app.ui.set_app_version(env!("CARGO_PKG_VERSION").into());
    let names: Vec<SharedString> = theme::NAMES
        .iter()
        .map(|n| SharedString::from(*n))
        .collect();
    app.ui.set_theme_names(ModelRc::from(names.as_slice()));
    let lang_names: Vec<SharedString> = LANGS.iter().map(|(n, _)| SharedString::from(*n)).collect();
    app.ui.set_langs(ModelRc::from(lang_names.as_slice()));
    app.start_level_timer();

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
            // Float config fields keep 2 decimals; everything else (sizes,
            // counts, radii, positions) is a genuine integer. The old logic
            // truncated unknown keys `as i64` — silence_level (0-1) always
            // became 0.
            const FLOAT_KEYS: [&str; 5] = [
                "silence_timeout",
                "overlay_opacity",
                "silence_level",
                "level_gain",
                "anim_speed",
            ];
            let patch = if FLOAT_KEYS.contains(&key.as_str()) {
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
    // gradient/color editors: mutate cfg, patch — the engine's config echo
    // re-resolves the look everywhere. One macro per repeating shape.
    macro_rules! wire_color {
        ($pick:ident, $clear:ident, $key:literal) => {{
            let a = Rc::clone(&app);
            app.ui.$pick(move |c| a.patch(kv($key, json!(hex(c)))));
            let a = Rc::clone(&app);
            app.ui.$clear(move || a.patch(kv($key, json!(""))));
        }};
    }
    macro_rules! wire_stops {
        ($set:ident, $add:ident, $remove:ident, $model:ident, $key:literal) => {{
            let a = Rc::clone(&app);
            app.ui.$set(move |i, c| {
                let mut stops: Vec<String> = a.$model.iter().map(hex).collect();
                if let Some(s) = stops.get_mut(i as usize) {
                    *s = hex(c);
                }
                a.patch(kv($key, json!(stops)));
            });
            let a = Rc::clone(&app);
            app.ui.$add(move || {
                let mut stops: Vec<String> = a.$model.iter().map(hex).collect();
                stops.push(stops.last().cloned().unwrap_or_else(|| "#E5322B".into()));
                a.patch(kv($key, json!(stops)));
            });
            let a = Rc::clone(&app);
            app.ui.$remove(move |i| {
                let mut stops: Vec<String> = a.$model.iter().map(hex).collect();
                if (i as usize) < stops.len() && stops.len() > 1 {
                    stops.remove(i as usize);
                }
                a.patch(kv($key, json!(stops)));
            });
        }};
    }
    wire_stops!(on_eq_set, on_eq_add, on_eq_remove, eq, "eq_colors");
    wire_stops!(
        on_trans_set,
        on_trans_add,
        on_trans_remove,
        trans,
        "trans_colors"
    );
    wire_color!(on_accent_picked, on_accent_clear, "accent_color");
    wire_color!(on_border_picked, on_border_clear, "border_color");
    wire_color!(on_pillbg_picked, on_pillbg_clear, "overlay_bg");
    wire_color!(on_done_picked, on_done_clear, "done_color");
    wire_color!(on_donering_picked, on_donering_clear, "done_ring");
    wire_color!(on_error_picked, on_error_clear, "error_color");
    {
        // per-corner radii: patch the full 4-list (tl,tr,br,bl)
        let a = Rc::clone(&app);
        app.ui.on_set_corner(move |i, v| {
            let (t, cfg) = {
                let cfg = a.cfg.borrow();
                (
                    theme::by_name(cfg["theme"].as_str().unwrap_or("macaw")),
                    cfg.clone(),
                )
            };
            let mut cc = theme::corners(t, &cfg);
            if let Some(slot) = cc.get_mut(i as usize) {
                *slot = v.max(0.0);
            }
            let list: Vec<i64> = cc.iter().map(|c| *c as i64).collect();
            a.patch(json!({ "corners": list, "corner_link": false }));
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_pin_overlay(move |on| {
            a.pinned.set(on);
            if on {
                let mode = a.preview_mode.borrow().clone();
                a.ls_send(json!({"cmd": "error", "text": "Preview"}));
                a.overlay.set_error_text("Preview".into());
                a.show_overlay(&mode, true);
            } else if !a.recording.get() {
                a.hide_overlay();
            }
        });
    }
    {
        // state chip switched: retarget the pinned on-screen indicator too
        let a = Rc::clone(&app);
        app.ui.on_state_picked(move |m| {
            *a.preview_mode.borrow_mut() = m.to_string();
            if a.pinned.get() && !a.recording.get() {
                a.show_overlay(m.as_str(), true);
            }
        });
    }
    app.ui.on_set_autostart(set_autostart);
    {
        let a = Rc::clone(&app);
        app.ui.on_pick_default_lang(move |i| {
            let code = LANGS.get(i as usize).map(|(_, c)| *c).unwrap_or("en");
            a.patch(kv("language", json!(code)));
        });
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
    {
        let a = Rc::clone(&app);
        app.ui.on_manage_providers(move || {
            a.ui.set_show_providers(true);
            a.ui.set_provider_test_result(SharedString::new());
            a.refresh_providers();
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_provider_select(move |i| {
            a.provider_sel.set(i);
            a.render_providers();
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_provider_set_enabled(move |id, v| {
            a.client.call(
                "providers.set",
                json!({ "id": id.to_string(), "enabled": v }),
                None,
            );
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_provider_save_config(move |id, base, model| {
            a.client.call(
                "providers.set",
                json!({ "id": id.to_string(), "base_url": base.to_string(), "model": model.to_string() }),
                None,
            );
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_provider_save_key(move |id, key| {
            a.client.call(
                "providers.set_key",
                json!({ "id": id.to_string(), "key": key.to_string() }),
                None,
            );
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_provider_clear_key(move |id| {
            a.client
                .call("providers.set_key", json!({ "id": id.to_string(), "key": "" }), None);
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_provider_test(move |id| {
            a.ui.set_provider_testing(true);
            a.ui.set_provider_test_result(SharedString::from("Testing…"));
            let tx = a.msg_tx.clone();
            a.client.call(
                "providers.test",
                json!({ "id": id.to_string() }),
                Some(Box::new(move |res| {
                    let out = match res {
                        Ok(v) => format!(
                            "✓ Connected — replied: {}",
                            v["reply"].as_str().unwrap_or("")
                        ),
                        Err(e) => format!("✗ {e}"),
                    };
                    let _ = tx.send(Msg::ProviderTest(out));
                })),
            );
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_provider_use(move |id| {
            a.client.call(
                "llm.set_active",
                json!({ "id": format!("provider:{}", id) }),
                None,
            );
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_llm_activate(move |id| {
            a.client
                .call("llm.set_active", json!({ "id": id.to_string() }), None);
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_llm_install(move |extra| {
            a.client
                .call("llm.install", json!({ "extra": extra.to_string() }), None);
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_llm_download(move |id| {
            a.client
                .call("llm.download", json!({ "id": id.to_string() }), None);
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_llm_delete(move |id| {
            a.client
                .call("llm.delete", json!({ "id": id.to_string() }), None);
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui
            .on_llm_cancel(move || a.client.call("models.cancel", json!({}), None));
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_llm_test(move |t| {
            a.ui.set_llm_test_busy(true);
            a.ui.set_llm_test_output(SharedString::new());
            a.ui.set_llm_test_stat(SharedString::new());
            let tx = a.msg_tx.clone();
            a.client.call(
                "llm.test",
                json!({ "text": t.to_string() }),
                Some(Box::new(move |res| {
                    let (out, stat) = match res {
                        Ok(v) => (
                            v["output"].as_str().unwrap_or_default().to_string(),
                            v["stat"].as_str().unwrap_or_default().to_string(),
                        ),
                        Err(e) => (format!("Error: {e}"), String::new()),
                    };
                    let _ = tx.send(Msg::LlmTest(out, stat));
                })),
            );
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_toggle_expand(move |id| {
            // master-detail: always select (no deselect on re-click)
            *a.expanded.borrow_mut() = id.to_string();
            a.render_models();
        });
    }
    app.ui.on_open_url(|url| {
        #[cfg(windows)]
        let mut cmd = {
            use std::os::windows::process::CommandExt;
            // `start` is a cmd.exe builtin; the empty "" is its window title slot.
            let mut c = std::process::Command::new("cmd");
            c.args(["/C", "start", "", url.as_str()]);
            c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW — no cmd flash
            c
        };
        #[cfg(not(windows))]
        let mut cmd = {
            let mut c = std::process::Command::new("xdg-open");
            c.arg(url.as_str());
            c
        };
        let _ = cmd
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn();
    });
    {
        // per-model language: merge into config.model_languages
        let a = Rc::clone(&app);
        app.ui.on_set_lang(move |id, idx| {
            let code = LANGS.get(idx as usize).map(|(_, c)| *c).unwrap_or("en");
            let mut all = a.cfg.borrow()["model_languages"].clone();
            if !all.is_object() {
                all = json!({});
            }
            all[id.to_string()] = json!(code);
            a.patch(kv("model_languages", all));
        });
    }
    {
        // per-model tunables: merge into config.model_params
        fn merge(a: &Rc<App>, id: &str, key: &str, val: Value) {
            let mut all = a.cfg.borrow()["model_params"].clone();
            if !all.is_object() {
                all = json!({});
            }
            if !all[id].is_object() {
                all[id] = json!({});
            }
            all[id][key] = val;
            a.patch(kv("model_params", all));
        }
        let a = Rc::clone(&app);
        app.ui.on_param_num(move |id, key, v| {
            // ints must not arrive as floats in YAML
            let val = if v.fract() == 0.0 {
                json!(v as i64)
            } else {
                json!((v as f64 * 100.0).round() / 100.0)
            };
            merge(&a, id.as_str(), key.as_str(), val);
        });
        let a = Rc::clone(&app);
        app.ui
            .on_param_bool(move |id, key, v| merge(&a, id.as_str(), key.as_str(), json!(v)));
    }
    {
        // per-model formatter tunables: merge into config.llm_params
        fn lmerge(a: &Rc<App>, id: &str, key: &str, val: Value) {
            let mut all = a.cfg.borrow()["llm_params"].clone();
            if !all.is_object() {
                all = json!({});
            }
            if !all[id].is_object() {
                all[id] = json!({});
            }
            all[id][key] = val;
            a.patch(kv("llm_params", all));
        }
        let a = Rc::clone(&app);
        app.ui.on_llm_param_num(move |id, key, v| {
            let val = if v.fract() == 0.0 {
                json!(v as i64)
            } else {
                json!((v as f64 * 100.0).round() / 100.0)
            };
            lmerge(&a, id.as_str(), key.as_str(), val);
        });
        let a = Rc::clone(&app);
        app.ui
            .on_llm_param_bool(move |id, key, v| lmerge(&a, id.as_str(), key.as_str(), json!(v)));
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_models_search(move |t| {
            *a.search.borrow_mut() = t.to_string();
            a.render_models();
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_models_filter(move |i| {
            a.filter.set(i);
            a.render_models();
        });
    }
    {
        // theme selector: stock resets overrides; custom applies its snapshot
        let a = Rc::clone(&app);
        app.ui.on_theme_picked(move |i| {
            let i = i as usize;
            let cfg = a.cfg.borrow();
            let mut customs: Vec<String> = cfg["custom_themes"]
                .as_object()
                .map(|o| o.keys().cloned().collect())
                .unwrap_or_default();
            customs.sort();
            let mut patch = Map::new();
            if i < theme::NAMES.len() {
                // stock pick: write the THEME's defaults (oled ships its own
                // bar geometry), resetting any overrides from the prior look
                for (k, d) in theme::theme_defaults(theme::NAMES[i]) {
                    patch.insert(k.to_string(), d);
                }
                patch.insert("theme".into(), json!(theme::NAMES[i]));
            } else if let Some(name) = customs.get(i - theme::NAMES.len()) {
                // custom pick: its base theme's defaults, then the snapshot —
                // fields the snapshot doesn't carry reset to the base look
                let based = cfg["custom_themes"][name]["based_on"]
                    .as_str()
                    .unwrap_or("macaw")
                    .to_string();
                for (k, d) in theme::theme_defaults(&based) {
                    patch.insert(k.to_string(), d);
                }
                if let Some(saved) = cfg["custom_themes"][name].as_object() {
                    for (k, v) in saved {
                        if k != "based_on" {
                            patch.insert(k.clone(), v.clone());
                        }
                    }
                }
                patch.insert("theme".into(), json!(format!("custom:{name}")));
            } else {
                return; // the transient "unsaved" entry
            }
            drop(cfg);
            a.patch(Value::Object(patch));
        });
    }
    {
        // save current look as a named custom theme (stock names protected)
        let a = Rc::clone(&app);
        app.ui.on_save_theme(move |name| {
            let name = name.trim().to_string();
            if name.is_empty() || theme::NAMES.contains(&name.as_str()) {
                a.toast("error", "Pick a name that isn't a built-in theme");
                return;
            }
            let cfg = a.cfg.borrow();
            let mut snapshot = Map::new();
            snapshot.insert("based_on".into(), json!(theme::base_name(&cfg)));
            for (k, d) in theme::override_defaults() {
                let v = &cfg[k];
                snapshot.insert(k.to_string(), if v.is_null() { d } else { v.clone() });
            }
            let mut all = cfg["custom_themes"].clone();
            if !all.is_object() {
                all = json!({});
            }
            all[&name] = Value::Object(snapshot);
            drop(cfg);
            let mut patch = Map::new();
            patch.insert("custom_themes".into(), all);
            patch.insert("theme".into(), json!(format!("custom:{name}")));
            a.patch(Value::Object(patch));
        });
    }
    {
        // delete the selected custom theme, fall back to its base
        let a = Rc::clone(&app);
        app.ui.on_delete_theme(move || {
            let cfg = a.cfg.borrow();
            let theme_str = cfg["theme"].as_str().unwrap_or("").to_string();
            let Some(name) = theme_str.strip_prefix("custom:") else {
                return;
            };
            let base = theme::base_name(&cfg);
            let mut all = cfg["custom_themes"].clone();
            if let Some(o) = all.as_object_mut() {
                o.remove(name);
            }
            drop(cfg);
            let mut patch = Map::new();
            for (k, d) in theme::theme_defaults(&base) {
                patch.insert(k.to_string(), d);
            }
            patch.insert("custom_themes".into(), all);
            patch.insert("theme".into(), json!(base));
            a.patch(Value::Object(patch));
        });
    }

    // Tray app: closing the main window hides it (and stops the mic meter).
    {
        let a = Rc::clone(&app);
        app.ui.window().on_close_requested(move || {
            let _ = a.ui.hide();
            a.sync_mic_monitor();
            slint::CloseRequestResponse::HideWindow
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_close_window(move || {
            let _ = a.ui.hide();
            a.sync_mic_monitor();
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_tab_changed(move |_| a.sync_mic_monitor());
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_restart_engine(move || a.restart_engine());
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_reload_model(move || {
            a.toast("info", "Reloading model…");
            a.client.call("model.reload", json!({}), None);
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_reload_formatter(move || {
            a.toast("info", "Reloading formatter…");
            let tx = a.msg_tx.clone();
            a.client.call(
                "llm.reload",
                json!({}),
                Some(Box::new(move |res| {
                    if let Ok(v) = res {
                        let _ = tx.send(Msg::FormatterStatus(v));
                    }
                })),
            );
        });
    }
    {
        let a = Rc::clone(&app);
        app.ui.on_restart_formatter(move || {
            a.toast("info", "Restarting formatter…");
            let tx = a.msg_tx.clone();
            a.client.call(
                "llm.restart",
                json!({}),
                Some(Box::new(move |res| {
                    if let Ok(v) = res {
                        let _ = tx.send(Msg::FormatterStatus(v));
                    }
                })),
            );
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
    // Rules must exist BEFORE the window maps (they only apply at map time);
    // the post-show dispatch fixes a window that mapped tiled anyway (rule
    // rejected / compositor restarted mid-session).
    hypr::install_main_rules();
    if flag.as_deref() != Some("--trigger") {
        let _ = app.ui.show();
    }
    slint::Timer::single_shot(std::time::Duration::from_millis(600), || {
        hypr::enforce_main_geometry();
    });

    slint::run_event_loop_until_quit().expect("event loop");
    APP.with(|a| a.borrow_mut().take());
    app.engine.borrow_mut().kill();
    single::release();
}
