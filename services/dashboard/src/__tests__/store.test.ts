// SPDX-License-Identifier: MIT
import { describe, expect, it } from "vitest";
import { initialState, reducer, type StreamEvent } from "../data/store";
import type { Telemetry } from "../types";

const tel = (ts: string, crack = 0): Telemetry => ({
  node: "n12", ts, tilt_x_deg: 0, tilt_y_deg: 0, vib_rms_g: 0, crack_mm: crack, ext_mm: 0, batt_v: 4,
});
const ev = (event: StreamEvent) => ({ type: "event" as const, event });

describe("reducer", () => {
  it("keeps history in time order when buffered readings replay late", () => {
    let s = reducer(initialState, ev({ kind: "telemetry", data: tel("2026-10-21T10:00:00Z") }));
    s = reducer(s, ev({ kind: "telemetry", data: tel("2026-10-21T10:02:00Z") }));
    s = reducer(s, ev({ kind: "telemetry", data: { ...tel("2026-10-21T10:01:00Z"), buffered: true } }));
    expect(s.history.n12.map((p) => p.ts.slice(11, 16))).toEqual(["10:00", "10:01", "10:02"]);
    expect(s.telemetry.n12.ts).toBe("2026-10-21T10:01:00Z");
  });

  it("attaches risk scores to the matching reading", () => {
    let s = reducer(initialState, ev({ kind: "telemetry", data: tel("2026-10-21T10:00:00Z") }));
    s = reducer(s, ev({ kind: "risk", data: { node: "n12", ts: "2026-10-21T10:00:00Z", score: 0.7, level: "warning" } }));
    expect(s.history.n12[0].score).toBe(0.7);
    expect(s.risk.n12.level).toBe("warning");
  });

  it("dedupes alerts and applies acknowledgements", () => {
    const alert = { id: "a1", ts: "2026-10-21T10:00:00Z", level: "critical" as const, nodes: ["n12"], reasons: [] };
    let s = reducer(initialState, ev({ kind: "alert", data: alert }));
    s = reducer(s, ev({ kind: "alert", data: alert }));
    expect(s.alerts).toHaveLength(1);
    s = reducer(s, ev({ kind: "alert_ack", data: { id: "a1", by: "Overman Soren", ts: "2026-10-21T10:05:00Z" } }));
    expect(s.alerts[0].acked_by).toBe("Overman Soren");
  });

  it("tracks siren and gateway state", () => {
    let s = reducer(initialState, ev({ kind: "siren", data: { id: "siren-01", on: true, ts: "" } }));
    s = reducer(s, ev({ kind: "gateway", data: { online: false, buffered: 42, ts: "" } }));
    expect(s.sirens["siren-01"].on).toBe(true);
    expect(s.gateway?.buffered).toBe(42);
  });

  it("merges server history with streamed points without duplicates", () => {
    let s = reducer(initialState, ev({ kind: "telemetry", data: tel("2026-10-21T10:05:00Z") }));
    s = reducer(s, { type: "history", node: "n12", points: [tel("2026-10-21T10:00:00Z"), tel("2026-10-21T10:05:00Z")] });
    expect(s.history.n12).toHaveLength(2);
  });
});
