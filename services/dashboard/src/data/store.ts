// SPDX-License-Identifier: MIT
// Dashboard state. Every source (live backend or in-browser demo) feeds the
// same events into this reducer, so the UI never knows which one is running.
import type {
  Alert, Delivery, GatewayStatus, HistoryPoint, Risk, RiskGrid, SirenCmd, SiteResponse, Telemetry,
} from "../types";

export const HISTORY_LEN = 360;

export interface Snapshot {
  telemetry?: Record<string, Telemetry>;
  risk?: Record<string, Risk>;
  gateway?: GatewayStatus | null;
  grid?: RiskGrid | null;
  sirens?: Record<string, SirenCmd>;
}

export type StreamEvent =
  | { kind: "telemetry"; data: Telemetry }
  | { kind: "risk"; data: Risk }
  | { kind: "grid"; data: RiskGrid }
  | { kind: "gateway"; data: GatewayStatus }
  | { kind: "siren"; data: SirenCmd & { id: string } }
  | { kind: "alert"; data: Alert }
  | { kind: "alert_ack"; data: { id: string; by: string; ts: string } }
  | { kind: "delivery"; data: Delivery };

export interface State {
  site: SiteResponse | null;
  connected: boolean;
  lastMessage: number | null;
  telemetry: Record<string, Telemetry>;
  risk: Record<string, Risk>;
  history: Record<string, HistoryPoint[]>;
  alerts: Alert[];
  gateway: GatewayStatus | null;
  grid: RiskGrid | null;
  /** Wall-clock receipt time of `grid`; simulated timestamps can run ahead of real time. */
  gridAt: number | null;
  sirens: Record<string, SirenCmd>;
  deliveries: Delivery[];
}

export const initialState: State = {
  site: null, connected: false, lastMessage: null, telemetry: {}, risk: {}, history: {}, alerts: [],
  gateway: null, grid: null, gridAt: null, sirens: {}, deliveries: [],
};

export type Action =
  | { type: "site"; site: SiteResponse }
  | { type: "snapshot"; snap: Snapshot; alerts: Alert[] }
  | { type: "connected"; connected: boolean }
  | { type: "history"; node: string; points: HistoryPoint[] }
  | { type: "deliveries"; deliveries: Delivery[] }
  | { type: "event"; event: StreamEvent };

function pushHistory(list: HistoryPoint[] | undefined, p: HistoryPoint): HistoryPoint[] {
  const out = list ? list.slice(-(HISTORY_LEN - 1)) : [];
  // Buffered readings replayed after an outage can arrive out of order.
  if (out.length && out[out.length - 1].ts > p.ts) {
    const i = out.findIndex((q) => q.ts > p.ts);
    out.splice(i, 0, p);
  } else {
    out.push(p);
  }
  return out;
}

export function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "site":
      return { ...s, site: a.site };
    case "connected":
      return { ...s, connected: a.connected };
    case "snapshot":
      return {
        ...s,
        telemetry: a.snap.telemetry ?? s.telemetry,
        risk: a.snap.risk ?? s.risk,
        gateway: a.snap.gateway ?? s.gateway,
        grid: a.snap.grid ?? s.grid,
        gridAt: a.snap.grid ? Date.now() : s.gridAt,
        sirens: a.snap.sirens ?? s.sirens,
        alerts: a.alerts,
      };
    case "deliveries":
      return { ...s, deliveries: a.deliveries.slice(0, 200) };
    case "history": {
      // Merge server history with anything already streamed in.
      const seen = new Set(a.points.map((p) => p.ts));
      const merged = [...a.points, ...(s.history[a.node] ?? []).filter((p) => !seen.has(p.ts))]
        .sort((x, y) => (x.ts < y.ts ? -1 : x.ts > y.ts ? 1 : 0))
        .slice(-HISTORY_LEN);
      return { ...s, history: { ...s.history, [a.node]: merged } };
    }
    case "event": {
      const now = Date.now();
      const e = a.event;
      switch (e.kind) {
        case "telemetry": {
          const r = s.risk[e.data.node];
          const point: HistoryPoint = { ...e.data, score: r?.score, level: r?.level };
          return {
            ...s, lastMessage: now,
            telemetry: { ...s.telemetry, [e.data.node]: e.data },
            history: { ...s.history, [e.data.node]: pushHistory(s.history[e.data.node], point) },
          };
        }
        case "risk": {
          // Attach the score to the matching telemetry point for the risk chart.
          const h = s.history[e.data.node];
          let history = s.history;
          if (h?.length) {
            const idx = h.length - 1 - [...h].reverse().findIndex((p) => p.ts <= e.data.ts);
            if (idx < h.length && idx >= 0) {
              const copy = h.slice();
              copy[idx] = { ...copy[idx], score: e.data.score, level: e.data.level };
              history = { ...s.history, [e.data.node]: copy };
            }
          }
          return { ...s, lastMessage: now, risk: { ...s.risk, [e.data.node]: e.data }, history };
        }
        case "grid":
          return { ...s, lastMessage: now, grid: e.data, gridAt: now };
        case "gateway":
          return { ...s, lastMessage: now, gateway: e.data };
        case "siren":
          return { ...s, lastMessage: now, sirens: { ...s.sirens, [e.data.id]: e.data } };
        case "alert":
          if (s.alerts.some((x) => x.id === e.data.id)) return s;
          return { ...s, lastMessage: now, alerts: [e.data, ...s.alerts].slice(0, 100) };
        case "alert_ack":
          return {
            ...s,
            alerts: s.alerts.map((x) => (x.id === e.data.id ? { ...x, acked_by: e.data.by, acked_ts: e.data.ts } : x)),
          };
        case "delivery":
          return { ...s, deliveries: [e.data, ...s.deliveries].slice(0, 200) };
      }
    }
  }
  return s;
}
