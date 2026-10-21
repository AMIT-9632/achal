// SPDX-License-Identifier: MIT
// Mirrors schemas/*.v1.json. Keep in sync with libs/achal-common/models.py.
export type Level = "normal" | "watch" | "warning" | "critical";

export interface NodeInfo { id: string; lat: number; lon: number; x_m: number; y_m: number }
export interface Siren { id: string; label: string; lat: number; lon: number }
export interface SiteResponse { site: { name: string; languages: string[] }; nodes: NodeInfo[]; sirens: Siren[] }

export interface Telemetry {
  node: string; ts: string; tilt_x_deg: number; tilt_y_deg: number; vib_rms_g: number;
  crack_mm: number; ext_mm: number; batt_v: number; hops?: number; rssi_dbm?: number; buffered?: boolean;
}
export interface Risk { node: string; ts: string; score: number; level: Level; ttf_hours?: number | null; reasons?: string[] }
export interface Alert { id: string; ts: string; level: Level; nodes: string[]; reasons: string[]; siren?: boolean }
