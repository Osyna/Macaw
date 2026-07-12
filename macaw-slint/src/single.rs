//! Single instance over a unix socket (no DBus).
//!
//! First instance binds $XDG_RUNTIME_DIR/macaw-ui.sock and serves argv lines;
//! later instances write their args and exit. A stale socket (crash leftover)
//! is detected by a failed connect and reclaimed.

use std::io::{Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
use std::sync::mpsc::Sender;

fn sock_path() -> PathBuf {
    let dir = std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/tmp".into());
    PathBuf::from(dir).join("macaw-ui.sock")
}

/// Commands a second invocation forwards to the running instance.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Cmd {
    Show,
    Settings,
    Models,
    Trigger,
    Stop,
}

fn parse(line: &str) -> Cmd {
    match line.trim() {
        "--settings" => Cmd::Settings,
        "--models" => Cmd::Models,
        "--trigger" => Cmd::Trigger,
        "--stop" => Cmd::Stop,
        _ => Cmd::Show,
    }
}

/// Returns `None` when another instance is already running (args were
/// forwarded — caller must exit). Otherwise starts serving and reports
/// forwarded commands through `tx`.
pub fn acquire(argv_flag: Option<&str>, tx: Sender<Cmd>) -> Option<UnixListener> {
    let path = sock_path();
    match UnixStream::connect(&path) {
        Ok(mut stream) => {
            // Running instance: forward our flag (or a bare "show").
            let msg = argv_flag.unwrap_or("--show");
            let _ = stream.write_all(msg.as_bytes());
            return None;
        }
        Err(_) => {
            let _ = std::fs::remove_file(&path); // stale or absent — reclaim
        }
    }
    let listener = UnixListener::bind(&path).ok()?;
    let accept = listener.try_clone().ok()?;
    std::thread::Builder::new()
        .name("single-instance".into())
        .spawn(move || {
            for stream in accept.incoming().map_while(Result::ok) {
                let mut buf = String::new();
                let mut s = stream;
                let _ = s.set_read_timeout(Some(std::time::Duration::from_millis(200)));
                if s.read_to_string(&mut buf).is_ok() || !buf.is_empty() {
                    let _ = tx.send(parse(&buf));
                }
            }
        })
        .expect("spawn single-instance thread");
    Some(listener)
}

/// Best-effort cleanup on exit.
pub fn release() {
    let _ = std::fs::remove_file(sock_path());
}
