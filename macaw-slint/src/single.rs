//! Single instance + argv forwarding (no DBus).
//!
//! Unix: first instance binds $XDG_RUNTIME_DIR/macaw-ui.sock and serves argv
//! lines; later instances write their args and exit. A stale socket (crash
//! leftover) is detected by a failed connect and reclaimed.
//! Windows: same protocol over a loopback TCP port — the OS reclaims the
//! port on crash, so there is no stale-socket case.

use std::io::{Read, Write};
use std::sync::mpsc::Sender;

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

fn handle(mut s: impl Read, tx: &Sender<Cmd>) {
    let mut buf = String::new();
    if s.read_to_string(&mut buf).is_ok() || !buf.is_empty() {
        let _ = tx.send(parse(&buf));
    }
}

#[cfg(unix)]
mod imp {
    use super::*;
    use std::os::unix::net::{UnixListener, UnixStream};
    use std::path::PathBuf;

    pub struct Lock(#[allow(dead_code)] UnixListener);

    fn sock_path() -> PathBuf {
        let dir = std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/tmp".into());
        PathBuf::from(dir).join("macaw-ui.sock")
    }

    /// Returns `None` when another instance is already running (args were
    /// forwarded — caller must exit). Otherwise starts serving and reports
    /// forwarded commands through `tx`.
    pub fn acquire(argv_flag: Option<&str>, tx: Sender<Cmd>) -> Option<Lock> {
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
                while let Ok((s, _)) = accept.accept() {
                    let _ = s.set_read_timeout(Some(std::time::Duration::from_millis(200)));
                    handle(s, &tx);
                }
            })
            .expect("spawn single-instance thread");
        Some(Lock(listener))
    }

    /// Best-effort cleanup on exit.
    pub fn release() {
        let _ = std::fs::remove_file(sock_path());
    }
}

#[cfg(windows)]
mod imp {
    use super::*;
    use std::net::{TcpListener, TcpStream};

    // Loopback-only; fixed like the engine trigger port (47539). A concurrent
    // non-macaw binder just costs us single-instancing, never correctness.
    const ADDR: &str = "127.0.0.1:47542";

    pub struct Lock(#[allow(dead_code)] TcpListener);

    pub fn acquire(argv_flag: Option<&str>, tx: Sender<Cmd>) -> Option<Lock> {
        if let Ok(mut stream) = TcpStream::connect(ADDR) {
            let msg = argv_flag.unwrap_or("--show");
            let _ = stream.write_all(msg.as_bytes());
            return None;
        }
        let listener = TcpListener::bind(ADDR).ok()?;
        let accept = listener.try_clone().ok()?;
        std::thread::Builder::new()
            .name("single-instance".into())
            .spawn(move || {
                while let Ok((s, _)) = accept.accept() {
                    let _ = s.set_read_timeout(Some(std::time::Duration::from_millis(200)));
                    handle(s, &tx);
                }
            })
            .expect("spawn single-instance thread");
        Some(Lock(listener))
    }

    /// The OS releases the TCP port when the listener drops — nothing to do.
    pub fn release() {}
}

pub use imp::{acquire, release};
