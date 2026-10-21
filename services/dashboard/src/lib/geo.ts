// SPDX-License-Identifier: MIT
// Local-metre <-> lat/lon helpers and inverse-distance weighting for the risk layer.
import type { NodeInfo, RiskGrid } from "../types";

const EARTH_RADIUS_M = 6_371_000;
const deg = (rad: number) => (rad * 180) / Math.PI;

/** Same equirectangular approximation as achal_common.config.SiteConfig.to_latlon. */
export function makeProjector(nodes: NodeInfo[]) {
  const n = nodes[0];
  const lat0 = n ? n.lat - deg(n.y_m / EARTH_RADIUS_M) : 0;
  const cosLat = Math.cos((lat0 * Math.PI) / 180);
  const lon0 = n ? n.lon - deg(n.x_m / (EARTH_RADIUS_M * cosLat)) : 0;
  return (x: number, y: number): [number, number] => [
    lat0 + deg(y / EARTH_RADIUS_M),
    lon0 + deg(x / (EARTH_RADIUS_M * cosLat)),
  ];
}

/**
 * Inverse-distance-weighted grid from point scores (power 2). Used until the
 * risk engine publishes its own grid, and as the fallback when it goes stale.
 */
export function idwGrid(points: { x: number; y: number; v: number }[], pad = 30, step = 2.5, reach = 12): RiskGrid | null {
  if (points.length === 0) return null;
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad;
  const cols = Math.ceil((Math.max(...xs) + pad - x0) / step) + 1;
  const rows = Math.ceil((Math.max(...ys) + pad - y0) / step) + 1;
  const values = new Array<number>(rows * cols);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const x = x0 + c * step, y = y0 + r * step;
      let num = 0, den = 0, exact: number | null = null, dmin = Infinity;
      for (const p of points) {
        const d2 = (p.x - x) ** 2 + (p.y - y) ** 2;
        if (d2 < 1e-6) { exact = p.v; break; }
        dmin = Math.min(dmin, Math.sqrt(d2));
        const w = 1 / d2;
        num += w * p.v; den += w;
      }
      // Fade out away from the sensors: we do not claim to know risk where nothing measures it.
      const fade = dmin <= reach ? 1 : Math.exp(-(((dmin - reach) / reach) ** 2));
      values[r * cols + c] = exact ?? (num / den) * fade;
    }
  }
  return { ts: new Date().toISOString(), x0_m: x0, y0_m: y0, dx_m: step, dy_m: step, rows, cols, values, method: "idw-client" };
}

/** Score 0..1 -> RGBA. Low risk is fully transparent so the basemap stays readable. */
export function riskRgba(v: number): [number, number, number, number] {
  const s = Math.max(0, Math.min(1, v));
  if (s < 0.1) return [0, 0, 0, 0];
  // yellow (watch) -> orange (warning) -> red (critical)
  const stops: [number, [number, number, number]][] = [[0.1, [249, 168, 37]], [0.6, [239, 108, 0]], [0.9, [198, 40, 40]]];
  let rgb = stops[stops.length - 1][1];
  for (let i = 0; i < stops.length - 1; i++) {
    const [a, ca] = stops[i], [b, cb] = stops[i + 1];
    if (s <= b) {
      const f = (s - a) / (b - a);
      rgb = [0, 1, 2].map((k) => Math.round(ca[k] + f * (cb[k] - ca[k]))) as [number, number, number];
      break;
    }
  }
  const ramp = Math.min(1, (s - 0.1) / 0.3); // soft edge instead of a hard cut-off
  return [rgb[0], rgb[1], rgb[2], Math.round(ramp * (60 + 140 * s))];
}
