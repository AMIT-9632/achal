# AI risk engine

`services/risk-engine` turns raw node readings into a 0-1 risk score, a level (normal, watch,
warning, critical), reason codes a resident or mine official can understand, a time-to-failure
estimate, a risk map, and an alert when the site level changes. It runs on the gateway-class
hardware the rest of ACHAL uses: one evaluation of all 25 nodes takes tens of milliseconds in
Python and NumPy (measured in `reports/kpi/kpi-report.json`, `eval_ms_median`).

Code: `services/risk-engine/src/achal_risk/` (pure engine, no MQTT and no wall clock) and
`services/risk-engine/src/risk_engine.py` (the MQTT service). Because the engine is pure, the
live service and the KPI harness run the same code: the KPIs describe what runs on site.

## Pipeline

Every `eval_period_s` (default 10 minutes of sensor time) the engine scores all nodes at once
from the last 24 hours of readings, held in a ring buffer slotted by timestamp so packets
replayed late from the gateway buffer land where they belong.

```
readings ─► features ─► rules ───────────────┐
                    ├─► Isolation Forest ─────┼─► evidence = max(rules, 0.8·iforest, trend)
                    └─► time-to-failure ─► trend ┘          │
                                                  neighbour consensus + fault handling
                                                            │
                                                  hysteresis ─► node level ─► site level ─► alert
```

### 1. Features (`features.py`)

| Feature | How |
| --- | --- |
| tilt rate (6 h, 24 h), crack and extensometer rates (6 h) | robust least-squares slope, after thermal compensation |
| tilt level, crack opening, extensometer change | median of the last 30 min minus the install baseline |
| vibration ratio | 90th percentile of the last hour / the node's baseline RMS |
| vibration bursts | readings in the last hour above 4x baseline |
| temperature excess | node temperature minus the site median (fire-heated ground) |
| noise ratio | current residual noise / baseline noise (sensor health) |

**Thermal compensation.** Low-cost MEMS inclinometers, crack gauges and extensometer wires all
respond to temperature, and the daily cycle moves every node at once, which neighbour consensus
cannot reject. For each node and channel the engine fits
`y = a + b·t + c·t² + g·(T - T̄)` over 24 h (a full diurnal cycle, so g is not confused with the
trend; the quadratic term absorbs slow movement) and subtracts `g·T` before computing rates.
Coefficients are learned at installation from a quiet day (`commission`) and refreshed hourly,
only on nodes that are quiet. Until they are known, rate-driven evidence is held below Warning.

**Robust slopes.** Points more than 6 robust sigmas (1.4826 x MAD, Hampel 1974) from the first
fit are dropped and the line refitted, so a single spike cannot fake a rate.

### 2. Rules (`rules.py`)

Each feature maps onto the score through its (watch, warning, critical) thresholds, which land
on the level boundaries 0.35 / 0.60 / 0.85. Rates lead: the engine flags a node tilting at
0.02 deg/day long before its cumulative tilt reaches a damage limit.

| Feature | Watch | Warning | Critical | Reason code |
| --- | --- | --- | --- | --- |
| tilt rate | 0.02 deg/day | 0.08 | 0.30 | `tilt_rate_high` |
| tilt level | 0.06 deg | 0.115 (1/500) | 0.19 (1/300) | `tilt_rate_high` |
| crack opening rate / level | 0.3 mm/day / 1 mm | 1.5 / 3 | 5 / 10 | `crack_opening` |
| extensometer rate / level (22.5 m) | 1 mm/day / 3 mm | 4 / 10 | 12 / 22 (≈1 mm/m) | `ext_stretch` |
| vibration ratio | 3x | 6x | 15x | `vibration_burst` |
| temperature excess | 8 degC | 20 | 40 | `ground_heating` |

Tilt levels follow the angular-distortion damage limits (1/500 first cracking, 1/300 structural
damage; Bjerrum 1963). The other values are starting assumptions. `calibrate_floor` then raises
each rate's watch threshold to 1.5x the 99.9th percentile seen on normal-scenario data, so sensor
noise alone can never reach Watch; the calibrated values used for the KPIs are in the report.

Vibration alone (opencast blasting, traffic) can reach Watch but never Warning. A sensor flagged
faulty cannot raise deformation alarms.

### 3. Isolation Forest (`anomaly.py`)

An Isolation Forest (Liu, Ting and Zhou 2008; scikit-learn, 150 trees) over the 11-feature
vector, trained only on normal-scenario seeds that are never used for evaluation. Its score is
rescaled so the 99.9th percentile of training data sits at the Watch boundary: it catches
combinations no single rule covers (for example a noisy sensor that is also drifting), and is
weighted 0.8 in the evidence so it cannot outvote the physics on its own.

### 4. Time to failure (`ttf.py`)

Before a brittle collapse, displacement velocity grows as `v ∝ 1/(t_f - t)`, so `1/v` falls on
a straight line to zero at the failure time (Fukuzono 1985; Voight 1988). Differentiating noisy
data is fragile, so the engine fits the integrated law `d(t) = a - A·ln(t_f - t)` directly to a
thermally compensated displacement proxy (tilt projected on its direction of motion, crack
opening, extensometer change; 10-minute means over 24 h), scanning candidate `t_f`. Guards that
keep it honest:

- fit only the current phase of motion (after the best broken-line break), and need 4 h of it;
- the log law must explain at least half of what a constant velocity leaves unexplained;
- motion well above noise, and no extrapolation beyond twice the observed span;
- only nodes that have been moving (rules above 0.3) for 6 h get an estimate;
- the predicted failure time must agree with an estimate made 1-3 h earlier, and with a
  neighbour (median over the node and its neighbours, at least two estimates).

The trend score is 0.95 at 2 h, 0.85 at 6 h, 0.6 at 24 h, 0.35 at 72 h, and the reason code is
`ttf_short` when the time to failure is under a day.

### 5. Neighbour consensus and faults (`engine.py`)

Real ground movement is shared: a failing pillar at 60 m depth is felt over a radius of about
24 m, more than one grid spacing (22.5 m). A node's support is `c = min(1, (agreeing + s) / 2)`
where `agreeing` counts healthy neighbours (8-neighbourhood) whose evidence is at least 0.3 and
`s = 0.5` if two independent sensor groups (tilt, strain, vibration with deformation, heat) agree
on the node itself. The fused score is `evidence x (0.45 + 0.55·c)`: a lone node tops out at
Watch, one neighbour plus self-corroboration or two neighbours reach Warning and Critical.

Faults: a deformation channel exactly flat for 2 h (stuck), three or more isolated spikes in 6 h,
or battery below 3.3 V marks the node `sensor_fault_suspected`; its deformation rules and its
time-to-failure are muted and it does not count as an agreeing neighbour. A lone single-group
signal with no agreeing neighbour is also marked suspect. A node with no data for 5 periods is
`node_silent`; next to a Warning it is held at Warning (it may have been destroyed).

### 6. Hysteresis and alerts

Levels go up after 2 consecutive ticks above a boundary (20 min) and down one level at a time
after 18 ticks (3 h) below the boundary minus 0.05, so they do not flap. The site level is the
highest node level; each change publishes an `Alert` with the nodes at that level, the union of
their reason codes, the median time-to-failure, the centroid, and `siren: true` at Warning and
above (the service also sends `siren/{id}/cmd`).

### 7. Risk grid (`grid.py`)

Inverse-distance weighting (power 2; Shepard 1968) of fused node scores onto a 5 m raster from
(-40, -40) to (140, 140) m, published retained on `achal/{site}/risk/grid`
(`schemas/risk-grid.v1.json`). Kriging can replace it without changing the contract.

## KPI harness (`kpi.py`)

```bash
cd services/risk-engine/src
python -m achal_risk.kpi --seeds 20 --out ../../../reports/kpi          # W1 simulator, ~40 min on 4 cores
python -m achal_risk.kpi --seeds 2 --scenarios pillar_collapse,faults   # quick check
```

It trains on normal seeds 1001-1004, then replays 5 scenarios x N seeds (each seed is a different
event: location, timing, size) with 2% random packet loss, scoring each run under five engine
configurations so the report shows what every layer adds:

| Config | Layers |
| --- | --- |
| `v0_stub` | the scaffold's fixed thresholds on the latest reading |
| `rules` | rate and level rules only |
| `rules_iforest` | + Isolation Forest |
| `plus_consensus` | + neighbour consensus and fault handling |
| `full` | + hysteresis and time-to-failure (what ships) |

Each run starts with a commissioning day (thermal calibration from a quiet day of the same
sensors). Results go to `reports/kpi/kpi-report.json` (summary + metadata) and
`reports/kpi/kpi-runs.csv` (every run x config); `docs/kpis.md` quotes them.
`--source surrogate` uses a small built-in generator (`surrogate.py`) for development only.

## Running the service

`docker compose up` starts it with the field simulator mounted, so at start-up it trains the
Isolation Forest on two normal seeds (about a minute) and commissions thermal coefficients for
`SIM_SEED`. Environment: `RISK_EVAL_PERIOD_S` (600), `RISK_TRAIN` (1), `RISK_TRAIN_SEEDS`
(1001,1002), `RISK_TRAIN_HOURS` (48), `RISK_COMMISSION_SEED` (defaults to `SIM_SEED`).

## Limitations and next steps

- All tuning and all KPIs are on synthetic data from our own simulator. Thresholds must be
  recalibrated on field data; the harness makes that a one-command job.
- Thresholds and the consensus rule assume the 22.5 m grid; a sparser grid needs the consensus
  radius and the lone-node cap revisited.
- The time-to-failure estimate is only as good as the inverse-velocity law for the failure mode;
  trough damage is a growth process, not a brittle failure, so it rarely gets an estimate.
- Stretch goal after submission: an LSTM forecaster per node, compared in the same harness.
