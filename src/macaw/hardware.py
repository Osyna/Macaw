"""Best-effort hardware probe + model-fit ranking.

`probe()` detects what this machine offers (GPU vendor, VRAM, RAM, cores,
architecture) with zero hard dependencies — every source is optional and
failure degrades to "unknown". `rank()` scores the model catalog against
that profile using only the metadata models already declare in their YAML
(hardware, vram, rating, cloud, recommended), so new models participate
automatically. Results are deterministic and every pick carries a short
human-readable reason.
"""

from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess

# ── probe ────────────────────────────────────────────────────────────


def _nvidia_vram_gb() -> float | None:
    """Total VRAM of the first NVIDIA GPU, via nvidia-smi (None if no NVIDIA)."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        out = subprocess.run(
            [smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode != 0:
            return None
        first = out.stdout.strip().splitlines()[0]
        return round(float(first) / 1024, 1)  # MiB -> GiB
    except Exception:  # noqa: BLE001 — a probe must never break the engine
        return None


def _amd_vram_gb() -> float | None:
    """Total VRAM of the first AMD dGPU via sysfs (Linux; None if no AMD)."""
    for vendor_path in glob.glob("/sys/class/drm/card*/device/vendor"):
        try:
            if open(vendor_path).read().strip() != "0x1002":
                continue
            vram = os.path.join(os.path.dirname(vendor_path), "mem_info_vram_total")
            return round(int(open(vram).read().strip()) / 1024**3, 1)
        except OSError:
            continue
    return None


def _ram_gb() -> float:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        return round(pages * os.sysconf("SC_PAGE_SIZE") / 1024**3, 1)
    except (ValueError, OSError, AttributeError):
        return 0.0


def probe() -> dict:
    """One snapshot of this machine: cheap, safe, cached by the caller."""
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    apple = arm and platform.system() == "Darwin"
    nvidia = _nvidia_vram_gb()
    amd = None if nvidia is not None else _amd_vram_gb()
    gpu = "nvidia" if nvidia is not None else ("amd" if amd is not None else None)
    return {
        "os": platform.system().lower(),
        "arm": arm,
        "apple": apple,
        "cores": os.cpu_count() or 1,
        "ram_gb": _ram_gb(),
        "gpu": gpu,
        "vram_gb": nvidia or amd or 0.0,
    }


def summary(hw: dict) -> str:
    """One-line human description, e.g. 'NVIDIA GPU 24 GB · 16 cores · 62 GB RAM'."""
    parts = []
    if hw["gpu"]:
        parts.append(f"{hw['gpu'].upper()} GPU {hw['vram_gb']:.0f} GB")
    elif hw["apple"]:
        parts.append("Apple Silicon")
    elif hw["arm"]:
        parts.append("ARM CPU")
    else:
        parts.append("CPU only")
    parts.append(f"{hw['cores']} cores")
    if hw["ram_gb"]:
        parts.append(f"{hw['ram_gb']:.0f} GB RAM")
    return " · ".join(parts)


# ── ranking ──────────────────────────────────────────────────────────

_GB = re.compile(r"(\d+(?:\.\d+)?)\s*GB", re.IGNORECASE)


def _vram_need_gb(vram_field: str) -> float:
    """Parse a model's declared VRAM need ('~6 GB' -> 6.0; unknown -> 0)."""
    m = _GB.search(vram_field or "")
    return float(m.group(1)) if m else 0.0


def _fit(model: dict, hw: dict) -> tuple[int, str] | None:
    """(score, why) for one catalog entry, or None when it can't run here.

    Driven entirely by the model's own YAML metadata, so anything added to
    the catalog is ranked with no code change.
    """
    hw_field = model.get("hardware") or ""
    # "GPU recommended" / "Any GPU or 8-core CPU" still run on CPU — only an
    # unqualified GPU requirement is a hard one.
    needs_gpu = (
        "GPU" in hw_field and "CPU" not in hw_field and "recommended" not in hw_field
    )
    cpu_ok = not needs_gpu
    gpu_match = (hw["gpu"] == "nvidia" and "NVIDIA" in hw_field) or (
        hw["gpu"] == "amd" and "AMD" in hw_field
    )

    if model.get("cloud"):
        if not model.get("api_key_set"):
            return None  # can't suggest a cloud model without its key
        why = "cloud API — zero local compute, needs network"
        return 10 * model.get("rating", 0) - 15, why

    if needs_gpu:
        if not gpu_match:
            return None  # this machine lacks the GPU family it needs
        need = _vram_need_gb(model.get("vram") or "")
        if need and hw["vram_gb"] and hw["vram_gb"] < need:
            return None  # not enough VRAM
        score = 10 * model.get("rating", 0) + 30
        if need and hw["vram_gb"] >= 2 * need:
            score += 4  # comfortable headroom
        why = f"uses your {hw['vram_gb']:.0f} GB {hw['gpu'].upper()} GPU"
    else:
        score = 10 * model.get("rating", 0) + (10 if cpu_ok else 0)
        if hw["gpu"] is None:
            score += 8  # right-sized for a GPU-less machine
            why = "runs fully on CPU — no GPU needed"
        else:
            why = "resource-friendly — light on CPU, leaves the GPU free"
        if hw["arm"]:
            score += 8
            why = "light ONNX build — ideal on ARM CPUs"

    if model.get("recommended"):
        score += 8
    if model.get("rating", 0) >= 4:
        why += " · top-rated"
    return score, why


def rank(models: list[dict], hw: dict, top: int = 5, light_slots: int = 2) -> None:
    """Annotate payload dicts in place: fit_rank (1 = best pick, 0 = unranked)
    and fit_why. Deterministic: score desc, then rating desc, then id.

    `light_slots` picks are reserved for resource-friendly (CPU) models so a
    big-GPU machine still surfaces the best lightweight options — some, like
    Moonshine v2, are tiny AND very accurate.
    """
    scored = []
    for m in models:
        m["fit_rank"], m["fit_why"] = 0, ""
        res = _fit(m, hw)
        if res is not None:
            scored.append((res[0], m.get("rating", 0), m["id"], m, res[1]))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    picks = scored[:top]
    lights_in = sum(1 for t in picks if t[3].get("light"))
    if lights_in < light_slots:
        extra = [t for t in scored[top:] if t[3].get("light")]
        extra = extra[: light_slots - lights_in]
        if extra:
            picks = picks[: top - len(extra)] + extra
            picks.sort(key=lambda t: (-t[0], -t[1], t[2]))
    for i, (_, _, _, m, why) in enumerate(picks, start=1):
        m["fit_rank"], m["fit_why"] = i, why
