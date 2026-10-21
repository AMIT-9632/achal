// SPDX-License-Identifier: MIT
import { LEVELS, type Level, type Risk } from "../types";

export const levelColour: Record<Level, string> = {
  normal: "var(--normal)", watch: "var(--watch)", warning: "var(--warning)", critical: "var(--critical)",
};
// Leaflet paths need literal colours, CSS variables do not resolve in SVG attributes on every browser.
export const levelHex: Record<Level, string> = {
  normal: "#2e7d32", watch: "#f9a825", warning: "#ef6c00", critical: "#c62828",
};

export const rank = (l: Level) => LEVELS.indexOf(l);

export function maxLevel(risks: Iterable<Risk>): Level {
  let best: Level = "normal";
  for (const r of risks) if (rank(r.level) > rank(best)) best = r.level;
  return best;
}

/** Peak tilt of the two axes, in degrees. */
export const tiltOf = (t: { tilt_x_deg: number; tilt_y_deg: number }) =>
  Math.max(Math.abs(t.tilt_x_deg), Math.abs(t.tilt_y_deg));

/** "3.5 h", "45 min", or null when there is no estimate. */
export function formatTtf(hours: number | null | undefined, t: (k: string) => string): string | null {
  if (hours == null || !Number.isFinite(hours)) return null;
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} ${t("unit.min")}`;
  if (hours < 48) return `${hours.toFixed(1)} ${t("unit.h")}`;
  return `${Math.round(hours / 24)} ${t("unit.days")}`;
}
