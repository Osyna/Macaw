import "./base.css";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { initModels } from "./models";
import { initSettings } from "./settings";
import { applyTheme } from "./theme";
import { connect } from "./ws";

type Tab = "settings" | "models";

// Importing the tauri modules is safe in a plain browser; only *calling* them throws.
const inTauri = "__TAURI_INTERNALS__" in window;

function switchTab(tab: Tab): void {
  for (const name of ["settings", "models"] as const) {
    document.getElementById(`tab-${name}`)!.classList.toggle("active", name === tab);
    document.getElementById(`${name}-root`)!.classList.toggle("active", name === tab);
  }
}

async function showMain(): Promise<void> {
  if (!inTauri) return;
  const win = getCurrentWindow();
  await win.show();
  await win.setFocus();
}

function asTab(v: unknown): Tab {
  return v === "models" ? "models" : "settings";
}

async function main(): Promise<void> {
  document.getElementById("tab-settings")!.addEventListener("click", () => switchTab("settings"));
  document.getElementById("tab-models")!.addEventListener("click", () => switchTab("models"));

  if (inTauri) {
    await listen<string>("show-tab", (e) => {
      switchTab(asTab(e.payload));
      void showMain();
    });
  }

  const engine = await connect();

  try {
    const { config } = await engine.call("config.get");
    applyTheme(config.theme, config);
  } catch (e) {
    console.error("config.get failed", e);
  }

  initSettings(engine, document.getElementById("settings-root")!);
  initModels(engine, document.getElementById("models-root")!);

  engine.on("config", (d) => applyTheme(d.config?.theme, d.config ?? {}));
  engine.on("show", (d) => {
    switchTab(asTab(d?.window));
    void showMain();
  });

  if (inTauri) {
    // Tray "Toggle recording": this webview stays alive while the window is hidden
    // (close is intercepted as hide in Rust), so it owns the tray->engine bridge.
    await listen("tray-toggle", () => {
      engine.call("record.toggle").catch((e) => console.error("record.toggle failed", e));
    });
  }
}

void main();
