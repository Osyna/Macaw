import type { Json } from "./ws";

export interface Theme {
  bg: string;
  surface: string;
  control: string;
  fg: string;
  muted: string;
  border: string;
  accent: string;
  accent_fg: string;
  ok: string;
  warn: string;
  danger: string;
  overlay_bg: string;
  eq_idle: string;
  eq_colors: string[];
  corners: [number, number, number, number];
  border_color: string;
}

export const THEMES: Record<string, Theme> = {
  oled: {
    bg: "#0A0A0A",
    surface: "#0E0E0E",
    control: "#111111",
    fg: "#FFFFFF",
    muted: "#666666",
    border: "#2A2A2A",
    accent: "#4CAF7D",
    accent_fg: "#0A0A0A",
    ok: "#4CAF7D",
    warn: "#C9A227",
    danger: "#CC4444",
    overlay_bg: "#000000",
    eq_idle: "#3A3A3A",
    eq_colors: ["#4CAF7D", "#2FBF9F", "#38BDF8"],
    corners: [3, 3, 3, 3],
    border_color: "#2A2A2A",
  },
  macaw: {
    bg: "#FDF4EA",
    surface: "#FFFFFF",
    control: "#FBF1E4",
    fg: "#1B1712",
    muted: "#8A7C6C",
    border: "#EADBC7",
    accent: "#E5322B",
    accent_fg: "#FFFFFF",
    ok: "#2E9E6B",
    warn: "#B8860B",
    danger: "#C81F1A",
    overlay_bg: "#171310",
    eq_idle: "#E7D8C4",
    eq_colors: ["#E5322B", "#F7B500", "#2F6FD0"],
    corners: [18, 18, 18, 3],
    border_color: "#E5322B",
  },
  light: {
    bg: "#F5F5F7",
    surface: "#FFFFFF",
    control: "#FFFFFF",
    fg: "#1D1D1F",
    muted: "#86868B",
    border: "#D2D2D7",
    accent: "#2F6FD0",
    accent_fg: "#FFFFFF",
    ok: "#2E9E6B",
    warn: "#B8860B",
    danger: "#D14343",
    overlay_bg: "#FFFFFF",
    eq_idle: "#D2D2D7",
    eq_colors: ["#2F6FD0", "#00A6A6", "#7C5CFC"],
    corners: [16, 16, 16, 16],
    border_color: "#D2D2D7",
  },
  catppuccin: {
    bg: "#1E1E2E",
    surface: "#181825",
    control: "#313244",
    fg: "#CDD6F4",
    muted: "#A6ADC8",
    border: "#45475A",
    accent: "#CBA6F7",
    accent_fg: "#1E1E2E",
    ok: "#A6E3A1",
    warn: "#F9E2AF",
    danger: "#F38BA8",
    overlay_bg: "#11111B",
    eq_idle: "#45475A",
    eq_colors: ["#F38BA8", "#CBA6F7", "#89B4FA", "#A6E3A1"],
    corners: [16, 16, 3, 16],
    border_color: "#CBA6F7",
  },
};

/** Sets app-chrome CSS custom properties on :root. Config overrides win over theme defaults. */
export function applyTheme(name: string, cfg: Json = {}): void {
  const t = THEMES[name] ?? THEMES.oled;
  const vars: Record<string, string> = {
    "--bg": t.bg,
    "--surface": t.surface,
    "--control": t.control,
    "--fg": t.fg,
    "--muted": t.muted,
    "--border": t.border,
    "--accent": cfg?.accent_color || t.accent,
    "--accent-fg": t.accent_fg,
    "--ok": t.ok,
    "--warn": t.warn,
    "--danger": t.danger,
  };
  const s = document.documentElement.style;
  for (const [k, v] of Object.entries(vars)) s.setProperty(k, v);
}
