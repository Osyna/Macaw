import ctypes as _ctypes
import os as _os

# Pre-load pip-installed NVIDIA libs so ctranslate2 can find them at runtime.
# ROCm (AMD GPU) uses system-installed libs — no preloading needed.


def _preload_nvidia_libs() -> None:
    from importlib.util import find_spec

    lib_dirs: list[str] = []
    for mod in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            spec = find_spec(mod)
        except (ModuleNotFoundError, ValueError):
            continue
        if spec and spec.submodule_search_locations:
            for loc in spec.submodule_search_locations:
                lib_path = _os.path.join(loc, "lib")
                if _os.path.isdir(lib_path):
                    lib_dirs.append(lib_path)

    if not lib_dirs:
        return

    # Specific libs load for ctranslate2
    targets = ["libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9"]
    for d in lib_dirs:
        for name in targets:
            path = _os.path.join(d, name)
            if _os.path.isfile(path):
                try:
                    _ctypes.CDLL(path, mode=_ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


_preload_nvidia_libs()
