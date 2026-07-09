import { invoke } from "@tauri-apps/api/core";

export type Json = any;

export interface Engine {
  call(method: string, params?: Json): Promise<Json>;
  /** Subscribe to an engine event; returns unsubscribe. Synthetic "open" fires on every (re)connect. */
  on(event: string, fn: (data: Json) => void): () => void;
  readonly connected: boolean;
}

interface Creds {
  port: number;
  token: string;
}

async function creds(): Promise<Creds> {
  try {
    return await invoke<Creds>("engine_info");
  } catch {
    // Pure-browser dev fallback: ?token=dev&port=47540
    const q = new URLSearchParams(location.search);
    return { port: Number(q.get("port") ?? 47540), token: q.get("token") ?? "dev" };
  }
}

/** Resolves after the auth handshake succeeds; keeps reconnecting forever after that. */
export function connect(): Promise<Engine> {
  const listeners = new Map<string, Set<(data: Json) => void>>();
  const pending = new Map<number, { resolve: (v: Json) => void; reject: (e: Error) => void }>();
  let ws: WebSocket | null = null;
  let authed = false;
  let nextId = 1;
  let backoff = 500;

  const emit = (event: string, data: Json): void => {
    listeners.get(event)?.forEach((fn) => {
      try {
        fn(data);
      } catch (e) {
        console.error(`listener for "${event}" threw`, e);
      }
    });
  };

  const engine: Engine = {
    call(method, params) {
      if (!authed || !ws || ws.readyState !== WebSocket.OPEN) {
        return Promise.reject(new Error("engine not connected"));
      }
      const id = nextId++;
      const { promise, resolve, reject } = Promise.withResolvers<Json>();
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params: params ?? {} }));
      return promise;
    },
    on(event, fn) {
      let set = listeners.get(event);
      if (!set) listeners.set(event, (set = new Set()));
      set.add(fn);
      return () => set.delete(fn);
    },
    get connected() {
      return authed;
    },
  };

  const { promise: firstOpen, resolve: resolveFirst } = Promise.withResolvers<Engine>();
  let first = true;

  const open = async (): Promise<void> => {
    const { port, token } = await creds();
    const sock = new WebSocket(`ws://127.0.0.1:${port}`);
    ws = sock;
    sock.onopen = () => sock.send(JSON.stringify({ auth: token }));
    sock.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (!authed) {
        if (msg.ok === true) {
          authed = true;
          backoff = 500;
          emit("open", {});
          if (first) {
            first = false;
            resolveFirst(engine);
          }
        }
        return;
      }
      if (msg.id !== undefined) {
        const p = pending.get(msg.id);
        if (!p) return;
        pending.delete(msg.id);
        if (msg.error !== undefined) p.reject(new Error(String(msg.error)));
        else p.resolve(msg.result);
      } else if (msg.event) {
        emit(msg.event, msg.data ?? {});
      }
    };
    sock.onclose = () => {
      if (ws !== sock) return;
      ws = null;
      authed = false;
      pending.forEach((p) => p.reject(new Error("connection lost")));
      pending.clear();
      setTimeout(() => void open(), backoff);
      backoff = Math.min(backoff * 2, 10_000);
    };
    sock.onerror = () => sock.close();
  };

  void open();
  return firstOpen;
}
