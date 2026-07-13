//! System tray, one facade, two implementations:
//! unix — StatusNotifierItem via ksni (pure D-Bus, no GTK);
//! windows — notification-area icon via tray-icon (Win32).
//!
//! Menu callbacks run off the UI thread and must never block, so every
//! action is forwarded through the app command channel. Recording state
//! flows back in via `TrayHandle::update`.

/// 24x24 ARGB32 (network order), baked from the 32px app icon.
static ICON: &[u8] = include_bytes!("tray-icon-24.argb");

#[cfg(unix)]
mod imp {
    use std::sync::mpsc::Sender;

    use ksni::menu::StandardItem;
    use ksni::{Icon, MenuItem, Tray};

    use crate::single::Cmd;

    pub struct MacawTray {
        tx: Sender<Cmd>,
        pub recording: bool,
    }

    impl Tray for MacawTray {
        fn id(&self) -> String {
            "macaw".into()
        }

        fn title(&self) -> String {
            if self.recording {
                "Macaw — recording".into()
            } else {
                "Macaw".into()
            }
        }

        fn icon_pixmap(&self) -> Vec<Icon> {
            vec![Icon {
                width: 24,
                height: 24,
                data: super::ICON.to_vec(),
            }]
        }

        fn activate(&mut self, _x: i32, _y: i32) {
            let _ = self.tx.send(Cmd::Show);
        }

        fn menu(&self) -> Vec<MenuItem<Self>> {
            let send = |cmd: Cmd| {
                let tx = self.tx.clone();
                Box::new(move |_t: &mut Self| {
                    let _ = tx.send(cmd);
                })
            };
            vec![
                StandardItem {
                    label: if self.recording {
                        "Stop recording".into()
                    } else {
                        "Start recording".into()
                    },
                    activate: send(Cmd::Trigger),
                    ..Default::default()
                }
                .into(),
                MenuItem::Separator,
                StandardItem {
                    label: "Settings…".into(),
                    activate: send(Cmd::Settings),
                    ..Default::default()
                }
                .into(),
                StandardItem {
                    label: "Models…".into(),
                    activate: send(Cmd::Models),
                    ..Default::default()
                }
                .into(),
                MenuItem::Separator,
                StandardItem {
                    label: "Quit".into(),
                    activate: send(Cmd::Stop),
                    ..Default::default()
                }
                .into(),
            ]
        }
    }

    pub type TrayHandle = ksni::blocking::Handle<MacawTray>;

    /// Spawn the tray service. `assume_sni_available` keeps us alive when the
    /// app starts before waybar; the item appears once a watcher registers.
    pub fn spawn(tx: Sender<Cmd>) -> Option<TrayHandle> {
        use ksni::blocking::TrayMethods;
        match (MacawTray {
            tx,
            recording: false,
        })
        .assume_sni_available(true)
        .spawn()
        {
            Ok(h) => Some(h),
            Err(e) => {
                eprintln!("[shell] tray unavailable: {e}");
                None
            }
        }
    }
}

#[cfg(windows)]
mod imp {
    use std::cell::Cell;
    use std::sync::mpsc::Sender;

    use tray_icon::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
    use tray_icon::{
        Icon, MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent,
    };

    use crate::single::Cmd;

    /// Mirror of the ksni tray's mutable state, so main.rs can keep the same
    /// `handle.update(|t| t.recording = …)` call on both platforms.
    pub struct MacawTray {
        pub recording: bool,
    }

    pub struct TrayHandle {
        tray: TrayIcon,
        toggle: MenuItem,
        recording: Cell<bool>,
    }

    impl TrayHandle {
        pub fn update(&self, f: impl FnOnce(&mut MacawTray)) {
            let mut t = MacawTray {
                recording: self.recording.get(),
            };
            f(&mut t);
            if t.recording == self.recording.get() {
                return;
            }
            self.recording.set(t.recording);
            let (label, tip) = if t.recording {
                ("Stop recording", "Macaw — recording")
            } else {
                ("Start recording", "Macaw")
            };
            self.toggle.set_text(label);
            let _ = self.tray.set_tooltip(Some(tip));
        }
    }

    /// ksni stores ARGB32 network order; tray-icon wants RGBA8.
    fn rgba() -> Vec<u8> {
        super::ICON
            .chunks_exact(4)
            .flat_map(|p| [p[1], p[2], p[3], p[0]])
            .collect()
    }

    pub fn spawn(tx: Sender<Cmd>) -> Option<TrayHandle> {
        let icon = Icon::from_rgba(rgba(), 24, 24).ok()?;
        let toggle = MenuItem::new("Start recording", true, None);
        let settings = MenuItem::new("Settings…", true, None);
        let models = MenuItem::new("Models…", true, None);
        let quit = MenuItem::new("Quit", true, None);
        let menu = Menu::new();
        menu.append_items(&[
            &toggle,
            &PredefinedMenuItem::separator(None),
            &settings,
            &models,
            &PredefinedMenuItem::separator(None),
            &quit,
        ])
        .ok()?;
        let ids = [
            (toggle.id().clone(), Cmd::Trigger),
            (settings.id().clone(), Cmd::Settings),
            (models.id().clone(), Cmd::Models),
            (quit.id().clone(), Cmd::Stop),
        ];
        let tray = match TrayIconBuilder::new()
            .with_menu(Box::new(menu))
            .with_tooltip("Macaw")
            .with_icon(icon)
            .build()
        {
            Ok(t) => t,
            Err(e) => {
                eprintln!("[shell] tray unavailable: {e}");
                return None;
            }
        };
        // Menu + icon events arrive on global receivers; forward as commands.
        {
            let tx = tx.clone();
            std::thread::spawn(move || {
                while let Ok(ev) = MenuEvent::receiver().recv() {
                    if let Some((_, cmd)) = ids.iter().find(|(id, _)| *id == ev.id) {
                        let _ = tx.send(*cmd);
                    }
                }
            });
        }
        std::thread::spawn(move || {
            while let Ok(ev) = TrayIconEvent::receiver().recv() {
                if let TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                } = ev
                {
                    let _ = tx.send(Cmd::Show);
                }
            }
        });
        Some(TrayHandle {
            tray,
            toggle,
            recording: Cell::new(false),
        })
    }
}

pub use imp::{spawn, TrayHandle};
