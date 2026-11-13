# Energy Grid Telemetry Platform

**CSCI 414/514 Platform-Based Development: Final Practical Exam, Option 09 (Track A, NoSQL)**

A smart-meter simulator sends power readings across three routed IP segments to an HTTPS API backed by MongoDB. As the assignment brief puts it, *engineering evidence matters more than interface polish*. This repository holds the network, the service, the security policy, the measurements, and a scripted failure-and-recovery workflow.

```
BUILD  →  PROTECT  →  MEASURE  →  RECOVER
```

| | |
|---|---|
| **Application** | Option 09: Energy Grid Telemetry Platform (smart meters → API → NoSQL) |
| **Networking focus** | Telemetry reliability and bandwidth: throttle the meter uplink and measure reading freshness |
| **Datastore** | MongoDB 7.0, collection `grid.readings` |
| **Dataset** | [UCI Individual Household Electric Power Consumption](https://archive.ics.uci.edu/dataset/235/individual+household+electric+power+consumption) |
| **Stack** | Docker Compose, Linux routing and nftables, Python/FastAPI, OpenSSL/TLS, tc/netem, tcpdump/Wireshark, iperf3, Bash |
| **Optional extension** | Anomaly flag for consumption power above 6.0 kW |

### Report and evidence

- **Final report:** [`docs/CSCI414_Energy_Grid_Final_Report.docx`](docs/CSCI414_Energy_Grid_Final_Report.docx). The numbers and labels below match the report. Its Appendix C indexes the evidence for every claim.
- **Evidence folders:**
  - [`ops/results/`](ops/results/): benchmark CSVs, `summary.csv`, ping and iperf3 output, recovery consoles
  - [`ops/results/evidence/`](ops/results/evidence/): the Nov 12 recovery runs, the secure redeployment and the generation feature
  - [`ops/captures/`](ops/captures/): packet captures from Nov 10
  - [`ops/logs/`](ops/logs/): the API log
  - [`docs/report_assets/`](docs/report_assets/): the report checks, screenshots and source hashes

The report draws on three evidence sets and names the set with every result. This README does the same.

| Evidence set | When | What it contains |
|---|---|---|
| **November 10 set** | 10 Nov 2026, original test host | Packet captures, first benchmark run with ping and iperf3, link-failure correlation |
| **November 12 Mac set** | 12 Nov 2026, Mac-local deployment | Second benchmark run (workload only), both recovery runs, secure redeployment |
| **Report checks** | 13 Nov 2026, 06:20–08:02 UTC, same Mac deployment | CRUD and error transcript, TLS, ACL denial, traced transaction, traceroutes, terminal screenshots |

---

## 1. Team and roles

| Role | Member | Ownership |
|---|---|---|
| Network Lead | Chidera Izuora | Topology, routing, segmentation, impairment experiments |
| Platform Lead | Teagen Lee | Application, API, NoSQL data model, TLS |
| Validation Lead | Yohannes Nigusse | Monitoring, automation, measurements, evidence |

All three contributed equally overall, and each can explain the full architecture.

---

## 2. Reference architecture

| Zone | Container | Role |
|---|---|---|
| **Client zone** | `meter` | Smart-meter simulator and load generator; sends readings over HTTPS |
| **Service zone** | `api` | FastAPI on TCP 8443 (TLS); validates, timestamps and stores readings; iperf3 server on 5201 |
| **Data zone** | `db` | MongoDB on TCP 27017; no permitted path from the meter subnet |
| **Operations** | `router` + `ops/` scripts | Forwarding, firewall, packet capture, impairment, health checks, logs |

```
             SEGMENT 1                    SEGMENT 2                    SEGMENT 3
        meters_net 10.10.1.0/24      api_net 10.10.2.0/24         db_net 10.10.3.0/24

        ┌──────────────┐                ┌──────────────┐             ┌──────────────┐
        │    meter     │                │     api      │             │      db      │
        │  10.10.1.10  │                │  10.10.2.10  │             │  10.10.3.10  │
        └──────┬───────┘                └──────┬───────┘             └──────┬───────┘
               │ via .254                      │ via .254                   │ via .254
        ┌──────┴───────────────────────────────┴────────────────────────────┴──────┐
        │                     router  (ip_forward=1, nftables)                     │
        │       eth0 10.10.1.254       eth1 10.10.2.254       eth2 10.10.3.254     │
        └──────────────────────────────────────────────────────────────────────────┘

   meter ──HTTPS :8443──▶ api ──MongoDB :27017──▶ db        meter ──✕──▶ db  (dropped)
```

No container publishes a host port, so every client command in this README runs inside the `meter` container with `docker exec`.

### End-to-end transaction

1. The meter (`meter/meter_sim.py`) sends a request to `https://10.10.2.10:8443`.
2. The router forwards it from segment 1 to segment 2. The firewall permits TCP 8443.
3. The API (`api/app.py`) validates the reading, records `received_at`, computes `freshness_ms`, sets the anomaly flag and writes to MongoDB. That write is a second routed connection, from segment 2 to segment 3.
4. The response returns to the meter over the established connection.
5. Logs (`ops/logs/api.log`), router captures (`ops/captures/`) and client timers (`ops/results/`) record each stage.

---

## 3. Quick start

**Prerequisites:** Docker with Compose and Linux containers that allow `NET_ADMIN`/`NET_RAW`. The host also needs Bash, curl, unzip, OpenSSL with `req -addext`, and `column`. The Dockerfiles install the Python and network tools inside the containers. Base images are `python:3.12-slim`, `mongo:7.0` and `ubuntu:24.04`.

```bash
git clone https://github.com/TeagenLee/csci414-energy-grid.git
cd csci414-energy-grid
./ops/deploy.sh --with-firewall   # dataset, certificate, build, import, nftables
./ops/healthcheck.sh              # 13 checks; exit 0 and "ALL PASS" only if all pass
```

`ops/deploy.sh` runs these steps:

1. Downloads and extracts the UCI dataset into `data/` if `data/household_power_consumption.txt` is missing.
2. Creates a self-signed RSA-2048 certificate (CN `grid-api`, SAN `IP:10.10.2.10,DNS:api`, valid 365 days) if `certs/api.crt` or `certs/api.key` is missing or empty, then runs `chmod 644` on both files.
3. Runs `docker compose up -d --build`.
4. Polls `/health` over verified TLS up to 40 times, 2 s apart. If the API never becomes healthy, it exits 1.
5. Runs `import_uci.py`, which deletes existing `source=uci-historical` records and imports the first 20,000 usable rows as meter `uci-house-1`.
6. Loads the firewall, but only with `--with-firewall`.

> **Notes**
> - The firewall is opt-in. Without `--with-firewall`, no ACL is loaded. The router does not restore its rules on restart; run `./ops/firewall.sh on` again.
> - The key file is left at mode 644 by `deploy.sh`.
> - Deployment from a fresh clone onto an empty MongoDB volume has **not** been re-run. The secure redeployment on 12 Nov reused the existing dataset and volume (`ops/results/evidence/deploy_verify_20261112/`).
> - Container names and subnets are fixed, so only one copy of the stack can run on a host.

---

## 4. Module A: Network infrastructure

| Network | Subnet | Endpoint | Router interface | Docker bridge gateway |
|---|---|---|---|---|
| `meters_net` | 10.10.1.0/24 | meter 10.10.1.10 | eth0 10.10.1.254 | 10.10.1.1 |
| `api_net` | 10.10.2.0/24 | api 10.10.2.10 | eth1 10.10.2.254 | 10.10.2.1 |
| `db_net` | 10.10.3.0/24 | db 10.10.3.10 | eth2 10.10.3.254 | 10.10.3.1 |

At startup, each endpoint installs routes to the other two /24 subnets through its local `.254` router address (`docker-compose.yml` lines 41–42, 60–61 and 81–82). The router runs with `net.ipv4.ip_forward=1`. Interface names come from the report checks (`docs/report_assets/interfaces_routes.txt`) and can differ on another host.

**Verify:**

```bash
docker exec router ip -br -4 addr
docker exec meter  ip route
docker exec meter  ping -c 3 10.10.2.10
docker exec api    ping -c 3 10.10.3.10
docker exec meter  traceroute -n -I -m 5 -q 1 -w 1 10.10.2.10
docker exec meter  timeout 30 traceroute -n -I -m 5 -q 1 -w 1 10.10.3.10
```

The traceroutes need `-I` (ICMP) because the firewall forwards ICMP echo from the meter but not UDP probes. In the report checks (13 Nov, 08:02 UTC), the trace to the API completed in two hops through 10.10.1.254. The trace to the database got a reply from 10.10.1.254 at hop 1 and no reply at hops 2–5. Over those two traces, the router's `DENY meters to DB` counter rose from 20 to 24 packets (`docs/report_assets/traceroute_20261113.txt`).

---

## 5. Module B: Distributed service

### API

| Method & path | Purpose | Responses (from `api/app.py`) |
|---|---|---|
| `GET /health` | MongoDB ping | 200 `{"status":"ok","db":"up","db_ping_ms":…}`; 503 `{"status":"degraded","db":"down"}` |
| `POST /readings` | Create one reading | 201 `{id, freshness_ms, anomaly}`; 422 on validation error |
| `POST /readings/batch` | Create 1–1,000 readings | 201 `{inserted, db_ms, max_freshness_ms, anomalies}`; 422 if empty or over 1,000 |
| `GET /meters/{id}/latest` | Most recent reading, by `received_at` | 200; 404 `no readings for meter …` |
| `GET /meters/{id}/readings?limit=N&reading_type=T` | Recent readings, by `received_at` | 200; 400 if `limit` is outside 1–500 or `reading_type` is invalid |
| `PATCH /readings/{id}` | `$set` `flagged` and/or `note` | 200; 400 for a malformed ID or empty change; 404 if absent |
| `GET /anomalies?limit=N` | Readings with `anomaly=true` (limit capped at 200) | 200 |
| `GET /stats` | Counts by source and type, plus the meter list | 200 |

Any MongoDB error on a database-backed route returns 503 `{"error":"database unavailable"}`.

### Data model: `grid.readings`

| Field(s) | Meaning and validation |
|---|---|
| `meter_id`, `ts` | Meter identity (1–40 chars); measurement or replay timestamp string |
| `sent_at`, `received_at` | Client send time and API receipt time, epoch seconds |
| `global_active_power`, `voltage`, `global_intensity` | kW ≥ 0, V > 0, A ≥ 0; reactive power and sub-metering 1–3 are carried from the dataset |
| `reading_type`, `generation_kw` | `consumption` (default) or `generation`. Generation requires `generation_kw` ≥ 0; consumption must omit it |
| `freshness_ms`, `anomaly` | (receipt − `sent_at`) in ms, computed before the insert; `anomaly` = active power > 6.0 kW |
| `source`; `flagged`, `note` | `uci-historical` or `synthetic-replay`; operator annotations written by `PATCH` |

Indexes: `(meter_id, ts desc)` and `(anomaly)`.

**Data placement:** MongoDB is on its own segment, so every database-backed request crosses a second routed hop (API → router → DB). The report checks traced one POST: the MongoDB insert took 1.46 ms on the wire, the API recorded 28.9 ms of server time, and the SYN-to-response time at the meter was about 36 ms (report §5.1).

**Records:** the importer loads 20,000 historical rows. The redeployment transcript reports 20,000 imported and 4 skipped. The simulator replays up to 5,000 more rows with fresh `sent_at` values.

### Repeatable queries

```bash
# create → read → update; prints 201, 200, 200
docker exec meter python meter_sim.py send --meter meter-demo

# error cases; prints 422, 404, 400, 400
docker exec meter python meter_sim.py errors

# the report's asserting client (creates, reads, annotates, re-reads, then 6 error cases)
docker exec -i meter python - < docs/report_assets/report_client.py

# reads over verified TLS
docker exec meter curl --cacert /certs/api.crt 'https://10.10.2.10:8443/meters/uci-house-1/readings?limit=2'
docker exec meter curl --cacert /certs/api.crt 'https://10.10.2.10:8443/anomalies?limit=5'
docker exec meter curl --cacert /certs/api.crt  https://10.10.2.10:8443/stats

# synthetic generation (illustrative clear-sky model, 5.0 kW peak at 12:00)
docker exec meter python meter_sim.py generate --meter pv-demo-001 --hour 12
```

Every command above writes new records to MongoDB. None of them writes to the evidence folders. `report_client.py` reuses the meter IDs from the report, so a second run adds more records under `report-demo-20261112`.

**Recorded results (report checks, 13 Nov 06:20 UTC):** `docs/report_assets/report_transaction.txt` shows POST 201, GET 200, PATCH 200 and GET 200 with the same ID. It also shows the six error cases: 422, 404, 400, 400, 422 and 400. For generation, `--hour 12`, `9` and `2` returned `generation_kw` 5.000, 3.536 and 0.000 (`generation_*.txt`).

---

## 6. Module C: Network security

### Access control: `router/firewall.nft`

The `inet grid` forward chain has a **default-drop** policy.

| Source → destination | Decision |
|---|---|
| established/related | accept |
| 10.10.1.0/24 → 10.10.2.10 TCP 8443 (HTTPS) | accept |
| 10.10.1.0/24 → 10.10.2.10 TCP 5201 (iperf3) | accept |
| 10.10.1.0/24 → 10.10.2.0/24 ICMP echo-request | accept |
| 10.10.2.10 → 10.10.3.10 TCP 27017 (MongoDB) | accept |
| 10.10.2.0/24 → 10.10.3.0/24 ICMP echo-request | accept |
| **10.10.1.0/24 → 10.10.3.0/24** | **counted drop** (`DENY meters to DB`) |
| anything else | counted, then policy drop |

The rules filter forwarded traffic only. They do not cover the Docker host or the router's own traffic.

### Encryption

Uvicorn terminates TLS on 8443. Clients trust exactly `certs/api.crt` (`--cacert` for curl, `Session.verify` for Python), so each request verifies both the certificate and the server name. No command uses `curl -k`. In the report checks, the meter negotiated **TLS 1.3, TLS_AES_256_GCM_SHA384**, with a SAN match for 10.10.2.10 and HTTP 200 (report Figure 3).

### Allow and deny test

```bash
./ops/firewall.sh show

# ALLOWED: meter → API over verified TLS
docker exec meter curl -sv --max-time 5 --cacert /certs/api.crt https://10.10.2.10:8443/health 2>&1 | grep -E 'SSL connection|verify ok|< HTTP'

# BLOCKED: meter → MongoDB directly
docker exec meter nc -zvw2 10.10.3.10 27017; echo "nc exit=$?"
```

`./ops/healthcheck.sh` asserts the verified-TLS path, API → DB TCP 27017, meter → DB TCP 27017 blocked, and plain HTTP refused.

**Recorded results:**

- **Report checks, 13 Nov:**
  - `nc` timed out with exit 1.
  - The `DENY meters to DB` counter rose from 4 to 6 packets (`firewall_before.txt`, `firewall_after.txt`).
  - `report_demo.pcap` shows only the meter's SYN and one retransmission (frames 149–150), with no reply.
- **November 10 set:** `ops/captures/deny_test.pcap` shows unanswered SYNs to 27017 and unanswered echo requests.

TLS protects what the meter and API say to each other. The ACL decides which conversations can exist at all.

---

## 7. Module D: Observability

| Layer | Where |
|---|---|
| Packets: tcpdump on the router's `any` interface, which sees all three segments | `ops/captures/*.pcap` (Nov 10); `docs/report_assets/report_demo.pcap` (report checks) |
| API log: method, path, status, server time | `ops/logs/api.log` |
| Client: per-request latency and status | `ops/results/bench_*.csv`, `ops/results/watch_failure_*.csv` |
| Health and recovery consoles | `ops/results/failure_*.txt` (Nov 10), `ops/results/evidence/recovery_20261112/` (Nov 12) |

To take a new capture during a transaction, use a new name so the committed captures are not overwritten:

```bash
./ops/capture.sh demo_txn 15 &
sleep 2; docker exec meter python meter_sim.py send --meter meter-demo; wait
```

`capture.sh` writes `ops/captures/<name>.pcap` and prints the first 15 packets. In Wireshark, filter on `tcp.port==8443` for the meter-to-API connection and `tcp.port==27017` for API to DB.

**Correlations in the report:**

- **One transaction across both hops (report checks, §5.1):** ObjectId `6af6ac1e1dd9bfba6656b461` connects three records: the client's POST response, the PATCH line in the API log, and the plaintext MongoDB update in frame 55 of `report_demo.pcap`.
- **Link failure (November 10 set, §5.2):**
  1. The API-side router interface went down at 20:50:57.789 UTC.
  2. The router sent ICMP Network Unreachable to the meter 0.23 s later (frame 516 of `ops/captures/link_failure.pcap`).
  3. `/health` began failing, and client requests failed at the transport level (status 0) with nothing logged by the API.
  4. After the interface came up, a new handshake completed within about 0.2 s, and the API logged `/health` 200 and a POST 201 within 0.8 s.

  Of 105 watch requests, 100 succeeded. Sources: `ops/results/failure_link.txt`, `docs/report_assets/historical_20261110/`.

---

## 8. Module E: Network performance

**Workload:** `ops/experiments.sh` runs the same workload under each condition: `meter_sim.py bench --meters 5 --batches 20 --batch-size 50`. That is five concurrent meters, each sending 20 batch POSTs of 50 readings with a latest-reading GET after every batch: 100 POSTs, 100 GETs and 5,000 readings per condition. `ops/impair.sh` applies `tc netem` to the egress of the meter's `eth0`. The conditions run in this order:

| Condition | `ops/impair.sh` argument | netem setting |
|---|---|---|
| Baseline | `clear` | none |
| Delay | `delay` | `delay 100ms 20ms` |
| Loss | `loss` | `loss 5%` |
| Rate limit | `throttle` | `rate 256kbit` |

**Metrics:**
- POST and GET latency is client time per request; p50 and p95 are taken over the 100 requests of each type.
- Server p50 is the API middleware time, taken over the 100 POSTs only. It can include time spent receiving the request body, so it is not database time.
- Freshness is the p50 and p95 of each batch's maximum (receipt − `sent_at`).
- Throughput is readings acknowledged with 201, divided by wall time.

```bash
./ops/impair.sh show
./ops/impair.sh clear
./ops/experiments.sh
```

> **Run experiments in a scratch clone.** `experiments.sh` overwrites `ops/results/net_<condition>.txt` and appends to `ops/results/summary.csv`, and `fail_recover.sh` overwrites `ops/results/watch_failure_<mode>.{log,csv}`. All of these are committed evidence. To leave them untouched, run any experiment, recovery test or capture in a separate clone, for example `git clone … /tmp/grid-scratch`. Only one stack can run on a host at a time.

### Results: workload, one trial per condition, all 200 requests succeeded in every run

Source: `ops/results/summary.csv`, computed from `bench_*.csv`. All times in ms. Compare conditions within a set, not across sets.

**November 10 set (10 Nov 2026, original test host)**

| Condition | POST p50 / p95 | GET p50 / p95 | Server p50 | Freshness p50 / p95 | Readings/s |
|---|---:|---:|---:|---:|---:|
| Baseline | 28.6 / 47.6 | 20.3 / 32.1 | 17.4 | 15.0 / 30.2 | 4,446.9 |
| Delay 100 ± 20 ms | 125.0 / 219.4 | 109.5 / 127.6 | 25.2 | 121.3 / 216.1 | 858.0 |
| Loss 5% | 15.0 / 51.4 | 21.6 / 226.8 | 9.0 | 8.3 / 40.1 | 3,193.4 |
| Rate 256 kbit/s | 1,493.7 / 2,454.9 | 1,022.7 / 1,981.0 | 483.2 | 1,490.1 / 2,451.1 | 98.6 |

**November 12 Mac set (12 Nov 2026, Mac-local deployment)**

| Condition | POST p50 / p95 | GET p50 / p95 | Server p50 | Freshness p50 / p95 | Readings/s |
|---|---:|---:|---:|---:|---:|
| Baseline | 6.2 / 10.7 | 4.6 / 6.5 | 4.2 | 3.3 / 7.2 | 18,427.6 |
| Delay 100 ± 20 ms | 203.0 / 415.6 | 108.1 / 128.3 | 100.2 | 201.6 / 402.3 | 634.5 |
| Loss 5% | 3.7 / 26.7 | 4.6 / 7.0 | 2.3 | 1.7 / 19.0 | 7,094.3 |
| Rate 256 kbit/s | 1,504.3 / 2,465.5 | 1,010.3 / 1,976.1 | 487.1 | 1,499.2 / 2,460.1 | 98.6 |

### Results: network probes (November 10 set only, 10 Nov 2026)

Source: `ops/results/net_*.txt`, from a 20-packet ping and a 10 s iperf3 run, meter → API. The Nov 12 Mac set has no ping or iperf3 records.

| Condition | Ping RTT avg, ms (lost/sent) | iperf3 receive |
|---|---:|---:|
| Baseline | 0.094 (0/20) | 16.6 Gbit/s |
| Delay 100 ± 20 ms | 97.755 (0/20) | 830 kbit/s |
| Loss 5% | 0.092 (1/20) | 394 Mbit/s |
| Rate 256 kbit/s | 3.221 (0/20) | 209 kbit/s |

### Interpretation (report §6.3)

- **Rate limit:** the shaped uplink is the dominant bottleneck.
  - At 256 kbit/s, throughput fell about 45× (Nov 10) and 187× (Nov 12) to 98.6 readings/s in both sets.
  - Freshness p95 (2,451–2,460 ms) nearly equals POST p95 (2,455–2,466 ms), so readings wait in the uplink before the API receives them. This is the Option 09 effect.
  - Server p50 also rose, to about 485 ms. Server time can include receiving the body over the shaped link, and MongoDB was not timed separately, so a smaller database contribution can't be ruled out.
- **Delay:**
  - The added 100 ms appears in ping RTT (0.094 → 97.8 ms, Nov 10) and in POST p95 and freshness.
  - In the Nov 10 set, server p50 barely changed (17.4 → 25.2 ms).
  - In the Nov 12 set, server p50 rose to 100.2 ms. This is consistent with the timer including body receipt over the delayed uplink, but it was not attributed to the database.
- **Loss:**
  - iperf3 recorded 17,492 sender retransmissions (Nov 10), and every application request still succeeded.
  - The effect shows in the tail: GET p95 rose from 32.1 to 226.8 ms while GET p50 stayed near baseline.
  - In the Nov 12 set, POST p50 under loss (3.7 ms) was lower than baseline (6.2 ms). With one trial per condition in a fixed order, that difference can't be separated from run-to-run variation, so it is not read as loss improving performance.
- **What was not measured:**
  - The benchmark counts acknowledged requests and readings. It does not reconcile records against MongoDB.
  - Each condition had one trial, with no confidence intervals.
  - The two sets ran on different hosts.

---

## 9. Module F: Automation and recovery

| Script | What it does |
|---|---|
| `ops/deploy.sh [--with-firewall]` | Dataset download, certificate bootstrap, build, health wait, import, optional firewall |
| `ops/healthcheck.sh` | 13 checks; exit 0 only if all pass |
| `ops/firewall.sh on\|off\|show` | Load, flush, or list the router ruleset (no argument = `show`) |
| `ops/impair.sh clear\|delay\|loss\|throttle\|throttle_delay\|show` | Apply or remove netem on the meter uplink, then show the qdisc |
| `ops/experiments.sh` | Baseline, delay, loss and throttle benchmarks, plus ping and iperf3 |
| `ops/capture.sh <name> <seconds>` | tcpdump on the router into `ops/captures/<name>.pcap` |
| `ops/fail_recover.sh db\|link` | Controlled failure, detection, restoration and verification |

### Failure exercise

```bash
./ops/fail_recover.sh db     # docker stop db / docker start db
./ops/fail_recover.sh link   # router's API-side interface down / up
```

The script runs these steps in order:

1. Runs the healthcheck.
2. Starts a 60 s client watch that sends one reading every 0.5 s.
3. Injects the fault.
4. Polls verified `/health` until it fails.
5. Holds the fault for 10 s.
6. Reverses the fault and polls until health returns.
7. Repeats the healthcheck and one client request.

The fault command, the restore command and each `/health` probe run under an 8 s watchdog (`CMD_TIMEOUT_S`). Each polling loop is capped at 60 s (`DETECT_CAP_S`, `RECOVER_CAP_S`). A capped detection loop exits 4 and a capped recovery loop exits 5. The before and after healthchecks, the client watch and the final request are not wrapped by the watchdog. INT, TERM and EXIT traps restore the fault, and an interrupted run exits nonzero. The INT/TERM path has not been tested.

### Results: November 12 Mac set (12 Nov 2026)

Source: `ops/results/evidence/recovery_20261112/`, report Table 10.

| Measure | DB stop/start | API-side link down/up |
|---|---|---|
| Detection (fault command → first failing probe) | 2.476 s | 5.316 s |
| Restoration verified (restore command → first healthy probe) | 1.529 s | 0.417 s |
| Client-observed outage | 11.9 s | 13.1 s |
| Watch requests succeeded | 93/98 (94.9%) | 89/96 (92.7%) |
| Failure seen by client | 5 × HTTP 503 `{"error":"database unavailable"}` | 1 ReadTimeout + 6 ConnectTimeout (status 0) |
| Healthcheck before / after; exit status | ALL PASS / ALL PASS; 0 | ALL PASS / ALL PASS; 0 |
| Post-restoration client request | POST 201, GET 200 | POST 201, GET 200 |

Most of the 12–13 s client-observed outage is the deliberate 10 s hold. The client watch has no retry queue, and readings whose requests failed during the outage were not resent. The top-level `ops/results/watch_failure_*` files are copies of these Nov 12 runs. `ops/results/failure_*.txt` are the consoles from an earlier version of the script on the November 10 host.

---

## 10. Three required experimental states

| State | How to reproduce (in a scratch clone) | Recorded evidence |
|---|---|---|
| **1. Baseline** | `./ops/impair.sh clear && ./ops/healthcheck.sh` | `bench_baseline_*.csv`, `net_baseline.txt` (Nov 10) |
| **2. Congested** | `./ops/experiments.sh` | `bench_{delay,loss,throttle}_*.csv`, `summary.csv` (Nov 10 and Nov 12) |
| **3. Failure + recovery** | `./ops/fail_recover.sh db` or `./ops/fail_recover.sh link` | `evidence/recovery_20261112/` (Nov 12); `link_failure.pcap` and `failure_link.txt` (Nov 10) |

---

## 11. Repository layout

```
.
├── docker-compose.yml        # 3 networks, 4 services (router, db, api, meter)
├── api/                      # FastAPI service (app.py) + UCI importer (import_uci.py)
├── meter/                    # simulator: send / errors / bench / watch / generate
├── db/                       # MongoDB 7.0 image
├── router/                   # router image + firewall.nft
├── certs/                    # api.crt; api.key is generated and git-ignored
├── data/                     # UCI dataset, downloaded by deploy.sh (git-ignored)
├── docs/                     # final report + report_assets/ (report-check evidence)
└── ops/
    ├── *.sh                  # deploy, healthcheck, firewall, impair, experiments, capture, fail_recover
    ├── captures/             # Nov 10 pcaps: normal_txn, deny_test, link_failure
    ├── logs/                 # api.log
    └── results/              # benchmark CSVs, net_*.txt, failure consoles, evidence/
```

---

## 12. Requirements mapping

| Criterion | Where it is shown |
|---|---|
| Network architecture & routing | §2, §4; report §2 |
| NoSQL service integration | §5; report §3 |
| Security implementation | §6; report §4 |
| Observability & packet evidence | §7; report §5, Appendix A |
| Performance experiments | §8; report §6 |
| Automation & recovery | §9; report §7 |
| Documentation & demonstration | This README, final report, slides, demo video (submitted separately) |

---

## 13. Data, licensing, and limitations

These match report §9.

- **Dataset:** G. Hebrail and A. Berard, *Individual Household Electric Power Consumption* [Dataset], UCI Machine Learning Repository, 2006. DOI [10.24432/C58K54](https://doi.org/10.24432/C58K54), CC BY 4.0. It is downloaded at deploy time and is git-ignored.
- **Data:** consumption is historical household data used as a proxy, not live grid data. Generation comes from a modeled sine curve with no weather input and no measured output. The anomaly flag tests consumption power only.
- **Security scope:** TLS authenticates the server only; there are no client certificates. The API → MongoDB hop is plaintext, and MongoDB has no authentication. The ACL covers router-forwarded traffic only and loads only with `--with-firewall` or `ops/firewall.sh on`. The private key is mode 644 and git-ignored.
- **Measurement design:** each condition had one trial, in a fixed order, against a growing collection, with no confidence intervals. The two sets ran on different hosts. Server time is not database time, and MongoDB was not instrumented separately.
- **Delivery guarantees:** there is no retry queue or idempotency key. Zero request errors does not mean records were reconciled against MongoDB. The single router is a single point of failure.
- **Queries, deployment and recovery:**
  - Latest and recent queries sort by `received_at`, which the declared `(meter_id, ts)` index does not cover.
  - A fresh-clone deployment onto an empty volume was not re-run.
  - The recovery script's INT/TERM cleanup was not tested.
  - An earlier stall of the recovery script was not reproduced, and its cause is unknown.
