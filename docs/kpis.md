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
| Cost per node | ≤ ₹3,000 | _pending_ | `hardware/bom.csv` |
| Cost per hectare covered | _pending_ | _pending_ | BOM × nodes / area |
| People covered by one panel kit | 900 (config) | — | `exposure` in site config |
| Alert languages | 4 | 4 (Santali under review) | `services/alerts/src/templates` |

SDG alignment: 11.5 (disaster losses), 3.9 (deaths from hazards), 8.8 (safe work), 9.1 (resilient
infrastructure), 13.1 (resilience to hazards).
