#!/usr/bin/env bash
# Update the PKGBUILD to a released version and regenerate .SRCINFO.
# Run from a checkout after the GitHub release exists:
#   packaging/aur/bump.sh 0.2.0
# Then commit PKGBUILD + .SRCINFO to the AUR repo.
set -euo pipefail
version="${1:?usage: bump.sh <version>}"
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"

url="https://github.com/Osyna/Macaw/archive/v${version}.tar.gz"
echo "==> Fetching $url for checksum"
sha="$(curl -fsSL "$url" | sha256sum | cut -d' ' -f1)"

sed -i -e "s/^pkgver=.*/pkgver=${version}/" \
       -e "s/^pkgrel=.*/pkgrel=1/" \
       -e "s/^sha256sums=.*/sha256sums=('${sha}')/" PKGBUILD

if command -v makepkg >/dev/null; then
    makepkg --printsrcinfo > .SRCINFO
    echo "==> Wrote PKGBUILD + .SRCINFO for v${version}"
else
    echo "==> PKGBUILD updated (sha256=${sha}). Run 'makepkg --printsrcinfo > .SRCINFO' on Arch."
fi
