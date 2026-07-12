//! StatusNotifierItem tray via ksni (pure D-Bus — no GTK).
//!
//! Menu callbacks run on ksni's service task and must never block, so every
//! action is forwarded through the app command channel. Recording state
//! flows back in via `Handle::update`.

use std::sync::mpsc::Sender;

use ksni::menu::StandardItem;
use ksni::{Icon, MenuItem, Tray};

use crate::single::Cmd;

/// 24x24 ARGB32 (network order), baked from src-tauri/icons/32x32.png.
static ICON: &[u8] = include_bytes!("tray-icon-24.argb");

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
            data: ICON.to_vec(),
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
