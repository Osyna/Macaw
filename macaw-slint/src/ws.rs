//! Blocking WebSocket client for the macaw engine (one thread, no runtime).
//!
//! Protocol (ws://127.0.0.1:47540): first frame {"auth": token}, then
//! {"id","method","params"} -> {"id","result"|"error"} replies interleaved
//! with broadcast {"event","data"} frames.
//!
//! One thread owns the socket. Full duplex without async: the underlying
//! TcpStream gets a short read timeout, so the loop alternates "drain
//! incoming frames" / "flush queued requests". Replies resolve callbacks
//! registered per request id; events fan out through a channel to main.

use std::collections::HashMap;
use std::net::TcpStream;
use std::sync::mpsc::{channel, Receiver, Sender};
use std::time::Duration;

use serde_json::{json, Value};
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{Error as WsError, Message, WebSocket};

pub type Reply = Box<dyn FnOnce(Result<Value, String>) + Send + 'static>;

/// Engine broadcast, decoded just enough for the UI.
#[derive(Debug, Clone)]
pub enum Event {
    Connected,
    Disconnected,
    State {
        state: String,
        detail: String,
    },
    Level {
        rms: f32,
    },
    Config {
        config: Value,
    },
    Models,
    Progress {
        op: String,
        key: String,
        msg: String,
        pct: Option<f32>,
        done: bool,
        ok: Option<bool>,
    },
    Toast {
        level: String,
        msg: String,
    },
    Show {
        window: String,
    },
    HotkeyCaptured {
        spec: String,
    },
}

enum Outgoing {
    Call {
        method: String,
        params: Value,
        reply: Option<Reply>,
    },
    Shutdown,
}

/// Cheap-to-clone handle for issuing RPCs from any thread.
#[derive(Clone)]
pub struct Client {
    tx: Sender<Outgoing>,
}

impl Client {
    /// Fire an RPC; `reply` runs on the ws thread — hop into the UI event
    /// loop yourself if you touch widgets.
    pub fn call(&self, method: &str, params: Value, reply: Option<Reply>) {
        let _ = self.tx.send(Outgoing::Call {
            method: method.into(),
            params,
            reply,
        });
    }

    pub fn shutdown(&self) {
        let _ = self.tx.send(Outgoing::Shutdown);
    }
}

/// Spawn the client thread. Reconnects until `Shutdown`; every (re)connect
/// emits `Connected` after auth so the app can re-pull config/models.
pub fn spawn(port: u16, token: String, events: Sender<Event>) -> Client {
    let (tx, rx) = channel::<Outgoing>();
    let client = Client { tx };
    std::thread::Builder::new()
        .name("ws".into())
        .spawn(move || run(port, &token, &events, &rx))
        .expect("spawn ws thread");
    client
}

fn run(port: u16, token: &str, events: &Sender<Event>, rx: &Receiver<Outgoing>) {
    let url = format!("ws://127.0.0.1:{port}");
    loop {
        let mut ws = match connect(&url, token) {
            Some(ws) => ws,
            None => {
                // Engine not up yet (boot race) or restarting — retry, but
                // drain control messages so quit is never blocked on it.
                match rx.recv_timeout(Duration::from_millis(300)) {
                    Ok(Outgoing::Shutdown)
                    | Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => return,
                    _ => continue,
                }
            }
        };
        let _ = events.send(Event::Connected);

        let mut next_id: u64 = 1;
        let mut pending: HashMap<u64, Reply> = HashMap::new();
        'conn: loop {
            // 1. Flush queued outbound calls.
            loop {
                match rx.try_recv() {
                    Ok(Outgoing::Call {
                        method,
                        params,
                        reply,
                    }) => {
                        let id = next_id;
                        next_id += 1;
                        if let Some(r) = reply {
                            pending.insert(id, r);
                        }
                        let frame = json!({"id": id, "method": method, "params": params});
                        if ws.send(Message::text(frame.to_string())).is_err() {
                            break 'conn;
                        }
                    }
                    Ok(Outgoing::Shutdown) => {
                        let _ = ws.close(None);
                        return;
                    }
                    Err(std::sync::mpsc::TryRecvError::Empty) => break,
                    Err(std::sync::mpsc::TryRecvError::Disconnected) => return,
                }
            }
            // 2. Drain incoming frames until the read times out.
            match ws.read() {
                Ok(Message::Text(txt)) => {
                    if let Ok(v) = serde_json::from_str::<Value>(&txt) {
                        dispatch(v, &mut pending, events);
                    }
                }
                Ok(Message::Close(_)) => break 'conn,
                Ok(_) => {} // ping/pong handled by tungstenite; binary unused
                Err(WsError::Io(e))
                    if e.kind() == std::io::ErrorKind::WouldBlock
                        || e.kind() == std::io::ErrorKind::TimedOut => {}
                Err(e) => {
                    eprintln!("[shell] ws read error: {e}");
                    break 'conn;
                }
            }
        }
        // Connection dropped: fail pending calls, tell the app, reconnect.
        for (_, reply) in pending.drain() {
            reply(Err("engine connection lost".into()));
        }
        let _ = events.send(Event::Disconnected);
    }
}

fn connect(url: &str, token: &str) -> Option<WebSocket<MaybeTlsStream<TcpStream>>> {
    let (mut ws, _resp) = tungstenite::connect(url).ok()?;
    ws.send(Message::text(json!({"auth": token}).to_string()))
        .ok()?;
    if let MaybeTlsStream::Plain(stream) = ws.get_ref() {
        // Short read timeout turns the blocking read into a poll step.
        let _ = stream.set_read_timeout(Some(Duration::from_millis(40)));
    }
    Some(ws)
}

fn dispatch(v: Value, pending: &mut HashMap<u64, Reply>, events: &Sender<Event>) {
    if let Some(id) = v.get("id").and_then(Value::as_u64) {
        if let Some(reply) = pending.remove(&id) {
            if let Some(err) = v.get("error") {
                reply(Err(err
                    .as_str()
                    .map(String::from)
                    .unwrap_or_else(|| err.to_string())));
            } else {
                reply(Ok(v.get("result").cloned().unwrap_or(Value::Null)));
            }
        }
        return;
    }
    let Some(event) = v.get("event").and_then(Value::as_str) else {
        return;
    };
    let d = v.get("data").cloned().unwrap_or(Value::Null);
    let s = |k: &str| {
        d.get(k)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    };
    let ev = match event {
        "state" => Event::State {
            state: s("state"),
            detail: s("detail"),
        },
        "level" => Event::Level {
            rms: d.get("rms").and_then(Value::as_f64).unwrap_or(0.0) as f32,
        },
        "config" => Event::Config {
            config: d.get("config").cloned().unwrap_or(Value::Null),
        },
        "models" | "llm" => Event::Models,
        "progress" => Event::Progress {
            op: s("op"),
            key: s("key"),
            msg: s("msg"),
            pct: d.get("pct").and_then(Value::as_f64).map(|p| p as f32),
            done: d.get("done").and_then(Value::as_bool).unwrap_or(false),
            ok: d.get("ok").and_then(Value::as_bool),
        },
        "toast" => Event::Toast {
            level: s("level"),
            msg: s("msg"),
        },
        "show" => Event::Show {
            window: s("window"),
        },
        "hotkey_captured" => Event::HotkeyCaptured { spec: s("spec") },
        _ => return,
    };
    let _ = events.send(ev);
}
