"""Reproducible import of the UCI Household Power dataset into MongoDB.
Usage: python import_uci.py /data/household_power_consumption.txt [max_rows]
Rows go in as meter 'uci-house-1' with source='uci-historical'.
"""
import os, sys, time
from datetime import datetime
from pymongo import MongoClient, ASCENDING, DESCENDING

path = sys.argv[1]
max_rows = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
db = MongoClient(os.getenv("MONGO_URI", "mongodb://10.10.3.10:27017"))["grid"]
col = db["readings"]
col.delete_many({"source": "uci-historical"})        # idempotent re-runs

docs, skipped = [], 0
with open(path) as f:
    next(f)                                            # header
    for line in f:
        if len(docs) >= max_rows:
            break
        p = line.strip().split(";")
        if len(p) != 9 or "?" in p:                    # dataset marks missing values with '?'
            skipped += 1
            continue
        ts = datetime.strptime(p[0] + " " + p[1], "%d/%m/%Y %H:%M:%S").isoformat()
        now = time.time()
        docs.append({
            "meter_id": "uci-house-1", "ts": ts, "sent_at": now, "received_at": now,
            "freshness_ms": 0.0,
            "global_active_power": float(p[2]), "global_reactive_power": float(p[3]),
            "voltage": float(p[4]), "global_intensity": float(p[5]),
            "sub_metering_1": float(p[6]), "sub_metering_2": float(p[7]),
            "sub_metering_3": float(p[8]),
            "anomaly": float(p[2]) > 6.0, "source": "uci-historical",
        })
col.insert_many(docs)
col.create_index([("meter_id", ASCENDING), ("ts", DESCENDING)])
print(f"imported {len(docs)} UCI rows (skipped {skipped} rows with missing values)")
print(f"total documents in grid.readings: {col.count_documents({})}")
