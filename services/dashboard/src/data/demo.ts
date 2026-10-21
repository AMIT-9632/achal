// SPDX-License-Identifier: MIT
// In-browser demo: a small, self-contained imitation of the ACHAL pipeline so
// the dashboard can be shown with no backend (offline pitch, static hosting).
// All values are SYNTHETIC and only illustrate the UI. The real physics lives in
// services/field-sim and the real scoring in services/risk-engine.
//
// Story (one loop = 200 ticks, 1 tick = 1 s real = 10 min simulated):
//   0-29     quiet ground
//   30-139   accelerating creep over a failing pillar near the panel centre
//   70-84    gateway uplink lost, readings buffered, local siren still works
//   110-139  vibration bursts, then collapse at tick 140
//   170      ground stabilises, all clear
import { LEVELS, type Alert, type Level, type Risk, type SiteResponse, type Telemetry } from "../types";
import type { Action } from "./store";

const SITE_ID = "jharia-demo";
const ORIGIN = { lat: 23.748, lon: 86.414 };
const ROWS = 5, COLS = 5, SPACING = 22.5, PANEL = 100;
const TICK_MS = 1000, SIM_MIN_PER_TICK = 10, LOOP = 200;
const FAIL_TICK = 140, CREEP_START = 30, OUTAGE = [70, 85], CLEAR_TICK = 170;
const EPICENTRE = { x: 55, y: 58 };

function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const R = 6_371_000;
const toLatLon = (x: number, y: number) => ({
  lat: ORIGIN.lat + ((y / R) * 180) / Math.PI,
  lon: ORIGIN.lon + ((x / (R * Math.cos((ORIGIN.lat * Math.PI) / 180))) * 180) / Math.PI,
});

export function demoSite(): SiteResponse {
  const off = (PANEL - (COLS - 1) * SPACING) / 2;
  const nodes = Array.from({ length: ROWS * COLS }, (_, i) => {
    const [row, col] = [Math.floor(i / COLS), i % COLS];
    const x = off + col * SPACING, y = off + row * SPACING;
    return { id: `n${String(i).padStart(2, "0")}`, x_m: x, y_m: y, ...toLatLon(x, y) };
  });
  const sirens = [
    { id: "siren-01", label: "Village edge (north)", ...toLatLon(-30, 10) },
    { id: "siren-02", label: "Road crossing (east)", ...toLatLon(130, 90) },
  ];
  return {
    site: { site_id: SITE_ID, name: "Jharia-type bord-and-pillar panel (synthetic)", district: "Dhanbad",
      state: "Jharkhand", languages: ["en", "hi", "sat", "bn"], exposure: { households: 180, people: 900 } },
    nodes, sirens,
  };
}

/** Creep displacement (mm) at the epicentre: inverse-velocity shape, v ~ 1/(tf - t). */
function creep(tick: number): number {
  if (tick < CREEP_START) return 0;
  const t = Math.min(tick, FAIL_TICK - 0.5);
  const base = -40 * Math.log((FAIL_TICK - t) / (FAIL_TICK - CREEP_START));
  return tick >= FAIL_TICK && tick < CLEAR_TICK + 30 ? base + 120 : base;
}

function levelFor(s: number): Level {
  return s >= 0.85 ? "critical" : s >= 0.6 ? "warning" : s >= 0.35 ? "watch" : "normal";
}

export function startDemo(dispatch: (a: Action) => void) {
  const site = demoSite();
  const rnd = mulberry32(42);
  const start = Date.now() - 2 * 3600_000;
  const levels: Record<string, Level> = {};
  const crack: Record<string, number> = {};
  let siteLevel: Level = "normal";
  let tick = 0, seq = 0;
  const outbox: Telemetry[] = [];

  dispatch({ type: "site", site });
  dispatch({ type: "connected", connected: true });
  dispatch({ type: "snapshot", snap: { sirens: {} }, alerts: [] });

  const step = () => {
    const loopTick = tick % LOOP;
    if (loopTick === 0) Object.keys(crack).forEach((k) => (crack[k] = 0));
    const ts = new Date(start + tick * SIM_MIN_PER_TICK * 60_000).toISOString();
    const d = creep(loopTick), dPrev = creep(loopTick - 1);
    const offline = loopTick >= OUTAGE[0] && loopTick < OUTAGE[1];
    const risks: Risk[] = [];

    for (const n of site.nodes) {
      const dist = Math.hypot(n.x_m - EPICENTRE.x, n.y_m - EPICENTRE.y);
      const w = Math.exp(-(dist ** 2) / (2 * 16 ** 2));
      const g = () => (rnd() - 0.5) * 2;
      const tiltMag = 0.003 * d * w * (dist / 16) + 0.002 * g();
      const ang = Math.atan2(n.y_m - EPICENTRE.y, n.x_m - EPICENTRE.x);
      crack[n.id] = (crack[n.id] ?? 0) + Math.max(0, (d - dPrev) * w * 0.08) + 0.002 * rnd();
      const burst = loopTick >= 110 && loopTick < FAIL_TICK + 5 && rnd() < 0.25 + 0.5 * w;
      const vib = 0.002 + 0.001 * rnd() + (burst ? (0.02 + 0.06 * rnd()) * w : 0);
      const tel: Telemetry = {
        node: n.id, ts, seq: seq++, tilt_x_deg: tiltMag * Math.cos(ang), tilt_y_deg: tiltMag * Math.sin(ang),
        vib_rms_g: vib, vib_peak_g: vib * 3, crack_mm: crack[n.id], ext_mm: d * w * 0.05,
        temp_c: 28 + 3 * Math.sin((tick / 144) * 2 * Math.PI), batt_v: 4.1 - (tick % 2000) * 0.0001,
        hops: Math.max(1, Math.ceil((n.x_m + 20) / 30)), rssi_dbm: -70 - n.x_m * 0.25 + 4 * g(),
      };
      if (offline) outbox.push({ ...tel, buffered: true });
      else dispatch({ type: "event", event: { kind: "telemetry", data: tel } });

      // Stand-in scoring that only mimics the risk engine's output shape.
      const tiltRate = Math.abs(0.003 * (d - dPrev) * w * (dist / 16)) * 6; // deg per hour
      const rules = Math.min(1, Math.max(tiltMag / 0.25, crack[n.id] / 6, vib / 0.06, tiltRate / 0.02));
      const trend = loopTick > CREEP_START && loopTick < FAIL_TICK ? Math.min(1, w * (d / 60)) : 0;
      const iforest = Math.min(1, rules * 0.8 + 0.05 * rnd());
      const s0 = 0.45 * rules + 0.3 * iforest + 0.25 * trend;
      risks.push({ node: n.id, ts, score: s0, level: "normal", components: { rules, iforest, trend } });
    }

    // Consensus: a node's score counts in full only if >= 2 neighbours agree.
    for (const r of risks) {
      const me = site.nodes.find((n) => n.id === r.node)!;
      const agreeing = risks.filter((o) => {
        const n = site.nodes.find((q) => q.id === o.node)!;
        return o !== r && Math.hypot(n.x_m - me.x_m, n.y_m - me.y_m) < SPACING * 1.5 && o.score > 0.3;
      }).length;
      const consensus = Math.min(1, agreeing / 3);
      r.components = { ...r.components, consensus };
      r.agreeing_neighbours = agreeing;
      const score = Math.max(0, Math.min(1, agreeing >= 2 ? r.score + 0.1 * consensus : r.score * 0.6));
      r.score = Math.round(score * 1000) / 1000;
      r.level = levelFor(score);
      const c = r.components;
      r.reasons = [
        ...(c.rules > 0.5 && crack[r.node] > 2 ? ["crack_opening"] : []),
        ...(c.rules > 0.4 ? ["tilt_rate_high"] : []),
        ...(c.iforest > 0.5 ? ["iforest_anomaly"] : []),
        ...(agreeing >= 2 && score > 0.35 ? ["neighbour_consensus"] : []),
      ];
      const hoursLeft = ((FAIL_TICK - loopTick) * SIM_MIN_PER_TICK) / 60;
      if (loopTick > 60 && loopTick < FAIL_TICK && score > 0.35) {
        r.ttf_hours = Math.round(hoursLeft * (0.9 + 0.2 * rnd()) * 10) / 10;
        if (hoursLeft < 6) r.reasons.push("ttf_short");
      }
      if (loopTick >= 110 && loopTick < FAIL_TICK + 5 && score > 0.5) r.reasons.push("vibration_burst");
      levels[r.node] = r.level;
      dispatch({ type: "event", event: { kind: "risk", data: r } });
    }

    if (loopTick === OUTAGE[1]) {
      for (const t of outbox.splice(0)) dispatch({ type: "event", event: { kind: "telemetry", data: t } });
    }
    dispatch({ type: "event", event: { kind: "gateway",
      data: { online: !offline, buffered: offline ? outbox.length : 0, ts } } });

    let newLevel = Object.values(levels).reduce<Level>(
      (a, l) => (LEVELS.indexOf(l) > LEVELS.indexOf(a) ? l : a), "normal");
    if (loopTick >= CLEAR_TICK) newLevel = "normal";
    if (newLevel !== siteLevel) {
      const hot = risks.filter((r) => r.level === newLevel).map((r) => r.node);
      const top = risks.filter((r) => r.level === newLevel).sort((a, b) => b.score - a.score)[0];
      const alert: Alert = {
        id: `demo-${tick}`, site: SITE_ID, ts, level: newLevel, previous_level: siteLevel,
        nodes: newLevel === "normal" ? [] : hot, reasons: top?.reasons?.length ? top.reasons : ["level_change"],
        ttf_hours: top?.ttf_hours ?? null, siren: LEVELS.indexOf(newLevel) >= 2,
      };
      dispatch({ type: "event", event: { kind: "alert", data: alert } });
      const sirenOn = LEVELS.indexOf(newLevel) >= 2;
      for (const s of site.sirens) {
        dispatch({ type: "event", event: { kind: "siren",
          data: { id: s.id, on: sirenOn, level: newLevel, ts, source: offline ? "gateway-local" : "risk-engine" } } });
      }
      const recipients = [
        ["Sarpanch, Bastacola", "hi", "sms"], ["Ward member", "sat", "sms"], ["Shift overman", "bn", "sms"],
        ["Mine safety officer", "en", "email"],
      ];
      for (const [recipient, lang, channel] of recipients) {
        dispatch({ type: "event", event: { kind: "delivery",
          data: { alert_id: alert.id, recipient, lang, channel, status: "sent (demo)", ts, tier: 0 } } });
      }
      siteLevel = newLevel;
    }
    tick++;
  };

  for (let i = 0; i < 12; i++) step(); // some history on first paint
  const timer = setInterval(step, TICK_MS);
  return {
    stop: () => clearInterval(timer),
    ack: (id: string, by: string) =>
      dispatch({ type: "event", event: { kind: "alert_ack", data: { id, by, ts: new Date().toISOString() } } }),
  };
}
