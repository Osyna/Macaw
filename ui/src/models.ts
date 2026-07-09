// Models tab — ports the PyQt ModelsTab UX. Self-renders into #models-root.
// All model-provided strings go through textContent (never innerHTML).
import { open as shellOpen } from "@tauri-apps/plugin-shell";

import type { Engine, Json } from "./ws";
import "./models.css";

const LANGUAGES: ReadonlyArray<readonly [string, string]> = [
  ["English", "en"],
  ["French", "fr"],
  ["German", "de"],
  ["Spanish", "es"],
  ["Italian", "it"],
  ["Portuguese", "pt"],
  ["Dutch", "nl"],
  ["Polish", "pl"],
  ["Russian", "ru"],
  ["Japanese", "ja"],
  ["Chinese", "zh"],
];

function fmtSize(n: number): string {
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`;
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  cls = "",
  text?: string,
): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function btn(text: string, cls: string, onClick: () => void): HTMLButtonElement {
  const b = el("button", cls ? `m-btn ${cls}` : "m-btn", text);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

function stars(rating: Json): HTMLElement {
  const n = Math.max(0, Math.min(5, Math.trunc(Number(rating) || 0)));
  const w = el("span", "m-stars");
  w.append(
    el("span", "m-stars-on", "★".repeat(n)),
    el("span", "m-stars-off", "☆".repeat(5 - n)),
  );
  return w;
}

function badge(text: string, cls = ""): HTMLElement {
  return el("span", cls ? `m-badge ${cls}` : "m-badge", text);
}

const clampPct = (p: number | null): number => Math.max(0, Math.min(100, p ?? 0));

interface Op {
  op: "install" | "download";
  key: string; // model id for downloads, extra name for installs
  msg: string;
  pct: number | null; // 0..100, null = indeterminate
  error: string | null;
}

const opText = (op: Op): string =>
  op.op === "download"
    ? `Downloading… ${Math.round(clampPct(op.pct))}%`
    : op.msg || "Installing…";

export function initModels(engine: Engine, root: HTMLElement): void {
  let models: Json[] = [];
  let config: Json = {};
  let selected: string | null = null;
  let engineState = { state: "idle", model: "" };
  const ops = new Map<string, Op>(); // "download:<id>" | "install:<extra>"
  let installQueue: string[] = []; // 'Install all' runs extras one at a time

  // In-place progress refs: high-rate progress ticks update these instead of
  // re-rendering (keeps focus in inputs and the armed Delete button alive).
  let opShownKey: string | null = null;
  let opFill: HTMLElement | null = null;
  let opMsgEl: HTMLElement | null = null;

  root.classList.add("models");
  const offlineBanner = el("div", "m-offline", "engine offline");
  const titleEl = el("div", "m-title", "Choose a model");
  const listEl = el("div", "m-list");
  const installAllBtn = btn("Install all optional backends ↓", "m-ok", () => {
    installQueue = missingExtras();
    pumpInstalls();
  });
  const left = el("div", "m-left");
  left.append(titleEl, listEl, installAllBtn);
  const detailEl = el("div", "m-detail");
  root.append(offlineBanner, left, detailEl);

  const missingExtras = (): string[] => [
    ...new Set(
      models.filter((m) => m.extra && !m.available).map((m) => String(m.extra)),
    ),
  ];

  const opKeyFor = (m: Json): string | null => {
    if (ops.has(`download:${m.id}`)) return `download:${m.id}`;
    if (m.extra && ops.has(`install:${m.extra}`)) return `install:${m.extra}`;
    return null;
  };

  async function rpc(method: string, params?: Json): Promise<Json | null> {
    try {
      return await engine.call(method, params);
    } catch (e) {
      console.error(method, e);
      render(); // reflect a possibly-dropped connection
      return null;
    }
  }

  async function refetch(): Promise<void> {
    const list = await rpc("models.list");
    if (Array.isArray(list)) {
      models = list.sort(
        (a, b) =>
          (a.cloud ? 1 : 0) - (b.cloud ? 1 : 0) ||
          (Number(b.rating) || 0) - (Number(a.rating) || 0),
      );
    }
    render();
  }

  // -- actions -----------------------------------------------------

  function pumpInstalls(): void {
    const extra = installQueue.shift();
    if (extra) void doInstall(extra);
  }

  async function doInstall(extra: string): Promise<void> {
    ops.set(`install:${extra}`, {
      op: "install",
      key: extra,
      msg: `Installing ${extra}…`,
      pct: null,
      error: null,
    });
    render();
    await rpc("models.install", { extra });
  }

  async function doDownload(id: string): Promise<void> {
    ops.set(`download:${id}`, {
      op: "download",
      key: id,
      msg: "Downloading…",
      pct: 0,
      error: null,
    });
    render();
    await rpc("models.download", { id });
  }

  async function doSetActive(id: string): Promise<void> {
    engineState = { state: "loading", model: id }; // optimistic; state events confirm
    await rpc("models.set_active", { id });
    await refetch();
  }

  async function doDelete(id: string): Promise<void> {
    await rpc("models.delete", { id });
    await refetch();
  }

  async function cancelOp(key: string, errored: boolean): Promise<void> {
    ops.delete(key);
    installQueue = [];
    if (!errored) await rpc("models.cancel");
    await refetch();
  }

  async function saveApiKey(value: string): Promise<void> {
    if (value === String(config.openai_api_key ?? "")) return;
    config = { ...config, openai_api_key: value };
    await rpc("config.set", { patch: { openai_api_key: value } });
    await refetch(); // api_key_set changed
  }

  function saveLanguage(id: string, code: string): void {
    const all: Json = { ...(config.model_languages ?? {}) };
    all[id] = code;
    config = { ...config, model_languages: all };
    void rpc("config.set", { patch: { model_languages: all } });
  }

  function saveParam(id: string, key: string, value: Json): void {
    const all: Json = { ...(config.model_params ?? {}) };
    all[id] = { ...(all[id] ?? {}), [key]: value };
    config = { ...config, model_params: all };
    void rpc("config.set", { patch: { model_params: all } });
  }

  // -- render ------------------------------------------------------

  function render(): void {
    opShownKey = opFill = opMsgEl = null;
    const off = !engine.connected;
    root.classList.toggle("m-off", off);
    offlineBanner.style.display = off ? "block" : "none";
    titleEl.textContent = `Choose a model (${models.length})`;
    installAllBtn.style.display = missingExtras().length ? "" : "none";
    renderList();
    renderDetail();
  }

  function renderList(): void {
    listEl.textContent = "";
    if (!models.length) {
      listEl.append(
        el("div", "m-empty", engine.connected ? "No models found." : "Engine offline."),
      );
      return;
    }
    if (!selected || !models.some((m) => m.id === selected)) {
      selected = String((models.find((m) => m.active) ?? models[0]).id);
    }
    for (const m of models) {
      const card = el("div", "m-card");
      if (m.id === selected) card.classList.add("sel");
      if (!m.ready) card.classList.add("dim");
      const top = el("div", "m-card-top");
      top.append(el("span", "m-card-label", String(m.label)));
      if (engineState.state === "loading" && engineState.model === m.id)
        top.append(el("span", "m-spin"));
      const meta = el("div", "m-card-meta");
      meta.append(el("span", "m-chip", String(m.backend)));
      if (!m.cloud && m.size) meta.append(el("span", "", String(m.size)));
      if (m.languages) meta.append(el("span", "", String(m.languages)));
      const bottom = el("div", "m-card-bottom");
      bottom.append(stars(m.rating));
      bottom.append(badge(m.cloud ? "cloud" : m.streaming ? "streaming" : "offline"));
      if (m.recommended) bottom.append(badge("recommended", "m-badge-rec"));
      if (m.active) bottom.append(badge("Active", "m-badge-active"));
      card.append(top, meta, bottom);
      card.addEventListener("click", () => {
        if (selected !== m.id) {
          selected = String(m.id);
          render();
        }
      });
      listEl.append(card);
    }
  }

  function statusLine(m: Json): [string, string] {
    if (m.cloud) {
      if (!m.available) return [`Needs macaw[${m.extra || "openai"}]`, "warn"];
      if (!m.api_key_set) return ["Needs an OpenAI API key", "warn"];
      if (m.active) return ["● Active model", "ok"];
      return ["Ready · cloud (uses your API key)", "ok"];
    }
    if (!m.available) return [`Needs macaw[${m.extra}]`, "warn"];
    if (m.active) return ["● Active model", "ok"];
    if (m.disk_size > 0) return [`Downloaded · ${fmtSize(m.disk_size)}`, "ok"];
    if (!m.repo) return ["Ready · downloads on first use", "ok"];
    return ["Not downloaded", "muted"];
  }

  function renderDetail(): void {
    detailEl.textContent = "";
    const m = models.find((x) => x.id === selected);
    if (!m) return;

    const head = el("div", "m-head");
    head.append(el("h2", "m-name", String(m.label)), stars(m.rating));
    detailEl.append(head);

    const kind = m.cloud ? "cloud" : m.streaming ? "streaming" : "offline";
    const meta = [
      m.backend,
      ...(m.cloud ? [] : [m.size]),
      m.speed,
      m.languages,
      [kind, ...(m.recommended ? ["recommended"] : [])].join(" · "),
    ]
      .filter(Boolean)
      .join(" · ");
    detailEl.append(el("div", "m-meta", meta));

    const [stText, stCls] = statusLine(m);
    detailEl.append(el("div", `m-status ${stCls}`, stText));

    if (m.notes) {
      const notes = el("div", "m-notes");
      for (const raw of String(m.notes).split("\n")) {
        const line = raw.trim();
        if (line) notes.append(el("p", "", line));
      }
      detailEl.append(notes);
    }
    const pros: Json[] = Array.isArray(m.pros) ? m.pros : [];
    const cons: Json[] = Array.isArray(m.cons) ? m.cons : [];
    if (pros.length || cons.length) {
      const pc = el("div", "m-proscons");
      for (const p of pros) pc.append(el("div", "m-pro", `+ ${p}`));
      for (const c of cons) pc.append(el("div", "m-con", `− ${c}`));
      detailEl.append(pc);
    }

    const specs = el("div", "m-specs");
    const kv = (k: string, v: Json): void => {
      if (!v) return;
      const cell = el("div", "m-kv");
      cell.append(el("div", "m-k", k), el("div", "m-v", String(v)));
      specs.append(cell);
    };
    kv("Hardware", m.hardware);
    kv("VRAM", m.vram);
    if (!m.cloud) kv("Size", m.size);
    kv("Speed", m.speed);
    kv("Languages", m.languages);
    kv("Minimal", m.min_specs);
    kv("Recommended", m.rec_specs);
    if (m.disk_size > 0) kv("On disk", fmtSize(Number(m.disk_size)));
    if (specs.childElementCount) detailEl.append(specs);

    const links = el("div", "m-links");
    const link = (label: string, url: string): void => {
      const cell = el("div", "m-kv");
      const a = el("a", "m-link", url.replace(/^https?:\/\//, ""));
      a.href = url;
      a.title = url;
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        // Tauri shell plugin; window.open when running in a bare browser (dev).
        shellOpen(url).catch(() => void window.open(url, "_blank"));
      });
      cell.append(el("div", "m-k", label), a);
      links.append(cell);
    };
    if (m.source_url) link("Library", String(m.source_url));
    const repo = String(m.repo ?? "");
    if (repo)
      link("Download", repo.startsWith("http") ? repo : `https://huggingface.co/${repo}`);
    if (links.childElementCount) detailEl.append(links);

    if (m.cloud) detailEl.append(apiKeySection(m));

    const opKey = opKeyFor(m);
    if (opKey) detailEl.append(activitySection(opKey, ops.get(opKey)!));
    else detailEl.append(actionsRow(m));

    if (m.lang_select) detailEl.append(languageSection(m));

    detailEl.append(el("hr", "m-sep"), paramsSection(m));
  }

  function apiKeySection(m: Json): HTMLElement {
    const sec = el("div", "m-section");
    const head = el("div", "m-sec-title", "OpenAI API key");
    head.append(
      m.api_key_set
        ? el("span", "m-key-set", "● set")
        : el("span", "m-key-unset", "○ not set"),
    );
    const row = el("div", "m-keyrow");
    const input = el("input", "m-input");
    input.type = "password";
    input.placeholder = "sk-…";
    input.value = String(config.openai_api_key ?? "");
    input.addEventListener("change", () => void saveApiKey(input.value.trim()));
    const show = btn("show", "m-ghost", () => {
      input.type = input.type === "password" ? "text" : "password";
      show.textContent = input.type === "password" ? "show" : "hide";
    });
    row.append(input, show);
    sec.append(
      head,
      row,
      el("div", "m-hint", "Stored in config.yaml, or set $OPENAI_API_KEY."),
    );
    return sec;
  }

  function activitySection(key: string, op: Op): HTMLElement {
    const sec = el("div", "m-activity");
    const bar = el("div", "m-bar");
    const fill = el("div", "m-bar-fill");
    bar.append(fill);
    if (op.error) fill.style.width = "0%";
    else if (op.op === "download") fill.style.width = `${clampPct(op.pct)}%`;
    else bar.classList.add("m-ind"); // installs have no percentage
    const msg = el("div", op.error ? "m-op-msg err" : "m-op-msg", op.error ?? opText(op));
    const b = btn(op.error ? "Close" : "Cancel", "m-danger", () =>
      void cancelOp(key, !!op.error),
    );
    sec.append(bar, msg, b);
    opShownKey = key;
    opFill = fill;
    opMsgEl = msg;
    return sec;
  }

  function actionsRow(m: Json): HTMLElement {
    const row = el("div", "m-actions");
    const loading = engineState.state === "loading";
    if (!m.cloud) {
      if (!m.available && m.extra) {
        row.append(
          btn(`Install ${m.extra} backend ↓`, "m-ok", () =>
            void doInstall(String(m.extra)),
          ),
        );
      } else if (m.available && m.repo && !(m.disk_size > 0)) {
        row.append(
          btn(`Download ~${String(m.size).replace(/^~/, "")}`, "", () =>
            void doDownload(String(m.id)),
          ),
        );
      }
    }
    if (m.active) {
      if (engineState.state === "error")
        row.append(btn("Retry", "", () => void doSetActive(String(m.id))));
      row.append(
        el(
          "span",
          "m-pill-active",
          loading && engineState.model === m.id ? "Loading…" : "Active",
        ),
      );
    } else if (m.ready) {
      const b = btn("Set active", "m-accent", () => void doSetActive(String(m.id)));
      b.disabled = loading; // one load at a time
      row.append(b);
    }
    if (!m.cloud && m.disk_size > 0) {
      // two-step inline confirm; arming is local state, no re-render
      const del = btn("Delete", "m-danger", () => {
        if (!del.classList.contains("m-armed")) {
          del.classList.add("m-armed");
          del.textContent = "Really delete?";
          setTimeout(() => {
            del.classList.remove("m-armed");
            del.textContent = "Delete";
          }, 4000);
          return;
        }
        void doDelete(String(m.id));
      });
      del.disabled = loading; // don't delete files mid-load
      row.append(del);
    }
    return row;
  }

  function languageSection(m: Json): HTMLElement {
    const sec = el("div", "m-section");
    sec.append(el("div", "m-sec-title", "Language"));
    const sel = el("select", "m-select");
    for (const [name, code] of LANGUAGES) {
      const o = el("option", "", name);
      o.value = code;
      sel.append(o);
    }
    const cur = String(m.cur_lang || config.model_languages?.[m.id] || "en");
    sel.value = LANGUAGES.some(([, c]) => c === cur) ? cur : "en";
    sel.addEventListener("change", () => saveLanguage(String(m.id), sel.value));
    sec.append(sel);
    return sec;
  }

  function paramsSection(m: Json): HTMLElement {
    const sec = el("div", "m-section m-params");
    sec.append(el("div", "m-sec-title", "Parameters"));
    const params: Json[] = Array.isArray(m.params) ? m.params : [];
    if (!params.length) {
      sec.append(el("div", "m-hint", "This model has no adjustable parameters."));
      return sec;
    }
    for (const p of params) {
      const row = el("div", "m-param-row");
      row.append(el("label", "m-param-label", String(p.label ?? p.key)));
      row.append(paramControl(String(m.id), p, m.cur_params?.[p.key] ?? p.default));
      sec.append(row);
      if (p.hint) sec.append(el("div", "m-hint", String(p.hint)));
    }
    return sec;
  }

  function paramControl(id: string, p: Json, value: Json): HTMLElement {
    if (p.kind === "bool") {
      const wrap = el("label", "m-toggle");
      const input = el("input");
      input.type = "checkbox";
      input.checked = !!value;
      input.addEventListener("change", () =>
        saveParam(id, String(p.key), input.checked),
      );
      wrap.append(input, el("span", "m-toggle-track"));
      return wrap;
    }
    const input = el("input", "m-input m-num");
    input.type = "number";
    if (p.min != null) input.min = String(p.min);
    if (p.max != null) input.max = String(p.max);
    input.step = String(p.step ?? (p.kind === "int" ? 1 : 0.1));
    input.value = String(value ?? "");
    input.addEventListener("change", () => {
      let v = p.kind === "int" ? parseInt(input.value, 10) : parseFloat(input.value);
      if (Number.isNaN(v)) return;
      if (p.min != null) v = Math.max(Number(p.min), v);
      if (p.max != null) v = Math.min(Number(p.max), v);
      input.value = String(v);
      saveParam(id, String(p.key), v);
    });
    return input;
  }

  // -- events ------------------------------------------------------

  engine.on("models", () => void refetch());

  engine.on("config", (d: Json) => {
    // Track config only; skip re-render so a config echo from our own
    // config.set never yanks focus mid-typing. models events cover the rest.
    if (d?.config) config = d.config;
  });

  engine.on("state", (d: Json) => {
    const prev = engineState;
    engineState = {
      state: String(d?.state ?? "idle"),
      model: String(d?.model ?? ""),
    };
    // Only load/error transitions matter here; ignore recording chatter.
    if (
      prev.state === "loading" ||
      prev.state === "error" ||
      engineState.state === "loading" ||
      engineState.state === "error"
    )
      void refetch();
  });

  engine.on("progress", (d: Json) => {
    if (!d || (d.op !== "install" && d.op !== "download")) return;
    const key = `${d.op}:${d.key}`;
    if (d.done) {
      if (d.ok === false) {
        ops.set(key, {
          op: d.op,
          key: String(d.key),
          msg: "",
          pct: null,
          error: String(d.msg || "Failed."),
        });
        installQueue = [];
        render();
      } else {
        ops.delete(key);
        if (d.op === "install") pumpInstalls();
        void refetch();
      }
      return;
    }
    const existed = ops.has(key);
    const op: Op = {
      op: d.op,
      key: String(d.key),
      msg: String(d.msg ?? ""),
      pct: typeof d.pct === "number" ? d.pct : null,
      error: null,
    };
    ops.set(key, op);
    if (opShownKey === key && opFill && opMsgEl) {
      // in-place tick — cheap, keeps input focus
      if (op.op === "download") opFill.style.width = `${clampPct(op.pct)}%`;
      opMsgEl.textContent = opText(op);
    } else if (!existed) {
      render(); // op started elsewhere (e.g. CLI) — surface it
    }
  });

  engine.on("open", () => void init()); // reconnect → full resync

  async function init(): Promise<void> {
    try {
      const [cfg, st] = await Promise.all([
        engine.call("config.get"),
        engine.call("status"),
      ]);
      config = cfg?.config ?? {};
      engineState = {
        state: String(st?.state ?? "idle"),
        model: String(st?.model ?? ""),
      };
    } catch (e) {
      console.error("models init", e);
    }
    await refetch();
  }

  void init();
}
