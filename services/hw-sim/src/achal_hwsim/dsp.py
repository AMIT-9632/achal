# SPDX-License-Identifier: MIT
"""ctypes binding to firmware/lib/achal_dsp, the node's own signal processing.

The simulator does not re-implement the node's maths: it compiles the C file
the ESP32 and Wokwi firmware build from, and calls it. A virtual node therefore
filters, computes tilt and vibration features and decides alarm levels with
exactly the code that would run on the board.

The shared library is built on first use with the system C compiler (cc/gcc/
clang) into a cache directory next to this file. Set ACHAL_DSP_LIB to use a
prebuilt one.
"""

from __future__ import annotations

import ctypes as C
import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
SRC = REPO / "firmware" / "lib" / "achal_dsp" / "achal_dsp.c"
CACHE = Path(os.environ.get("ACHAL_DSP_CACHE", Path(__file__).resolve().parent / ".build"))

LVL_NORMAL, LVL_WARNING, LVL_CRITICAL = 0, 1, 2
CAUSE_TILT, CAUSE_DISP, CAUSE_VIB = 1, 2, 4
TOF_VALID, TOF_SIGMA_FAIL, TOF_SIGNAL_FAIL, TOF_MIN_RANGE_FAIL, TOF_PHASE_FAIL, TOF_HW_FAIL = 0, 1, 2, 3, 4, 5
TOF_TIMEOUT = 255


class VibT(C.Structure):
    _fields_ = [("rms_g", C.c_float), ("peak_g", C.c_float), ("ppv_mm_s", C.c_float), ("dom_hz", C.c_float)]


class TofSample(C.Structure):
    _fields_ = [("range_mm", C.c_uint16), ("status", C.c_uint8), ("signal_mcps", C.c_float), ("sigma_mm", C.c_float)]


class TofCfg(C.Structure):
    _fields_ = [
        ("min_valid", C.c_uint8),
        ("max_sigma_mm", C.c_float),
        ("min_signal_mcps", C.c_float),
        ("max_range_mm", C.c_uint16),
    ]


class DetCfg(C.Structure):
    _fields_ = [
        ("tilt_warn_deg", C.c_float),
        ("tilt_crit_deg", C.c_float),
        ("disp_warn_mm", C.c_float),
        ("disp_crit_mm", C.c_float),
        ("ppv_warn_scale", C.c_float),
        ("vib_can_go_critical", C.c_uint8),
        ("persist_n", C.c_uint8),
        ("clear_n", C.c_uint8),
        ("hysteresis", C.c_float),
    ]


class DetState(C.Structure):
    _fields_ = [
        ("level", C.c_uint8),
        ("cause", C.c_uint8),
        ("run_warn", C.c_uint8),
        ("run_crit", C.c_uint8),
        ("run_quiet", C.c_uint8),
    ]


def _build() -> Path:
    env = os.environ.get("ACHAL_DSP_LIB")
    if env:
        return Path(env)
    digest = hashlib.sha256(SRC.read_bytes()).hexdigest()[:12]
    out = CACHE / f"libachal_dsp-{digest}.so"
    if out.exists():
        return out
    cc = next((c for c in (os.environ.get("CC"), "cc", "gcc", "clang") if c and shutil.which(c)), None)
    if cc is None:
        raise RuntimeError("no C compiler found to build firmware/lib/achal_dsp; set ACHAL_DSP_LIB")
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(f".{os.getpid()}.tmp")
    # -ffp-contract=off: no fused multiply-add, closer to the ESP32's single-precision FPU results
    subprocess.run(
        [
            cc,
            "-std=c99",
            "-O2",
            "-fPIC",
            "-shared",
            "-ffp-contract=off",
            "-Wall",
            "-Wextra",
            str(SRC),
            "-o",
            str(tmp),
            "-lm",
        ],
        check=True,
    )
    tmp.replace(out)
    return out


_lib = C.CDLL(str(_build()))
_lib.achal_mpu_counts_to_g.restype = C.c_float
_lib.achal_mpu_counts_to_g.argtypes = [C.c_int16, C.c_uint8]
_lib.achal_accel_temp_comp.restype = C.c_float
_lib.achal_accel_temp_comp.argtypes = [C.c_float] * 4
_lib.achal_mpu_temp_c.restype = C.c_float
_lib.achal_mpu_temp_c.argtypes = [C.c_int16]
_lib.achal_tilt_deg.argtypes = [C.c_float, C.c_float, C.c_float, C.POINTER(C.c_float), C.POINTER(C.c_float)]
_lib.achal_ema.restype = C.c_float
_lib.achal_ema.argtypes = [C.c_float, C.c_float, C.c_float]
_lib.achal_median3.restype = C.c_float
_lib.achal_median3.argtypes = [C.c_float] * 3
_lib.achal_vib_features.argtypes = [C.POINTER(C.c_float), C.c_uint16, C.c_float, C.POINTER(VibT)]
_lib.achal_dgms_ppv_limit_mm_s.restype = C.c_float
_lib.achal_dgms_ppv_limit_mm_s.argtypes = [C.c_float]
_lib.achal_tof_filter.restype = C.c_int
_lib.achal_tof_filter.argtypes = [
    C.POINTER(TofCfg),
    C.POINTER(TofSample),
    C.c_uint8,
    C.POINTER(C.c_float),
    C.POINTER(C.c_uint8),
]
_lib.achal_det_default.argtypes = [C.POINTER(DetCfg)]
_lib.achal_det_reset.argtypes = [C.POINTER(DetState)]
_lib.achal_detect.restype = C.c_uint8
_lib.achal_detect.argtypes = [
    C.POINTER(DetCfg),
    C.POINTER(DetState),
    C.c_float,
    C.c_float,
    C.c_int,
    C.c_float,
    C.c_float,
]


def counts_to_g(counts: int, fs_g: int = 2) -> float:
    return _lib.achal_mpu_counts_to_g(int(counts), fs_g)


def temp_comp(a_g: float, k_g_per_c: float, t_c: float, t_ref_c: float) -> float:
    return _lib.achal_accel_temp_comp(a_g, k_g_per_c, t_c, t_ref_c)


def mpu_temp_c(counts: int) -> float:
    return _lib.achal_mpu_temp_c(int(counts))


def tilt_deg(mx: float, my: float, mz: float) -> tuple[float, float]:
    tx, ty = C.c_float(), C.c_float()
    _lib.achal_tilt_deg(mx, my, mz, C.byref(tx), C.byref(ty))
    return tx.value, ty.value


def ema(prev: float | None, x: float, alpha: float) -> float:
    return _lib.achal_ema(float("nan") if prev is None else prev, x, alpha)


def median3(a: float, b: float, c: float) -> float:
    return _lib.achal_median3(a, b, c)


@dataclass
class Vib:
    rms_g: float
    peak_g: float
    ppv_mm_s: float
    dom_hz: float


def vib_features(a_g: np.ndarray, fs_hz: float) -> Vib:
    arr = np.ascontiguousarray(a_g, dtype=np.float32)
    out = VibT()
    _lib.achal_vib_features(arr.ctypes.data_as(C.POINTER(C.c_float)), len(arr), fs_hz, C.byref(out))
    return Vib(out.rms_g, out.peak_g, out.ppv_mm_s, out.dom_hz)


def dgms_ppv_limit(dom_hz: float) -> float:
    return _lib.achal_dgms_ppv_limit_mm_s(dom_hz)


def tof_cfg(
    min_valid: int = 3, max_sigma_mm: float = 15.0, min_signal_mcps: float = 0.25, max_range_mm: int = 1200
) -> TofCfg:
    return TofCfg(min_valid, max_sigma_mm, min_signal_mcps, max_range_mm)


def tof_filter(cfg: TofCfg, samples: list[tuple[int, int, float, float]]) -> tuple[float | None, int]:
    """samples: (range_mm, status, signal_mcps, sigma_mm). Returns (median mm or None, n_valid)."""
    n = min(len(samples), 32)
    arr = (TofSample * max(n, 1))(
        *[TofSample(int(min(r, 65535)), int(s), float(sig), float(sg)) for r, s, sig, sg in samples[:n]]
    )
    mm, nv = C.c_float(), C.c_uint8()
    ok = _lib.achal_tof_filter(C.byref(cfg), arr, n, C.byref(mm), C.byref(nv))
    return (mm.value if ok else None), nv.value


def det_cfg(**overrides: float) -> DetCfg:
    cfg = DetCfg()
    _lib.achal_det_default(C.byref(cfg))
    for k, v in overrides.items():
        if not hasattr(cfg, k):
            raise KeyError(k)
        setattr(cfg, k, v)
    return cfg


class Detector:
    """Stateful wrapper around achal_detect for one node."""

    def __init__(self, cfg: DetCfg | None = None) -> None:
        self.cfg = cfg or det_cfg()
        self.st = DetState()
        _lib.achal_det_reset(C.byref(self.st))

    def step(self, tilt_change_deg: float, disp_change_mm: float | None, ppv_mm_s: float, dom_hz: float) -> int:
        valid = disp_change_mm is not None and np.isfinite(disp_change_mm)
        return _lib.achal_detect(
            C.byref(self.cfg),
            C.byref(self.st),
            float(tilt_change_deg),
            float(disp_change_mm) if valid else 0.0,
            int(valid),
            float(ppv_mm_s),
            float(dom_hz),
        )

    @property
    def level(self) -> int:
        return self.st.level

    @property
    def cause(self) -> int:
        return self.st.cause
