#!/usr/bin/env bash
# Configuration + service validation (Module F). Exit code 0 = all PASS.
cd "$(dirname "$0")/.."
fail=0
ok()  { printf "  [PASS] %s\n" "$1"; }
bad() { printf "  [FAIL] %s\n" "$1"; fail=1; }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }
checknot() { if eval "$2" >/dev/null 2>&1; then bad "$1"; else ok "$1"; fi; }

echo "=== healthcheck $(date '+%F %T') ==="
for c in router db api meter; do
  check "container $c running" "[ \"\$(docker inspect -f '{{.State.Running}}' $c)\" = true ]"
done
check "router ip_forward = 1"            "docker exec router sysctl -n net.ipv4.ip_forward | grep -qx 1"
check "firewall loaded on router"        "docker exec router nft list table inet grid"
check "meter -> router (10.10.1.254)"    "docker exec meter ping -c1 -W1 10.10.1.254"
check "meter -> api ping (routed)"       "docker exec meter ping -c1 -W1 10.10.2.10"
check "api -> db ping (routed)"          "docker exec api ping -c1 -W1 10.10.3.10"
check "api -> db tcp/27017"              "docker exec api nc -z -w2 10.10.3.10 27017"
check "meter -> API /health over TLS"    "docker exec meter curl -sf --max-time 3 --cacert /certs/api.crt https://10.10.2.10:8443/health"
checknot "meter -> db tcp/27017 BLOCKED" "docker exec meter nc -z -w2 10.10.3.10 27017"
checknot "meter -> api plain HTTP refused" "docker exec meter curl -sf --max-time 3 http://10.10.2.10:8443/health"
echo "=== result: $([ $fail -eq 0 ] && echo ALL PASS || echo SOME CHECKS FAILED) ==="
exit $fail
