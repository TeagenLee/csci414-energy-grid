"""Smart-meter simulator / test client (runs in SEGMENT 1).

  python meter_sim.py send                       -> send one reading, print response
  python meter_sim.py bench --label baseline     -> identical repeatable workload, saves CSV + summary
  python meter_sim.py watch --label x --duration 60  -> 1 reading every 0.5s, logs up/down timeline
  python meter_sim.py errors                     -> shows error responses (422/404/400)
  python meter_sim.py generate --meter pv-001 --hour 13.5  -> one synthetic generation reading
"""
import argparse, csv, math, os, random, statistics, sys, threading, time
from datetime import datetime
import requests

API = os.getenv("API_URL", "https://10.10.2.10:8443")
CA = os.getenv("CA_CERT", "/certs/api.crt")
DATA = "/data/household_power_consumption.txt"
RESULTS = "/results"
TIMEOUT = (5, 20)          # connect, read seconds


def load_rows(n=5000):
    rows = []
    if os.path.exists(DATA):
        with open(DATA) as f:
            next(f)
            for line in f:
                p = line.strip().split(";")
                if len(p) == 9 and "?" not in p:
                    rows.append(p)
                if len(rows) >= n:
                    break
    if not rows:  # fallback: fully synthetic
        print("dataset not found, generating synthetic rows", file=sys.stderr)
        for i in range(n):
            gap = round(random.uniform(0.2, 4.5), 3)
            rows.append(["16/12/2006", f"{(i // 60) % 24:02d}:{i % 60:02d}:00", gap, 0.1,
                         round(random.uniform(232, 246), 2), round(gap * 4.3, 1), 0, 1, 17])
    return rows


ROWS = load_rows()


def make_reading(meter_id, i):
    p = ROWS[i % len(ROWS)]
    ts = datetime.strptime(f"{p[0]} {p[1]}", "%d/%m/%Y %H:%M:%S").isoformat()
    return {
        "meter_id": meter_id, "ts": ts, "sent_at": time.time(),
        "global_active_power": float(p[2]), "global_reactive_power": float(p[3]),
        "voltage": float(p[4]), "global_intensity": float(p[5]),
        "sub_metering_1": float(p[6]), "sub_metering_2": float(p[7]),
        "sub_metering_3": float(p[8]), "source": "synthetic-replay",
    }


# Generation is explicitly simulated, not relabeled consumption data: the UCI
# dataset has no generation column, so generation_kw comes from a documented
# synthetic model rather than copying any of the fields above. Model: a
# clear-sky PV-like curve, zero before 6:00 and after 18:00, peaking at a
# configurable capacity at solar noon (hour 12), shaped by a half-cosine over
# the daylight window. This is explicitly an illustrative simulation, not a
# weather- or irradiance-calibrated forecast.
GEN_CAPACITY_KW = float(os.getenv("GEN_CAPACITY_KW", "5.0"))   # nameplate PV capacity, kW
GEN_SUNRISE_HOUR = 6.0
GEN_SUNSET_HOUR = 18.0


def solar_kw(hour_of_day: float, capacity_kw: float = GEN_CAPACITY_KW) -> float:
    if hour_of_day <= GEN_SUNRISE_HOUR or hour_of_day >= GEN_SUNSET_HOUR:
        return 0.0
    span = GEN_SUNSET_HOUR - GEN_SUNRISE_HOUR
    phase = (hour_of_day - GEN_SUNRISE_HOUR) / span      # 0..1 across the daylight window
    shape = math.sin(phase * math.pi)                    # 0 at sunrise/sunset, 1 at noon
    return round(capacity_kw * shape, 3)


def make_generation_reading(meter_id, i, hour_of_day=None):
    p = ROWS[i % len(ROWS)]                   # reuse the dataset's own timestamps for ts/i cadence
    ts = datetime.strptime(f"{p[0]} {p[1]}", "%d/%m/%Y %H:%M:%S")
    if hour_of_day is not None:
        # override ts's time-of-day to match the requested hour, keeping the
        # dataset row's date, so the stored/returned ts and the generation
        # value it was computed from always agree with each other
        h = int(hour_of_day) % 24
        m = int(round((hour_of_day - int(hour_of_day)) * 60)) % 60
        ts = ts.replace(hour=h, minute=m, second=0, microsecond=0)
        hour = hour_of_day
    else:
        hour = ts.hour + ts.minute / 60.0
    return {
        "meter_id": meter_id, "ts": ts.isoformat(), "sent_at": time.time(),
        # global_active_power/voltage/global_intensity are schema-required
        # fields carried over unchanged from the dataset row for this
        # timestamp; they describe consumption at the same instant, not the
        # generation value itself, which is reported separately below.
        "global_active_power": float(p[2]), "global_reactive_power": float(p[3]),
        "voltage": float(p[4]), "global_intensity": float(p[5]),
        "sub_metering_1": float(p[6]), "sub_metering_2": float(p[7]),
        "sub_metering_3": float(p[8]), "source": "synthetic-replay",
        "reading_type": "generation",
        "generation_kw": solar_kw(hour),
    }


def session():
    s = requests.Session()
    s.verify = CA
    s.trust_env = False      # ignore proxy / CA-bundle env vars
    return s


def pct(vals, q):
    if not vals:
        return 0.0
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, int(round(q / 100 * (len(vals) - 1)))))
    return vals[k]


# ---------------------------------------------------------------- send
def cmd_send(a):
    r = session().post(f"{API}/readings", json=make_reading(a.meter, random.randint(0, 4999)), timeout=TIMEOUT)
    print(r.status_code, r.json())
    rid = r.json().get("id")
    r2 = session().get(f"{API}/meters/{a.meter}/latest", timeout=TIMEOUT)
    print(r2.status_code, r2.json())
    if rid:
        r3 = session().patch(f"{API}/readings/{rid}", json={"flagged": True, "note": "checked by operator"}, timeout=TIMEOUT)
        print(r3.status_code, r3.json())


# ---------------------------------------------------------------- generate
def cmd_generate(a):
    reading = make_generation_reading(a.meter, random.randint(0, 4999), hour_of_day=a.hour)
    print(f"synthetic generation model: clear-sky half-cosine, capacity={GEN_CAPACITY_KW}kW, "
          f"daylight {GEN_SUNRISE_HOUR:.0f}:00-{GEN_SUNSET_HOUR:.0f}:00, hour_of_day={reading['ts'][11:16]}")
    r = session().post(f"{API}/readings", json=reading, timeout=TIMEOUT)
    print("POST", r.status_code, r.json())
    r2 = session().get(f"{API}/meters/{a.meter}/latest", timeout=TIMEOUT)
    print("GET latest", r2.status_code, r2.json())
    r3 = session().get(f"{API}/meters/{a.meter}/readings?reading_type=generation&limit=5", timeout=TIMEOUT)
    print("GET readings?reading_type=generation", r3.status_code, r3.json())


# ---------------------------------------------------------------- bench
def cmd_bench(a):
    os.makedirs(RESULTS, exist_ok=True)
    rows, lock = [], threading.Lock()
    start = time.time()

    def meter_worker(m):
        s = session()
        meter_id = f"meter-{m:03d}"
        for b in range(a.batches):
            batch = [make_reading(meter_id, m * 1000 + b * a.batch_size + k) for k in range(a.batch_size)]
            t0 = time.perf_counter()
            rec = {"label": a.label, "meter": meter_id, "op": "POST batch", "seq": b, "status": 0,
                   "latency_ms": 0, "server_ms": "", "max_freshness_ms": "", "readings": a.batch_size,
                   "error": ""}
            try:
                r = s.post(f"{API}/readings/batch", json={"readings": batch}, timeout=TIMEOUT)
                rec["status"] = r.status_code
                rec["server_ms"] = r.headers.get("X-Server-Time-ms", "")
                if r.status_code == 201:
                    rec["max_freshness_ms"] = r.json()["max_freshness_ms"]
            except requests.RequestException as e:
                rec["error"] = e.__class__.__name__
            rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            # identical read operation after every write
            t1 = time.perf_counter()
            rd = {"label": a.label, "meter": meter_id, "op": "GET latest", "seq": b, "status": 0,
                  "latency_ms": 0, "server_ms": "", "max_freshness_ms": "", "readings": 0, "error": ""}
            try:
                r = s.get(f"{API}/meters/{meter_id}/latest", timeout=TIMEOUT)
                rd["status"] = r.status_code
                rd["server_ms"] = r.headers.get("X-Server-Time-ms", "")
            except requests.RequestException as e:
                rd["error"] = e.__class__.__name__
            rd["latency_ms"] = round((time.perf_counter() - t1) * 1000, 2)
            with lock:
                rows.extend([rec, rd])

    threads = [threading.Thread(target=meter_worker, args=(m,)) for m in range(1, a.meters + 1)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    dur = time.time() - start

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    detail = f"{RESULTS}/bench_{a.label}_{stamp}.csv"
    with open(detail, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    posts = [r for r in rows if r["op"] == "POST batch"]
    gets = [r for r in rows if r["op"] == "GET latest"]
    ok_posts = [r for r in posts if r["status"] == 201]
    ok_gets = [r for r in gets if r["status"] == 200]
    fresh = [float(r["max_freshness_ms"]) for r in ok_posts]
    pl = [r["latency_ms"] for r in ok_posts]
    gl = [r["latency_ms"] for r in ok_gets]
    srv = [float(r["server_ms"]) for r in ok_posts if r["server_ms"]]
    delivered = sum(r["readings"] for r in ok_posts)
    total_req = len(posts) + len(gets)
    errors = total_req - len(ok_posts) - len(ok_gets)
    summary = {
        "time": stamp, "label": a.label, "requests": total_req, "errors": errors,
        "error_rate_pct": round(100 * errors / total_req, 2),
        "readings_sent": len(posts) * a.batch_size, "readings_delivered": delivered,
        "duration_s": round(dur, 2), "throughput_readings_per_s": round(delivered / dur, 1),
        "post_p50_ms": round(pct(pl, 50), 1), "post_p95_ms": round(pct(pl, 95), 1),
        "get_p50_ms": round(pct(gl, 50), 1), "get_p95_ms": round(pct(gl, 95), 1),
        "server_p50_ms": round(pct(srv, 50), 1),
        "freshness_p50_ms": round(pct(fresh, 50), 1), "freshness_p95_ms": round(pct(fresh, 95), 1),
    }
    sfile = f"{RESULTS}/summary.csv"
    new = not os.path.exists(sfile)
    with open(sfile, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary.keys()))
        if new:
            w.writeheader()
        w.writerow(summary)
    print(f"\n=== BENCH '{a.label}' : {a.meters} meters x {a.batches} batches x {a.batch_size} readings ===")
    for k, v in summary.items():
        print(f"  {k:28s} {v}")
    print(f"  detail CSV -> {detail}\n  summary row appended -> {sfile}")


# ---------------------------------------------------------------- watch
def cmd_watch(a):
    os.makedirs(RESULTS, exist_ok=True)
    out = f"{RESULTS}/watch_{a.label}.csv"
    s = session()
    end = time.time() + a.duration
    i, state, down_since, outages = 0, "UP", None, []
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["wallclock", "seq", "result", "status", "latency_ms", "detail"])
        while time.time() < end:
            t0 = time.perf_counter()
            try:
                r = s.post(f"{API}/readings", json=make_reading("meter-watch", i), timeout=(2, 3))
                ok, status, detail = r.status_code == 201, r.status_code, r.text[:60]
            except requests.RequestException as e:
                ok, status, detail = False, 0, e.__class__.__name__
            ms = round((time.perf_counter() - t0) * 1000, 1)
            now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            res = "OK" if ok else "FAIL"
            w.writerow([now, i, res, status, ms, detail]); f.flush()
            print(f"{now} seq={i:4d} {res:4s} status={status} {ms}ms {'' if ok else detail}", flush=True)
            if not ok and state == "UP":
                state, down_since = "DOWN", time.time()
                print(f"{now} >>> OUTAGE DETECTED", flush=True)
            elif ok and state == "DOWN":
                outages.append(time.time() - down_since)
                state = "UP"
                print(f"{now} >>> SERVICE RECOVERED after {outages[-1]:.1f}s", flush=True)
            i += 1
            time.sleep(max(0, 0.5 - (time.perf_counter() - t0)))
    print(f"\nwatch '{a.label}' done: {i} requests, outages={['%.1fs' % o for o in outages]}  log -> {out}")


# ---------------------------------------------------------------- errors
def cmd_errors(a):
    s = session()
    print("1) invalid reading (negative power, missing voltage) ->")
    r = s.post(f"{API}/readings", json={"meter_id": "meter-001", "ts": "x", "sent_at": 0,
                                         "global_active_power": -5, "global_intensity": 1}, timeout=TIMEOUT)
    print("  ", r.status_code, r.text[:200])
    print("2) unknown meter ->")
    r = s.get(f"{API}/meters/does-not-exist/latest", timeout=TIMEOUT); print("  ", r.status_code, r.text)
    print("3) bad reading id on update ->")
    r = s.patch(f"{API}/readings/not-an-id", json={"flagged": True}, timeout=TIMEOUT); print("  ", r.status_code, r.text)
    print("4) bad limit ->")
    r = s.get(f"{API}/meters/meter-001/readings?limit=9999", timeout=TIMEOUT); print("  ", r.status_code, r.text)


p = argparse.ArgumentParser()
sub = p.add_subparsers(dest="cmd", required=True)
x = sub.add_parser("send"); x.add_argument("--meter", default="meter-001"); x.set_defaults(f=cmd_send)
x = sub.add_parser("bench"); x.add_argument("--label", required=True)
x.add_argument("--meters", type=int, default=5); x.add_argument("--batches", type=int, default=20)
x.add_argument("--batch-size", type=int, default=50); x.set_defaults(f=cmd_bench)
x = sub.add_parser("watch"); x.add_argument("--label", required=True)
x.add_argument("--duration", type=int, default=60); x.set_defaults(f=cmd_watch)
x = sub.add_parser("errors"); x.set_defaults(f=cmd_errors)
x = sub.add_parser("generate"); x.add_argument("--meter", default="pv-001")
x.add_argument("--hour", type=float, default=None,
                help="hour of day 0-24 (e.g. 13.5 = 13:30) for the synthetic solar curve; "
                     "defaults to the replayed dataset row's own time of day")
x.set_defaults(f=cmd_generate)
a = p.parse_args(); a.f(a)
