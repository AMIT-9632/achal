# ACHAL dashboard

Operator and panchayat view of one site: live node map, risk heatmap, node
detail with charts and a "why this alert" explanation, siren and gateway
health, alert acknowledgement. English, Hindi, Bengali and Santali (draft).
Works on a phone.

## Run

```bash
npm ci
npm run dev                 # http://localhost:5173, proxies /api and /ws to API_URL (default :8000)
npm run dev -- --open "/?demo=1"   # no backend needed: in-browser demo simulation
npm test                    # unit tests (i18n coverage, reducer, IDW heatmap)
npm run build               # production build, served by nginx in docker compose (:8080)
npm run build:demo          # static build that always runs the demo (for GitHub Pages / offline pitch)
```

URL options: `?demo=1` demo mode, `?lang=hi|bn|sat|en` language (also remembered per browser).

## What it shows

| Area | Source |
| --- | --- |
| Node colours, tooltips | `risk` and `telemetry` WebSocket events, snapshot from `GET /api/nodes` |
| Risk heatmap | `grid` events (`achal/{site}/risk/grid`, W3). If none arrived in the last 5 min the dashboard computes its own inverse-distance grid from node scores, faded out away from sensors |
| Node charts | `GET /api/nodes/{id}/history?minutes=1440` merged with live readings (rolling 360 points) |
| Why this alert | `RiskAssessment.components`, `reasons`, `agreeing_neighbours`, `ttf_hours` |
| Gateway / sirens | `gateway` and `siren` events; a siren fired by the gateway with no uplink is labelled as local |
| Alerts | `alert` events; acknowledge posts `POST /api/alerts/{id}/ack` (asks for an operator token on 401) |
| Messages sent | `delivery` events from the alerts service (no phone numbers) |

## Design notes

- No react-leaflet (Hippocratic-2.1 licence, not OSI). Leaflet is driven directly in `components/MapView.tsx`.
- No chart library: `components/LineChart.tsx` is a small SVG chart.
- Every data source feeds the same reducer (`data/store.ts`), so the demo and the live system render identically.
- Missing Santali strings fall back to Hindi, then English, and a banner marks the translation as a draft.
- All data shown is synthetic (simulator or in-browser demo) and labelled as such.
