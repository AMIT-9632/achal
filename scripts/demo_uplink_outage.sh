#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Uplink-outage demo: cut the MQTT broker for N seconds while the field keeps reporting,
# bring it back, and check that every reading the gateway received reached the database.
#
#   scripts/demo_uplink_outage.sh [outage_seconds=120]
#
# Needs the stack running (docker compose up -d). Uses the gateway's /status and the
# backend's /api/stats; prints a verdict and exits non-zero if anything was lost.
set -euo pipefail
OUTAGE=${1:-120}
GW=${GATEWAY_URL:-http://localhost:9100}
API=${API_URL:-http://localhost:8000}
SITE=${SITE_ID:-jharia-demo}
DC=${DC:-docker compose}

field() { curl -fsS "$1" | python3 -c "import json,sys; print(json.load(sys.stdin)$2)"; }
drain() {  # wait until the gateway outbox is empty and the backend has caught up
  for _ in $(seq 1 120); do
    b=$(field "$GW/status" '["buffered"]'); i=$(field "$GW/status" '["inflight"]')
    [ "$b" = 0 ] && [ "$i" = 0 ] && sleep 3 && return 0
    sleep 1
  done
  echo "gateway did not drain" >&2; return 1
}
snapshot() { echo "$(field "$GW/status" '["readings_unique"]') $(field "$API/api/stats?site=$SITE" '["telemetry_rows"]')"; }

echo "1/5 pausing the field and draining, to take a clean baseline"
$DC stop field-sim >/dev/null; drain
read -r U0 D0 <<<"$(snapshot)"
echo "    gateway readings so far: $U0, database rows: $D0"

echo "2/5 field running normally for 20 s"
$DC start field-sim >/dev/null; sleep 20

echo "3/5 UPLINK DOWN: stopping the broker for $OUTAGE s (gateway keeps receiving and buffering)"
$DC stop mosquitto >/dev/null
for t in $(seq "$OUTAGE" -10 1); do
  printf "    %3ss left, gateway online=%s buffered=%s\n" "$t" "$(field "$GW/status" '["online"]')" \
    "$(field "$GW/status" '["buffered"]')"
  sleep 10
done

echo "4/5 UPLINK BACK: starting the broker; the gateway replays its buffer"
$DC start mosquitto >/dev/null; sleep 20
$DC stop field-sim >/dev/null; drain
read -r U1 D1 <<<"$(snapshot)"
MAX=$(field "$GW/status" '["max_buffered"]')

RECEIVED=$((U1 - U0)); STORED=$((D1 - D0)); LOST=$((RECEIVED - STORED))
echo "5/5 result"
echo "    readings the gateway received during the test: $RECEIVED"
echo "    readings stored in TimescaleDB:                 $STORED"
echo "    peak gateway buffer during the outage:          $MAX"
echo "    buffered rows in the database (all time):       $(field "$API/api/stats?site=$SITE" '["buffered_rows"]')"
$DC start field-sim >/dev/null
if [ "$LOST" -eq 0 ]; then echo "    ZERO READINGS LOST"; else echo "    LOST $LOST READINGS"; exit 1; fi
