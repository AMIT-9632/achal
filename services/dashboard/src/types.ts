// SPDX-License-Identifier: MIT
// Mirrors schemas/*.v1.json. Keep in sync with libs/achal-common/models.py.
export type Level = "normal" | "watch" | "warning" | "critical";
export const LEVELS: Level[] = ["normal", "watch", "warning", "critical"];

export interface NodeInfo { id: string; lat: number; lon: number; x_m: number; y_m: number }
export interface Siren { id: string; label: string; lat: number; lon: number }
export interface SiteResponse {
  site: { site_id: string; name: string; district?: string; state?: string; languages: string[];
    exposure?: { households: number; people: number } };
  nodes: NodeInfo[];
  sirens: Siren[];
}

export interface Telemetry {
  node: string; ts: string; seq?: number; tilt_x_deg: number; tilt_y_deg: number; vib_rms_g: number;
  vib_peak_g?: number; crack_mm: number; ext_mm: number; temp_c?: number; batt_v: number;
  hops?: number; rssi_dbm?: number; snr_db?: number; buffered?: boolean;
}

export interface Risk {
  node: string; ts: string; score: number; level: Level; ttf_hours?: number | null;
  components?: Record<string, number>; reasons?: string[]; agreeing_neighbours?: number;
}

export interface Alert {
  id: string; site?: string; ts: string; level: Level; previous_level?: Level; nodes: string[];
  reasons: string[]; ttf_hours?: number | null; siren?: boolean;
  // Added by the backend when an operator acknowledges (not part of alert.v1).
  acked_by?: string; acked_ts?: string;
}

/** Retained on achal/{site}/gateway/status. */
export interface GatewayStatus { online: boolean; buffered: number; ts: string }

/** achal/{site}/siren/{id}/cmd */
export interface SirenCmd { on: boolean; level?: Level; ts: string; source?: string }

/** achal/{site}/risk/grid, row-major, row 0 at y0_m (south). Proposed to W3. */
export interface RiskGrid {
  ts: string; x0_m: number; y0_m: number; dx_m: number; dy_m: number;
  rows: number; cols: number; values: number[]; method?: string;
}

/** achal/{site}/alert/delivery, published by the alerts service. */
export interface Delivery {
  alert_id: string; recipient: string; channel: string; lang: string; status: string; ts: string; tier?: number;
}

/** One point of a node's history: telemetry plus the risk score at that time. */
export interface HistoryPoint extends Telemetry { score?: number; level?: Level }
