#!/usr/bin/env bash
# Macaw installer — fetches the latest AppImage release and wires up a
# launcher. The tarball from https://github.com/Osyna/Macaw/releases works
# too if you'd rather place the binaries yourself; this script covers the rest.
set -euo pipefail

REPO="Osyna/Macaw"
BIN_DIR="$HOME/.local/bin"
APPIMAGE="$BIN_DIR/Macaw.AppImage"
CLI_WRAPPER="$BIN_DIR/macaw"
DESKTOP_FILE="$HOME/.local/share/applications/macaw.desktop"
ICON_FILE="$HOME/.local/share/icons/hicolor/256x256/apps/macaw.png"
UINPUT_RULE="/etc/udev/rules.d/99-uinput-input-group.rules"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}==>${NC} $*"; }
warn()  { echo -e "${YELLOW}==>${NC} $*"; }
error() { echo -e "${RED}==>${NC} $*"; }

# Auto-accepts the default when non-interactive (curl | bash)
prompt_yn() {
    local prompt="$1" default="${2:-y}"
    if [[ ! -t 0 ]]; then
        info "(non-interactive) auto-accepting default: $default"
        [[ "$default" == "y" ]]; return
    fi
    local hint choice
    [[ "$default" == "y" ]] && hint="[Y/n]" || hint="[y/N]"
    read -rp "$prompt $hint " choice </dev/tty || choice="$default"
    case "${choice:-$default}" in [yY]*) return 0 ;; *) return 1 ;; esac
}

fetch() { # fetch URL DEST
    if command -v curl &>/dev/null; then curl -fSL --progress-bar "$1" -o "$2"
    elif command -v wget &>/dev/null; then wget -q --show-progress "$1" -O "$2"
    else error "curl or wget required."; exit 1; fi
}

uninstall() {
    info "Removing Macaw..."
    pkill -f "Macaw.AppImage" 2>/dev/null && sleep 1 || true
    rm -f "$APPIMAGE" "$CLI_WRAPPER" "$DESKTOP_FILE" "$ICON_FILE"
    info "Done. Kept: config (~/.config/macaw), backends (~/.local/share/macaw),"
    info "models (~/.cache/huggingface) — delete those dirs if unwanted."
}

# Re-running the installer on an existing install: offer the choice the old
# installer had instead of silently re-downloading.
existing_install_menu() {
    [[ -f "$APPIMAGE" ]] || return 0
    warn "Macaw is already installed at $APPIMAGE"
    if [[ ! -t 0 && ! -r /dev/tty ]]; then
        info "(non-interactive) reinstalling."
        return 0
    fi
    local choice
    read -rp "Reinstall (r) / Uninstall (u) / Quit (q)? [r/u/q] " choice </dev/tty || choice=r
    case "${choice:-r}" in
        [uU]*) uninstall; exit 0 ;;
        [qQ]*) exit 0 ;;
        *) ;; # reinstall
    esac
}

# Global hotkey (evdev) and auto-type (ydotool) read/write the kernel input
# layer: the user must be in the 'input' group and /dev/uinput group-writable.
setup_input_access() {
    local needs=()
    id -nG "$USER" | grep -qw input || needs+=("add '$USER' to the 'input' group")
    [[ -f "$UINPUT_RULE" ]] || needs+=("create udev rule $UINPUT_RULE for /dev/uinput")
    [[ ${#needs[@]} -eq 0 ]] && { info "Input-layer access: OK"; return 0; }

    echo
    info "The global hotkey and auto-type need input-layer access (uses sudo):"
    printf '  - %s\n' "${needs[@]}"
    if [[ ! -t 0 && ! -r /dev/tty ]]; then
        warn "No terminal for sudo — re-run install.sh interactively to finish input setup."
        return 0
    fi
    if ! prompt_yn "Apply these changes?" "y"; then
        warn "Skipped. Hotkey/auto-type may not work; re-run this script to fix."
        return 0
    fi
    if ! id -nG "$USER" | grep -qw input; then
        sudo usermod -aG input "$USER"
        warn "Added '$USER' to 'input' group — log out and back in for it to apply."
    fi
    if [[ ! -f "$UINPUT_RULE" ]]; then
        echo 'KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="input", MODE="0660", TAG+="uaccess"' \
            | sudo tee "$UINPUT_RULE" >/dev/null
        sudo modprobe uinput 2>/dev/null || true
        sudo udevadm control --reload-rules 2>/dev/null || true
        sudo udevadm trigger /dev/uinput 2>/dev/null || true
        info "Created udev rule: $UINPUT_RULE"
    fi
}

install_app() {
    info "Installing Macaw — speech-to-text for Linux"
    existing_install_menu

    info "Resolving latest release..."
    local api="https://api.github.com/repos/$REPO/releases/latest"
    local url
    url=$( { command -v curl &>/dev/null && curl -fsSL "$api" || wget -qO- "$api"; } \
        | grep -o '"browser_download_url": *"[^"]*\.AppImage"' \
        | grep -o 'https://[^"]*' | head -n1 )
    if [[ -z "$url" ]]; then
        error "No AppImage found in the latest release of $REPO."
        exit 1
    fi

    info "Downloading $(basename "$url")..."
    mkdir -p "$BIN_DIR"
    fetch "$url" "$APPIMAGE.part"
    mv "$APPIMAGE.part" "$APPIMAGE"
    chmod +x "$APPIMAGE"
    info "Installed $APPIMAGE"

    mkdir -p "$(dirname "$ICON_FILE")"
    fetch "https://raw.githubusercontent.com/$REPO/main/contrib/macaw.png" "$ICON_FILE" \
        || warn "Icon download failed (launcher will use a generic icon)."

    # Same content as contrib/macaw.desktop with __BIN__ filled in.
    mkdir -p "$(dirname "$DESKTOP_FILE")"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Macaw
GenericName=Speech-to-text
Comment=Fast local speech-to-text dictation
Exec=$APPIMAGE
Icon=macaw
Terminal=false
Categories=Utility;Accessibility;
Keywords=speech;dictation;transcription;voice;whisper;stt;
StartupNotify=false
EOF
    command -v update-desktop-database &>/dev/null \
        && update-desktop-database "$(dirname "$DESKTOP_FILE")" 2>/dev/null || true

    # Thin `macaw` CLI: flags are handled by the running app instance
    # (--settings, --models, --trigger toggles recording, --stop quits).
    cat > "$CLI_WRAPPER" <<EOF
#!/bin/sh
exec "$APPIMAGE" "\$@"
EOF
    chmod +x "$CLI_WRAPPER"

    setup_input_access

    echo
    info "Done! Launch 'Macaw' from your app menu or run: $APPIMAGE"
    info "STT backends and models are installed from the app's Models window."
    if ! echo ":$PATH:" | grep -q ":$BIN_DIR:"; then
        warn "$BIN_DIR is not on your PATH (menu launcher works regardless)."
    fi
}

case "${1:-}" in
    --uninstall|uninstall) uninstall ;;
    "")                    install_app ;;
    *)                     error "Usage: install.sh [--uninstall]"; exit 1 ;;
esac
