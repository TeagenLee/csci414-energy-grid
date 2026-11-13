from datetime import datetime, timezone
import requests, time, json
s=requests.Session(); s.verify="/certs/api.crt"; s.trust_env=False
base="https://10.10.2.10:8443"
meter="report-demo-20261112"
def req(method,path,body=None):
    print("BEGIN",datetime.now(timezone.utc).isoformat(),method,path,flush=True)
    r=s.request(method,base+path,json=body,timeout=(5,20))
    print("HTTP",r.status_code,r.text,flush=True)
    return r
reading={"meter_id":meter,"ts":datetime.now(timezone.utc).isoformat(),
         "sent_at":time.time(),"global_active_power":2.5,"voltage":240.0,
         "global_intensity":10.4,"source":"synthetic-replay"}
print("REQUEST_BODY",json.dumps(reading),flush=True)
r=req("POST","/readings",reading); r.raise_for_status(); rid=r.json()["id"]
r=req("GET","/meters/"+meter+"/latest"); r.raise_for_status()
assert r.json()["id"]==rid
r=req("PATCH","/readings/"+rid,{"flagged":True,"note":"report demonstration"}); r.raise_for_status()
r=req("GET","/meters/"+meter+"/latest"); r.raise_for_status()
assert r.json()["id"]==rid and r.json()["flagged"] is True
bad=dict(reading,global_active_power=-5); bad.pop("voltage")
assert req("POST","/readings",bad).status_code==422
assert req("GET","/meters/report-demo-absent-20261112/latest").status_code==404
assert req("PATCH","/readings/not-an-id",{"flagged":True}).status_code==400
assert req("GET","/meters/"+meter+"/readings?limit=9999").status_code==400
bad=dict(reading,reading_type="generation")
assert req("POST","/readings",bad).status_code==422
assert req("GET","/meters/"+meter+"/readings?reading_type=invalid").status_code==400
print("DEMO_ASSERTIONS_PASS",rid,flush=True)
