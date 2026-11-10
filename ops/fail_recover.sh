#!/usr/bin/env bash
# Controlled failure + automated detection + recovery (Module F)
# ./ops/fail_recover.sh db      -> stop MongoDB container
# ./ops/fail_recover.sh link    -> take router's API-side interface down
set -uo pipefail
cd "$(dirname "$0")/.."
MODE=${1:-db}
ts() { date '+%H:%M:%S.%3N'; }
healthy() { docker exec meter curl -sf --max-time 2 --cacert /certs/api.crt https://10.10.2.10:8443/health >/dev/null 2>&1; }
RIF=$(docker exec router ip -o -4 addr show | awk '/10\.10\.2\.254/{print $2}')

echo "$(ts) BEFORE: health check"; ./ops/healthcheck.sh | tail -1
echo "$(ts) starting client watch (1 request / 0.5s for 60s)"
docker exec meter python meter_sim.py watch --label "failure_$MODE" --duration 60 > "ops/results/watch_failure_${MODE}.log" 2>&1 &
WATCH=$!
sleep 10

echo "$(ts) >>> INDUCING FAILURE: $MODE"
if [ "$MODE" = db ]; then docker stop db >/dev/null
else docker exec router ip link set "$RIF" down; fi
T_FAIL=$(date +%s.%N)

# detection loop
until ! healthy; do sleep 0.5; done
T_DET=$(date +%s.%N)
echo "$(ts) >>> DETECTED: /health failing ($(awk "BEGIN{printf \"%.1f\", $T_DET - $T_FAIL}") s after failure)"
docker compose logs --tail 5 api 2>/dev/null | grep -E "ERROR|HEALTH" | tail -3

sleep 10   # leave it broken long enough to see in the watch log

echo "$(ts) >>> RECOVERING"
if [ "$MODE" = db ]; then docker start db >/dev/null
else docker exec router ip link set "$RIF" up; fi
until healthy; do sleep 0.5; done
T_REC=$(date +%s.%N)
echo "$(ts) >>> RECOVERED: service healthy again ($(awk "BEGIN{printf \"%.1f\", $T_REC - $T_FAIL}") s total outage)"

wait $WATCH
echo "$(ts) AFTER: health check"; ./ops/healthcheck.sh | tail -1
echo "$(ts) same client request again:"; docker exec meter python meter_sim.py send --meter meter-001 | head -2
echo; echo "client view (from watch log):"; grep -E "OUTAGE|RECOVERED|done" "ops/results/watch_failure_${MODE}.log"
