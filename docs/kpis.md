# Impact and technical KPIs

All numbers come from reproducible simulation runs (`SIM_SEED`) until field data exists.
Risk-engine rows quote `reports/kpi/kpi-report.json` (W1 simulator, 20 seeds per scenario,
2% packet loss, engine trained on separate normal seeds; regenerate with
`python -m achal_risk.kpi --seeds 20`, see [`risk-engine.md`](risk-engine.md)). All data is synthetic.

| KPI | Target | Measured (v0.1) | How |
| --- | --- | --- | --- |
| Warning lead time before collapse | ≥ 6 h (pillar collapse), ≥ 7 days (trough) | Pillar collapse: median 22.0 h (min 16.3 h). Trough: median 24.6 h (min 22.0 h); in the simulator, damage begins about 26 h after onset, so the warning comes 1.1 h after onset and the 7-day target cannot apply. Fire: median 60.2 h (min 53.8 h) | First Warning on a hazard node vs. `truth.failure_ts` |
| Event recall | ≥ 95% | 100% (60/60: trough, pillar collapse, fire, 20 seeds each) | Warning on a hazard node between onset and failure |
| False alarms per node per month | ≤ 0.1 | 0.0053 (2 in 380 node-months; 95% upper bound 0.017). False sirens: 0.07 per site per month. v0 fixed thresholds: 2.0 per node-month and 37 sirens per month | Warning episodes outside any hazard: normal (14 d), faults (7 d), and pre-onset time in every run |
| Time-to-failure error, last 12 h before collapse | — | median 0.15 h (pillar collapse), estimate available 100% of the time | Inverse-velocity (Fukuzono) fit vs. true time left |
| Sensor faults flagged | — | 100% (80/80: stuck, spike, drift, battery) | `sensor_fault_suspected` on the faulty node |
| Risk evaluation time, 25 nodes | — | 33 ms median per 10-minute tick (Python, laptop CPU) | `eval_ms_median` |
| Packet delivery, 30% nodes down | ≥ 95% | 100.000% mesh vs 98.85% single-hop (100-node colliery block, 10 seeds × 12 h); 100.000% vs 76.4% in the harsh-radio profile | `mesh_study.py` → [`mesh-study.md`](mesh-study.md), model in [`mesh.md`](mesh.md) |
| Data loss during uplink cut | 0 readings | 0 lost: 4,225 received / 4,225 stored (broker down 120 s at ×60 = 2 h field time); 52,104 / 52,104 from cold start with a ~1.5-day field-time outage at ×1200 | `scripts/demo_uplink_outage.sh`, [`gateway.md`](gateway.md) |
| Gateway siren with no cloud (fallback rules) | siren before every collapse, 0 false | 20/20 pillar collapses, median 8.4 h lead (min 4.3 h); 0 false sirens in 40 normal + faults runs | `rules_eval.py` → [`gateway-rules-eval.md`](gateway-rules-eval.md) |
| Cost per node | ≤ ₹3,000 | ₹4,240 at retail single-unit prices incl. GST (21% of it still estimates; includes the VL53L0X displacement gauge added 2026-10-27); not yet met, bulk cell and module prices are the path | `hardware/costing.py` → `hardware/cost-summary.md` |
| Cost per hectare covered (25 nodes + gateway + 2 sirens) | — | ₹1,24,923 | same |
| Cost per person protected | — | ₹139 (900 people in site config) | same |
| Node battery autonomy with no sun, 60 s period | — | 8 days relay, 26 days leaf (calculated from datasheets, not measured) | `hardware/power_budget.py` → `hardware/power-budget.md` |
| People covered by one panel kit | 900 (config) | — | `exposure` in site config |
| Alert languages | 4 | 4 (Santali under review) | `services/alerts/src/templates` |

SDG alignment and how each KPI maps to an SDG target: [`sdg-mapping.md`](sdg-mapping.md).

## What each engine layer buys (ablation, same 100 runs)

| Engine | False alarms / node-month | False sirens / month | Pillar-collapse lead (median) | Faults flagged |
| --- | --- | --- | --- | --- |
| v0 fixed thresholds (scaffold) | 2.02 | 37.3 | 3.5 h | 0% |
| Rate rules | 0.045 | 0.92 | 22.1 h | 0% |
| + Isolation Forest | 0.050 | 0.92 | 22.1 h | 0% |
| + neighbour consensus, fault handling | 0.013 | 0.26 | 22.1 h | 100% |
| + hysteresis, time-to-failure (shipped) | 0.0053 | 0.07 | 22.0 h | 100% |

Recall is 100% for every configuration. The layers trade nothing in recall: rate rules add the
lead time, while consensus and hysteresis remove false alarms. On its own the Isolation Forest
adds false Watches, because faults look anomalous; consensus then contains them.

