# Impact and technical KPIs

All numbers come from reproducible simulation runs (`SIM_SEED`) until field data exists.
W3 fills the "Measured" column from the KPI harness; nothing here is to be quoted before then.

| KPI | Target | Measured (v0.1) | How |
| --- | --- | --- | --- |
| Warning lead time before collapse | ≥ 6 h (pillar collapse), ≥ 7 days (trough) | _pending_ | First Warning vs. ground-truth event time |
| Event recall | ≥ 95% | _pending_ | Scenarios 2–3, 20 seeds each |
| False alarms per node per month | ≤ 0.1 | _pending_ | Scenarios 1 and 4 (noise, drift, faults) |
| Packet delivery, 30% nodes down | ≥ 95% | _pending_ | Mesh sim, random failures |
| Data loss during 6 h uplink cut | 0 readings | _pending_ | Gateway buffer test |
| Cost per node | ≤ ₹3,000 | ₹3,773 at retail single-unit prices incl. GST (19% of it still estimates); not yet met, bulk cell and module prices are the path | `hardware/costing.py` → `hardware/cost-summary.md` |
| Cost per hectare covered (25 nodes + gateway + 2 sirens) | — | ₹1,13,271 | same |
| Cost per person protected | — | ₹126 (900 people in site config) | same |
| Node battery autonomy with no sun, 60 s period | — | 8 days relay, 26 days leaf (calculated from datasheets, not measured) | `hardware/power_budget.py` → `hardware/power-budget.md` |
| People covered by one panel kit | 900 (config) | — | `exposure` in site config |
| Alert languages | 4 | 4 (Santali under review) | `services/alerts/src/templates` |

SDG alignment and how each KPI maps to an SDG target: [`sdg-mapping.md`](sdg-mapping.md).
