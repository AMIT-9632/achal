// SPDX-License-Identifier: MIT
// ACHAL operator dashboard: live node map, risk heatmap, node detail with
// "why this alert", sirens and gateway health, alert acknowledgement, 4 languages.
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import AlertList from "./components/AlertList";
import MapView from "./components/MapView";
import NodePanel from "./components/NodePanel";
import StatusStrip from "./components/StatusStrip";
import { demoSource, liveSource, wantsDemo, type Source } from "./data/sources";
import { initialState, reducer } from "./data/store";
import { initialLang, isDraft, languageNames, translator, type Lang } from "./i18n";
import { idwGrid } from "./lib/geo";
import { maxLevel, rank } from "./lib/levels";

// A risk-engine grid older than this is ignored in favour of the client-side IDW.
const GRID_STALE_MS = 5 * 60_000;

export default function App() {
  const [lang, setLang] = useState<Lang>(initialLang);
  const t = useMemo(() => translator(lang), [lang]);
  const [state, dispatch] = useReducer(reducer, initialState);
  const [fatal, setFatal] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [showHeatmap, setShowHeatmap] = useState(true);
  const asideRef = useRef<HTMLElement>(null);
  const source = useMemo<Source>(() => (wantsDemo() ? demoSource() : liveSource((e) => setFatal(e.message))), []);

  useEffect(() => source.start(dispatch), [source]);

  useEffect(() => {
    document.documentElement.lang = lang;
    try {
      localStorage.setItem("achal.lang", lang);
    } catch {
      /* storage blocked */
    }
  }, [lang]);

  // Load server history when a node is opened, and show its panel from the top.
  useEffect(() => {
    if (!selected) return;
    asideRef.current?.scrollTo({ top: 0 });
    let live = true;
    source.history(selected, 24 * 60).then((points) => {
      if (live && points) dispatch({ type: "history", node: selected, points });
    });
    return () => { live = false; };
  }, [selected, source]);

  // Jump to the hottest node when the site escalates and nothing is open.
  const topAlert = state.alerts[0];
  useEffect(() => {
    if (topAlert && rank(topAlert.level) >= rank("warning") && topAlert.nodes.length) {
      setSelected((cur) => cur ?? topAlert.nodes[0]);
    }
  }, [topAlert]);

  const siteLevel = maxLevel(Object.values(state.risk));
  const nodes = state.site?.nodes ?? [];

  const grid = useMemo(() => {
    const g = state.grid;
    if (g && state.gridAt && Date.now() - state.gridAt < GRID_STALE_MS) return g;
    const pts = nodes.filter((n) => state.risk[n.id]).map((n) => ({ x: n.x_m, y: n.y_m, v: state.risk[n.id].score }));
    return idwGrid(pts);
  }, [state.grid, state.gridAt, state.risk, nodes]);

  const ack = useCallback((id: string, by: string, token?: string) => source.ack(id, by, token), [source]);

  if (fatal) {
    return (
      <div className="fatal">
        <h1>{t("title")}</h1>
        <p>{t("connection.lost")} ({fatal})</p>
        <p><a href="?demo=1">{t("mode.demo")}</a></p>
      </div>
    );
  }
  if (!state.site) return <p className="loading">…</p>;

  return (
    <div className={`app site-${siteLevel}`}>
      <header>
        <div className="brand">
          <h1>{t("title")}</h1>
          <span className="muted">{state.site.site.name}</span>
        </div>
        <div className="header-right">
          <span className={`badge big lvl-${siteLevel}`} aria-live="assertive">
            {t("site_level")}: {t(`level.${siteLevel}`)}
          </span>
          <select value={lang} onChange={(e) => setLang(e.target.value as Lang)} aria-label="Language / भाषा">
            {(Object.keys(languageNames) as Lang[]).map((l) => <option key={l} value={l}>{languageNames[l]}</option>)}
          </select>
        </div>
      </header>
      {isDraft(lang) && <div className="draft-banner" role="note">{t("translation_draft")} · {languageNames[lang]}</div>}
      <StatusStrip mode={source.mode} connected={state.connected} gateway={state.gateway} sirens={state.site.sirens}
        sirenState={state.sirens} exposure={state.site.site.exposure} t={t} />
      <main>
        <div className="map">
          <MapView nodes={nodes} sirens={state.site.sirens} telemetry={state.telemetry} risk={state.risk}
            sirenState={state.sirens} grid={grid} showHeatmap={showHeatmap} selected={selected} onSelect={setSelected} t={t} />
          <div className="map-tools">
            <label><input type="checkbox" checked={showHeatmap} onChange={(e) => setShowHeatmap(e.target.checked)} /> {t("layer.heatmap")}</label>
            <ul className="legend" aria-hidden>
              {(["normal", "watch", "warning", "critical"] as const).map((l) => (
                <li key={l}><span className={`swatch lvl-${l}`} />{t(`level.${l}`)}</li>
              ))}
            </ul>
          </div>
        </div>
        <aside ref={asideRef}>
          {selected ? (
            <NodePanel node={selected} telemetry={state.telemetry[selected]} risk={state.risk[selected]}
              history={state.history[selected] ?? []} onClose={() => setSelected(null)} t={t} />
          ) : (
            <p className="card muted hint">{t("select_node")}</p>
          )}
          <section className="card">
            <h2>{t("alerts")}</h2>
            <AlertList alerts={state.alerts} deliveries={state.deliveries} onAck={ack} onSelectNode={setSelected} t={t} />
          </section>
          <p className="muted footnote">{t("synthetic")}</p>
        </aside>
      </main>
    </div>
  );
}
