"""Energy Grid Telemetry API (Module B).
Smart meters -> HTTPS API -> MongoDB.
"""
import logging, os, time
from datetime import datetime, timezone
from typing import List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import PyMongoError

# ---------- logging (stdout + /logs/api.log) ----------
os.makedirs("/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(), logging.FileHandler("/logs/api.log")],
)
log = logging.getLogger("grid-api")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://10.10.3.10:27017")
ANOMALY_KW = float(os.getenv("ANOMALY_KW", "6.0"))  # alert threshold (kW)

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000,
                     connectTimeoutMS=2000, socketTimeoutMS=5000)
db = client["grid"]
readings = db["readings"]

app = FastAPI(title="Energy Grid Telemetry API")


class Reading(BaseModel):
    meter_id: str = Field(min_length=1, max_length=40)
    ts: str                                   # reading timestamp (ISO)
    sent_at: float                            # epoch secs when the meter sent it
    global_active_power: float = Field(ge=0)  # kW
    global_reactive_power: float = Field(ge=0, default=0)
    voltage: float = Field(gt=0)              # V
    global_intensity: float = Field(ge=0)     # A
    sub_metering_1: float = 0
    sub_metering_2: float = 0
    sub_metering_3: float = 0
    source: str = "synthetic-replay"


class Batch(BaseModel):
    readings: List[Reading] = Field(min_length=1, max_length=1000)


class ReadingUpdate(BaseModel):
    flagged: Optional[bool] = None
    note: Optional[str] = None


def ensure_indexes():
    readings.create_index([("meter_id", ASCENDING), ("ts", DESCENDING)])
    readings.create_index([("anomaly", ASCENDING)])


@app.on_event("startup")
def startup():
    try:
        ensure_indexes()
        log.info("startup: connected to MongoDB at %s, indexes ok", MONGO_URI)
    except PyMongoError as e:
        log.error("startup: MongoDB not reachable (%s)", e.__class__.__name__)


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    log.info("%s %s %s %d %.1fms", request.client.host if request.client else "-",
             request.method, request.url.path, response.status_code, ms)
    response.headers["X-Server-Time-ms"] = f"{ms:.2f}"
    return response


@app.exception_handler(PyMongoError)
async def mongo_down(request: Request, exc: PyMongoError):
    log.error("DB ERROR on %s %s: %s", request.method, request.url.path, exc.__class__.__name__)
    return JSONResponse(status_code=503, content={"error": "database unavailable"})


def to_doc(r: Reading) -> dict:
    now = time.time()
    d = r.model_dump()
    d["received_at"] = now
    d["freshness_ms"] = round((now - r.sent_at) * 1000, 2)
    d["anomaly"] = r.global_active_power > ANOMALY_KW
    if d["anomaly"]:
        log.warning("ANOMALY meter=%s power=%.2fkW ts=%s", r.meter_id, r.global_active_power, r.ts)
    return d


def clean(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id"))
    return doc


@app.get("/health")
def health():
    t0 = time.perf_counter()
    try:
        client.admin.command("ping")
    except PyMongoError as e:
        log.error("HEALTH FAIL: db unreachable (%s)", e.__class__.__name__)
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "down"})
    return {"status": "ok", "db": "up", "db_ping_ms": round((time.perf_counter() - t0) * 1000, 2)}


@app.post("/readings", status_code=201)          # CREATE (single)
def create_reading(r: Reading):
    d = to_doc(r)
    res = readings.insert_one(d)
    return {"id": str(res.inserted_id), "freshness_ms": d["freshness_ms"], "anomaly": d["anomaly"]}


@app.post("/readings/batch", status_code=201)    # CREATE (batch from a meter uplink)
def create_batch(b: Batch):
    docs = [to_doc(r) for r in b.readings]
    t0 = time.perf_counter()
    readings.insert_many(docs, ordered=False)
    db_ms = (time.perf_counter() - t0) * 1000
    fresh = [d["freshness_ms"] for d in docs]
    return {"inserted": len(docs), "db_ms": round(db_ms, 2),
            "max_freshness_ms": max(fresh), "anomalies": sum(d["anomaly"] for d in docs)}


@app.get("/meters/{meter_id}/latest")             # READ (most recent reading)
def latest(meter_id: str):
    doc = readings.find_one({"meter_id": meter_id}, sort=[("received_at", DESCENDING)])
    if not doc:
        raise HTTPException(404, f"no readings for meter {meter_id}")
    doc = clean(doc)
    doc["age_s"] = round(time.time() - doc["received_at"], 3)
    return doc


@app.get("/meters/{meter_id}/readings")           # READ (recent window)
def recent(meter_id: str, limit: int = 20):
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit must be 1..500")
    cur = readings.find({"meter_id": meter_id}).sort("received_at", DESCENDING).limit(limit)
    return [clean(d) for d in cur]


@app.patch("/readings/{reading_id}")              # UPDATE
def update_reading(reading_id: str, u: ReadingUpdate):
    try:
        oid = ObjectId(reading_id)
    except InvalidId:
        raise HTTPException(400, "invalid reading id")
    changes = {k: v for k, v in u.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(400, "nothing to update")
    res = readings.update_one({"_id": oid}, {"$set": changes})
    if res.matched_count == 0:
        raise HTTPException(404, "reading not found")
    return {"updated": reading_id, "changes": changes}


@app.get("/anomalies")
def anomalies(limit: int = 20):
    cur = readings.find({"anomaly": True}).sort("received_at", DESCENDING).limit(min(limit, 200))
    return [clean(d) for d in cur]


@app.get("/stats")
def stats():
    return {
        "total_readings": readings.count_documents({}),
        "historical_uci": readings.count_documents({"source": "uci-historical"}),
        "synthetic": readings.count_documents({"source": "synthetic-replay"}),
        "anomalies": readings.count_documents({"anomaly": True}),
        "meters": sorted(readings.distinct("meter_id")),
    }
