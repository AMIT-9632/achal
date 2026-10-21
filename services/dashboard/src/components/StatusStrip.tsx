// SPDX-License-Identifier: MIT
// Field health at a glance: link to the backend, gateway uplink and buffer, sirens.
import type { T } from "../i18n";
import type { GatewayStatus, Siren, SirenCmd } from "../types";

interface Props {
  mode: "live" | "demo";
  connected: boolean;
  gateway: GatewayStatus | null;
  sirens: Siren[];
  sirenState: Record<string, SirenCmd>;
  exposure?: { households: number; people: number };
  t: T;
}

export default function StatusStrip({ mode, connected, gateway, sirens, sirenState, exposure, t }: Props) {
  return (
    <ul className="status" aria-live="polite">
      <li className={connected ? "ok" : "bad"}>
        <span className="dot" />{connected ? t("connection.live") : t("connection.lost")}
      </li>
      {gateway && (
        <li className={gateway.online ? "ok" : "bad"}>
          <span className="dot" />{t("gateway")}: {gateway.online ? t("gateway.online") : t("gateway.offline")}
          {gateway.buffered > 0 && <> · {gateway.buffered} {t("gateway.buffered")}</>}
        </li>
      )}
      {sirens.map((s) => {
        const st = sirenState[s.id];
        return (
          <li key={s.id} className={st?.on ? "alarm" : ""} title={s.label}>
            📢 {s.label}: {st?.on ? t("siren.on") : t("siren.off")}
            {st?.on && st.source === "gateway-local" && <small> ({t("siren.local")})</small>}
          </li>
        );
      })}
      {exposure && (
        <li className="muted">{exposure.people} {t("people_exposed")} · {exposure.households} {t("households")}</li>
      )}
      {mode === "demo" && <li className="demo">{t("mode.demo")}</li>}
    </ul>
  );
}
