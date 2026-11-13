#!/usr/bin/env bash
# Controlled failure + automated detection + recovery (Module F)
# ./ops/fail_recover.sh db      -> stop MongoDB container
# ./ops/fail_recover.sh link    -> take router's API-side interface down
set -uo pipefail
cd "$(dirname "$0")/.."
MODE=${1:-db}
CMD_TIMEOUT_S="${CMD_TIMEOUT_S:-8}"   # watchdog cap for a single docker/curl command
DETECT_CAP_S="${DETECT_CAP_S:-60}"    # bounded wait for /health to start failing
RECOVER_CAP_S="${RECOVER_CAP_S:-60}"  # bounded wait for /health to come back

now() { date +%s.%N; }
# ts(): BSD date (macOS) has no %N, so '+%H:%M:%S.%3N' prints the literal ".3N"
# instead of milliseconds. Derive ms from now()'s epoch seconds instead, which
# works on both BSD and GNU date. Validated in ops/diag_fail_db.sh.
ts() {
  local n hhmmss frac
  n=$(now)
  hhmmss=$(date -r "${n%.*}" '+%H:%M:%S' 2>/dev/null || date -d "@${n%.*}" '+%H:%M:%S' 2>/dev/null)
  frac="${n#*.}"
  printf '%s.%s' "$hhmmss" "${frac:0:3}"
}
# duration(): $1=start epoch $2=end epoch. Uses awk -v, never an inline
# `$(awk "BEGIN{...}")` built from interpolated shell text -- that pattern broke
# under nested command substitution (doubled output, awk syntax errors) during
# the diag_fail_db.sh investigation. -v passes values as data, not program text.
duration() { awk -v a="$2" -v b="$1" 'BEGIN{printf "%.3f", a - b}'; }

# run_with_timeout: portable watchdog (no dependency on GNU `timeout`, absent on
# this host). Backgrounds the command in its own process group and kills that
# group if it outlives $CMD_TIMEOUT_S, so a single hung docker/curl call cannot
# block the script indefinitely -- distinct from the bounded polling loops below,
# which bound the number of iterations, not an individual command's own hang.
# NOTE: this can only kill the local CLI process; it cannot cancel work already
# accepted by the Docker daemon.
run_with_timeout() {
  local cap="$1"; shift
  local had_monitor; case "$-" in *m*) had_monitor=1 ;; *) had_monitor=0 ;; esac
  set -m
  "$@" &
  local cpid=$!
  [ "$had_monitor" -eq 0 ] && set +m
  local waited=0
  while kill -0 "$cpid" 2>/dev/null; do
    sleep 0.1
    waited=$(awk -v w="$waited" 'BEGIN{printf "%.1f", w + 0.1}')
    if awk -v w="$waited" -v c="$cap" 'BEGIN{exit !(w >= c)}'; then
      echo "$(ts) [fail_recover] WATCHDOG: command exceeded ${cap}s, killing: $*" >&2
      kill -TERM -- "-$cpid" 2>/dev/null; kill -TERM "$cpid" 2>/dev/null
      sleep 0.5
      kill -KILL -- "-$cpid" 2>/dev/null; kill -KILL "$cpid" 2>/dev/null
      wait "$cpid" 2>/dev/null
      return 124
    fi
  done
  wait "$cpid"
}

# healthy(): --max-time raised from 2s to 5s. The API's Mongo client uses
# serverSelectionTimeoutMS=2000, so /health itself can take ~2000-2500ms to
# return a 503 while the DB is down (confirmed in ops/logs/api.log during the
# original investigation). A 2s curl timeout raced that server-side timing and
# produced flaky, silent iterations. 5s gives clear headroom. The docker-exec
# call itself is now watchdog-wrapped so a hung CLI call can't stall this
# function past CMD_TIMEOUT_S either.
healthy() {
  run_with_timeout "$CMD_TIMEOUT_S" \
    docker exec meter curl -sf --max-time 5 --cacert /certs/api.crt https://10.10.2.10:8443/health >/dev/null 2>&1
}

# bounded, logged wait: replaces the old unbounded `until ! healthy; do sleep 0.5; done`.
# Logs a progress line every iteration (so a stall is visible instead of silent)
# and returns 1 instead of looping forever if the cap is exceeded.
# $1 = "fail" (wait for healthy to start failing) or "ok" (wait for it to recover)
wait_for_state() {
  local target="$1" cap="$2" t0 dt n=0
  t0=$(now)
  while :; do
    n=$((n+1))
    if healthy; then ok=1; else ok=0; fi
    dt=$(duration "$t0" "$(now)")
    echo "$(ts) [fail_recover] poll #$n healthy=$ok dt=${dt}s"
    if [ "$target" = fail ] && [ "$ok" -eq 0 ]; then return 0; fi
    if [ "$target" = ok ]   && [ "$ok" -eq 1 ]; then return 0; fi
    if awk -v d="$dt" -v c="$cap" 'BEGIN{exit !(d > c)}'; then
      echo "$(ts) [fail_recover] wait_for_state($target): CAP EXCEEDED after ${dt}s (cap=${cap}s)"
      return 1
    fi
    sleep 0.5
  done
}

RIF=$(docker exec router ip -o -4 addr show | awk '/10\.10\.2\.254/{print $2}')

# ---------- cleanup: restores whichever mode was disrupted, on any exit path ----------
# Armed (FAILURE_INJECTED=1) immediately before the disruptive call, not after,
# so a signal arriving mid-call still triggers restoration. IN_CLEANUP/CLEANUP_DONE
# guard against re-entrant or duplicate cleanup (concurrent second signal, or EXIT
# firing after INT already ran it). SIGNAL_RECEIVED forces a nonzero final exit
# even when restoration itself succeeds -- an interrupted experiment is not a
# clean run regardless of recovery outcome.
FAILURE_INJECTED=0
CLEANUP_DONE=0
IN_CLEANUP=0
SIGNAL_RECEIVED=0
EXIT_STATUS=0

cleanup() {
  if [ "$IN_CLEANUP" -eq 1 ]; then return; fi
  IN_CLEANUP=1
  trap '' INT TERM
  local sig="${1:-EXIT}"
  [ "$sig" = INT ] || [ "$sig" = TERM ] && SIGNAL_RECEIVED=1
  if [ "$CLEANUP_DONE" -eq 1 ]; then
    [ "$SIGNAL_RECEIVED" -eq 1 ] && [ "$EXIT_STATUS" -eq 0 ] && EXIT_STATUS=6
    exit "$EXIT_STATUS"
  fi
  CLEANUP_DONE=1
  echo "$(ts) [fail_recover] cleanup (reason=$sig, failure_injected=$FAILURE_INJECTED)"

  if [ "$FAILURE_INJECTED" -eq 1 ]; then
    if [ "$MODE" = db ]; then
      local state
      state=$(docker inspect -f '{{.State.Status}}' db 2>/dev/null || echo unknown)
      if [ "$state" != "running" ]; then
        echo "$(ts) [fail_recover] restoring: docker start db"
        run_with_timeout "$CMD_TIMEOUT_S" docker start db >/dev/null 2>&1 \
          || { echo "$(ts) [fail_recover] docker start db FAILED -- manual intervention required"; EXIT_STATUS=2; }
      else
        echo "$(ts) [fail_recover] db already running"
      fi
    else
      echo "$(ts) [fail_recover] restoring: router interface $RIF up"
      run_with_timeout "$CMD_TIMEOUT_S" docker exec router ip link set "$RIF" up >/dev/null 2>&1 \
        || { echo "$(ts) [fail_recover] interface restore FAILED -- manual intervention required"; EXIT_STATUS=2; }
    fi

    echo "$(ts) [fail_recover] verifying restoration (cap=${RECOVER_CAP_S}s)..."
    if wait_for_state ok "$RECOVER_CAP_S"; then
      echo "$(ts) [fail_recover] RESTORATION VERIFIED: /health reachable again"
    else
      echo "$(ts) [fail_recover] RESTORATION NOT VERIFIED -- check manually:"
      echo "$(ts) [fail_recover]   docker logs db --tail 50   /  docker exec router ip -o -4 addr show"
      EXIT_STATUS=3
    fi
  else
    echo "$(ts) [fail_recover] no failure was injected, nothing to restore"
  fi

  if [ "$SIGNAL_RECEIVED" -eq 1 ] && [ "$EXIT_STATUS" -eq 0 ]; then
    EXIT_STATUS=6
    echo "$(ts) [fail_recover] note: run was interrupted by $sig -- forcing nonzero exit despite successful restoration"
  fi
  echo "$(ts) [fail_recover] cleanup complete. final_exit_status=$EXIT_STATUS"
  IN_CLEANUP=0
  exit "$EXIT_STATUS"
}
trap 'cleanup INT'  INT
trap 'cleanup TERM' TERM
trap 'cleanup EXIT' EXIT

# ================================================================= main
echo "$(ts) BEFORE: health check"; ./ops/healthcheck.sh | tail -1
echo "$(ts) starting client watch (1 request / 0.5s for 60s)"
docker exec meter python meter_sim.py watch --label "failure_$MODE" --duration 60 > "ops/results/watch_failure_${MODE}.log" 2>&1 &
WATCH=$!
sleep 10

echo "$(ts) >>> INDUCING FAILURE: $MODE"
T_FAIL_ISSUED=$(now)
FAILURE_INJECTED=1   # armed BEFORE the disruptive call so a signal mid-call still restores
if [ "$MODE" = db ]; then
  run_with_timeout "$CMD_TIMEOUT_S" docker stop db >/dev/null
else
  run_with_timeout "$CMD_TIMEOUT_S" docker exec router ip link set "$RIF" down
fi
T_FAIL_ISSUED_DT=$(duration "$T_FAIL_ISSUED" "$(now)")
echo "$(ts) >>> failure command returned after ${T_FAIL_ISSUED_DT}s (script-measured, not yet detected by health check)"

# detection loop: bounded, logged every 0.5s instead of the old silent
# `until ! healthy; do sleep 0.5; done` (which could not loop forever given
# healthy()'s exit codes, but gave zero visible progress per iteration).
echo "$(ts) >>> starting bounded detection loop (cap=${DETECT_CAP_S}s)"
if wait_for_state fail "$DETECT_CAP_S"; then
  T_DETECTED=$(now)
  det_dt=$(duration "$T_FAIL_ISSUED" "$T_DETECTED")
  echo "$(ts) >>> DETECTED: /health failing (script-measured: ${det_dt}s from failure command issued to first failing probe)"
  docker compose logs --tail 5 api 2>/dev/null | grep -E "ERROR|HEALTH" | tail -3
else
  echo "$(ts) >>> DETECTION CAPPED OUT after ${DETECT_CAP_S}s -- health check never observed a failure"
  EXIT_STATUS=4
fi

sleep 10   # leave it broken long enough to see in the watch log

echo "$(ts) >>> RECOVERING"
T_RECOVER_ISSUED=$(now)
if [ "$MODE" = db ]; then
  run_with_timeout "$CMD_TIMEOUT_S" docker start db >/dev/null
else
  run_with_timeout "$CMD_TIMEOUT_S" docker exec router ip link set "$RIF" up
fi
echo "$(ts) >>> starting bounded recovery-verification loop (cap=${RECOVER_CAP_S}s)"
if wait_for_state ok "$RECOVER_CAP_S"; then
  T_HEALTH_RESTORED=$(now)
  rec_dt=$(duration "$T_RECOVER_ISSUED" "$T_HEALTH_RESTORED")
  echo "$(ts) >>> RECOVERED: /health passing again (script-measured: ${rec_dt}s from recovery command issued to first healthy probe)"
else
  echo "$(ts) >>> RECOVERY CAPPED OUT after ${RECOVER_CAP_S}s -- health check never observed recovery"
  EXIT_STATUS=5
fi

wait $WATCH
echo "$(ts) AFTER: health check"; ./ops/healthcheck.sh | tail -1
echo "$(ts) same client request again:"; docker exec meter python meter_sim.py send --meter meter-001 | head -2
echo
echo "client-observed view (meter_sim.py watch's own timeline, independent of this script's probes):"
grep -E "OUTAGE|RECOVERED|done" "ops/results/watch_failure_${MODE}.log"
# intentionally no explicit `exit` here -- falls through to the EXIT trap, which
# re-verifies restoration (idempotent) and exits with EXIT_STATUS: nonzero if
# anything above was capped out, unverified, or interrupted by a signal.
