// SPDX-License-Identifier: MIT
// Where the dashboard gets its data: the ACHAL backend (REST + WebSocket), or
// the in-browser demo simulator for offline presentations and static hosting.
import type { Alert, Delivery, HistoryPoint, SiteResponse } from "../types";
import { startDemo } from "./demo";
import type { Action, Snapshot } from "./store";

export type AckResult = "ok" | "unauthorized" | "error";

export interface Source {
  mode: "live" | "demo";
  start(dispatch: (a: Action) => void): () => void;
  history(node: string, minutes: number): Promise<HistoryPoint[] | null>;
  ack(alertId: string, by: string, token?: string): Promise<AckResult>;
}

const API = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

export function liveSource(onFatal: (err: Error) => void): Source {
  return {
    mode: "live",
    start(dispatch) {
      let ws: WebSocket | null = null;
      let stopped = false;
      let retry = 1000;
      let timer: ReturnType<typeof setTimeout> | undefined;

      const loadSnapshot = async () => {
        const [snap, alerts] = await Promise.all([getJson<Snapshot>("/api/nodes"), getJson<Alert[]>("/api/alerts")]);
        dispatch({ type: "snapshot", snap, alerts });
        // Older backends have no /api/deliveries; the list then fills from the stream.
        const deliveries = await getJson<Delivery[]>("/api/deliveries").catch(() => null);
        if (deliveries) dispatch({ type: "deliveries", deliveries });
      };

      const open = () => {
        const base = API ? new URL(API, location.href) : location;
        const proto = base.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(`${proto}://${base.host}/ws`);
        ws.onopen = () => {
          retry = 1000;
          dispatch({ type: "connected", connected: true });
          // Catch up on anything missed while disconnected.
          loadSnapshot().catch(() => undefined);
        };
        ws.onmessage = (e) => {
          try {
            dispatch({ type: "event", event: JSON.parse(e.data) });
          } catch {
            /* ignore malformed frames */
          }
        };
        ws.onclose = () => {
          dispatch({ type: "connected", connected: false });
          if (!stopped) timer = setTimeout(open, (retry = Math.min(retry * 2, 15000)));
        };
      };

      getJson<SiteResponse>("/api/site")
        .then((site) => {
          dispatch({ type: "site", site });
          open();
        })
        .catch(onFatal);

      return () => {
        stopped = true;
        clearTimeout(timer);
        ws?.close();
      };
    },
    async history(node, minutes) {
      try {
        return await getJson<HistoryPoint[]>(`/api/nodes/${encodeURIComponent(node)}/history?minutes=${minutes}`);
      } catch {
        return null; // backend without history: the dashboard keeps its own rolling buffer
      }
    },
    async ack(alertId, by, token) {
      try {
        const r = await fetch(`${API}/api/alerts/${encodeURIComponent(alertId)}/ack`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
          body: JSON.stringify({ by }),
        });
        if (r.status === 401 || r.status === 403) return "unauthorized";
        return r.ok ? "ok" : "error";
      } catch {
        return "error";
      }
    },
  };
}

export function demoSource(): Source {
  let ackHook: ((id: string, by: string) => void) | null = null;
  return {
    mode: "demo",
    start(dispatch) {
      const demo = startDemo(dispatch);
      ackHook = demo.ack;
      return demo.stop;
    },
    async history() {
      return null;
    },
    async ack(alertId, by) {
      ackHook?.(alertId, by);
      return "ok";
    },
  };
}

/** Demo mode when the URL has ?demo, or when built with `npm run build:demo`. */
export function wantsDemo(): boolean {
  return import.meta.env.VITE_DEMO === "1" || new URLSearchParams(location.search).has("demo");
}
