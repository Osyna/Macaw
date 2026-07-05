# AppImage

A self-contained, portable build of macaw (CPU / base Whisper backend). It
bundles a Python interpreter, PyQt6/Qt, and the Whisper backend, so it runs on
any glibc Linux with no system Python.

GPU backends (Parakeet/Voxtral/NeMo, CUDA/ROCm) are **not** included — they are
large and machine-specific. For GPU, use the install script or the AUR package.

## Build locally

```sh
python -m build --wheel                 # produces dist/macaw-*.whl
packaging/appimage/build.sh             # → macaw-<version>-x86_64.AppImage
```

Uses [`python-appimage`](https://github.com/niess/python-appimage): it takes the
`macaw.desktop` + `macaw.png` here and the built wheel (from
`requirements.txt`, generated per build), pip-installs everything into a
manylinux Python AppImage, and emits the final `.AppImage`.

CI builds this automatically on every `v*` tag (see `.github/workflows/release.yml`)
and attaches it to the GitHub Release.
