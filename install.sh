#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="macaw"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
UINPUT_RULE="/etc/udev/rules.d/99-uinput-input-group.rules"
MACAW_SRC=""
CLEANUP_TMPDIR=""

# Non-interactive detection (e.g. curl | bash)
if [[ ! -t 0 ]]; then
    NON_INTERACTIVE=true
else
    NON_INTERACTIVE=false
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${GREEN}==>${NC} $*"; }
warn()  { echo -e "${YELLOW}==>${NC} $*"; }
error() { echo -e "${RED}==>${NC} $*"; }
step()  { echo -e "${BLUE}==>${NC} $*"; }

# Prompt yes/no — auto-accepts default when non-interactive (curl | bash)
# Usage: prompt_yn "Install them now?" "y"   → default=yes
#        prompt_yn "Install anyway?" "n"      → default=no
prompt_yn() {
    local prompt="$1" default="${2:-y}"
    if $NON_INTERACTIVE; then
        info "(non-interactive) auto-accepting default: $default"
        [[ "$default" == "y" ]] && return 0 || return 1
    fi
    local hint
    [[ "$default" == "y" ]] && hint="[Y/n]" || hint="[y/N]"
    local choice
    read -rp "$prompt $hint " choice </dev/tty
    case "${choice:-$default}" in
        [yY]*) return 0 ;;
        *)     return 1 ;;
    esac
}

# --- Source acquisition ---

cleanup_source() {
    if [[ -n "$CLEANUP_TMPDIR" ]] && [[ -d "$CLEANUP_TMPDIR" ]]; then
        rm -rf "$CLEANUP_TMPDIR"
    fi
}

acquire_source() {
    # Already in the macaw repo?
    if [[ -f "pyproject.toml" ]] && grep -q 'name = "macaw"' pyproject.toml 2>/dev/null; then
        MACAW_SRC="$(pwd)"
        info "Installing from local source: $MACAW_SRC"
        return
    fi

    step "Downloading macaw source..."
    CLEANUP_TMPDIR="$(mktemp -d)"
    trap cleanup_source EXIT

    local repo_url="https://github.com/Osyna/Macaw"

    if has_cmd git; then
        git clone --depth 1 "$repo_url" "$CLEANUP_TMPDIR/macaw"
        MACAW_SRC="$CLEANUP_TMPDIR/macaw"
    elif has_cmd curl; then
        curl -fSL "$repo_url/archive/refs/heads/main.tar.gz" -o "$CLEANUP_TMPDIR/macaw.tar.gz"
        tar xzf "$CLEANUP_TMPDIR/macaw.tar.gz" -C "$CLEANUP_TMPDIR"
        MACAW_SRC="$CLEANUP_TMPDIR/Macaw-main"
    elif has_cmd wget; then
        wget -q "$repo_url/archive/refs/heads/main.tar.gz" -O "$CLEANUP_TMPDIR/macaw.tar.gz"
        tar xzf "$CLEANUP_TMPDIR/macaw.tar.gz" -C "$CLEANUP_TMPDIR"
        MACAW_SRC="$CLEANUP_TMPDIR/Macaw-main"
    else
        error "Cannot download source: git, curl, or wget required."
        exit 1
    fi

    if [[ ! -f "$MACAW_SRC/pyproject.toml" ]]; then
        error "Downloaded source is missing pyproject.toml."
        exit 1
    fi
    info "Source acquired: $MACAW_SRC"
}

# --- Distro & display server detection ---

DISTRO=""
DISPLAY_SERVER=""

detect_distro() {
    if [[ -f /etc/os-release ]]; then
        # shellcheck source=/dev/null
        . /etc/os-release
        case "${ID:-}" in
            arch|endeavouros|manjaro|garuda|cachyos|artix) DISTRO="arch" ;;
            ubuntu|pop|linuxmint|elementary|zorin|neon)    DISTRO="debian" ;;
            debian|raspbian)                               DISTRO="debian" ;;
            fedora|nobara|ultramarine)                     DISTRO="fedora" ;;
            opensuse*|sles)                                DISTRO="suse" ;;
            void)                                          DISTRO="void" ;;
            nixos)                                         DISTRO="nix" ;;
            alpine)                                        DISTRO="alpine" ;;
            gentoo)                                        DISTRO="gentoo" ;;
            *)                                             DISTRO="unknown" ;;
        esac
    else
        DISTRO="unknown"
    fi
}

detect_display_server() {
    if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
        DISPLAY_SERVER="wayland"
    elif [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]]; then
        DISPLAY_SERVER="wayland"
    else
        DISPLAY_SERVER="x11"
    fi
}

# --- Package manager helpers ---

pkg_install() {
    local packages=("$@")
    [[ ${#packages[@]} -eq 0 ]] && return 0

    info "Installing system packages: ${packages[*]}"
    case "$DISTRO" in
        arch)    sudo pacman -S --needed --noconfirm "${packages[@]}" ;;
        debian)  sudo apt-get install -y "${packages[@]}" ;;
        fedora)  sudo dnf install -y "${packages[@]}" ;;
        suse)    sudo zypper install -y "${packages[@]}" ;;
        void)    sudo xbps-install -y "${packages[@]}" ;;
        alpine)  sudo apk add "${packages[@]}" ;;
        gentoo)  sudo emerge --noreplace "${packages[@]}" ;;
        *)
            error "Unknown distro — cannot auto-install packages."
            error "Please install manually: ${packages[*]}"
            exit 1
            ;;
    esac
}

# Map a generic dependency name to the distro-specific package name.
pkg_name() {
    local dep="$1"
    case "$dep" in
        python3)
            case "$DISTRO" in
                arch)    echo "python" ;;
                gentoo)  echo "dev-lang/python" ;;
                *)       echo "python3" ;;
            esac ;;
        portaudio)
            case "$DISTRO" in
                debian)  echo "libportaudio2" ;;
                alpine)  echo "portaudio-dev" ;;
                gentoo)  echo "media-libs/portaudio" ;;
                *)       echo "portaudio" ;;
            esac ;;
        wl-clipboard)
            case "$DISTRO" in
                gentoo)  echo "gui-apps/wl-clipboard" ;;
                *)       echo "wl-clipboard" ;;
            esac ;;
        xclip)
            case "$DISTRO" in
                gentoo)  echo "x11-misc/xclip" ;;
                *)       echo "xclip" ;;
            esac ;;
        ydotool)
            case "$DISTRO" in
                gentoo)  echo "app-misc/ydotool" ;;
                *)       echo "ydotool" ;;
            esac ;;
        wtype)
            case "$DISTRO" in
                gentoo)  echo "gui-apps/wtype" ;;
                *)       echo "wtype" ;;
            esac ;;
        xdotool)
            case "$DISTRO" in
                gentoo)  echo "x11-misc/xdotool" ;;
                *)       echo "xdotool" ;;
            esac ;;
        *)
            echo "$dep" ;;
    esac
}

# --- Checks ---

check_portaudio() {
    if pkg-config --exists portaudio-2.0 2>/dev/null; then return 0; fi
    if python3 -c "import ctypes; ctypes.cdll.LoadLibrary('libportaudio.so.2')" 2>/dev/null; then return 0; fi
    if ldconfig -p 2>/dev/null | grep -q 'libportaudio'; then return 0; fi
    for lib in /usr/lib/libportaudio.so* /usr/lib64/libportaudio.so* \
               /usr/lib/x86_64-linux-gnu/libportaudio.so* \
               /usr/lib/aarch64-linux-gnu/libportaudio.so*; do
        [[ -e "$lib" ]] && return 0
    done
    return 1
}

has_cmd() { command -v "$1" &>/dev/null; }

# --- Detection ---

is_installed() {
    systemctl --user is-enabled "$SERVICE_NAME" &>/dev/null
}

# --- Uninstall ---

# Delete downloaded Whisper models from the HuggingFace cache.
# Runs through the macaw venv python so it reuses faster-whisper's own
# repo list — respects HF_HOME/HF_HUB_CACHE and never touches unrelated
# HuggingFace models the user may have cached. Must run BEFORE the package
# is uninstalled (needs the venv's faster_whisper + huggingface_hub).
purge_models() {
    local py result
    py="$(get_macaw_python)"
    result="$("$py" - <<'PY' 2>/dev/null
try:
    from faster_whisper.utils import _MODELS
    from huggingface_hub import scan_cache_dir
    repos = set(_MODELS.values())
    info = scan_cache_dir()
    sel = [r for r in info.repos if r.repo_id in repos]
    revs = [rev.commit_hash for r in sel for rev in r.revisions]
    if not revs:
        print("NONE")
    else:
        total = sum(r.size_on_disk for r in sel)
        info.delete_revisions(*revs).execute()
        size = f"{total/1e9:.1f} GB" if total >= 1e9 else f"{total/1e6:.0f} MB"
        print(f"OK:{len(sel)}:{size}")
except Exception:
    print("ERR")
PY
)"
    case "${result%%:*}" in
        OK)
            info "Removed ${result#OK:} of cached models."
            ;;
        NONE)
            info "No cached models to remove."
            ;;
        *)
            warn "Could not auto-remove models. Clear them from macaw Settings,"
            warn "or delete: \${HF_HOME:-~/.cache/huggingface}/hub/models--Systran--faster-*whisper*"
            ;;
    esac
}

uninstall() {
    info "Stopping and disabling service..."
    systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true

    if [[ -f "$SERVICE_FILE" ]]; then
        rm "$SERVICE_FILE"
        info "Removed $SERVICE_FILE"
    fi

    local dropin_dir
    dropin_dir="$(dirname "$SERVICE_FILE")/${SERVICE_NAME}.service.d"
    if [[ -d "$dropin_dir" ]]; then
        rm -rf "$dropin_dir"
        info "Removed $dropin_dir"
    fi

    systemctl --user daemon-reload

    # Remove desktop launcher + icon
    local data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
    rm -f "$data_home/applications/macaw.desktop" \
          "$data_home/icons/hicolor/256x256/apps/macaw.png"
    update-desktop-database "$data_home/applications" 2>/dev/null || true

    # Purge downloaded models BEFORE removing the package (needs the venv).
    if prompt_yn "Delete downloaded Whisper models (frees disk space)?" "y"; then
        purge_models
    fi

    if has_cmd uv; then
        info "Uninstalling macaw via uv..."
        uv tool uninstall macaw
    elif has_cmd pip; then
        info "Uninstalling macaw via pip..."
        pip uninstall -y macaw
    else
        warn "Neither uv nor pip found — remove the package manually."
    fi

    # Remove user config last (default no — keep it for a future reinstall).
    local config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/macaw"
    if [[ -d "$config_dir" ]] && prompt_yn "Remove macaw config ($config_dir)?" "n"; then
        rm -rf "$config_dir"
        info "Removed $config_dir"
    fi

    info "Uninstall complete."
}

# --- Install steps ---

ensure_systemd() {
    if ! has_cmd systemctl; then
        error "systemd not found. macaw's install script requires systemd for service management."
        echo
        info "You can still install and run manually:"
        echo "  uv tool install /path/to/macaw"
        echo "  macaw"
        exit 1
    fi
}

ensure_python() {
    step "Checking Python..."

    local python_cmd=""
    for cmd in python3 python; do
        if has_cmd "$cmd"; then
            python_cmd="$cmd"
            break
        fi
    done

    if [[ -z "$python_cmd" ]]; then
        warn "Python not found — installing..."
        pkg_install "$(pkg_name python3)"
        python_cmd="python3"
    fi

    local version
    version="$("$python_cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    local major="${version%%.*}"
    local minor="${version##*.}"

    if [[ "$major" -lt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -lt 10 ]]; }; then
        error "Python $version found, but 3.10+ is required."
        error "Please upgrade Python manually."
        exit 1
    fi
    info "Python $version OK."
}

ensure_uv() {
    step "Checking package installer..."

    if has_cmd uv; then
        info "uv found."
        return 0
    fi

    if has_cmd pip; then
        info "pip found (uv is recommended for faster installs)."
        return 0
    fi

    warn "No Python package installer found — installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Source the env so uv is available in this session
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    if ! has_cmd uv; then
        error "uv installation failed. Install uv or pip manually."
        exit 1
    fi
    info "uv installed."
}

ensure_system_deps() {
    step "Checking system dependencies..."

    local to_install=()

    # --- Paste tool ---
    local has_paste=false
    for tool in ydotool wtype xdotool; do
        if has_cmd "$tool"; then
            has_paste=true
            info "Paste tool: $tool"
            break
        fi
    done
    if ! $has_paste; then
        if [[ "$DISPLAY_SERVER" == "wayland" ]]; then
            to_install+=("$(pkg_name ydotool)")
            to_install+=("$(pkg_name wtype)")
        else
            to_install+=("$(pkg_name xdotool)")
        fi
    fi

    # --- Clipboard tool ---
    local has_clipboard=false
    if has_cmd wl-copy && has_cmd wl-paste; then
        has_clipboard=true
        info "Clipboard: wl-clipboard"
    elif has_cmd xclip; then
        has_clipboard=true
        info "Clipboard: xclip"
    elif has_cmd xsel; then
        has_clipboard=true
        info "Clipboard: xsel"
    fi
    if ! $has_clipboard; then
        if [[ "$DISPLAY_SERVER" == "wayland" ]]; then
            to_install+=("$(pkg_name wl-clipboard)")
        else
            to_install+=("$(pkg_name xclip)")
        fi
    fi

    # --- PortAudio ---
    if check_portaudio; then
        info "PortAudio: found"
    else
        to_install+=("$(pkg_name portaudio)")
    fi

    # --- Install missing system packages ---
    if [[ ${#to_install[@]} -gt 0 ]]; then
        echo
        warn "Missing system packages: ${to_install[*]}"
        if prompt_yn "Install them now?" "y"; then
            pkg_install "${to_install[@]}"
        else
            error "Cannot continue without required packages."
            exit 1
        fi
    fi

    # --- Verify everything landed ---
    local still_missing=()

    local has_paste_after=false
    for tool in ydotool wtype xdotool; do
        has_cmd "$tool" && has_paste_after=true && break
    done
    $has_paste_after || still_missing+=("paste tool (ydotool/wtype/xdotool)")

    local has_clip_after=false
    if { has_cmd wl-copy && has_cmd wl-paste; } || has_cmd xclip || has_cmd xsel; then
        has_clip_after=true
    fi
    $has_clip_after || still_missing+=("clipboard tool")

    check_portaudio || still_missing+=("portaudio")

    if [[ ${#still_missing[@]} -gt 0 ]]; then
        error "Still missing after install attempt:"
        for dep in "${still_missing[@]}"; do
            echo "  - $dep"
        done
        exit 1
    fi

    info "All system dependencies OK."
}

# --- ydotool / uinput setup (requires sudo) ---

ensure_ydotool_access() {
    # Only relevant if ydotool is installed
    has_cmd ydotool || return 0

    step "Setting up ydotool (evdev input)..."

    local needs_sudo=false

    # 1. Check user is in the 'input' group
    if ! id -nG "$USER" | grep -qw input; then
        warn "User '$USER' is not in the 'input' group."
        needs_sudo=true
    else
        info "User in 'input' group: OK"
    fi

    # 2. Check /dev/uinput is accessible
    #    brltty and other packages can install ACLs that strip group access
    #    even when the file mode says 0660.
    local uinput_ok=false
    if [[ -e /dev/uinput ]]; then
        if python3 -c "
import os
fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
os.close(fd)
" 2>/dev/null; then
            uinput_ok=true
            info "/dev/uinput access: OK"
        else
            warn "/dev/uinput exists but is not accessible (ACL or permission issue)."
            needs_sudo=true
        fi
    else
        warn "/dev/uinput device not found."
        needs_sudo=true
    fi

    # 3. Apply fixes if needed
    if $needs_sudo; then
        echo
        info "ydotool needs access to /dev/uinput for keyboard simulation."
        info "The following will be done with sudo:"
        # Show what we'll do
        if ! id -nG "$USER" | grep -qw input; then
            echo "  - Add '$USER' to the 'input' group"
        fi
        if ! $uinput_ok; then
            echo "  - Fix /dev/uinput permissions (ACL grant for 'input' group)"
            echo "  - Create udev rule at $UINPUT_RULE for persistence"
        fi
        echo

        if ! prompt_yn "Apply these changes?" "y"; then
            warn "Skipping ydotool setup — it may not work without /dev/uinput access."
            warn "Auto-type mode will fall back to wtype (less compatible)."
            return 0
        fi

        # Add user to input group
        if ! id -nG "$USER" | grep -qw input; then
            sudo usermod -aG input "$USER"
            info "Added '$USER' to 'input' group."
            # newgrp would start a subshell — instead we note the need
            warn "Group change takes effect on next login. Continuing with ACL fix..."
        fi

        # Fix /dev/uinput ACL now (immediate effect, no re-login needed)
        if ! $uinput_ok && [[ -e /dev/uinput ]]; then
            sudo setfacl -m g:input:rw /dev/uinput
            info "Fixed /dev/uinput ACL for current session."
        fi

        # Load the uinput kernel module if not loaded
        if [[ ! -e /dev/uinput ]]; then
            sudo modprobe uinput
            info "Loaded uinput kernel module."
            # Also make it load on boot
            if [[ -d /etc/modules-load.d ]]; then
                echo "uinput" | sudo tee /etc/modules-load.d/uinput.conf >/dev/null
                info "Created /etc/modules-load.d/uinput.conf for boot-time loading."
            fi
            # Fix permissions on the newly created device
            if [[ -e /dev/uinput ]]; then
                sudo chgrp input /dev/uinput
                sudo chmod 0660 /dev/uinput
                sudo setfacl -m g:input:rw /dev/uinput
            fi
        fi

        # Create persistent udev rule (survives reboots, overrides brltty ACLs)
        if [[ ! -f "$UINPUT_RULE" ]]; then
            echo 'KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="input", MODE="0660", TAG+="uaccess"' \
                | sudo tee "$UINPUT_RULE" >/dev/null
            sudo udevadm control --reload-rules 2>/dev/null || true
            sudo udevadm trigger /dev/uinput 2>/dev/null || true
            info "Created udev rule: $UINPUT_RULE"
        else
            info "udev rule already exists: $UINPUT_RULE"
        fi
    fi

    # 4. Start ydotoold user service
    if systemctl --user list-unit-files ydotool.service &>/dev/null; then
        systemctl --user reset-failed ydotool 2>/dev/null || true
        systemctl --user enable ydotool 2>/dev/null || true
        if systemctl --user start ydotool 2>/dev/null; then
            info "ydotoold daemon: running"
        else
            # Might fail if ACL hasn't propagated yet — try direct start
            if ydotoold &>/dev/null & then
                sleep 0.5
                if pgrep -x ydotoold &>/dev/null; then
                    info "ydotoold daemon: started manually"
                else
                    warn "ydotoold failed to start — will retry on next boot after re-login."
                fi
            fi
        fi
    else
        # No systemd unit — start daemon directly
        if ! pgrep -x ydotoold &>/dev/null; then
            ydotoold &>/dev/null &
            sleep 0.5
            if pgrep -x ydotoold &>/dev/null; then
                info "ydotoold daemon: started"
            else
                warn "ydotoold failed to start — auto-type will fall back to wtype."
            fi
        else
            info "ydotoold daemon: already running"
        fi
    fi
}

# --- ROCm support ---

# Get the Python interpreter from the macaw tool venv.
get_macaw_python() {
    if has_cmd uv; then
        local venv_dir
        venv_dir="$(uv tool dir)/macaw"
        if [[ -x "${venv_dir}/bin/python" ]]; then
            echo "${venv_dir}/bin/python"
            return 0
        fi
    fi
    echo "python3"
}

verify_rocm_environment() {
    step "Verifying ROCm environment..."
    local rocm_path="${ROCM_PATH:-/opt/rocm}"

    if [[ ! -d "$rocm_path" ]]; then
        error "ROCm installation not found at $rocm_path"
        info "Set ROCM_PATH if installed elsewhere."
        return 1
    fi

    # ROCm version
    local rocm_version=""
    if [[ -f "${rocm_path}/.info/version" ]]; then
        rocm_version="$(head -1 "${rocm_path}/.info/version" 2>/dev/null)"
    fi
    if [[ -n "$rocm_version" ]]; then
        info "ROCm version: $rocm_version"
    else
        warn "Could not determine ROCm version."
    fi

    # Check GPU agent via rocminfo
    if has_cmd rocminfo; then
        local gpu_agent
        gpu_agent="$(rocminfo 2>/dev/null | grep -oP 'Name:\s+\Kgfx\w+' | head -1)" || true
        if [[ -n "$gpu_agent" ]]; then
            info "ROCm GPU agent: $gpu_agent"
        else
            warn "rocminfo found no GPU agent — ROCm may not be functional."
        fi
    fi

    # Ensure ROCm libs are discoverable for this session
    if [[ -d "${rocm_path}/lib" ]] && [[ ":${LD_LIBRARY_PATH:-}:" != *":${rocm_path}/lib:"* ]]; then
        export LD_LIBRARY_PATH="${rocm_path}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi

    info "ROCm environment OK."
}

# Find the latest CTranslate2 version that has ROCm wheels and is
# compatible with the installed faster-whisper.
# Outputs one line:  COMPATIBLE:<ver>  |  INCOMPATIBLE:<ver>:<constraint>  |  ERROR:<msg>
find_compatible_rocm_ct2() {
    local macaw_python="$1"

    "$macaw_python" -c '
import json, sys, re, urllib.request

# -- Get faster-whisper ctranslate2 constraint from installed metadata --
spec = None
constraint_str = ""
try:
    import importlib.metadata
    reqs = importlib.metadata.requires("faster-whisper") or []
    for req in reqs:
        if req.lower().startswith("ctranslate2"):
            constraint_str = req
            from packaging.specifiers import SpecifierSet
            match = re.search(r"[><=!~]+[^;]+", req.replace("(", "").replace(")", ""))
            if match:
                spec = SpecifierSet(match.group(0).strip())
            break
except Exception:
    pass

if constraint_str:
    print("INFO:" + constraint_str, file=sys.stderr)

# -- Query GitHub releases --
try:
    url = "https://api.github.com/repos/OpenNMT/CTranslate2/releases?per_page=20"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        releases = json.loads(resp.read())
except Exception as e:
    print("ERROR:GitHub API failed: " + str(e))
    sys.exit(0)

from packaging.version import Version

# -- Find releases with ROCm wheels, newest first --
best_incompatible = None
for release in releases:
    tag = release.get("tag_name", "")
    if not tag.startswith("v"):
        continue
    ver_str = tag[1:]
    has_rocm = any(
        a["name"] == "rocm-python-wheels-Linux.zip"
        for a in release.get("assets", [])
    )
    if not has_rocm:
        continue
    try:
        ver = Version(ver_str)
    except Exception:
        continue

    if spec is None or ver in spec:
        print("COMPATIBLE:" + ver_str)
        sys.exit(0)

    if best_incompatible is None:
        best_incompatible = ver_str

if best_incompatible:
    print("INCOMPATIBLE:" + best_incompatible + ":" + constraint_str)
else:
    print("ERROR:No CTranslate2 releases found with ROCm wheels")
' 2>&1
}

install_rocm_ctranslate2() {
    step "Installing ROCm CTranslate2 wheel for AMD GPU acceleration..."

    # 1. Verify ROCm environment (path, version, GPU agent, LD_LIBRARY_PATH)
    if ! verify_rocm_environment; then
        warn "Skipping ROCm CTranslate2 — ROCm environment not ready."
        return 0
    fi

    local macaw_python
    macaw_python="$(get_macaw_python)"

    local pyver
    pyver="$("$macaw_python" -c 'import sys; print(f"cp{sys.version_info.major}{sys.version_info.minor}")')"
    info "Python version for wheel: $pyver"

    # 2. Find latest compatible CTranslate2 version with ROCm wheels
    local ct2_version=""
    step "Querying GitHub for CTranslate2 ROCm releases..."
    local find_result
    find_result="$(find_compatible_rocm_ct2 "$macaw_python")" || true

    if [[ -z "$find_result" ]]; then
        find_result="ERROR:Could not determine compatible version"
    fi

    # Print any INFO lines from stderr (faster-whisper constraint)
    while IFS= read -r line; do
        case "$line" in INFO:*) info "  ${line#INFO:}" ;; esac
    done <<< "$find_result"

    # Get the last non-INFO line (the actual result)
    local result_line
    result_line="$(grep -v '^INFO:' <<< "$find_result" | tail -1)"
    local result_type="${result_line%%:*}"

    case "$result_type" in
        COMPATIBLE)
            ct2_version="${result_line#COMPATIBLE:}"
            info "Latest compatible CTranslate2: v${ct2_version}"
            ;;
        INCOMPATIBLE)
            local rest="${result_line#INCOMPATIBLE:}"
            ct2_version="${rest%%:*}"
            local ct2_constraint="${rest#*:}"
            warn "Latest CTranslate2 with ROCm wheels is v${ct2_version}"
            warn "but faster-whisper requires: ${ct2_constraint}"
            warn "Installing v${ct2_version} will replace the current ctranslate2."
            echo
            if prompt_yn "Install anyway (downgrade)?" "n"; then
                info "Proceeding with v${ct2_version}..."
            else
                info "Skipping ROCm wheel. Using CPU-only."
                return 0
            fi
            ;;
        ERROR)
            local errmsg="${result_line#ERROR:}"
            warn "Could not find CTranslate2 ROCm release: ${errmsg}"
            warn "You can install manually from:"
            warn "  https://github.com/OpenNMT/CTranslate2/releases"
            warn "Download the ROCm wheel for Python $pyver and install with:"
            warn "  $macaw_python -m pip install --force-reinstall <wheel_file>"
            return 0
            ;;
    esac

    # 3. Download
    local ct2_release_url="https://github.com/OpenNMT/CTranslate2/releases/download/v${ct2_version}"
    local zip_name="rocm-python-wheels-Linux.zip"

    local tmpdir
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' RETURN

    info "Downloading ROCm wheels from CTranslate2 v${ct2_version}..."
    if ! curl -fSL -o "${tmpdir}/${zip_name}" "${ct2_release_url}/${zip_name}"; then
        warn "Download failed. You can install manually:"
        warn "  curl -LO ${ct2_release_url}/${zip_name}"
        warn "  unzip ${zip_name}"
        warn "  $macaw_python -m pip install --force-reinstall ctranslate2-*${pyver}*.whl"
        return 0
    fi

    # Log SHA256 for auditability
    local actual_sha256
    actual_sha256="$(sha256sum "${tmpdir}/${zip_name}" | cut -d' ' -f1)"
    info "SHA256: ${actual_sha256}"

    # 4. Extract and find matching wheel
    unzip -q "${tmpdir}/${zip_name}" -d "${tmpdir}/wheels"

    local wheel
    wheel="$(find "${tmpdir}/wheels" -name "ctranslate2-*${pyver}*manylinux*.whl" | head -1)"

    if [[ -z "$wheel" ]]; then
        warn "No CTranslate2 ROCm wheel found for Python $pyver."
        warn "Available wheels:"
        find "${tmpdir}/wheels" -name "ctranslate2-*.whl" -exec basename {} \; | sed 's/^/  /'
        warn "Continuing with CPU-only."
        return 0
    fi

    info "Found wheel: $(basename "$wheel")"

    # 5. Install into the macaw venv
    if has_cmd uv; then
        local venv_dir
        venv_dir="$(uv tool dir)/macaw"
        if [[ -d "$venv_dir" ]]; then
            uv pip install --python "${venv_dir}/bin/python" --force-reinstall "$wheel"
        else
            error "Could not find macaw tool venv at $venv_dir"
            error "Install manually: $macaw_python -m pip install --force-reinstall $(basename "$wheel")"
            return 0
        fi
    else
        pip install --force-reinstall "$wheel"
    fi

    # 6. Post-install verification: confirm GPU is actually detected
    step "Verifying ROCm GPU detection..."
    local verify_result
    verify_result="$("$macaw_python" -c '
import ctranslate2
n = ctranslate2.get_cuda_device_count()
if n > 0:
    ct = sorted(ctranslate2.get_supported_compute_types("cuda"))
    print("OK:" + str(n) + ":" + ", ".join(ct))
else:
    print("NO_GPU")
' 2>/dev/null)" || true

    case "${verify_result%%:*}" in
        OK)
            local gpu_count
            gpu_count="$(echo "$verify_result" | cut -d: -f2)"
            local compute_types
            compute_types="$(echo "$verify_result" | cut -d: -f3)"
            info "ROCm CTranslate2 installed — ${gpu_count} GPU(s) detected"
            info "Supported compute types: ${compute_types}"
            ;;
        NO_GPU)
            warn "CTranslate2 ROCm installed but no GPU detected."
            warn "Check ROCm installation and GPU compatibility."
            warn "Transcription will fall back to CPU."
            warn "Set MACAW_FORCE_CPU=1 in the service env if GPU causes crashes."
            ;;
        *)
            warn "Could not verify GPU detection (CTranslate2 import may have failed)."
            warn "The service will attempt GPU at runtime and fall back to CPU if needed."
            warn "Set MACAW_FORCE_CPU=1 in the service env to force CPU mode."
            ;;
    esac
}

# --- Main install ---
# NOTE: not named `install` — that would shadow coreutils `install(1)`, which we
# use below to place the icon, turning that call into infinite recursion.
run_install() {
    echo
    info "Installing macaw — speech-to-text for Linux"
    echo

    detect_distro
    detect_display_server
    info "Distro: $DISTRO | Display: $DISPLAY_SERVER"
    echo

    ensure_systemd
    ensure_python
    ensure_uv
    acquire_source
    ensure_system_deps
    ensure_ydotool_access

    echo
    step "Installing macaw..."

    # Detect GPU for acceleration
    # faster-whisper uses CTranslate2 which supports CUDA (NVIDIA) natively
    # and ROCm (AMD) via a separate wheel from GitHub releases.
    local pip_extra=""
    local gpu_type="none"
    local has_rocm=false

    # Check for NVIDIA GPU
    if has_cmd nvidia-smi; then
        gpu_type="nvidia"
        pip_extra="[cuda]"
    else
        for nvcc_path in nvcc /opt/cuda/bin/nvcc /usr/local/cuda/bin/nvcc; do
            if has_cmd "$nvcc_path" || [[ -x "$nvcc_path" ]]; then
                gpu_type="nvidia"
                pip_extra="[cuda]"
                break
            fi
        done
    fi

    # Check for AMD GPU
    if [[ "$gpu_type" == "none" ]]; then
        if lspci 2>/dev/null | grep -qi "amd.*vga\|radeon\|amdgpu"; then
            gpu_type="amd"
            # Check if ROCm is installed
            if has_cmd rocminfo || [[ -d /opt/rocm ]] || [[ -d "${ROCM_PATH:-}" ]]; then
                has_rocm=true
            fi
        fi
    fi

    case "$gpu_type" in
        nvidia)
            info "NVIDIA GPU detected — installing with CUDA acceleration"
            ;;
        amd)
            if $has_rocm; then
                info "AMD GPU + ROCm detected — will install ROCm CTranslate2 wheel for GPU acceleration"
            else
                warn "AMD GPU detected but ROCm is not installed."
                info "Install ROCm for GPU acceleration: https://rocm.docs.amd.com/en/latest/deploy/linux/quick_start.html"
                info "Proceeding with CPU-only (int8) — still fast for dictation."
            fi
            ;;
        *)
            warn "No GPU detected — installing CPU-only"
            ;;
    esac

    if has_cmd uv; then
        info "Installing macaw via uv..."
        # uv picks the interpreter before resolving and won't backtrack, so it
        # can grab a too-new CPython that lacks onnxruntime/CUDA wheels. Pin the
        # range to match requires-python in pyproject.toml (raise together).
        uv tool install --python '>=3.10,<3.14' "${MACAW_SRC}${pip_extra}" --force --reinstall
    else
        info "Installing macaw via pip..."
        local pip_args=(install --user "${MACAW_SRC}${pip_extra}")
        if pip install --user --dry-run "$MACAW_SRC" 2>&1 | grep -q "externally-managed"; then
            warn "PEP 668 detected — using --break-system-packages (consider using uv instead)"
            pip_args+=(--break-system-packages)
        fi
        pip "${pip_args[@]}"
    fi

    # Install ROCm CTranslate2 wheel for AMD GPUs
    if [[ "$gpu_type" == "amd" ]] && $has_rocm; then
        install_rocm_ctranslate2
    fi

    # Verify the command is available
    local macaw_bin
    macaw_bin="$(command -v macaw 2>/dev/null || true)"
    if [[ -z "$macaw_bin" ]]; then
        # Common case: ~/.local/bin not in PATH
        if [[ -x "$HOME/.local/bin/macaw" ]]; then
            macaw_bin="$HOME/.local/bin/macaw"
            warn "macaw found at $macaw_bin but not in PATH."
            warn "Add to your shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\""
        else
            error "macaw command not found after install. Check your PATH."
            exit 1
        fi
    fi
    info "Installed: $macaw_bin"

    # Install systemd user service from static template
    mkdir -p "$(dirname "$SERVICE_FILE")"
    cp "$MACAW_SRC/contrib/macaw.service" "$SERVICE_FILE"
    # Patch ExecStart to the actual binary path
    sed -i "s|ExecStart=.*|ExecStart=$macaw_bin|" "$SERVICE_FILE"
    info "Created $SERVICE_FILE"

    # Desktop launcher + icon → macaw shows up in the application menu
    local data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
    local apps_dir="$data_home/applications"
    local icon_dir="$data_home/icons/hicolor/256x256/apps"
    mkdir -p "$apps_dir" "$icon_dir"
    install -Dm644 "$MACAW_SRC/contrib/macaw.png" "$icon_dir/macaw.png"
    sed "s|^Exec=macaw$|Exec=$macaw_bin|" \
        "$MACAW_SRC/contrib/macaw.desktop" > "$apps_dir/macaw.desktop"
    update-desktop-database "$apps_dir" 2>/dev/null || true
    gtk-update-icon-cache "$data_home/icons/hicolor" 2>/dev/null || true
    info "Installed desktop launcher"

    # ROCm environment drop-in for AMD GPUs
    # NOTE: No LD_LIBRARY_PATH for CUDA — the Python code auto-detects
    # pip-bundled nvidia libs (nvidia-cublas-cu12, nvidia-cudnn-cu12) and
    # loads them at import time, avoiding version mismatches with system
    # or third-party CUDA installations (e.g. Ollama).
    if [[ "$gpu_type" == "amd" ]] && $has_rocm; then
        local rocm_path="${ROCM_PATH:-/opt/rocm}"
        if [[ -d "$rocm_path" ]]; then
            local dropin_dir
            dropin_dir="$(dirname "$SERVICE_FILE")/${SERVICE_NAME}.service.d"
            mkdir -p "$dropin_dir"
            cat > "$dropin_dir/rocm.conf" <<EOF
[Service]
Environment=ROCM_PATH=${rocm_path}
Environment=LD_LIBRARY_PATH=${rocm_path}/lib
EOF
            info "Created ROCm drop-in: $dropin_dir/rocm.conf"
        fi
    fi

    # Enable and start
    systemctl --user daemon-reload
    systemctl --user enable --now "$SERVICE_NAME"

    echo
    info "macaw is running!"
    echo
    local trigger_cmd
    trigger_cmd="$(command -v macaw 2>/dev/null || echo "$HOME/.local/bin/macaw") --trigger"
    info "Bind a hotkey to toggle recording:"
    echo
    echo "  Hyprland:  bind = , F9, exec, $trigger_cmd"
    echo "  Sway:      bindsym F9 exec $trigger_cmd"
    echo "  KDE/GNOME: bind F9 to '$trigger_cmd' in keyboard settings"
    echo
    info "Try:  macaw --status   ·   macaw --help"
    info "Check logs: journalctl --user -u macaw -f"
}

# --- Main ---

if is_installed; then
    warn "macaw service is already installed."
    if prompt_yn "Uninstall?" "n"; then
        uninstall
    else
        info "No changes made."
    fi
else
    run_install
fi
