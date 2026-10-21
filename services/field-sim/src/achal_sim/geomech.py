# SPDX-License-Identifier: MIT
"""Surface ground movement over shallow coal workings.

Sign conventions (all scenarios, all services):
- x points east, y points north, in metres from the panel's south-west corner.
- Subsidence S is positive downwards, in mm.
- Surface slope (tilt) is reported for the upward displacement w = -S, so
  tilt_x > 0 means the ground surface rises towards +x (east). On the western
  flank of a trough the ground dips towards the centre, so tilt_x < 0 there.
- Horizontal displacement U points towards the trough centre and follows the
  Aversin/Budryk-Knothe relation U = B * grad(S), with B = r / sqrt(2*pi).
- Horizontal strain e = dU/dx, in mm/m, positive in tension.

Every bowl here has a fixed shape whose amplitude grows with time, so the
simulator evaluates each shape once per node and only rescales it per sample.
Formulas and sources: docs/field-sim-model.md and docs/references.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SQRT_PI = math.sqrt(math.pi)
SQRT_2PI = math.sqrt(2 * math.pi)


@dataclass(frozen=True)
class Response:
    """Surface response at one point. mm, mm/m (tilt, strain) and mm (displacement)."""

    s_mm: float = 0.0
    tilt_x: float = 0.0
    tilt_y: float = 0.0
    u_x: float = 0.0
    u_y: float = 0.0
    strain_x: float = 0.0
    strain_y: float = 0.0

    def scaled(self, k: float) -> Response:
        return Response(*(k * v for v in self._values()))

    def __add__(self, other: Response) -> Response:
        return Response(*(a + b for a, b in zip(self._values(), other._values(), strict=True)))

    def _values(self) -> tuple[float, ...]:
        return (self.s_mm, self.tilt_x, self.tilt_y, self.u_x, self.u_y, self.strain_x, self.strain_y)


class Bowl:
    """A subsidence shape S(x, y) in mm with horizontal-movement coefficient b_m."""

    b_m: float

    def subsidence(self, x: float, y: float) -> float:
        raise NotImplementedError

    def response(self, x: float, y: float, h: float = 0.05) -> Response:
        """Slopes and strains by central differences (h in metres; shapes are smooth)."""
        s = self.subsidence(x, y)
        sxp, sxm = self.subsidence(x + h, y), self.subsidence(x - h, y)
        syp, sym = self.subsidence(x, y + h), self.subsidence(x, y - h)
        dsdx, dsdy = (sxp - sxm) / (2 * h), (syp - sym) / (2 * h)
        d2sdx2, d2sdy2 = (sxp - 2 * s + sxm) / h**2, (syp - 2 * s + sym) / h**2
        return Response(
            s_mm=s,
            tilt_x=-dsdx,
            tilt_y=-dsdy,
            u_x=self.b_m * dsdx,
            u_y=self.b_m * dsdy,
            strain_x=self.b_m * d2sdx2,
            strain_y=self.b_m * d2sdy2,
        )


@dataclass(frozen=True)
class KnotheTrough(Bowl):
    """Knothe (1957) influence-function trough over a rectangular extraction.

    S(x, y) = S_max * Fx(x) * Fy(y), Fx = 0.5 * [erf(sqrt(pi)(x - x1)/r) - erf(sqrt(pi)(x - x2)/r)]
    where r = H / tan(beta) is the radius of major influence and x1..x2, y1..y2 are
    the panel edges moved inwards by the inflection-point offset. Sub-critical panels
    (narrower than about 2r) automatically give less than S_max at the centre.
    """

    x1: float
    x2: float
    y1: float
    y2: float
    s_max_mm: float
    r_m: float

    @property
    def b_m(self) -> float:  # type: ignore[override]
        return self.r_m / SQRT_2PI

    def _f(self, v: float, a: float, b: float) -> float:
        k = SQRT_PI / self.r_m
        return 0.5 * (math.erf(k * (v - a)) - math.erf(k * (v - b)))

    def subsidence(self, x: float, y: float) -> float:
        return self.s_max_mm * self._f(x, self.x1, self.x2) * self._f(y, self.y1, self.y2)

    @classmethod
    def over_panel(cls, x1: float, x2: float, y1: float, y2: float, *, depth_m: float, extraction_m: float,
                   subsidence_factor: float, angle_of_draw_deg: float, inflection_offset_m: float = 0.0
                   ) -> KnotheTrough:
        """Build from mining geometry. The angle of draw (from vertical) sets r = H * tan(angle)."""
        r = depth_m * math.tan(math.radians(angle_of_draw_deg))
        d = inflection_offset_m
        return cls(x1 + d, x2 - d, y1 + d, y2 - d, subsidence_factor * extraction_m * 1000.0, r)


@dataclass(frozen=True)
class GaussianBowl(Bowl):
    """Local bowl S = D * exp(-rho^2 / R^2) for a pothole or a creep zone over one pillar."""

    cx: float
    cy: float
    depth_mm: float
    radius_m: float

    @property
    def b_m(self) -> float:  # type: ignore[override]
        return self.radius_m / SQRT_2PI

    def subsidence(self, x: float, y: float) -> float:
        rho2 = (x - self.cx) ** 2 + (y - self.cy) ** 2
        return self.depth_mm * math.exp(-rho2 / self.radius_m**2)


# Time functions: return the fraction (0..1, or more) of a bowl's amplitude reached at time t (hours).

def knothe_time(t_h: float, onset_h: float, rate_per_h: float) -> float:
    """Knothe's first-order time function: dS/dt = c (S_final - S)."""
    if t_h <= onset_h:
        return 0.0
    return 1.0 - math.exp(-rate_per_h * (t_h - onset_h))


def knothe_time_inverse(fraction: float, onset_h: float, rate_per_h: float) -> float:
    """Time at which knothe_time reaches `fraction` (< 1)."""
    return onset_h - math.log(1.0 - fraction) / rate_per_h


def creep_to_failure(t_h: float, onset_h: float, failure_h: float, end_gap_h: float = 1 / 6) -> float:
    """Accelerating creep with linear inverse velocity (Fukuzono 1985, alpha = 2).

    v = 1 / (k (t_f - t))  =>  u = (1/k) ln((t_f - t0) / (t_f - t)).
    Normalised so u = 1 at t_f - end_gap_h; held there afterwards (the bowl then fails).
    """
    if t_h <= onset_h:
        return 0.0
    t = min(t_h, failure_h - end_gap_h)
    full = math.log((failure_h - onset_h) / end_gap_h)
    return math.log((failure_h - onset_h) / (failure_h - t)) / full


def collapse_time(t_h: float, failure_h: float, tau_h: float = 0.25) -> float:
    """Sudden sag at failure: most of the drop within ~tau, then it settles."""
    if t_h < failure_h:
        return 0.0
    return 1.0 - math.exp(-(t_h - failure_h) / tau_h)
