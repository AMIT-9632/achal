// SPDX-License-Identifier: MIT
import { useState } from "react";
import type { AckResult } from "../data/sources";
import type { T } from "../i18n";
import { formatTtf, levelColour } from "../lib/levels";
import type { Alert, Delivery } from "../types";

interface Props {
  alerts: Alert[];
  deliveries: Delivery[];
  onAck: (id: string, by: string, token?: string) => Promise<AckResult>;
  onSelectNode: (id: string) => void;
  t: T;
}

function readName(): string {
  try {
    return localStorage.getItem("achal.operator") ?? "";
  } catch {
    return "";
  }
}

function AckForm({ id, onAck, t }: { id: string; onAck: Props["onAck"]; t: T }) {
  const [name, setName] = useState(readName);
  const [token, setToken] = useState("");
  const [needToken, setNeedToken] = useState(false);
  const [state, setState] = useState<"idle" | "busy" | "error">("idle");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setState("busy");
    try {
      localStorage.setItem("achal.operator", name.trim());
    } catch {
      /* storage blocked */
    }
    const r = await onAck(id, name.trim(), token || undefined);
    if (r === "unauthorized") setNeedToken(true);
    setState(r === "ok" ? "idle" : "error");
  };

  return (
    <form className="ack" onSubmit={submit}>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("ack_name")} aria-label={t("ack_name")} required />
      {needToken && (
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder={t("ack_token")}
          aria-label={t("ack_token")} />
      )}
      <button type="submit" disabled={state === "busy"}>{t("ack")}</button>
      {state === "error" && <span className="error">{t("ack_failed")}</span>}
    </form>
  );
}

export default function AlertList({ alerts, deliveries, onAck, onSelectNode, t }: Props) {
  if (alerts.length === 0) return <p className="muted">{t("no_alerts")}</p>;
  return (
    <ol className="alerts">
      {alerts.map((a, i) => {
        const ttf = formatTtf(a.ttf_hours, t);
        const sent = deliveries.filter((d) => d.alert_id === a.id);
        const needsAck = a.level !== "normal" && !a.acked_by;
        return (
          <li key={a.id} className={`alert lvl-border-${a.level}`} style={{ borderColor: levelColour[a.level] }}>
            <div className="alert-head">
              <span className={`badge lvl-${a.level}`}>{t(`level.${a.level}`)}</span>
              {a.siren && <span title={t("siren.on")} aria-label={t("siren.on")}>📢</span>}
              <time className="muted">{new Date(a.ts).toLocaleString()}</time>
            </div>
            <div>{a.reasons.map((r) => t(`reason.${r}`)).join(" · ")}</div>
            {ttf && <div className="ttf">{t("ttf")}: {ttf}</div>}
            {a.nodes.length > 0 && (
              <div className="node-links">
                {t("nodes")}:{" "}
                {a.nodes.map((n) => <button key={n} className="link" onClick={() => onSelectNode(n)}>{n}</button>)}
              </div>
            )}
            {sent.length > 0 && (
              <details>
                <summary className="muted">{t("deliveries")}: {sent.length}</summary>
                <ul className="deliveries">
                  {sent.map((d, k) => (
                    <li key={k}>{d.recipient} · {d.channel} · {d.lang} · <span className="muted">{d.status}</span></li>
                  ))}
                </ul>
              </details>
            )}
            {a.acked_by ? (
              <div className="muted">✓ {t("acked_by")} {a.acked_by}{a.acked_ts && ` · ${new Date(a.acked_ts).toLocaleTimeString()}`}</div>
            ) : needsAck && i < 5 ? (
              <AckForm id={a.id} onAck={onAck} t={t} />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
