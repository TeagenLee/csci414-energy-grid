#!/usr/bin/env bash
# ./ops/capture.sh <name> <seconds>  -> tcpdump on the router (sees all 3 segments)
set -euo pipefail
NAME=${1:-capture}; SECS=${2:-20}
docker exec router timeout "$SECS" tcpdump -Z root -i any -nn -s 0 -w "/captures/${NAME}.pcap" \
  'host 10.10.1.10 or host 10.10.2.10 or host 10.10.3.10' || true
echo "saved ops/captures/${NAME}.pcap"
docker exec router tcpdump -nn -r "/captures/${NAME}.pcap" 2>/dev/null | head -15
