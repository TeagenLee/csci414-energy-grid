#!/usr/bin/env bash
# Runs the IDENTICAL workload under each network condition (Module E).
set -euo pipefail
cd "$(dirname "$0")/.."
BENCH="python meter_sim.py bench --meters 5 --batches 20 --batch-size 50"
measure_net() {
  echo "--- RTT (20 pings meter -> api) ---"
  docker exec meter ping -c 20 -i 0.2 10.10.2.10 | tail -2
  echo "--- throughput (iperf3 10s meter -> api) ---"
  docker exec meter iperf3 -c 10.10.2.10 -t 10 | tail -4
}
for cond in clear delay loss throttle; do
  label=$([ "$cond" = clear ] && echo baseline || echo "$cond")
  echo; echo "################ CONDITION: $label  $(date '+%T') ################"
  ./ops/impair.sh "$cond"
  measure_net 2>&1 | tee "ops/results/net_${label}.txt"
  docker exec meter $BENCH --label "$label"
done
./ops/impair.sh clear
echo; echo "################ SUMMARY ################"
column -s, -t ops/results/summary.csv
