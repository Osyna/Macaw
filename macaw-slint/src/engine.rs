//! Engine process lifecycle.
//!
//! The UI owns one `macaw-engine` child (the headless Python engine). Its
//! stdin is held open for the whole app lifetime — the engine exits on stdin
//! EOF, so dropping the child's stdin (or dying) is our crash-safe cleanup.
//! Resolution order:
//!   1. $MACAW_ENGINE_CMD          — dev override, e.g. "uv run macaw-engine"
//!   2. <exe_dir>/macaw-engine     — packaged layout (binary sidecar)
//!   3. "macaw-engine" on $PATH    — installed engine

use std::io::{BufRead, BufReader};
use std::process::{Child, ChildStdin, Command, Stdio};

pub struct Engine {
    child: Option<Child>,
    /// Held, never written: keeps the engine's stdin-EOF watchdog armed.
    _stdin: Option<ChildStdin>,
}

impl Engine {
    pub fn spawn(token: &str, ws_port: u16) -> Engine {
        let (bin, mut args) = resolve();
        args.extend([
            "--token".into(),
            token.into(),
            "--ws-port".into(),
            ws_port.to_string(),
        ]);
        match Command::new(&bin)
            .args(&args)
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
                let stdin = child.stdin.take();
                eprintln!("[shell] engine: {bin}");
                Engine {
                    child: Some(child),
                    _stdin: stdin,
                }
            }
            Err(e) => {
                eprintln!("[shell] failed to spawn engine `{bin}`: {e}");
                Engine {
                    child: None,
                    _stdin: None,
                }
            }
        }
    }

    pub fn kill(&mut self) {
        self._stdin = None; // EOF: polite shutdown signal
        if let Some(mut child) = self.child.take() {
            // Give the engine a beat to exit on EOF, then make sure.
            for _ in 0..10 {
                if matches!(child.try_wait(), Ok(Some(_))) {
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn resolve() -> (String, Vec<String>) {
    if let Ok(cmd) = std::env::var("MACAW_ENGINE_CMD") {
        let mut argv: Vec<String> = cmd.split_whitespace().map(String::from).collect();
        if !argv.is_empty() {
            let bin = argv.remove(0);
            return (bin, argv);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let sidecar = exe.with_file_name("macaw-engine");
        if sidecar.is_file() {
            return (sidecar.to_string_lossy().into_owned(), vec![]);
        }
    }
    ("macaw-engine".into(), vec![])
}

/// Engine output -> our stderr, line-buffered, `[engine]`-prefixed.
fn forward(stream: impl std::io::Read + Send + 'static) {
    std::thread::spawn(move || {
        for line in BufReader::new(stream).lines().map_while(Result::ok) {
            eprintln!("[engine] {line}");
        }
    });
}
