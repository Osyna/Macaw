//! Handle to the layer-shell overlay child process (macaw-overlay).
//!
//! Wayland-only. The child exits(2) when zwlr_layer_shell_v1 is missing
//! (e.g. GNOME); any send/aliveness failure makes the caller fall back to
//! the plain winit overlay window. Protocol: one JSON object per line on
//! the child's stdin; stdin EOF is its shutdown signal.

use std::io::Write;
use std::process::{Child, ChildStdin, Command, Stdio};

use serde_json::Value;

pub struct LsOverlay {
    child: Child,
    stdin: ChildStdin,
}

impl LsOverlay {
    pub fn spawn() -> Option<LsOverlay> {
        std::env::var_os("WAYLAND_DISPLAY")?;
        let bin = std::env::current_exe()
            .ok()?
            .with_file_name("macaw-overlay");
        let mut child = Command::new(&bin)
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::inherit()) // its diagnostics join ours
            .spawn()
            .map_err(|e| eprintln!("[shell] overlay spawn failed: {e}"))
            .ok()?;
        let stdin = child.stdin.take()?;
        eprintln!("[shell] layer-shell overlay: {}", bin.display());
        Some(LsOverlay { child, stdin })
    }

    /// False = the child is gone (caller should fall back and drop us).
    pub fn send(&mut self, v: &Value) -> bool {
        if matches!(self.child.try_wait(), Ok(Some(_))) {
            return false;
        }
        let mut line = v.to_string();
        line.push('\n');
        self.stdin.write_all(line.as_bytes()).is_ok()
    }

    pub fn kill(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
