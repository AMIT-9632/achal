# ADR 0002 (proposed): node status flags in telemetry v2

- Status: **proposed** (W5, 2026-10-21). Review after 27 Oct; no schema change before the freeze.
- Context: the reference firmware reports fault and status bits for every reading (accelerometer
  missing, ADC missing, low battery, enclosure tampered or knocked over, missed beacon so the time
  is estimated, woke on a vibration burst since the last report). `telemetry.v1` has
  `additionalProperties: false` and no field for them, so the gateway radio prints them on a
  `# flags` log line and they never reach the risk engine.
- Decision (proposed): add an optional `flags` object to `telemetry.v2` with named booleans
  (`tilt_sensor_fault`, `adc_fault`, `low_batt`, `tamper`, `no_sync`, `motion_wake`) rather than a
  raw bit mask, so the JSON stays self-describing. The gateway radio maps bits to names
  (`firmware/tools/achal_frame.py:FLAGS` already holds the mapping). field-sim emits the same
  flags for injected faults.
- Consequences: the risk engine can suppress alerts from nodes with a known sensor fault (fewer
  false alarms) and treat `motion_wake` as an extra vibration signal. Consumers of v1 keep
  working; v2 is published on the same topic with `"v": 2`, and services accept both during the
  transition.
