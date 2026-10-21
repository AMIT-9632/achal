# SPDX-License-Identifier: MIT
"""The five ACHAL scenarios, built deterministically from (site, scenario, seed).

Each seed moves the event (which part of the panel, when, how big) inside
documented ranges, so a KPI run over N seeds is N different events, not one
event with N noise draws. Ranges are simulation assumptions to be calibrated
against CMPDI / BCCL / DGMS records; see docs/field-sim-model.md.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from achal_common.config import SiteConfig

from achal_sim.geomech import (
    Bowl,
    GaussianBowl,
    KnotheTrough,
    collapse_time,
    creep_to_failure,
    knothe_time,
    knothe_time_inverse,
)

SCENARIOS = ("normal", "trough", "pillar_collapse", "faults", "fire")

DEFAULT_HOURS = {"normal": 72.0, "trough": 504.0, "pillar_collapse": 48.0, "faults": 72.0, "fire": 96.0}

SUBSIDENCE_FACTOR = 0.6       # S_max / extraction height for caved bord-and-pillar workings (assumption)
DAMAGE_TILT_MM_PER_M = 10.0   # trough "failure": first node past this tilt ...
DAMAGE_STRAIN_MM_PER_M = 3.0  # ... or this tensile strain (structural damage thresholds, assumption)
TROUGH_HAZARD_FRACTION = 0.1  # in the trough: final subsidence >= 10% of S_max, or damaging tension
POTHOLE_HAZARD_MM = 5.0       # a node is in a pothole zone if its precursor creep reaches 5 mm
FIRE_HAZARD_DT_C = 3.0        # a node is over the fire once it is 3 degC above ambient

FAULT_KINDS = ("stuck", "spike", "drift", "battery")


@dataclass(frozen=True)
class Component:
    """A bowl whose amplitude follows a time function of hours since start."""

    kind: str  # trough | precursor | collapse | fire_sag
    bowl: Bowl
    time: Callable[[float], float]


@dataclass(frozen=True)
class Pothole:
    """A pillar (or fire-weakened roof) that creeps then fails at failure_h."""

    cx: float
    cy: float
    onset_h: float
    failure_h: float
    radius_m: float


@dataclass(frozen=True)
class HeatSource:
    """Underground fire below (cx, cy): the surface hot zone grows outwards."""

    cx: float
    cy: float
    onset_h: float
    dt_max_c: float
    tau_h: float
    r0_m: float
    growth_m_per_h: float
    r_max_m: float

    def delta_t(self, x: float, y: float, t_h: float) -> float:
        if t_h <= self.onset_h:
            return 0.0
        age = t_h - self.onset_h
        r = min(self.r_max_m, self.r0_m + self.growth_m_per_h * age)
        rho2 = (x - self.cx) ** 2 + (y - self.cy) ** 2
        return self.dt_max_c * (1 - math.exp(-age / self.tau_h)) * math.exp(-rho2 / r**2)


@dataclass(frozen=True)
class Fault:
    node: str
    kind: str
    onset_h: float


@dataclass
class ScenarioPlan:
    name: str
    seed: int
    duration_h: float
    components: list[Component] = field(default_factory=list)
    potholes: list[Pothole] = field(default_factory=list)
    heat: HeatSource | None = None
    faults: dict[str, Fault] = field(default_factory=dict)
    trough_nodes: set[str] = field(default_factory=set)
    pothole_nodes: set[str] = field(default_factory=set)
    onset_h: float | None = None
    failure_h: float | None = None
    params: dict[str, float | str] = field(default_factory=dict)


def build_plan(site: SiteConfig, name: str, seed: int, duration_h: float | None = None) -> ScenarioPlan:
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}, expected one of {', '.join(SCENARIOS)}")
    rng = random.Random(f"{seed}:{name}:plan")
    plan = ScenarioPlan(name=name, seed=seed, duration_h=duration_h or DEFAULT_HOURS[name])
    builder = {"normal": _normal, "trough": _trough, "pillar_collapse": _pillar_collapse,
               "faults": _faults, "fire": _fire}[name]
    builder(site, plan, rng)
    return plan


def _normal(site: SiteConfig, plan: ScenarioPlan, rng: random.Random) -> None:
    """Stable ground: noise, diurnal temperature, battery cycles and daily opencast blasts only."""


def _trough(site: SiteConfig, plan: ScenarioPlan, rng: random.Random) -> None:
    """Pillars crush under one half of the panel; a Knothe trough forms over days."""
    p = site.panel
    side = rng.choice(["east", "west", "north", "south"])
    w, ln = p.width_m, p.length_m
    x1, x2, y1, y2 = {
        "east": (0.5 * w, w, 0.0, ln), "west": (0.0, 0.5 * w, 0.0, ln),
        "north": (0.0, w, 0.5 * ln, ln), "south": (0.0, w, 0.0, 0.5 * ln),
    }[side]
    bowl = KnotheTrough.over_panel(x1, x2, y1, y2, depth_m=p.seam_depth_m, extraction_m=p.extraction_height_m,
                                   subsidence_factor=SUBSIDENCE_FACTOR, angle_of_draw_deg=p.angle_of_draw_deg)
    onset = 24.0 * rng.uniform(0.75, 1.25)
    rate = 0.15 / 24 * rng.uniform(0.8, 1.25)  # Knothe c, per hour (63% of final in ~a week)
    plan.components.append(Component("trough", bowl, partial(knothe_time, onset_h=onset, rate_per_h=rate)))
    plan.onset_h = onset

    worst = 0.0  # fraction of the final trough at which the first node passes a damage threshold
    for node in site.node_ids():
        pos = site.node_position_m(node)
        r = bowl.response(pos.x, pos.y)
        tilt = math.hypot(r.tilt_x, r.tilt_y)
        tension = max(r.strain_x, r.strain_y, 0.0)
        if r.s_mm >= TROUGH_HAZARD_FRACTION * bowl.s_max_mm or tension >= DAMAGE_STRAIN_MM_PER_M:
            plan.trough_nodes.add(node)  # inside the trough, or in the tension belt beyond its edge
        worst = max(worst, tilt / DAMAGE_TILT_MM_PER_M, tension / DAMAGE_STRAIN_MM_PER_M)
    if worst > 1:
        plan.failure_h = knothe_time_inverse(1 / worst, onset, rate)
    plan.params = {"section": side, "s_max_mm": round(bowl.s_max_mm, 1), "r_m": round(bowl.r_m, 2),
                   "knothe_c_per_day": round(rate * 24, 4), "onset_h": round(onset, 3)}


def _add_pothole(site: SiteConfig, plan: ScenarioPlan, cx: float, cy: float, onset: float, failure: float,
                 creep_mm: float, creep_r: float, depth_mm: float, radius: float) -> None:
    creep = GaussianBowl(cx, cy, creep_mm, creep_r)
    hole = GaussianBowl(cx, cy, depth_mm, radius)
    plan.components.append(Component("precursor", creep,
                                     partial(creep_to_failure, onset_h=onset, failure_h=failure)))
    plan.components.append(Component("collapse", hole, partial(collapse_time, failure_h=failure)))
    plan.potholes.append(Pothole(cx, cy, onset, failure, radius))
    for node in site.node_ids():
        pos = site.node_position_m(node)
        if creep.subsidence(pos.x, pos.y) >= POTHOLE_HAZARD_MM or hole.subsidence(pos.x, pos.y) >= POTHOLE_HAZARD_MM:
            plan.pothole_nodes.add(node)


def _seam_influence_radius(site: SiteConfig) -> float:
    """Gaussian radius of a point source at seam depth seen through the Knothe kernel exp(-pi rho^2 / r^2).

    Creep in one pillar loads the whole overburden, so the surface feels it over r / sqrt(pi)
    (about 24 m at 60 m depth), not just above the pillar. The pothole itself stays local.
    """
    p = site.panel
    return p.seam_depth_m * math.tan(math.radians(p.angle_of_draw_deg)) / math.sqrt(math.pi)


def _pillar_collapse(site: SiteConfig, plan: ScenarioPlan, rng: random.Random) -> None:
    """One pillar creeps with accelerating rate, then fails: a pothole opens at the surface."""
    p = site.panel
    cx, cy = rng.uniform(0.2, 0.8) * p.width_m, rng.uniform(0.2, 0.8) * p.length_m
    onset = rng.uniform(8, 16)
    failure = onset + rng.uniform(18, 30)
    creep_mm, creep_r = rng.uniform(40, 100), _seam_influence_radius(site) * rng.uniform(0.85, 1.15)
    depth_mm, radius = rng.uniform(1200, 2500), rng.uniform(5, 8)
    _add_pothole(site, plan, cx, cy, onset, failure, creep_mm, creep_r, depth_mm, radius)
    plan.onset_h, plan.failure_h = onset, failure
    plan.params = {"cx_m": round(cx, 2), "cy_m": round(cy, 2), "onset_h": round(onset, 3),
                   "failure_h": round(failure, 3), "creep_mm": round(creep_mm, 1), "creep_r_m": round(creep_r, 2),
                   "pothole_depth_mm": round(depth_mm, 0), "pothole_r_m": round(radius, 2)}


def _faults(site: SiteConfig, plan: ScenarioPlan, rng: random.Random) -> None:
    """Stable ground, but four nodes develop sensor faults: one of each kind."""
    nodes = rng.sample(site.node_ids(), len(FAULT_KINDS))
    for node, kind in zip(nodes, FAULT_KINDS, strict=True):
        plan.faults[node] = Fault(node, kind, rng.uniform(4, 24))
    plan.params = {f.node: f"{f.kind}@{f.onset_h:.2f}h" for f in plan.faults.values()}


def _fire(site: SiteConfig, plan: ScenarioPlan, rng: random.Random) -> None:
    """Coal-seam fire below one corner: surface heats, ground sags, the burnt roof then gives way."""
    p = site.panel
    cx = rng.choice([0.2, 0.8]) * p.width_m + rng.uniform(-5, 5)
    cy = rng.choice([0.2, 0.8]) * p.length_m + rng.uniform(-5, 5)
    onset = rng.uniform(4, 8)
    plan.heat = HeatSource(cx, cy, onset, dt_max_c=rng.uniform(30, 55), tau_h=30.0, r0_m=10.0,
                           growth_m_per_h=0.4, r_max_m=35.0)
    sag = GaussianBowl(cx, cy, rng.uniform(80, 160), 22.0)
    plan.components.append(Component("fire_sag", sag, partial(knothe_time, onset_h=onset, rate_per_h=1 / 36)))
    failure = onset + rng.uniform(54, 72)
    _add_pothole(site, plan, cx, cy, failure - 12.0, failure, creep_mm=rng.uniform(20, 40),
                 creep_r=_seam_influence_radius(site),
                 depth_mm=rng.uniform(600, 1000), radius=5.0)
    plan.onset_h, plan.failure_h = onset, failure
    plan.params = {"cx_m": round(cx, 2), "cy_m": round(cy, 2), "onset_h": round(onset, 3),
                   "failure_h": round(failure, 3), "dt_max_c": round(plan.heat.dt_max_c, 1)}
