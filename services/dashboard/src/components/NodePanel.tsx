// SPDX-License-Identifier: MIT
import type { T } from "../i18n";
import { levelHex, tiltOf } from "../lib/levels";
import type { HistoryPoint, Risk, Telemetry } from "../types";
import LineChart from "./LineChart";
import WhyPanel from "./WhyPanel";

interface Props {
  node: string;
  telemetry?: Telemetry;
  risk?: Risk;
  history: HistoryPoint[];
  onClose: () => void;
  t: T;
}

export default function NodePanel({ node, telemetry, risk, history, onClose, t }: Props) {
  const times = history.map((p) => p.ts);
  const col = (f: (p: HistoryPoint) => number | undefined) => ({ times, values: history.map(f) });
  return (
    <section className="card node-panel" aria-label={`${t("node")} ${node}`}>
      <div className="card-head">
        <h2>{t("node")} {node}</h2>
        <button className="ghost" onClick={onClose} aria-label={t("close")}>✕</button>
      </div>
      {telemetry && (
        <dl className="facts">
          <div><dt>{t("tilt")}</dt><dd>{tiltOf(telemetry).toFixed(3)}°</dd></div>
          <div><dt>{t("crack")}</dt><dd>{telemetry.crack_mm.toFixed(2)} mm</dd></div>
          <div><dt>{t("vibration")}</dt><dd>{telemetry.vib_rms_g.toFixed(4)} g</dd></div>
          <div><dt>{t("battery")}</dt><dd>{telemetry.batt_v.toFixed(2)} V</dd></div>
          {telemetry.hops != null && <div><dt>{t("hops")}</dt><dd>{telemetry.hops}</dd></div>}
          {telemetry.rssi_dbm != null && <div><dt>{t("rssi")}</dt><dd>{Math.round(telemetry.rssi_dbm)} dBm</dd></div>}
          <div className="wide"><dt>{t("last_seen")}</dt><dd>{new Date(telemetry.ts).toLocaleString()}</dd></div>
        </dl>
      )}
      {risk && <WhyPanel risk={risk} t={t} />}
      <div className="charts">
        <LineChart title={t("chart.risk")} series={col((p) => p.score)} domain={[0, 1]} digits={2}
          colour={levelHex[risk?.level ?? "normal"]}
          guides={[{ value: 0.35, colour: levelHex.watch }, { value: 0.6, colour: levelHex.warning },
            { value: 0.85, colour: levelHex.critical }]} />
        <LineChart title={t("chart.tilt")} series={col(tiltOf)} digits={3} />
        <LineChart title={t("chart.crack")} series={col((p) => p.crack_mm)} digits={2} />
        <LineChart title={t("chart.vibration")} series={col((p) => p.vib_rms_g)} digits={4} />
      </div>
    </section>
  );
}
