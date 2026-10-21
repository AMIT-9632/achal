// SPDX-License-Identifier: MIT
// Minimal dependency-free SVG line chart for node time series.
import { useState } from "react";

export interface Series { values: (number | null | undefined)[]; times: string[] }

interface Props {
  title: string;
  series: Series;
  colour?: string;
  digits?: number;
  /** Horizontal guide lines, e.g. level thresholds on the risk chart. */
  guides?: { value: number; colour: string }[];
  domain?: [number, number];
}

const W = 320, H = 96, PAD_L = 4, PAD_R = 4, PAD_T = 6, PAD_B = 6;

export default function LineChart({ title, series, colour = "var(--accent)", digits = 3, guides = [], domain }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const pts = series.values.map((v, i) => ({ v, i })).filter((p): p is { v: number; i: number } => p.v != null);
  const latest = pts.length ? pts[pts.length - 1].v : null;
  const n = series.values.length;

  let [lo, hi] = domain ?? [Math.min(...pts.map((p) => p.v)), Math.max(...pts.map((p) => p.v))];
  if (!pts.length) [lo, hi] = [0, 1];
  if (hi - lo < 1e-9) { lo -= 0.5 * (Math.abs(lo) || 1) * 0.1; hi += 0.5 * (Math.abs(hi) || 1) * 0.1; }
  const x = (i: number) => PAD_L + (n > 1 ? (i / (n - 1)) * (W - PAD_L - PAD_R) : 0);
  const y = (v: number) => PAD_T + (1 - (v - lo) / (hi - lo)) * (H - PAD_T - PAD_B);
  const d = pts.map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join("");

  const shown = hover != null ? series.values[hover] : latest;
  const shownTime = hover != null ? series.times[hover] : series.times[n - 1];

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const f = (e.clientX - r.left) / r.width;
    setHover(Math.max(0, Math.min(n - 1, Math.round(f * (n - 1)))));
  };

  return (
    <figure className="chart">
      <figcaption>
        <span>{title}</span>
        <span className="chart-value">
          {shown != null ? shown.toFixed(digits) : "–"}
          {shownTime && <small> · {new Date(shownTime).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small>}
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={title}
        onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {guides.filter((g) => g.value > lo && g.value < hi).map((g) => (
          <line key={g.value} x1={PAD_L} x2={W - PAD_R} y1={y(g.value)} y2={y(g.value)}
            stroke={g.colour} strokeDasharray="3 3" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        ))}
        <path d={d} fill="none" stroke={colour} strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        {hover != null && (
          <line x1={x(hover)} x2={x(hover)} y1={0} y2={H} stroke="var(--muted)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        )}
      </svg>
    </figure>
  );
}
