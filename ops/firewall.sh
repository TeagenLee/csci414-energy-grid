#!/usr/bin/env bash
# ./ops/firewall.sh on|off|show
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-show}" in
  on)   docker exec router nft -f /etc/grid/firewall.nft && echo "firewall ON" ;;
  off)  docker exec router nft flush ruleset && echo "firewall OFF (everything routed)" ;;
  show) docker exec router nft list ruleset ;;
  *) echo "usage: $0 on|off|show"; exit 1 ;;
esac
