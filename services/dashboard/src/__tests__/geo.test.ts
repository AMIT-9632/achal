// SPDX-License-Identifier: MIT
import { describe, expect, it } from "vitest";
import { idwGrid, makeProjector, riskRgba } from "../lib/geo";
import { formatTtf, maxLevel } from "../lib/levels";

describe("idwGrid", () => {
  it("passes through the data points and stays within their range", () => {
    const pts = [{ x: 0, y: 0, v: 0 }, { x: 50, y: 0, v: 1 }, { x: 0, y: 50, v: 0.5 }];
    const g = idwGrid(pts, 10, 5)!;
    const at = (x: number, y: number) => g.values[((y - g.y0_m) / g.dy_m) * g.cols + (x - g.x0_m) / g.dx_m];
    expect(at(50, 0)).toBeCloseTo(1);
    expect(at(0, 0)).toBeCloseTo(0);
    expect(Math.min(...g.values)).toBeGreaterThanOrEqual(0);
    expect(Math.max(...g.values)).toBeLessThanOrEqual(1);
  });

  it("returns null with no points", () => {
    expect(idwGrid([])).toBeNull();
  });
});

describe("projector", () => {
  it("round-trips a node's own position and puts north up", () => {
    const nodes = [{ id: "n00", x_m: 10, y_m: 10, lat: 23.7481, lon: 86.4141 }];
    const p = makeProjector(nodes);
    const [lat, lon] = p(10, 10);
    expect(lat).toBeCloseTo(23.7481, 9);
    expect(lon).toBeCloseTo(86.4141, 9);
    expect(p(10, 110)[0]).toBeGreaterThan(lat);
  });
});

describe("levels", () => {
  it("riskRgba hides low risk", () => {
    expect(riskRgba(0.1)[3]).toBe(0);
    expect(riskRgba(0.95)[3]).toBeGreaterThan(150);
  });

  it("site level is the worst node level", () => {
    const r = (level: "normal" | "watch" | "warning" | "critical") => ({ node: "n", ts: "", score: 0, level });
    expect(maxLevel([r("watch"), r("critical"), r("normal")])).toBe("critical");
    expect(maxLevel([])).toBe("normal");
  });

  it("formats time to failure", () => {
    const t = (k: string) => k.replace("unit.", "");
    expect(formatTtf(0.5, t)).toBe("30 min");
    expect(formatTtf(5.25, t)).toBe("5.3 h");
    expect(formatTtf(null, t)).toBeNull();
  });
});
