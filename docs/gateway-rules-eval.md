# Gateway local siren rules on the simulator scenarios (synthetic)

`python services/gateway/src/rules_eval.py --seeds 20 --period-s 300` on 2026-10-21, site jharia-demo, default thresholds (see [gateway.md](gateway.md)). Lead time is failure time minus first siren. This is the no-cloud fallback layer; the AI risk engine is scored separately in kpis.md.

| Scenario | Runs | Runs with a siren | Siren before failure | Median lead (h) | Shortest lead (h) | False sirens |
| --- | --- | --- | --- | --- | --- | --- |
| normal | 20 | 0 | — | — | — | 0 |
| trough | 20 | 20 | 20 | 23.72 | 21.04 | — |
| pillar_collapse | 20 | 20 | 20 | 8.35 | 4.34 | — |
| faults | 20 | 0 | — | — | — | 0 |
| fire | 20 | 20 | 20 | 57.82 | 48.09 | — |

Per-run numbers: `data/gateway-rules-eval.json`.
