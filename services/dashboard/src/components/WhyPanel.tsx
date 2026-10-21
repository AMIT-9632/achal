// SPDX-License-Identifier: MIT
// "Why this alert": explains a risk score in plain words, so an operator or a
// sarpanch can see what the AI saw instead of trusting a black box.
import type { T } from "../i18n";
import { formatTtf, levelColour } from "../lib/levels";
import type { Risk } from "../types";

const ORDER = ["rules", "iforest", "trend", "consensus"];
const orderOf = (k: string) => (ORDER.includes(k) ? ORDER.indexOf(k) : ORDER.length);

export default function WhyPanel({ risk, t }: { risk: Risk; t: T }) {
  const comps = Object.entries(risk.components ?? {})
    .filter(([k]) => k !== "fused")
    .sort(([a], [b]) => orderOf(a) - orderOf(b));
  const ttf = formatTtf(risk.ttf_hours, t);
  return (
    <section className="why" aria-label={t("why")}>
      <h3>{t("why")}</h3>
      <div className="why-score">
        <span className="score-ring" style={{ borderColor: levelColour[risk.level] }}>{Math.round(risk.score * 100)}</span>
        <div>
          <b>{t(`level.${risk.level}`)}</b> · {t("risk_score")} {risk.score.toFixed(2)}
          <div className={ttf ? "ttf" : "muted"}>{ttf ? `${t("ttf")}: ${ttf}` : t("ttf_none")}</div>
          {risk.agreeing_neighbours != null && (
            <div className="muted">{risk.agreeing_neighbours} {t("why.neighbours")}</div>
          )}
        </div>
      </div>
      {comps.length > 0 && (
        <>
          <h4>{t("why.components")}</h4>
          <ul className="bars">
            {comps.map(([k, v]) => (
              <li key={k}>
                <span>{t(`comp.${k}`)}</span>
                <span className="bar"><span style={{ width: `${Math.round(Math.max(0, Math.min(1, v)) * 100)}%` }} /></span>
                <span className="num">{v.toFixed(2)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {!!risk.reasons?.length && (
        <>
          <h4>{t("why.reasons")}</h4>
          <ul className="chips">{risk.reasons.map((r) => <li key={r}>{t(`reason.${r}`)}</li>)}</ul>
        </>
      )}
    </section>
  );
}
