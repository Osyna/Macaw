/*
 * Recording overlay — transparent always-on-top pill with a live voice
 * equalizer. Port of the PyQt RecordingWindow
 * (`git show HEAD:src/macaw/gui/window.py`).
 *
 * Rendering math mirrored from the old code (line numbers = window.py @ HEAD):
 * - NUM_BARS = 24, SMOOTHING = 0.32 .............................. :218-219
 * - bar layout (shared by eq + loader) ........................... :96-117
 *     hpad  = min(max(corners), w*0.4)          # clear the rounded ends
 *     slot  = (w - 2*hpad) / N
 *     gap   = bar_spacing < 0 ? slot*0.58 : bar_spacing
 *     bw    = max(2, bar_width < 0 ? slot-gap : bar_width)
 *     row   = (bw+gap)*N - gap, scaled down uniformly if > avail
 *     br    = min(bar_radius, bw/2), start = hpad + (avail-row)/2
 * - eq bars: vpad = max(8, h*0.18), bottom-anchored, min height 2,
 *   bar_fade alpha = (100 + 155*level)/255 ....................... :128-143
 * - bar targets @ 15 Hz (every 2nd 30 Hz tick): energy > 0.05 ?
 *   min(1, e*(1 - dist*0.5)*rand(0.3,1)) : rand(0.02,0.08);
 *   smoothing: attack 0.32 / release 0.32*0.45 per 30 Hz tick
 *   (time constants ~86 ms / ~214 ms), frame-rate independent here  :359-376
 * - transcribing ("analysing") loader: t = phase*0.16 with phase++
 *   per 30 Hz tick (=> 4.8 rad/s); amp = max(sin(t - fx*5),
 *   0.6*sin(t*0.7 - fx*8)); height 3 + amp*(H*0.42), centred;
 *   bar_fade alpha = (60 + amp*175)/255 .......................... :146-162
 * - pill: overlay_bg + border at alpha 255*clamp(opacity,0.3,1) ... :60-79
 * - error text: fg, bold, font size max(11, H*0.30) (old "message"
 *   state, e.g. "No Model Selected") ............................. :205-212
 * - quiet bars blend from eq_idle into the palette as voice is heard
 *   (heard = level/0.12) — from the settings equalizer,
 *   gui/equalizer.py:47,108-117
 * - look resolution (config overrides over theme + corner
 *   precedence) — gui/theme.py:194-221 and the contract.
 *
 * NOTE: `text` (partial transcription) events are intentionally ignored —
 * the old overlay never displayed transcription text, only status messages.
 */
import { invoke } from "@tauri-apps/api/core";
import {
  LogicalPosition,
  LogicalSize,
  currentMonitor,
  getCurrentWindow,
} from "@tauri-apps/api/window";

import { THEMES, type Theme } from "./theme";
import type { Engine } from "./ws";
import "./overlay.css";

const NUM_BARS = 24; // window.py:218
const ATTACK = 0.32; // per 30 Hz tick, window.py:219
const RELEASE = ATTACK * 0.45; // window.py:375
const TICK_HZ = 30;
const RETARGET_S = 2 / TICK_HZ; // window.py:361
const HEARD_LEVEL = 0.12; // equalizer.py:47
const MARGIN = 24; // screen-edge margin
const ERROR_MS = 1500; // danger flash duration before hiding

const IS_TAURI = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

type Rgb = [number, number, number];

interface Look {
  overlayBg: string;
  opacity: number; // already clamped 0.3..1
  borderWidth: number;
  borderColor: string;
  corners: [number, number, number, number]; // tl, tr, br, bl
  eqColors: string[];
  eqIdle: string;
  barSpacing: number;
  barWidth: number;
  barRadius: number;
  barFade: boolean;
  danger: string;
  ok: string;
  fg: string;
  width: number;
  height: number;
  position: string;
  x: number;
  y: number;
}

function hexRgb(hex: string): Rgb {
  let h = hex.replace("#", "");
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  const v = parseInt(h, 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function rgba(c: Rgb, a: number): string {
  return `rgba(${c[0]},${c[1]},${c[2]},${a})`;
}

function lerpRgb(a: Rgb, b: Rgb, t: number): Rgb {
  t = Math.min(1, Math.max(0, t));
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ];
}

// Palette sampled at frac 0..1, interpolated between stops (window.py:14-31).
function paletteAt(stops: Rgb[], frac: number): Rgb {
  if (stops.length === 1) return stops[0];
  frac = Math.min(1, Math.max(0, frac));
  const pos = frac * (stops.length - 1);
  const i = Math.min(Math.floor(pos), stops.length - 2);
  return lerpRgb(stops[i], stops[i + 1], pos - i);
}

// Config overrides over theme defaults — mirrors gui/theme.py:194-221.
// The config fields the overlay reads (YAML schema subset, contract §Config).
interface OverlayCfg {
  theme?: string;
  overlay_opacity?: number;
  overlay_width?: number;
  overlay_height?: number;
  overlay_x?: number;
  overlay_y?: number;
  window_position?: string;
  eq_colors?: string[];
  border_width?: number;
  border_color?: string;
  corner_radius?: number;
  corners?: number[];
  corner_link?: boolean;
  bar_spacing?: number;
  bar_width?: number;
  bar_radius?: number;
  bar_fade?: boolean;
}

function resolveLook(cfg: OverlayCfg): Look {
  const t: Theme = THEMES[cfg.theme ?? "oled"] ?? THEMES.oled;
  let corners: [number, number, number, number];
  const cr = cfg.corner_radius;
  if (cfg.corner_link === false && Array.isArray(cfg.corners) && cfg.corners.length === 4) {
    corners = cfg.corners.map((c: number) => Math.max(0, Math.trunc(c))) as Look["corners"];
  } else if (cr !== undefined && cr >= 0) {
    corners = [cr, cr, cr, cr];
  } else {
    corners = [...t.corners] as Look["corners"];
  }
  return {
    overlayBg: t.overlay_bg,
    opacity: Math.max(0.3, Math.min(1, cfg.overlay_opacity ?? 0.94)), // window.py:62
    borderWidth: Math.max(0, Math.trunc(cfg.border_width ?? 0)),
    borderColor: cfg.border_color || t.border_color,
    corners,
    eqColors: Array.isArray(cfg.eq_colors) && cfg.eq_colors.length ? cfg.eq_colors : [...t.eq_colors],
    eqIdle: t.eq_idle,
    barSpacing: cfg.bar_spacing ?? -1,
    barWidth: cfg.bar_width ?? -1,
    barRadius: cfg.bar_radius ?? 0,
    barFade: cfg.bar_fade ?? true,
    danger: t.danger,
    ok: t.ok,
    fg: t.fg,
    width: cfg.overlay_width ?? 210,
    height: cfg.overlay_height ?? 52,
    position: cfg.window_position ?? "bottom_center",
    x: cfg.overlay_x ?? 0,
    y: cfg.overlay_y ?? 0,
  };
}

export function initOverlay(engine: Engine): void {
  const root = document.getElementById("overlay-root");
  if (!root) throw new Error("overlay: #overlay-root missing");
  root.innerHTML = '<div class="pill"><canvas></canvas><div class="msg"></div></div>';
  const pill = root.querySelector<HTMLDivElement>(".pill")!;
  const canvas = root.querySelector<HTMLCanvasElement>("canvas")!;
  const msg = root.querySelector<HTMLDivElement>(".msg")!;
  const ctx = canvas.getContext("2d")!;

  let look = resolveLook({});
  let stops: Rgb[] = look.eqColors.map(hexRgb);
  let mode: "eq" | "loader" | "error" | "off" = "off";
  let energy = 0;
  const bars = new Float64Array(NUM_BARS);
  const targets = new Float64Array(NUM_BARS);
  let retargetAcc = RETARGET_S; // retarget on the first animated frame
  let raf = 0;
  let lastT = 0;
  let stateT0 = 0;
  let errTimer: number | undefined;
  let cssW = 0;
  let cssH = 0;

  function fit(): void {
    const r = canvas.getBoundingClientRect();
    cssW = r.width;
    cssH = r.height;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(cssW * dpr));
    canvas.height = Math.max(1, Math.round(cssH * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  new ResizeObserver(() => {
    fit();
    if (!raf && mode !== "error") drawEq(); // keep the static frame crisp
  }).observe(canvas);
  fit();

  // ── look / geometry ───────────────────────────────────────────────

  function applyLookCss(): void {
    const a = look.opacity;
    const s = pill.style;
    s.setProperty("--pill-bg", rgba(hexRgb(look.overlayBg), a));
    s.setProperty("--pill-border-w", `${look.borderWidth}px`);
    s.setProperty(
      "--pill-border-c",
      look.borderWidth > 0 ? rgba(hexRgb(look.borderColor), a) : "transparent",
    );
    const [tl, tr, br, bl] = look.corners;
    s.setProperty("--pill-radius", `${tl}px ${tr}px ${br}px ${bl}px`);
    s.setProperty("--danger-bg", rgba(hexRgb(look.danger), a));
    s.setProperty("--ok", look.ok);
    s.setProperty("--fg", look.fg);
    msg.style.fontSize = `${Math.max(11, Math.round(look.height * 0.3))}px`; // window.py:209
    if (!IS_TAURI) {
      // browser dev: no window to size, size the root element instead
      root!.style.width = `${look.width}px`;
      root!.style.height = `${look.height}px`;
    }
  }

  // Placement map mirrors window.py:290-298 (24 px margins per new spec).
  async function applyGeometry(): Promise<void> {
    if (!IS_TAURI) return;
    try {
      // Wayland can't position toplevels; the Rust side anchors the window
      // via wlr-layer-shell when available. Err -> classic path (X11/Windows).
      await invoke("overlay_layout", {
        position: look.position,
        x: Math.round(look.x),
        y: Math.round(look.y),
        width: Math.round(look.width),
        height: Math.round(look.height),
      });
      return;
    } catch {
      /* fall through to native window positioning */
    }
    const win = getCurrentWindow();
    await win.setSize(new LogicalSize(look.width, look.height));
    let x = look.x;
    let y = look.y;
    if (look.position !== "custom") {
      const mon = await currentMonitor();
      const sf = mon?.scaleFactor || 1;
      const wa = mon?.workArea ?? {
        position: { x: 0, y: 0 },
        size: mon?.size ?? { width: look.width, height: look.height },
      };
      const sx = wa.position.x / sf;
      const sy = wa.position.y / sf;
      const sw = wa.size.width / sf;
      const sh = wa.size.height / sf;
      const ww = look.width;
      const wh = look.height;
      switch (look.position) {
        case "top_left":
          x = sx + MARGIN;
          y = sy + MARGIN;
          break;
        case "top_center":
          x = sx + (sw - ww) / 2;
          y = sy + MARGIN;
          break;
        case "top_right":
          x = sx + sw - ww - MARGIN;
          y = sy + MARGIN;
          break;
        case "bottom_left":
          x = sx + MARGIN;
          y = sy + sh - wh - MARGIN;
          break;
        case "bottom_right":
          x = sx + sw - ww - MARGIN;
          y = sy + sh - wh - MARGIN;
          break;
        default: // bottom_center + unknown values
          x = sx + (sw - ww) / 2;
          y = sy + sh - wh - MARGIN;
          break;
      }
    }
    await win.setPosition(new LogicalPosition(Math.round(x), Math.round(y)));
  }

  function applyConfig(cfg: OverlayCfg | undefined): void {
    look = resolveLook(cfg ?? {});
    stops = look.eqColors.map(hexRgb);
    applyLookCss();
    applyGeometry().catch((err) => console.error("overlay geometry:", err));
  }

  // ── animation ─────────────────────────────────────────────────────

  function startLoop(): void {
    if (raf) return;
    lastT = performance.now();
    raf = requestAnimationFrame(frame);
  }

  function stopLoop(): void {
    if (raf) {
      cancelAnimationFrame(raf);
      raf = 0;
    }
  }

  function frame(now: number): void {
    raf = requestAnimationFrame(frame);
    const dt = Math.min((now - lastT) / 1000, 0.1);
    lastT = now;
    if (mode === "eq") {
      stepBars(dt);
      drawEq();
    } else if (mode === "loader") {
      drawLoader((now - stateT0) / 1000);
    } else {
      stopLoop();
    }
  }

  // window.py:359-376, made frame-rate independent (per-tick factors
  // converted with 1-(1-s)^(dt*30)).
  function stepBars(dt: number): void {
    retargetAcc += dt;
    if (retargetAcc >= RETARGET_S) {
      retargetAcc %= RETARGET_S;
      const c = NUM_BARS / 2;
      for (let i = 0; i < NUM_BARS; i++) {
        if (energy > 0.05) {
          const dist = Math.abs(i - c) / c;
          targets[i] = Math.min(1, energy * (1 - dist * 0.5) * (0.3 + 0.7 * Math.random()));
        } else {
          targets[i] = 0.02 + 0.06 * Math.random();
        }
      }
    }
    const kAtk = 1 - Math.pow(1 - ATTACK, dt * TICK_HZ);
    const kRel = 1 - Math.pow(1 - RELEASE, dt * TICK_HZ);
    for (let i = 0; i < NUM_BARS; i++) {
      const d = targets[i] - bars[i];
      bars[i] += d * (d > 0 ? kAtk : kRel);
    }
  }

  // window.py:96-117
  function barLayout(w: number): { start: number; pitch: number; bw: number; br: number } {
    const hpad = Math.min(Math.max(...look.corners), w * 0.4);
    const avail = Math.max(1, w - 2 * hpad);
    const slot = avail / NUM_BARS;
    const gap = look.barSpacing < 0 ? slot * 0.58 : look.barSpacing;
    let bw = Math.max(2, look.barWidth < 0 ? slot - gap : look.barWidth);
    let pitch = bw + gap;
    const row = pitch * NUM_BARS - gap;
    if (row > avail) {
      const s = avail / row;
      bw *= s;
      pitch *= s;
    }
    const br = Math.min(Math.max(0, look.barRadius), bw / 2);
    const rowW = pitch * NUM_BARS - (pitch - bw);
    return { start: hpad + (avail - rowW) / 2, pitch, bw, br };
  }

  function bar(x: number, y: number, w: number, h: number, r: number): void {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, r);
    ctx.fill();
  }

  // window.py:128-143 (+ eq_idle blend from gui/equalizer.py:108-117)
  function drawEq(): void {
    ctx.clearRect(0, 0, cssW, cssH);
    const { start, pitch, bw, br } = barLayout(cssW);
    const vpad = Math.max(8, cssH * 0.18);
    const bot = cssH - vpad;
    const maxH = cssH - 2 * vpad;
    const heard = Math.min(1, energy / HEARD_LEVEL);
    const idle = hexRgb(look.eqIdle);
    for (let i = 0; i < NUM_BARS; i++) {
      const lv = bars[i];
      const h = Math.max(2, lv * maxH);
      const a = look.barFade ? (100 + 155 * Math.min(1, lv)) / 255 : 1;
      const frac = i / (NUM_BARS - 1);
      ctx.fillStyle = rgba(lerpRgb(idle, paletteAt(stops, frac), heard), a);
      bar(start + i * pitch, bot - h, bw, h, br);
    }
  }

  // window.py:146-162; t advances 0.16 per 30 Hz tick => 4.8 rad/s
  function drawLoader(elapsed: number): void {
    ctx.clearRect(0, 0, cssW, cssH);
    const { start, pitch, bw, br } = barLayout(cssW);
    const mid = cssH / 2;
    const maxH = cssH * 0.42;
    const t = elapsed * TICK_HZ * 0.16;
    for (let i = 0; i < NUM_BARS; i++) {
      const fx = i / (NUM_BARS - 1);
      const w1 = 0.5 + 0.5 * Math.sin(t - fx * 5);
      const w2 = 0.5 + 0.5 * Math.sin(t * 0.7 - fx * 8);
      const amp = Math.max(w1, w2 * 0.6);
      const h = 3 + amp * maxH;
      const a = look.barFade ? (60 + amp * 175) / 255 : 1;
      ctx.fillStyle = rgba(paletteAt(stops, fx), a);
      bar(start + i * pitch, mid - h / 2, bw, h, br);
    }
  }

  // ── state / visibility ────────────────────────────────────────────

  function show(): void {
    if (!IS_TAURI) return;
    const w = getCurrentWindow();
    w.show()
      // Click-through must be (re)applied while the window is realized —
      // setting it on a hidden GTK window aborts the process (tao unwrap).
      .then(() => w.setIgnoreCursorEvents(true))
      .catch((err) => console.error("overlay show:", err));
  }

  function hide(): void {
    if (IS_TAURI) {
      getCurrentWindow().hide().catch((err) => console.error("overlay hide:", err));
    } else {
      // browser dev: keep the pill visible with one static calm frame
      bars.fill(0.03);
      energy = 0;
      drawEq();
    }
  }

  function setState(state: string, detail?: string): void {
    if (errTimer !== undefined) {
      clearTimeout(errTimer);
      errTimer = undefined;
    }
    pill.classList.remove("error", "done");
    msg.textContent = "";
    if (state === "recording") {
      mode = "eq";
      show();
      startLoop();
    } else if (state === "transcribing") {
      mode = "loader";
      stateT0 = performance.now();
      show();
      startLoop();
    } else if (state === "done") {
      // Delivered to the clipboard — brief ✓ (engine flips to idle after 1.2s).
      mode = "off";
      stopLoop();
      ctx.clearRect(0, 0, cssW, cssH);
      msg.textContent = "✓";
      pill.classList.add("done");
      show();
    } else if (state === "error") {
      mode = "error";
      stopLoop();
      ctx.clearRect(0, 0, cssW, cssH);
      msg.textContent = detail || "Error";
      pill.classList.add("error"); // CSS keyframes do the danger flash
      show();
      errTimer = window.setTimeout(() => setState("idle"), ERROR_MS);
    } else {
      // idle | loading
      mode = "off";
      stopLoop();
      hide();
    }
  }

  // ── engine wiring ─────────────────────────────────────────────────

  // Event/RPC payloads arrive untyped from the WS bridge; narrow field by
  // field (trusted local engine, defensive defaults — no schema lib in deps).
  const obj = (v: unknown): Record<string, unknown> =>
    typeof v === "object" && v !== null ? (v as Record<string, unknown>) : {};

  engine.on("level", (d) => {
    const rms = obj(d).rms;
    energy = typeof rms === "number" ? Math.min(1, Math.max(0, rms)) : 0;
  });
  engine.on("state", (d) => {
    const e = obj(d);
    setState(
      typeof e.state === "string" ? e.state : "idle",
      typeof e.detail === "string" ? e.detail : undefined,
    );
  });
  engine.on("config", (d) => applyConfig(obj(d).config as OverlayCfg | undefined));

  async function refresh(): Promise<void> {
    const [cfg, status] = await Promise.all([engine.call("config.get"), engine.call("status")]);
    applyConfig(obj(cfg).config as OverlayCfg | undefined);
    const st = obj(status).state;
    setState(typeof st === "string" ? st : "idle");
  }
  engine.on("open", () => {
    refresh().catch((err) => console.error("overlay refresh:", err));
  });

  applyLookCss(); // sane defaults before the first config arrives
  refresh().catch((err) => console.error("overlay refresh:", err));
}
