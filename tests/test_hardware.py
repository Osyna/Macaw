"""Model-fit ranking: pure logic against synthetic machine profiles.
Run: uv run pytest tests/test_hardware.py"""

from __future__ import annotations

from macaw import hardware


def _m(id, hw="CPU / Any", vram="—", rating=3, cloud=False, key=False, rec=False):
    return {
        "id": id,
        "hardware": hw,
        "vram": vram,
        "rating": rating,
        "cloud": cloud,
        "api_key_set": key,
        "recommended": rec,
    }


NVIDIA_BOX = {
    "os": "linux",
    "arm": False,
    "apple": False,
    "cores": 16,
    "ram_gb": 64.0,
    "gpu": "nvidia",
    "vram_gb": 24.0,
}
CPU_BOX = {
    "os": "linux",
    "arm": False,
    "apple": False,
    "cores": 4,
    "ram_gb": 8.0,
    "gpu": None,
    "vram_gb": 0.0,
}
ARM_BOX = {
    "os": "linux",
    "arm": True,
    "apple": False,
    "cores": 8,
    "ram_gb": 8.0,
    "gpu": None,
    "vram_gb": 0.0,
}
SMALL_GPU = {
    "os": "linux",
    "arm": False,
    "apple": False,
    "cores": 8,
    "ram_gb": 16.0,
    "gpu": "nvidia",
    "vram_gb": 4.0,
}


def test_gpu_box_prefers_gpu_models():
    models = [
        _m("gpu-big", hw="NVIDIA GPU", vram="~6 GB", rating=5),
        _m("cpu-small", rating=5),
    ]
    hardware.rank(models, NVIDIA_BOX)
    assert models[0]["fit_rank"] == 1  # gpu-big wins on a 24 GB box
    assert "NVIDIA" in models[0]["fit_why"]
    assert models[1]["fit_rank"] == 2  # cpu model still suggested, ranked below


def test_cpu_box_excludes_gpu_models():
    models = [
        _m("gpu-big", hw="NVIDIA GPU", vram="~6 GB", rating=5),
        _m("cpu-small", rating=3),
    ]
    hardware.rank(models, CPU_BOX)
    assert models[0]["fit_rank"] == 0  # no GPU -> never suggested
    assert models[1]["fit_rank"] == 1
    assert "CPU" in models[1]["fit_why"]


def test_vram_ceiling_excludes_oversized_models():
    models = [
        _m("gpu-6gb", hw="NVIDIA GPU", vram="~6 GB", rating=5),
        _m("gpu-3gb", hw="NVIDIA GPU", vram="~3 GB", rating=4),
    ]
    hardware.rank(models, SMALL_GPU)  # 4 GB card
    assert models[0]["fit_rank"] == 0
    assert models[1]["fit_rank"] == 1


def test_cloud_needs_api_key():
    no_key = [_m("cloud", cloud=True, rating=5)]
    hardware.rank(no_key, CPU_BOX)
    assert no_key[0]["fit_rank"] == 0

    keyed = [_m("cloud", cloud=True, key=True, rating=5)]
    hardware.rank(keyed, CPU_BOX)
    assert keyed[0]["fit_rank"] == 1
    assert "cloud" in keyed[0]["fit_why"]


def test_arm_boosts_light_cpu_models():
    models = [_m("onnx-tiny", rating=3)]
    hardware.rank(models, ARM_BOX)
    assert models[0]["fit_rank"] == 1
    assert "ARM" in models[0]["fit_why"]


def test_rank_is_deterministic_and_capped():
    models = [_m(f"m{i}", rating=3) for i in range(9)]
    hardware.rank(models, CPU_BOX)
    ranked = [m for m in models if m["fit_rank"]]
    assert len(ranked) == 5  # top-5 cap
    assert [m["id"] for m in ranked] == sorted(m["id"] for m in ranked)  # id tiebreak


def test_amd_gpu_matches_amd_capable_models():
    amd_box = {**NVIDIA_BOX, "gpu": "amd", "vram_gb": 16.0}
    models = [
        _m("nvidia-only", hw="NVIDIA GPU", vram="~6 GB", rating=5),
        _m("any-gpu", hw="NVIDIA / AMD GPU", vram="~6 GB", rating=4),
    ]
    hardware.rank(models, amd_box)
    assert models[0]["fit_rank"] == 0  # CUDA-only model excluded on AMD
    assert models[1]["fit_rank"] == 1
