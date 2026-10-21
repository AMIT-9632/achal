# Hardware reference

| File | What it holds |
| --- | --- |
| `bom.csv` | Bill of materials for a node, the gateway and a siren post. Every quoted line has the supplier link and the date it was checked; lines without a quote are marked `estimate` |
| `costing.py` → `cost-summary.md` | Per-kit and per-site cost, cost per hectare and per person, comparison with other systems |
| `power_budget.py` → `power-budget.md` | Energy per period from datasheet currents and the firmware's timing, battery autonomy, solar margin |
| `wiring.md` | Node pin map and power path |

Regenerate after editing the BOM or the firmware timing:

```bash
pip install -e "libs/achal-common[dev]"
python hardware/costing.py && python hardware/power_budget.py
```

Prices are retail single units (2026-10-21) including 18% GST. The power budget is calculated,
not measured; the first prototype's bench numbers replace it.
