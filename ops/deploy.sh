#!/usr/bin/env bash
# One-command deploy (Module F - automation). Usage: ./ops/deploy.sh [--with-firewall]
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p ops/logs ops/results ops/captures data certs

echo "[1/6] dataset"
if [ ! -f data/household_power_consumption.txt ]; then
  curl -L --fail -o data/uci.zip \
    "https://archive.ics.uci.edu/static/public/235/individual+household+electric+power+consumption.zip"
  unzip -o data/uci.zip -d data/
fi
ls -lh data/household_power_consumption.txt

echo "[2/6] TLS certificate for the API (self-signed, SAN = 10.10.2.10)"
if [ ! -f certs/api.crt ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout certs/api.key -out certs/api.crt \
    -subj "/CN=grid-api" -addext "subjectAltName=IP:10.10.2.10,DNS:api"
fi
chmod 644 certs/api.key certs/api.crt

echo "[3/6] build + start containers"
docker compose up -d --build

echo "[4/6] waiting for API to answer over TLS..."
for i in $(seq 1 40); do
  if docker exec meter curl -s --max-time 2 --cacert /certs/api.crt https://10.10.2.10:8443/health | grep -q '"ok"'; then
    echo "  API healthy"; break
  fi
  sleep 2
  [ "$i" -eq 40 ] && { echo "  API never became healthy"; docker compose logs --tail 30 api; exit 1; }
done

echo "[5/6] import UCI dataset into MongoDB (runs inside api container, segment 2 -> 3)"
docker exec api python import_uci.py /data/household_power_consumption.txt 20000

if [ "${1:-}" = "--with-firewall" ]; then
  echo "[6/6] applying firewall"
  ./ops/firewall.sh on
else
  echo "[6/6] firewall NOT applied (run ./ops/firewall.sh on)"
fi
echo "deploy finished: $(date '+%F %T')"
