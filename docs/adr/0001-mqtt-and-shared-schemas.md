# ADR 0001: MQTT with versioned JSON schemas between all services

- Status: accepted (2026-10-21)
- Context: five workstreams build in parallel; hardware will later replace the simulator.
- Decision: all services exchange JSON over MQTT using `schemas/*.v1.json`. The field-to-gateway
  link carries the same telemetry JSON (over UDP in simulation, LoRa later, compacted to CBOR).
- Consequences: services can be developed and tested alone against recorded samples. A breaking
  change needs a new schema version and a new ADR.
