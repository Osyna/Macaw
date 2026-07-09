// Settings tab — ports the PyQt SettingsTab feature set. Self-renders into
// #settings-root. Live-saves via config.set patches: instant for toggles /
// selects / buttons, 400 ms debounce for number / color / slider input.
// All config-derived strings go through textContent (never innerHTML).
import { invoke } from "@tauri-apps/api/core";

import { THEMES, type Theme } from "./theme";
import type { Engine, Json } from "./ws";
import "./settings.css";

interface Cfg {
  device_index: number | null;
  language: string;
  output_mode: string;
  silence_timeout: number;
  window_position: string;
  sound_enabled: boolean;
  streaming: boolean;
  punctuation_hints: boolean;
  hotkey_enabled: boolean;
  hotkey: string;
  theme: string;
  app_theme: string;
  overlay_opacity: number;
  overlay_width: number;
  overlay_height: number;
  overlay_x: number;
  overlay_y: number;
  eq_colors: string[];
  accent_color: string;
  border_width: number;
  border_color: string;
  corner_radius: number;
  corners: number[];
  corner_link: boolean;
  bar_spacing: number;
  bar_width: number;
  bar_radius: number;
  bar_fade: boolean;
}

interface Device {
  index: number;
  name: string;
  default: boolean;
}

type Corners = [number, number, number, number];

const LANGUAGES: ReadonlyArray<readonly [string, string]> = [
  ["en", "English"],
  ["fr", "French"],
  ["de", "German"],
  ["es", "Spanish"],
  ["it", "Italian"],
  ["pt", "Portuguese"],
  ["nl", "Dutch"],
  ["pl", "Polish"],
  ["ru", "Russian"],
  ["ja", "Japanese"],
  ["zh", "Chinese"],
];

const POSITIONS: ReadonlyArray<readonly [string, string]> = [
  ["bottom_center", "Bottom center"],
  ["bottom_left", "Bottom left"],
  ["bottom_right", "Bottom right"],
  ["top_center", "Top center"],
  ["top_left", "Top left"],
  ["top_right", "Top right"],
  ["custom", "Custom (X / Y)"],
];

const N_BARS = 24; // matches the real overlay

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

function num(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

function hexRgb(c: string): [number, number, number] {
  let s = c.trim().replace(/^#/, "");
  if (s.length === 3) s = s.replace(/./g, (ch) => ch + ch);
  const n = parseInt(s, 16);
  if (s.length !== 6 || !Number.isFinite(n)) return [136, 136, 136];
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Normalized #rrggbb — input[type=color] rejects anything else. */
function hex6(c: string): string {
  const [r, g, b] = hexRgb(c);
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}

/** Color sampled from the palette at frac 0..1, lerped between stops. */
function paletteAt(cols: string[], frac: number): string {
  const first = cols[0];
  if (first === undefined) return "#888888";
  if (cols.length === 1) return first;
  const x = clamp(frac, 0, 1) * (cols.length - 1);
  const i = Math.min(cols.length - 2, Math.floor(x));
  const t = x - i;
  const a = hexRgb(cols[i] ?? first);
  const b = hexRgb(cols[i + 1] ?? first);
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * t)}, ${Math.round(
    a[1] + (b[1] - a[1]) * t,
  )}, ${Math.round(a[2] + (b[2] - a[2]) * t)})`;
}

function themeLabel(name: string): string {
  if (name === "oled") return "OLED";
  return name.charAt(0).toUpperCase() + name.slice(1);
}

async function invokeSafe<T>(cmd: string): Promise<T | null> {
  try {
    return await invoke<T>(cmd);
  } catch {
    return null; // no Tauri (browser dev) or command failed
  }
}

export async function initSettings(engine: Engine, root: HTMLElement): Promise<void> {
  let cfgRes: Json;
  try {
    cfgRes = await engine.call("config.get");
  } catch (e) {
    console.error("config.get failed", e);
    root.replaceChildren(el("div", "st-warn", "Engine unavailable — cannot load settings"));
    return;
  }
  const [devRes, status, autostart] = await Promise.all([
    engine.call("devices.list").catch((): Json => []),
    engine.call("status").catch((): Json => ({})),
    invokeSafe<boolean>("autostart_status"),
  ]);

  let cfg: Cfg = cfgRes?.config ?? {};
  const devices: Device[] = Array.isArray(devRes) ? devRes : [];
  const hotkeyOk = status?.hotkey_ok !== false;
  const typingOk = status?.typing_ok !== false; // missing field = assume OK

  // ── live save ──────────────────────────────────────────────────────────
  let pending: Partial<Cfg> = {};
  let flushTimer: number | undefined;

  const save = (patch: Partial<Cfg>): void => {
    engine.call("config.set", { patch }).catch((e: unknown) => {
      console.error("config.set failed", e); // next config event re-syncs us
    });
  };
  const flush = (): void => {
    flushTimer = undefined;
    const p = pending;
    pending = {};
    if (Object.keys(p).length) save(p);
  };
  /** Debounced path: number / color / slider input. Preview updates live. */
  const stagePatch = (patch: Partial<Cfg>): void => {
    Object.assign(cfg, patch);
    Object.assign(pending, patch);
    renderPreview();
    clearTimeout(flushTimer);
    flushTimer = window.setTimeout(flush, 400);
  };
  /** Instant path: toggles, selects, buttons. */
  const applyPatch = (patch: Partial<Cfg>): void => {
    Object.assign(cfg, patch);
    refresh();
    save(patch);
  };

  const binders: Array<() => void> = [];
  let renderPreview: () => void = () => {};
  const refresh = (): void => {
    for (const b of binders) b();
  };

  engine.on("config", (d: Json) => {
    // In-flight debounced edits win over the echo until they flush.
    cfg = { ...(d?.config ?? {}), ...pending };
    refresh();
  });
  engine.on("open", () => {
    // Reconnect: config may have changed while we were away.
    engine
      .call("config.get")
      .then((r: Json) => {
        cfg = { ...(r?.config ?? {}), ...pending };
        refresh();
      })
      .catch(() => {});
  });

  // ── derived config views ───────────────────────────────────────────────
  const themeOf = (): Theme => THEMES[cfg.theme] ?? THEMES.oled ?? Object.values(THEMES)[0]!;

  /** Effective per-corner radii (tl, tr, br, bl) — same precedence as the overlay. */
  const effCorners = (): Corners => {
    if (cfg.corner_link === false && Array.isArray(cfg.corners) && cfg.corners.length === 4) {
      return [num(cfg.corners[0], 0), num(cfg.corners[1], 0), num(cfg.corners[2], 0), num(cfg.corners[3], 0)];
    }
    const r = num(cfg.corner_radius, -1);
    if (r >= 0) return [r, r, r, r];
    return themeOf().corners;
  };

  // ── control factories ──────────────────────────────────────────────────
  const rawSwitch = (): { wrap: HTMLLabelElement; input: HTMLInputElement } => {
    const wrap = el("label", "st-switch");
    const input = el("input");
    input.type = "checkbox";
    wrap.append(input, el("span", "st-knob"));
    return { wrap, input };
  };

  const switchCtl = (get: () => boolean, set: (v: boolean) => void): HTMLLabelElement => {
    const { wrap, input } = rawSwitch();
    input.addEventListener("change", () => set(input.checked));
    binders.push(() => {
      input.checked = get();
    });
    return wrap;
  };

  type BoolKey = "sound_enabled" | "streaming" | "punctuation_hints" | "hotkey_enabled" | "bar_fade";
  const toggle = (key: BoolKey): HTMLLabelElement =>
    switchCtl(
      () => Boolean(cfg[key]),
      (v) => {
        const patch: Partial<Cfg> = {};
        patch[key] = v;
        applyPatch(patch);
      },
    );

  const selectCtl = (
    options: ReadonlyArray<readonly [string, string]>,
    get: () => string,
    set: (v: string) => void,
  ): HTMLSelectElement => {
    const s = el("select", "st-select");
    for (const [value, label] of options) {
      const o = el("option", "", label);
      o.value = value;
      s.append(o);
    }
    s.addEventListener("change", () => set(s.value));
    binders.push(() => {
      if (document.activeElement !== s) s.value = get();
    });
    return s;
  };

  const numberCtl = (
    get: () => number,
    set: (v: number) => void,
    min: number,
    max: number,
    step: number,
  ): HTMLInputElement => {
    const i = el("input", "st-num");
    i.type = "number";
    i.min = String(min);
    i.max = String(max);
    i.step = String(step);
    i.addEventListener("input", () => {
      const v = Number(i.value);
      if (i.value === "" || !Number.isFinite(v)) return;
      set(clamp(v, min, max));
    });
    binders.push(() => {
      if (document.activeElement !== i) i.value = String(get());
    });
    return i;
  };

  const colorCtl = (key: "accent_color" | "border_color", themeColor: () => string): HTMLDivElement => {
    const wrap = el("div", "st-color");
    const input = el("input");
    input.type = "color";
    input.addEventListener("input", () => {
      const patch: Partial<Cfg> = {};
      patch[key] = input.value;
      stagePatch(patch);
    });
    const reset = el("button", "st-mini", "Theme");
    reset.type = "button";
    reset.title = "Use the theme colour";
    reset.addEventListener("click", () => {
      const patch: Partial<Cfg> = {};
      patch[key] = "";
      applyPatch(patch);
    });
    binders.push(() => {
      const v = cfg[key] || "";
      wrap.classList.toggle("st-themed", v === "");
      if (document.activeElement !== input) input.value = hex6(v || themeColor());
    });
    wrap.append(input, reset);
    return wrap;
  };

  const radioGroup = (
    name: string,
    options: ReadonlyArray<readonly [string, string]>,
    get: () => string,
    set: (v: string) => void,
  ): HTMLDivElement => {
    const box = el("div", "st-radios");
    const inputs: HTMLInputElement[] = [];
    for (const [value, label] of options) {
      const lab = el("label", "st-radio");
      const inp = el("input");
      inp.type = "radio";
      inp.name = name;
      inp.value = value;
      inp.addEventListener("change", () => {
        if (inp.checked) set(value);
      });
      lab.append(inp, el("span", "", label));
      box.append(lab);
      inputs.push(inp);
    }
    binders.push(() => {
      for (const inp of inputs) inp.checked = inp.value === get();
    });
    return box;
  };

  const row = (label: string, ctrl: HTMLElement): HTMLDivElement => {
    const r = el("div", "st-row");
    r.append(el("span", "st-lbl", label), ctrl);
    return r;
  };
  const hint = (text: string): HTMLDivElement => el("div", "st-hint", text);
  const card = (title: string): { box: HTMLElement; body: HTMLElement } => {
    const box = el("section", "st-card");
    const body = el("div", "st-body");
    box.append(el("h2", "", title), body);
    return { box, body };
  };

  // ── 1. General ─────────────────────────────────────────────────────────
  const generalCard = (): HTMLElement => {
    const { box, body } = card("General");

    const micOpts: Array<readonly [string, string]> = [["", "System default"]];
    for (const d of devices) {
      micOpts.push([String(d.index), d.default ? `${d.name} (default)` : d.name]);
    }
    if (cfg.device_index != null && !devices.some((d) => d.index === cfg.device_index)) {
      micOpts.push([String(cfg.device_index), `Device ${cfg.device_index} (unavailable)`]);
    }
    body.append(
      row(
        "Microphone",
        selectCtl(
          micOpts,
          () => (cfg.device_index == null ? "" : String(cfg.device_index)),
          (v) => applyPatch({ device_index: v === "" ? null : Number(v) }),
        ),
      ),
    );

    const langs: Array<readonly [string, string]> = [...LANGUAGES];
    if (cfg.language && !langs.some(([code]) => code === cfg.language)) {
      langs.push([cfg.language, cfg.language]);
    }
    body.append(
      row(
        "Language",
        selectCtl(
          langs,
          () => cfg.language || "en",
          (v) => applyPatch({ language: v }),
        ),
      ),
    );

    body.append(
      row(
        "Output",
        radioGroup(
          "st-output-mode",
          [
            ["clipboard", "Clipboard"],
            ["type", "Type"],
          ],
          () => cfg.output_mode || "clipboard",
          (v) => applyPatch({ output_mode: v }),
        ),
      ),
    );
    const typeWarn = el("div", "st-warn", "No typing tool — install ydotool/wtype (Linux)");
    binders.push(() => {
      typeWarn.hidden = typingOk || cfg.output_mode !== "type";
    });
    body.append(typeWarn, hint("Type puts text straight into the focused window"));

    body.append(
      row(
        "Silence timeout (s)",
        numberCtl(
          () => num(cfg.silence_timeout, 3),
          (v) => stagePatch({ silence_timeout: v }),
          0,
          30,
          0.5,
        ),
      ),
      hint("Stop recording after this much silence"),
      row("Sound effects", toggle("sound_enabled")),
      row("Live typing (alpha)", toggle("streaming")),
      hint("Text appears as you speak (requires Type output)"),
      row("Punctuation hints", toggle("punctuation_hints")),
    );
    return box;
  };

  // ── 2. Hotkey ──────────────────────────────────────────────────────────
  const hotkeyCard = (): HTMLElement => {
    const { box, body } = card("Hotkey");
    body.append(row("Global hotkey", toggle("hotkey_enabled")));

    const combo = el("span", "st-kbd");
    binders.push(() => {
      combo.textContent = cfg.hotkey || "not set";
    });
    const capture = el("button", "", "Capture…");
    capture.type = "button";
    const cancel = el("button", "st-mini", "Cancel");
    cancel.type = "button";
    cancel.hidden = true;

    let offCaptured: (() => void) | null = null;
    const endCapture = (): void => {
      offCaptured?.();
      offCaptured = null;
      capture.textContent = "Capture…";
      capture.classList.remove("st-capturing");
      cancel.hidden = true;
    };
    capture.addEventListener("click", () => {
      if (offCaptured) return; // already capturing
      offCaptured = engine.on("hotkey_captured", (d: Json) => {
        endCapture();
        const spec = typeof d?.spec === "string" ? d.spec : "";
        if (spec) applyPatch({ hotkey: spec, hotkey_enabled: true });
      });
      capture.textContent = "Press keys…";
      capture.classList.add("st-capturing");
      cancel.hidden = false;
      engine.call("hotkey.capture_start").catch(() => endCapture());
    });
    cancel.addEventListener("click", () => {
      engine.call("hotkey.capture_cancel").catch(() => {});
      endCapture();
    });

    const ctl = el("div", "st-hotkey");
    ctl.append(combo, capture, cancel);
    body.append(row("Shortcut", ctl));
    if (!hotkeyOk) {
      body.append(
        el("div", "st-warn", "Hotkey unavailable — add your user to the “input” group (Linux), then re-login"),
      );
    } else {
      body.append(hint("Press it anywhere to start or stop recording"));
    }
    return box;
  };

  // ── 4. System (autostart) ──────────────────────────────────────────────
  const systemCard = (): HTMLElement => {
    const { box, body } = card("System");
    const { wrap, input } = rawSwitch();
    input.checked = autostart === true;
    input.disabled = autostart === null;
    input.addEventListener("change", () => {
      const on = input.checked;
      invoke(on ? "autostart_enable" : "autostart_disable").catch((e: unknown) => {
        console.error("autostart toggle failed", e);
        input.checked = !on;
      });
    });
    body.append(row("Start at login", wrap));
    if (autostart === null) body.append(hint("Unavailable outside the desktop app"));
    return box;
  };

  // ── 3. Appearance ──────────────────────────────────────────────────────
  const appearanceCard = (): HTMLElement => {
    const { box, body } = card("Appearance");
    box.classList.add("st-wide");

    // Live overlay preview — same shape rules as the real overlay window.
    const stage = el("div", "st-stage");
    stage.append(el("span", "st-cap", "LIVE PREVIEW"));
    const pill = el("div", "st-pill");
    const bars = el("div", "st-bars");
    pill.append(bars);
    stage.append(pill);
    body.append(stage);

    renderPreview = () => {
      const th = themeOf();
      const w = num(cfg.overlay_width, 210);
      const h = num(cfg.overlay_height, 52);
      const corners = effCorners();
      const borderW = clamp(num(cfg.border_width, 0), 0, 6);
      pill.style.width = `${w}px`;
      pill.style.height = `${h}px`;
      pill.style.opacity = String(clamp(num(cfg.overlay_opacity, 0.94), 0.3, 1));
      pill.style.background = th.overlay_bg;
      pill.style.borderRadius = corners.map((c) => `${c}px`).join(" ");
      pill.style.border = borderW > 0 ? `${borderW}px solid ${cfg.border_color || th.border_color}` : "none";
      const avail = (stage.clientWidth || 360) - 32;
      pill.style.transform = avail < w ? `scale(${avail / w})` : "";

      // Bar geometry mirrors the engine overlay's _bar_layout.
      const cols = Array.isArray(cfg.eq_colors) && cfg.eq_colors.length ? cfg.eq_colors : th.eq_colors;
      const hpad = Math.min(Math.max(0, ...corners), w * 0.4);
      const availW = Math.max(1, w - 2 * hpad);
      const slot = availW / N_BARS;
      const spacing = num(cfg.bar_spacing, -1);
      const widthCfg = num(cfg.bar_width, -1);
      let gap = spacing < 0 ? slot * 0.58 : spacing;
      let barW = Math.max(2, widthCfg < 0 ? slot - gap : widthCfg);
      const rowW = barW * N_BARS + gap * (N_BARS - 1);
      if (rowW > availW) {
        const k = availW / rowW;
        gap *= k;
        barW = Math.max(1, barW * k);
      }
      const barR = clamp(num(cfg.bar_radius, 0), 0, barW / 2);

      bars.style.columnGap = `${gap}px`;
      bars.replaceChildren();
      for (let i = 0; i < N_BARS; i++) {
        const frac = i / (N_BARS - 1);
        const dist = Math.abs(frac - 0.5) * 2;
        // Static pseudo-waveform: taller mid-bars with deterministic jitter.
        const amp = Math.max(0.1, (0.34 + 0.55 * Math.abs(Math.sin(i * 1.9 + 0.6))) * (1 - 0.4 * dist * dist));
        const bar = el("span", "st-bar");
        bar.style.width = `${barW}px`;
        bar.style.height = `${Math.max(3, amp * h * 0.62)}px`;
        bar.style.borderRadius = `${barR}px`;
        bar.style.background = paletteAt(cols, frac);
        bar.style.opacity = cfg.bar_fade !== false ? String(0.35 + 0.65 * Math.min(1, amp)) : "1";
        bars.append(bar);
      }
    };
    binders.push(() => renderPreview());

    const left = el("div", "st-col");
    const right = el("div", "st-col");
    const grid = el("div", "st-grid2");
    grid.append(left, right);
    body.append(grid);

    // Left: theme + placement.
    left.append(
      row(
        "Theme",
        selectCtl(
          Object.keys(THEMES).map((name) => [name, themeLabel(name)] as const),
          () => cfg.theme || "",
          (v) => applyPatch({ theme: v }),
        ),
      ),
      row(
        "App theme",
        selectCtl(
          [
            ["dark", "Dark"],
            ["light", "Light"],
            ["system", "System"],
          ],
          () => cfg.app_theme || "dark",
          (v) => applyPatch({ app_theme: v }),
        ),
      ),
      row(
        "Position",
        selectCtl(
          POSITIONS,
          () => cfg.window_position || "bottom_center",
          (v) => applyPatch({ window_position: v }),
        ),
      ),
    );
    const posBox = el("div", "st-col");
    posBox.append(
      row("Custom X", numberCtl(() => num(cfg.overlay_x, 0), (v) => stagePatch({ overlay_x: v }), 0, 10000, 10)),
      row("Custom Y", numberCtl(() => num(cfg.overlay_y, 0), (v) => stagePatch({ overlay_y: v }), 0, 10000, 10)),
    );
    binders.push(() => {
      posBox.hidden = cfg.window_position !== "custom";
    });
    left.append(
      posBox,
      row("Width (px)", numberCtl(() => num(cfg.overlay_width, 210), (v) => stagePatch({ overlay_width: v }), 120, 600, 10)),
      row("Height (px)", numberCtl(() => num(cfg.overlay_height, 52), (v) => stagePatch({ overlay_height: v }), 32, 160, 4)),
    );

    const slider = el("input");
    slider.type = "range";
    slider.min = "0.3";
    slider.max = "1";
    slider.step = "0.01";
    const pct = el("span", "st-pct");
    slider.addEventListener("input", () => {
      const v = Number(slider.value);
      pct.textContent = `${Math.round(v * 100)}%`;
      stagePatch({ overlay_opacity: v });
    });
    binders.push(() => {
      const v = clamp(num(cfg.overlay_opacity, 0.94), 0.3, 1);
      if (document.activeElement !== slider) slider.value = String(v);
      pct.textContent = `${Math.round(v * 100)}%`;
    });
    const sliderBox = el("div", "st-slider");
    sliderBox.append(slider, pct);
    left.append(row("Opacity", sliderBox));

    // Corners: linked = one radius, unlinked = four independent.
    left.append(
      row(
        "Link corners",
        switchCtl(
          () => cfg.corner_link !== false,
          (on) => {
            // Unlinking seeds the four steppers from the current effective shape.
            if (on) applyPatch({ corner_link: true });
            else applyPatch({ corner_link: false, corners: [...effCorners()] });
          },
        ),
      ),
    );
    const uniBox = el("div", "st-corner-uni");
    uniBox.append(
      numberCtl(
        () => {
          const r = num(cfg.corner_radius, -1);
          return r >= 0 ? r : effCorners()[0];
        },
        (v) => stagePatch({ corner_radius: v }),
        0,
        28,
        1,
      ),
    );
    const uniReset = el("button", "st-mini", "Theme");
    uniReset.type = "button";
    uniReset.title = "Use the theme's shape";
    uniReset.addEventListener("click", () => applyPatch({ corner_radius: -1 }));
    uniBox.append(uniReset);
    const uniRow = row("Corner radius", uniBox);

    const quad = el("div", "st-quad");
    const quadInputs: HTMLInputElement[] = [];
    for (const tag of ["TL", "TR", "BR", "BL"]) {
      const cell = el("span", "st-quad-cell");
      const inp = el("input", "st-num");
      inp.type = "number";
      inp.min = "0";
      inp.max = "28";
      inp.step = "1";
      inp.addEventListener("input", () => {
        const cur = effCorners();
        const vals = quadInputs.map((q, j) => {
          const n = Number(q.value);
          return q.value !== "" && Number.isFinite(n) ? clamp(n, 0, 28) : cur[j]!;
        });
        stagePatch({ corners: vals });
      });
      quadInputs.push(inp);
      cell.append(el("span", "st-quad-lbl", tag), inp);
      quad.append(cell);
    }
    const quadRow = row("Corners", quad);
    binders.push(() => {
      const linked = cfg.corner_link !== false;
      uniRow.hidden = !linked;
      quadRow.hidden = linked;
      const c = effCorners();
      quadInputs.forEach((q, j) => {
        if (document.activeElement !== q) q.value = String(c[j]);
      });
    });
    left.append(uniRow, quadRow);

    // Right: colours + equaliser bars.
    right.append(
      row("Accent colour", colorCtl("accent_color", () => themeOf().accent)),
      row("Border width (px)", numberCtl(() => num(cfg.border_width, 0), (v) => stagePatch({ border_width: v }), 0, 6, 1)),
      row("Border colour", colorCtl("border_color", () => themeOf().border_color)),
      row("Bar spacing (px)", numberCtl(() => num(cfg.bar_spacing, -1), (v) => stagePatch({ bar_spacing: v }), -1, 12, 1)),
      row("Bar width (px)", numberCtl(() => num(cfg.bar_width, -1), (v) => stagePatch({ bar_width: v }), -1, 24, 1)),
      hint("-1 = automatic"),
      row("Bar roundness (px)", numberCtl(() => num(cfg.bar_radius, 0), (v) => stagePatch({ bar_radius: v }), 0, 12, 1)),
      row("Bar fade", toggle("bar_fade")),
    );

    // eq_colors editor: swatch list with add / remove (min 1) + theme reset.
    const eqRow = el("div", "st-row st-stack");
    eqRow.append(el("span", "st-lbl", "Bar colours"));
    const eqBox = el("div", "st-eq");
    eqRow.append(eqBox);
    right.append(eqRow);
    const eqCurrent = (): string[] =>
      Array.isArray(cfg.eq_colors) && cfg.eq_colors.length ? cfg.eq_colors.slice() : themeOf().eq_colors.slice();
    const rebuildEq = (): void => {
      if (eqBox.contains(document.activeElement)) return; // don't yank an open picker
      eqBox.replaceChildren();
      const explicit = Array.isArray(cfg.eq_colors) && cfg.eq_colors.length > 0;
      const cols = eqCurrent();
      cols.forEach((c, idx) => {
        const item = el("span", "st-eq-item");
        const inp = el("input");
        inp.type = "color";
        inp.value = hex6(c);
        inp.addEventListener("input", () => {
          const next = eqCurrent();
          next[idx] = inp.value;
          stagePatch({ eq_colors: next });
        });
        const rm = el("button", "st-eq-rm", "×");
        rm.type = "button";
        rm.title = "Remove colour";
        rm.disabled = cols.length <= 1;
        rm.addEventListener("click", () => applyPatch({ eq_colors: eqCurrent().filter((_, j) => j !== idx) }));
        item.append(inp, rm);
        eqBox.append(item);
      });
      const add = el("button", "st-mini", "+ Add");
      add.type = "button";
      add.addEventListener("click", () => {
        const cur = eqCurrent();
        applyPatch({ eq_colors: [...cur, cur[cur.length - 1] ?? "#4caf7d"] });
      });
      eqBox.append(add);
      if (explicit) {
        const reset = el("button", "st-mini", "Theme");
        reset.type = "button";
        reset.title = "Use the theme colours";
        reset.addEventListener("click", () => applyPatch({ eq_colors: [] }));
        eqBox.append(reset);
      }
    };
    binders.push(rebuildEq);

    return box;
  };

  const wrap = el("div", "st-grid");
  wrap.append(generalCard(), hotkeyCard(), systemCard(), appearanceCard());
  root.replaceChildren(wrap);
  refresh();
}
