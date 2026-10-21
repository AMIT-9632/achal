// SPDX-License-Identifier: MIT
// Node map on plain Leaflet (BSD-2). react-leaflet is not used because its
// Hippocratic-2.1 licence is not OSI-approved (see docs/ip-register.md).
import L from "leaflet";
import { useEffect, useRef } from "react";
import type { T } from "../i18n";
import { makeProjector, riskRgba } from "../lib/geo";
import { levelHex, tiltOf } from "../lib/levels";
import type { NodeInfo, Risk, RiskGrid, Siren, SirenCmd, Telemetry } from "../types";

interface Props {
  nodes: NodeInfo[];
  sirens: Siren[];
  telemetry: Record<string, Telemetry>;
  risk: Record<string, Risk>;
  sirenState: Record<string, SirenCmd>;
  grid: RiskGrid | null;
  showHeatmap: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
  t: T;
}

function gridImage(grid: RiskGrid): string {
  const c = document.createElement("canvas");
  c.width = grid.cols;
  c.height = grid.rows;
  const ctx = c.getContext("2d")!;
  const img = ctx.createImageData(grid.cols, grid.rows);
  for (let r = 0; r < grid.rows; r++) {
    for (let col = 0; col < grid.cols; col++) {
      const [R, G, B, A] = riskRgba(grid.values[r * grid.cols + col]);
      // Canvas row 0 is the top (north); grid row 0 is the south edge.
      const o = ((grid.rows - 1 - r) * grid.cols + col) * 4;
      img.data.set([R, G, B, A], o);
    }
  }
  ctx.putImageData(img, 0, 0);
  return c.toDataURL();
}

export default function MapView(p: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const markers = useRef<Record<string, L.CircleMarker>>({});
  const sirenMarkers = useRef<Record<string, L.Marker>>({});
  const overlay = useRef<L.ImageOverlay | null>(null);
  const onSelect = useRef(p.onSelect);
  onSelect.current = p.onSelect;

  // Create the map once per site.
  useEffect(() => {
    if (!el.current || p.nodes.length === 0) return;
    const m = L.map(el.current, { zoomControl: true, attributionControl: true, zoomSnap: 0.25 });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 20, maxNativeZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(m);
    const bounds = L.latLngBounds([...p.nodes, ...p.sirens].map((n) => [n.lat, n.lon] as [number, number]));
    m.fitBounds(bounds.pad(0.1));
    for (const n of p.nodes) {
      markers.current[n.id] = L.circleMarker([n.lat, n.lon], { radius: 10, weight: 2, fillOpacity: 0.85 })
        .on("click", () => onSelect.current(n.id))
        .addTo(m);
    }
    for (const s of p.sirens) {
      sirenMarkers.current[s.id] = L.marker([s.lat, s.lon], {
        icon: L.divIcon({ className: "siren-icon", html: "<span>📢</span>", iconSize: [30, 30] }),
        keyboard: true, title: s.label,
      }).addTo(m);
    }
    map.current = m;
    const ro = new ResizeObserver(() => m.invalidateSize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      m.remove();
      map.current = null;
      markers.current = {};
      sirenMarkers.current = {};
      overlay.current = null;
    };
  }, [p.nodes, p.sirens]);

  // Node colours, tooltips and selection.
  useEffect(() => {
    for (const n of p.nodes) {
      const mk = markers.current[n.id];
      if (!mk) continue;
      const r = p.risk[n.id], tel = p.telemetry[n.id];
      const colour = levelHex[r?.level ?? "normal"];
      const isSel = p.selected === n.id;
      mk.setStyle({ color: isSel ? "#1d1d1b" : "#ffffff", fillColor: tel ? colour : "#9e9e9e", weight: isSel ? 4 : 2 });
      mk.setRadius(isSel ? 13 : 10);
      const lines = [`<b>${p.t("node")} ${n.id}</b>`];
      if (r) lines.push(`${p.t("risk")}: ${p.t(`level.${r.level}`)} (${r.score.toFixed(2)})`);
      if (tel) lines.push(`${p.t("tilt")}: ${tiltOf(tel).toFixed(3)}° · ${p.t("crack")}: ${tel.crack_mm.toFixed(2)} mm`);
      mk.bindTooltip(lines.join("<br/>"), { direction: "top", offset: [0, -8] });
    }
  }, [p.nodes, p.risk, p.telemetry, p.selected, p.t]);

  // Sirens: pulse while sounding.
  useEffect(() => {
    for (const s of p.sirens) {
      const mk = sirenMarkers.current[s.id];
      const st = p.sirenState[s.id];
      if (!mk) continue;
      mk.getElement()?.classList.toggle("on", !!st?.on);
      mk.bindTooltip(`${p.t("siren")}: ${s.label}<br/>${st?.on ? p.t("siren.on") : p.t("siren.off")}`);
    }
  }, [p.sirens, p.sirenState, p.t]);

  // Risk heatmap overlay.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (!p.showHeatmap || !p.grid) {
      overlay.current?.remove();
      overlay.current = null;
      return;
    }
    const g = p.grid;
    const proj = makeProjector(p.nodes);
    const sw = proj(g.x0_m - g.dx_m / 2, g.y0_m - g.dy_m / 2);
    const ne = proj(g.x0_m + (g.cols - 0.5) * g.dx_m, g.y0_m + (g.rows - 0.5) * g.dy_m);
    const url = gridImage(g);
    if (overlay.current) {
      overlay.current.setUrl(url).setBounds(L.latLngBounds(sw, ne));
    } else {
      overlay.current = L.imageOverlay(url, [sw, ne], { opacity: 0.75, className: "risk-overlay", interactive: false }).addTo(m);
      overlay.current.bringToBack();
    }
  }, [p.grid, p.showHeatmap, p.nodes]);

  return <div ref={el} className="map-canvas" role="application" aria-label={p.t("map")} />;
}
