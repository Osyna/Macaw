#!/usr/bin/env bash
# Build a self-contained CPU AppImage of macaw (base Whisper backend) from a
# built wheel. GPU extras (nemo/voxtral) are intentionally excluded — they are
# huge and machine-specific; GPU users install via `install.sh` / AUR.
#
# Usage:  packaging/appimage/build.sh [path/to/macaw-*.whl]
# Output: macaw-<version>-x86_64.AppImage in the current directory.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
PYVER="${PYVER:-3.12}"

wheel="${1:-$(ls "$root"/dist/macaw-*.whl 2>/dev/null | head -1 || true)}"
if [[ -z "${wheel:-}" || ! -f "$wheel" ]]; then
    echo "error: wheel not found. Build it first:  python -m build --wheel" >&2
    exit 1
fi
wheel="$(readlink -f "$wheel")"
echo "==> Packaging $(basename "$wheel") into an AppImage (python $PYVER)"

# Let AppImage tooling run without FUSE (CI containers) and quietly on desktops.
export APPIMAGE_EXTRACT_AND_RUN=1

python -m pip install --quiet --upgrade python-appimage

recipe="$(mktemp -d)"
trap 'rm -rf "$recipe"' EXIT
cp "$root/contrib/macaw.desktop" "$recipe/macaw.desktop"
cp "$root/contrib/macaw.png" "$recipe/macaw.png"
# python-appimage pip-installs these into the bundled interpreter
printf '%s\n' "$wheel" > "$recipe/requirements.txt"

python -m python_appimage build app -p "$PYVER" "$recipe"

# python-appimage names the output after the .desktop `Name` (e.g.
# Macaw-x86_64.AppImage). Rename to the versioned, lowercase artifact the
# release workflow and README expect (macaw-<version>-<arch>.AppImage).
ver="$(basename "$wheel" | sed -E 's/^macaw-([^-]+)-.*/\1/')"
built="$(ls ./*.AppImage | head -1)"
final="macaw-${ver}-$(uname -m).AppImage"
[ "$built" = "./$final" ] || mv -f "$built" "$final"

echo "==> Built:"
ls -1 ./*.AppImage
