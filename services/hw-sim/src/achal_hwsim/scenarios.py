# SPDX-License-Identifier: MIT
"""Stage 1: labelled ground-movement scenarios for a field of sensor nodes.

Each scenario gives, for every node and every reporting window, the *true*
ground state (tilt, crack opening at the ToF gauge, vibration bursts, box
temperature) plus the injected sensor faults and the ground-truth label.

Ground model (same family as services/field-sim): a Gaussian influence bowl
s(r) = S(t) exp(-pi r^2 / R^2) around an epicentre. Ground tilt is ds/dr; the
horizontal displacement is u = B ds/dr with B = R / sqrt(2 pi) (the usual
Knothe-Aversin proportionality), and the ToF gauge, installed across the
nearest mapped tension crack, sees the horizontal strain accumulated over
CRACK_ACC_M of ground (ASSUMPTION: strain localises into the crack).

Vibration bursts are sinusoids with an exponential envelope, scaled to a target
peak particle velocity (PPV) at the source and attenuated with distance as
PPV ~ r^-1.5 (scaled-distance law exponent typical of surface blasting).

Labels (illustrative thresholds, *not* engineering-validated):
  ground level 2 (critical)  true tilt change >= 0.30 deg or crack opening >= 15 mm
  ground level 1 (warning)   true tilt change >= 0.10 deg or crack opening >= 5 mm
  vib_exceed                 true PPV above the DGMS Circular 7/1997 limit
Benign machinery vibration is labelled normal: the detector must not alarm on it.

Time is compressed for the detection tests (a 30-minute run carries movement
that takes days in the field); the energy study uses real time instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

CRACK_ACC_M = 5.0
TILT_WARN, TILT_CRIT, DISP_WARN, DISP_CRIT = 0.10, 0.30, 5.0, 15.0
SCENARIOS = ("normal", "warning", "critical")


@dataclass
class Burst:
    t0: float
    dur_s: float
    ppv_src_mm_s: float
    f_hz: float
    x: float
    y: float
    kind: str  # machinery | blast | collapse

    def ppv_at(self, x: float, y: float) -> float:
        r = max(np.hypot(x - self.x, y - self.y), 5.0)
        return self.ppv_src_mm_s * (r / 5.0) ** -1.5

    def accel_g(self, t: np.ndarray, x: float, y: float) -> np.ndarray:
        """Vertical acceleration (g) at the node for absolute times t."""
        tau = t - self.t0
        on = (tau >= 0) & (tau <= self.dur_s)
        if not on.any():
            return np.zeros_like(t)
        amp_g = self.ppv_at(x, y) * 2 * np.pi * self.f_hz / 9806.65
        env = np.exp(-3.0 * tau / self.dur_s) if self.kind != "machinery" else np.sin(np.pi * tau / self.dur_s) ** 2
        return np.where(on, amp_g * env * np.sin(2 * np.pi * self.f_hz * tau), 0.0)


@dataclass
class Fault:
    node: int
    kind: str  # accel_drift | missing | tof_invalid | tof_rain | dead
    t0: float
    t1: float
    rate: float = 0.0  # meaning depends on kind


@dataclass
class Scenario:
    name: str
    seed: int
    xy: np.ndarray  # (n, 2) node positions, m
    duration_s: float
    interval_s: float
    epicentre: tuple[float, float]
    radius_m: float = 25.0
    s_max_mm: float = 0.0
    onset_s: float = 0.0
    failure_s: float = 0.0  # end of the movement ramp / collapse time
    accelerating: bool = False
    step_nodes: list[int] = field(default_factory=list)
    step_t: float = 0.0
    step_mm: float = 0.0
    bursts: list[Burst] = field(default_factory=list)
    faults: list[Fault] = field(default_factory=list)
    crack_acc_m: float = CRACK_ACC_M
    temp0_c: float = 28.0
    temp_rise_c: float = 8.0  # sun on the box over the run

    @property
    def n(self) -> int:
        return len(self.xy)

    def times(self) -> np.ndarray:
        return np.arange(0, self.duration_s, self.interval_s)

    def temp_c(self, t: float) -> float:
        return self.temp0_c + self.temp_rise_c * min(t / self.duration_s, 1.0)

    def _shape(self, t: float) -> float:
        if t <= self.onset_s or self.s_max_mm == 0:
            return 0.0
        if not self.accelerating:
            return min((t - self.onset_s) / (self.failure_s - self.onset_s), 1.0)
        # Fukuzono/Voight inverse-velocity creep: velocity ~ 1/(tf - t), displacement ~ -ln(1 - x)
        tf = self.failure_s
        x = min((t - self.onset_s) / (tf - self.onset_s), 0.999)
        return float(min(-np.log(1 - x) / -np.log(1 - 0.999), 1.0))

    def ground(self, i: int, t: float) -> tuple[float, float, float]:
        """True (tilt_x_deg, tilt_y_deg, crack_mm) at node i, time t."""
        S = self.s_max_mm * self._shape(t)
        dx, dy = self.xy[i, 0] - self.epicentre[0], self.xy[i, 1] - self.epicentre[1]
        r = np.hypot(dx, dy)
        R = self.radius_m
        e = np.exp(-np.pi * r * r / (R * R))
        slope = S * 2 * np.pi * r / (R * R) * e / 1000.0  # rad (mm/m / 1000)
        tilt = np.degrees(slope)
        tx, ty = (tilt * dx / r, tilt * dy / r) if r > 1e-6 else (0.0, 0.0)
        B = R / np.sqrt(2 * np.pi)
        curvature = S * 2 * np.pi / (R * R) * e * (2 * np.pi * r * r / (R * R) - 1) / 1000.0  # 1/m
        crack = max(B * curvature * self.crack_acc_m * 1000.0, 0.0)  # tension opens the crack (mm)
        if i in self.step_nodes and t >= self.step_t:
            crack += self.step_mm
        return float(tx), float(ty), float(crack)

    def label(self, i: int, t: float) -> int:
        tx, ty, crack = self.ground(i, t)
        tilt = np.hypot(tx, ty)
        if tilt >= TILT_CRIT or crack >= DISP_CRIT:
            return 2
        if tilt >= TILT_WARN or crack >= DISP_WARN:
            return 1
        return 0

    def vib_exceed(self, i: int, t0: float, t1: float) -> bool:
        from .dsp import dgms_ppv_limit

        x, y = self.xy[i]
        return any(b.t0 < t1 and b.t0 + b.dur_s > t0 and b.ppv_at(x, y) >= dgms_ppv_limit(b.f_hz) for b in self.bursts)

    def vib_g(self, i: int, t: np.ndarray) -> np.ndarray:
        x, y = self.xy[i]
        out = np.zeros_like(t)
        for b in self.bursts:
            if b.t0 <= t[-1] and b.t0 + b.dur_s >= t[0]:
                out += b.accel_g(t, x, y)
        return out

    def faults_at(self, i: int, t: float) -> list[Fault]:
        return [f for f in self.faults if f.node == i and f.t0 <= t < f.t1]


def grid(n: int, spacing_m: float, cols: int | None = None) -> np.ndarray:
    cols = cols or int(np.ceil(np.sqrt(n * 1.25)))
    return np.array([[(k % cols) * spacing_m, (k // cols) * spacing_m] for k in range(n)], dtype=float)


def build(
    name: str, seed: int = 1, n: int = 20, spacing_m: float = 10.0, duration_s: float = 1800.0, interval_s: float = 1.0
) -> Scenario:
    """The three labelled scenarios of the guide, with the same background faults in all three."""
    if name not in SCENARIOS:
        raise ValueError(f"scenario must be one of {SCENARIOS}")
    rng = np.random.default_rng(seed)
    xy = grid(n, spacing_m)
    ext = xy.max(0)
    epi = (float(rng.uniform(0.3, 0.7) * ext[0]), float(rng.uniform(0.3, 0.7) * ext[1]))
    sc = Scenario(name, seed, xy, duration_s, interval_s, epi)
    D = duration_s
    # Benign background in every scenario: machinery passing, sensor faults.
    for _ in range(int(rng.integers(3, 6))):
        sc.bursts.append(
            Burst(
                float(rng.uniform(0.05, 0.95) * D),
                float(rng.uniform(8, 30)),
                float(rng.uniform(1.0, 3.0)),
                float(rng.uniform(12, 30)),
                float(rng.uniform(0, ext[0])),
                float(rng.uniform(-10, ext[1] + 10)),
                "machinery",
            )
        )
    picks = rng.choice(n, 4, replace=False)
    sc.faults += [
        Fault(int(picks[0]), "accel_drift", 0.2 * D, D, rate=2e-3 / 3600),  # +2 mg per hour bias creep
        Fault(int(picks[1]), "missing", 0.0, D, rate=0.05),  # 5 % of windows lose samples
        Fault(int(picks[2]), "tof_invalid", 0.4 * D, 0.6 * D, rate=0.5),  # half the ToF shots fail
        Fault(int(picks[3]), "tof_rain", 0.0, D, rate=0.05),  # 5 % spurious short returns
    ]
    if name == "warning":
        sc.radius_m, sc.crack_acc_m = 20.0, 3.0
        sc.s_max_mm = 0.24 / np.degrees(1) * 1000 * sc.radius_m / 1.52  # peak tilt ~0.24 deg
        sc.onset_s, sc.failure_s = 0.15 * D, 0.75 * D
        sc.bursts.append(Burst(0.5 * D, 4.0, 6.0, 15.0, epi[0] + 15, epi[1], "blast"))
    elif name == "critical":
        sc.radius_m = 22.0
        sc.s_max_mm = 0.6 / np.degrees(1) * 1000 * sc.radius_m / 1.52
        sc.onset_s, sc.failure_s = 0.1 * D, 0.85 * D
        sc.accelerating = True
        d = np.hypot(xy[:, 0] - epi[0], xy[:, 1] - epi[1])
        sc.step_nodes = [int(k) for k in np.argsort(d)[:2]]
        sc.step_t, sc.step_mm = 0.7 * D, 20.0
        sc.bursts.append(Burst(0.85 * D, 6.0, 40.0, 6.0, epi[0], epi[1], "collapse"))
    return sc
