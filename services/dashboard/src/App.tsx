// SPDX-License-Identifier: MIT
// SCAFFOLD STUB (W0). Live node map + alert list. W4 adds node charts, risk
// heatmap, "why this alert" panel, siren status and operator acknowledgement.
import { useEffect, useMemo, useState } from "react";
import { CircleMarker, MapContainer, Popup, TileLayer } from "react-leaflet";
import { languageNames, translator, type Lang } from "./i18n";
import type { Alert, Level, Risk, SiteResponse, Telemetry } from "./types";

const levelColour: Record<Level, string> = {
  normal: "var(--normal)", watch: "var(--watch)", warning: "var(--warning)", critical: "var(--critical)",
};
const rank: Level[] = ["normal", "watch", "warning", "critical"];

export default function App() {
  const [lang, setLang] = useState<Lang>("hi");
  const t = useMemo(() => translator(lang), [lang]);
  const [site, setSite] = useState<SiteResponse | null>(null);
  const [telemetry, setTelemetry] = useState<Record<string, Telemetry>>({});
  const [risk, setRisk] = useState<Record<string, Risk>>({});
  const [alerts, setAlerts] = useState<Alert[]>([]);

  useEffect(() => {
    fetch("/api/site").then((r) => r.json()).then(setSite);
    fetch("/api/nodes").then((r) => r.json()).then((d) => { setTelemetry(d.telemetry); setRisk(d.risk); });
    fetch("/api/alerts").then((r) => r.json()).then(setAlerts);
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onmessage = (e) => {
      const { kind, data } = JSON.parse(e.data);
      if (kind === "telemetry") setTelemetry((p) => ({ ...p, [data.node]: data }));
      if (kind === "risk") setRisk((p) => ({ ...p, [data.node]: data }));
      if (kind === "alert") setAlerts((p) => [data, ...p].slice(0, 50));
    };
    return () => ws.close();
  }, []);

  const siteLevel = Object.values(risk).reduce<Level>(
    (acc, r) => (rank.indexOf(r.level) > rank.indexOf(acc) ? r.level : acc), "normal");

  if (!site) return <p style={{ padding: 16 }}>Loading…</p>;
  const centre: [number, number] = [site.nodes[12]?.lat ?? 23.748, site.nodes[12]?.lon ?? 86.414];

  return (
    <>
      <header>
        <h1>{t("title")}</h1>
        <span>{t("site_level")}: <span className={`badge lvl-${siteLevel}`}>{t(`level.${siteLevel}`)}</span></span>
        <select value={lang} onChange={(e) => setLang(e.target.value as Lang)} aria-label="Language">
          {(Object.keys(languageNames) as Lang[]).map((l) => <option key={l} value={l}>{languageNames[l]}</option>)}
        </select>
      </header>
      <main>
        <div className="map">
          <MapContainer center={centre} zoom={18} style={{ height: "100%", width: "100%" }}>
            <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' />
            {site.nodes.map((n) => {
              const r = risk[n.id], tel = telemetry[n.id];
              return (
                <CircleMarker key={n.id} center={[n.lat, n.lon]} radius={9}
                  pathOptions={{ color: levelColour[r?.level ?? "normal"], fillOpacity: 0.8 }}>
                  <Popup>
                    <b>{t("node")} {n.id}</b><br />
                    {tel && <>{t("tilt")}: {Math.max(Math.abs(tel.tilt_x_deg), Math.abs(tel.tilt_y_deg)).toFixed(4)}°<br />
                      {t("crack")}: {tel.crack_mm.toFixed(2)} mm<br />
                      {t("vibration")}: {tel.vib_rms_g.toFixed(4)} g<br />
                      {t("battery")}: {tel.batt_v.toFixed(2)} V</>}
                  </Popup>
                </CircleMarker>
              );
            })}
            {site.sirens.map((s) => (
              <CircleMarker key={s.id} center={[s.lat, s.lon]} radius={12}
                pathOptions={{ color: "#5c6bc0", dashArray: "4 4", fillOpacity: 0.2 }}>
                <Popup>{t("siren")}: {s.label}</Popup>
              </CircleMarker>
            ))}
          </MapContainer>
        </div>
        <aside>
          <h2>{t("alerts")}</h2>
          {alerts.length === 0 && <p className="muted">{t("no_alerts")}</p>}
          {alerts.map((a) => (
            <div key={a.id} className="alert" style={{ borderColor: levelColour[a.level] }}>
              <b>{t(`level.${a.level}`)}</b> · {a.nodes.join(", ")}<br />
              {a.reasons.map((r) => t(`reason.${r}`)).join(", ")}
              <div className="muted">{new Date(a.ts).toLocaleString()}</div>
            </div>
          ))}
          <p className="muted">{t("synthetic")}</p>
        </aside>
      </main>
    </>
  );
}
