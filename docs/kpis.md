# Impact and technical KPIs

All numbers come from reproducible simulation runs (`SIM_SEED`) until field data exists.
W3 fills the "Measured" column from the KPI harness; nothing here is to be quoted before then.

| KPI | Target | Measured (v0.1) | How |
| --- | --- | --- | --- |
| Warning lead time before collapse | ≥ 6 h (pillar collapse), ≥ 7 days (trough) | _pending_ | First Warning vs. ground-truth event time |
| Event recall | ≥ 95% | _pending_ | Scenarios 2–3, 20 seeds each |
| False alarms per node per month | ≤ 0.1 | _pending_ | Scenarios 1 and 4 (noise, drift, faults) |
| Packet delivery, 30% nodes down | ≥ 95% | 100.000% mesh vs 98.85% single-hop (100-node colliery block, 10 seeds × 12 h); 100.000% vs 76.4% in the harsh-radio profile | `mesh_study.py` → [`mesh-study.md`](mesh-study.md), model in [`mesh.md`](mesh.md) |
| Data loss during uplink cut | 0 readings | 0 lost: 4,225 received / 4,225 stored (broker down 120 s at ×60 = 2 h field time); 52,104 / 52,104 from cold start with a ~1.5-day field-time outage at ×1200 | `scripts/demo_uplink_outage.sh`, [`gateway.md`](gateway.md) |
| Gateway siren with no cloud (fallback rules) | siren before every collapse, 0 false | 20/20 pillar collapses, median 8.4 h lead (min 4.3 h); 0 false sirens in 40 normal + faults runs | `rules_eval.py` → [`gateway-rules-eval.md`](gateway-rules-eval.md) |
| Cost per node | ≤ ₹3,000 | ₹3,773 at retail single-unit prices incl. GST (19% of it still estimates); not yet met, bulk cell and module prices are the path | `hardware/costing.py` → `hardware/cost-summary.md` |
| Cost per hectare covered (25 nodes + gateway + 2 sirens) | — | ₹1,13,271 | same |
| Cost per person protected | — | ₹126 (900 people in site config) | same |
| Node battery autonomy with no sun, 60 s period | — | 8 days relay, 26 days leaf (calculated from datasheets, not measured) | `hardware/power_budget.py` → `hardware/power-budget.md` |
| People covered by one panel kit | 900 (config) | — | `exposure` in site config |
| Alert languages | 4 | 4 (Santali under review) | `services/alerts/src/templates` |

SDG alignment and how each KPI maps to an SDG target: [`sdg-mapping.md`](sdg-mapping.md).
