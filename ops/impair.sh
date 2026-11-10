#!/usr/bin/env bash
# tc/netem on the meters' UPLINK (meter container eth0 egress) - Module E
# ./ops/impair.sh clear|delay|loss|throttle|throttle_delay|show
set -euo pipefail
IF=eth0
run() { echo "+ tc $*"; docker exec meter tc "$@"; }
case "${1:-show}" in
  clear)          docker exec meter tc qdisc del dev $IF root 2>/dev/null || true; echo "uplink clear" ;;
  delay)          run qdisc replace dev $IF root netem delay 100ms 20ms ;;
  loss)           run qdisc replace dev $IF root netem loss 5% ;;
  throttle)       run qdisc replace dev $IF root netem rate 256kbit ;;
  throttle_delay) run qdisc replace dev $IF root netem delay 50ms rate 256kbit loss 1% ;;
  show)           ;;
  *) echo "usage: $0 clear|delay|loss|throttle|throttle_delay|show"; exit 1 ;;
esac
docker exec meter tc -s qdisc show dev $IF
