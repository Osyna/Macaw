use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};

use parking_lot::Mutex;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Fixed engine WS port (contract: ws://127.0.0.1:47540).
const WS_PORT: u16 = 47540;

/// wlr-layer-shell overlay positioning (Wayland). Normal toplevels cannot
/// self-position on Wayland — the compositor just floats them — so the
/// overlay anchors itself with gtk-layer-shell when the runtime lib exists.
/// dlopen keeps this a soft dependency: no lib -> `overlay_layout` errors and
/// the frontend falls back to setSize/setPosition (fine on X11/Windows).
#[cfg(target_os = "linux")]
mod layer_shell {
    use std::ffi::{c_char, c_int, CString};
    use std::path::PathBuf;
    use std::sync::{LazyLock, OnceLock};

    /// Set once in setup() (runtime input — needs the AppHandle). Lets the
    /// bundled AppImage copy of libgtk-layer-shell be found without any
    /// system install.
    pub static RESOURCE_DIR: OnceLock<PathBuf> = OnceLock::new();

    // gtk-layer-shell.h enums (ABI-stable C API)
    pub const LAYER_OVERLAY: c_int = 3;
    pub const EDGE_LEFT: c_int = 0;
    pub const EDGE_RIGHT: c_int = 1;
    pub const EDGE_TOP: c_int = 2;
    pub const EDGE_BOTTOM: c_int = 3;

    type GtkWin = *mut std::ffi::c_void;

    pub struct Api {
        _lib: libloading::Library, // keeps the symbols below alive
        pub is_supported: unsafe extern "C" fn() -> c_int,
        pub init_for_window: unsafe extern "C" fn(GtkWin),
        pub set_layer: unsafe extern "C" fn(GtkWin, c_int),
        pub set_anchor: unsafe extern "C" fn(GtkWin, c_int, c_int),
        pub set_margin: unsafe extern "C" fn(GtkWin, c_int, c_int),
        pub set_exclusive_zone: unsafe extern "C" fn(GtkWin, c_int),
        pub set_namespace: unsafe extern "C" fn(GtkWin, *const c_char),
    }

    unsafe impl Send for Api {}
    unsafe impl Sync for Api {}

    pub static API: LazyLock<Option<Api>> = LazyLock::new(|| unsafe {
        let lib = ["libgtk-layer-shell.so.0".into()]
            .into_iter()
            .chain(RESOURCE_DIR.get().into_iter().flat_map(|d| {
                ["libgtk-layer-shell.so.0", "resources/libgtk-layer-shell.so.0"]
                    .map(|rel| d.join(rel).into_os_string())
            }))
            .find_map(|cand| libloading::Library::new(cand).ok())?;
        macro_rules! sym {
            ($name:literal) => {
                *lib.get(concat!($name, "\0").as_bytes()).ok()?
            };
        }
        let api = Api {
            is_supported: sym!("gtk_layer_is_supported"),
            init_for_window: sym!("gtk_layer_init_for_window"),
            set_layer: sym!("gtk_layer_set_layer"),
            set_anchor: sym!("gtk_layer_set_anchor"),
            set_margin: sym!("gtk_layer_set_margin"),
            set_exclusive_zone: sym!("gtk_layer_set_exclusive_zone"),
            set_namespace: sym!("gtk_layer_set_namespace"),
            _lib: lib,
        };
        (api.is_supported)().ne(&0).then_some(api)
    });

    /// One-time init — MUST run on the GTK main thread, on a still-hidden
    /// (unrealized) window.
    pub fn init(win: GtkWin) {
        let api = API.as_ref().expect("checked by caller");
        unsafe {
            (api.init_for_window)(win);
            (api.set_layer)(win, LAYER_OVERLAY);
            (api.set_exclusive_zone)(win, 0);
            let ns = CString::new("macaw-overlay").unwrap();
            (api.set_namespace)(win, ns.as_ptr());
        }
    }

    /// Anchor + margins for a `window_position` config value.
    pub fn layout(win: GtkWin, position: &str, x: i32, y: i32) {
        let api = API.as_ref().expect("checked by caller");
        const M: c_int = 24; // screen-edge margin, matches the old overlay
        let (top, bottom, left, right, mt, mb, ml, mr) = match position {
            "top_left" => (1, 0, 1, 0, M, 0, M, 0),
            "top_center" => (1, 0, 0, 0, M, 0, 0, 0),
            "top_right" => (1, 0, 0, 1, M, 0, 0, M),
            "bottom_left" => (0, 1, 1, 0, 0, M, M, 0),
            "bottom_right" => (0, 1, 0, 1, 0, M, 0, M),
            "custom" => (1, 0, 1, 0, y.max(0), 0, x.max(0), 0),
            _ => (0, 1, 0, 0, 0, M, 0, 0), // bottom_center
        };
        unsafe {
            (api.set_anchor)(win, EDGE_TOP, top);
            (api.set_anchor)(win, EDGE_BOTTOM, bottom);
            (api.set_anchor)(win, EDGE_LEFT, left);
            (api.set_anchor)(win, EDGE_RIGHT, right);
            (api.set_margin)(win, EDGE_TOP, mt);
            (api.set_margin)(win, EDGE_BOTTOM, mb);
            (api.set_margin)(win, EDGE_LEFT, ml);
            (api.set_margin)(win, EDGE_RIGHT, mr);
        }
    }
}

enum EngineChild {
    /// Dev override (MACAW_ENGINE_CMD). Child keeps its piped stdin open for the
    /// app lifetime; the engine exits on stdin EOF (parent-death watchdog).
    Std(Child),
    /// Bundled sidecar; the plugin's CommandChild likewise holds stdin open.
    Sidecar(CommandChild),
    None,
}

struct EngineState {
    token: String,
    child: Mutex<EngineChild>,
}

#[tauri::command]
fn engine_info(state: tauri::State<'_, EngineState>) -> serde_json::Value {
    serde_json::json!({ "port": WS_PORT, "token": state.token })
}

#[tauri::command]
fn autostart_enable(app: AppHandle) -> Result<(), String> {
    use tauri_plugin_autostart::ManagerExt;
    app.autolaunch().enable().map_err(|e| e.to_string())
}

#[tauri::command]
fn autostart_disable(app: AppHandle) -> Result<(), String> {
    use tauri_plugin_autostart::ManagerExt;
    app.autolaunch().disable().map_err(|e| e.to_string())
}

#[tauri::command]
fn autostart_status(app: AppHandle) -> Result<bool, String> {
    use tauri_plugin_autostart::ManagerExt;
    app.autolaunch().is_enabled().map_err(|e| e.to_string())
}

/// Anchor/position the overlay window. Ok(()) only when layer-shell handled
/// it (Linux Wayland + lib present); any Err tells the frontend to fall back
/// to plain setSize/setPosition (correct on X11 and Windows).
#[cfg(target_os = "linux")]
#[tauri::command]
fn overlay_layout(
    app: AppHandle,
    position: String,
    x: i32,
    y: i32,
    width: i32,
    height: i32,
) -> Result<(), String> {
    use std::sync::atomic::{AtomicBool, Ordering};
    static LAYER_INIT: AtomicBool = AtomicBool::new(false);

    if layer_shell::API.is_none() {
        return Err("layer-shell unavailable".into());
    }
    let win = app
        .get_webview_window("overlay")
        .ok_or("no overlay window")?;
    let w2 = win.clone();
    win.run_on_main_thread(move || {
        let Ok(gtk_win) = w2.gtk_window() else { return };
        use gtk::glib::translate::ToGlibPtr;
        use gtk::prelude::{Cast, GtkWindowExt};
        let gtk_win = gtk_win.upcast::<gtk::Window>();
        let ptr: *mut gtk::ffi::GtkWindow = ToGlibPtr::to_glib_none(&gtk_win).0;
        let ptr = ptr.cast::<std::ffi::c_void>();
        // init_for_window must precede the first map; the overlay is created
        // hidden and only ever shown after a layout call, so this holds.
        if !LAYER_INIT.swap(true, Ordering::SeqCst) {
            layer_shell::init(ptr);
        }
        layer_shell::layout(ptr, &position, x, y);
        gtk_win.resize(width.max(1), height.max(1));
    })
    .map_err(|e| e.to_string())
}

#[cfg(not(target_os = "linux"))]
#[tauri::command]
fn overlay_layout(position: String, x: i32, y: i32, width: i32, height: i32) -> Result<(), String> {
    let _ = (position, x, y, width, height);
    Err("native positioning".into())
}

fn forward(stream: impl std::io::Read + Send + 'static) {
    std::thread::spawn(move || {
        for line in BufReader::new(stream).lines().map_while(Result::ok) {
            eprintln!("[engine] {line}");
        }
    });
}

fn spawn_engine(app: &AppHandle, token: &str) -> EngineChild {
    if let Ok(cmd) = std::env::var("MACAW_ENGINE_CMD") {
        let mut argv: Vec<String> = cmd.split_whitespace().map(String::from).collect();
        if argv.is_empty() {
            eprintln!("[engine] MACAW_ENGINE_CMD is empty");
            return EngineChild::None;
        }
        let bin = argv.remove(0);
        return match Command::new(&bin)
            .args(&argv)
            .args(["--token", token])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
        {
            Ok(mut child) => {
                if let Some(out) = child.stdout.take() {
                    forward(out);
                }
                if let Some(err) = child.stderr.take() {
                    forward(err);
                }
                EngineChild::Std(child)
            }
            Err(e) => {
                eprintln!("[engine] failed to spawn `{bin}`: {e}");
                EngineChild::None
            }
        };
    }

    let cmd = match app.shell().sidecar("macaw-engine") {
        Ok(cmd) => cmd,
        Err(e) => {
            eprintln!("[engine] sidecar unavailable: {e}");
            return EngineChild::None;
        }
    };
    match cmd.args(["--token", token]).spawn() {
        Ok((mut rx, child)) => {
            tauri::async_runtime::spawn(async move {
                while let Some(ev) = rx.recv().await {
                    match ev {
                        CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                            eprintln!("[engine] {}", String::from_utf8_lossy(&line).trim_end());
                        }
                        CommandEvent::Error(e) => eprintln!("[engine] error: {e}"),
                        CommandEvent::Terminated(p) => {
                            eprintln!("[engine] exited (code {:?})", p.code);
                            break;
                        }
                        _ => {}
                    }
                }
            });
            EngineChild::Sidecar(child)
        }
        Err(e) => {
            eprintln!("[engine] failed to spawn sidecar: {e}");
            EngineChild::None
        }
    }
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        // Wayland can't move windows between workspaces: an already-mapped
        // window would stay (and yank focus to) wherever it was first shown.
        // Unmap + remap makes the compositor place it on the CURRENT workspace.
        if w.is_visible().unwrap_or(false) && !w.is_focused().unwrap_or(false) {
            let _ = w.hide();
        }
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn show_tab(app: &AppHandle, tab: &str) {
    show_main(app);
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.emit("show-tab", tab);
    }
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let toggle = MenuItem::with_id(app, "toggle", "Toggle recording", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Settings…", true, None::<&str>)?;
    let models = MenuItem::with_id(app, "models", "Models…", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &toggle,
            &PredefinedMenuItem::separator(app)?,
            &settings,
            &models,
            &PredefinedMenuItem::separator(app)?,
            &quit,
        ],
    )?;

    TrayIconBuilder::with_id("macaw-tray")
        .icon(app.default_window_icon().expect("bundle icon missing").clone())
        .tooltip("Macaw")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            // The main webview is created hidden and never destroyed (close => hide),
            // so it is always alive to bridge tray actions to the engine over WS.
            "toggle" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.emit("tray-toggle", ());
                }
            }
            "settings" => show_tab(app, "settings"),
            "models" => show_tab(app, "models"),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // webkit2gtk's DMA-BUF renderer crashes the compositor connection on
    // NVIDIA + Wayland (Gdk "Error 71 dispatching to Wayland display" as soon
    // as a window maps). Falling back to the shared-memory path is the
    // ecosystem-standard fix and costs little on other GPUs.
    #[cfg(target_os = "linux")]
    {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
        // linuxdeploy's GTK AppRun hook exports GDK_BACKEND=x11 (blanket
        // workaround for webkit Wayland crashes — ours is the DMA-BUF one,
        // fixed above). XWayland kills wlr-layer-shell anchoring and gets the
        // overlay TILED on Hyprland, so reclaim native Wayland when present.
        if std::env::var_os("WAYLAND_DISPLAY").is_some() {
            std::env::set_var("GDK_BACKEND", "wayland,x11");
        }
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            // `macaw <flag>` (thin CLI wrapper) lands here when the app runs.
            let has = |f: &str| argv.iter().any(|a| a == f);
            if has("--stop") {
                app.exit(0);
            } else if has("--trigger") {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.emit("tray-toggle", ());
                }
            } else if has("--models") {
                show_tab(app, "models");
            } else if has("--settings") {
                show_tab(app, "settings");
            } else {
                show_main(app);
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .invoke_handler(tauri::generate_handler![
            engine_info,
            autostart_enable,
            autostart_disable,
            autostart_status,
            overlay_layout
        ])
        .setup(|app| {
            #[cfg(target_os = "linux")]
            match app.path().resource_dir() {
                Ok(dir) => {
                    eprintln!("[shell] resource_dir = {}", dir.display());
                    let _ = layer_shell::RESOURCE_DIR.set(dir);
                }
                Err(e) => eprintln!("[shell] resource_dir error: {e}"),
            }
            let token = uuid::Uuid::new_v4().to_string();
            let child = spawn_engine(app.handle(), &token);
            app.manage(EngineState {
                token,
                child: Mutex::new(child),
            });

            // NOTE: set_ignore_cursor_events on a *hidden* GTK window aborts
            // (tao unwraps a None GdkWindow) — the overlay sets click-through
            // from JS right after each show(), when the window is realized.

            build_tray(app.handle())?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Tray app: closing the main window hides it; the webview stays alive.
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Macaw")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                // Belt and braces: stdin EOF (pipe drop) already makes the engine
                // exit; kill covers a wedged child.
                if let Some(state) = app.try_state::<EngineState>() {
                    match std::mem::replace(&mut *state.child.lock(), EngineChild::None) {
                        EngineChild::Std(mut c) => {
                            let _ = c.kill();
                        }
                        EngineChild::Sidecar(c) => {
                            let _ = c.kill();
                        }
                        EngineChild::None => {}
                    }
                }
            }
        });
}
