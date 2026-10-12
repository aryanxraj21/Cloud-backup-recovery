"""Cloud Backup Verification and Recovery System - REST API (FastAPI).

Run:  uvicorn app.main:app --host 0.0.0.0 --port 8000
Docs: http://localhost:8000/docs  (Swagger UI, auto-generated)
"""
import json
import random
import threading
import time
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import backup_service as svc
from . import db
from .auth import current_user, login, require
from .observability import log_event, metrics
from .storage import store

app = FastAPI(title="Cloud Backup Verification and Recovery System", version="1.0.0",
              description="Create backups, inject deletion/corruption/outages, verify and restore with measured recovery.")


@app.on_event("startup")
def startup():
    db.init_db()
    svc.start_worker()
    log_event("service_started")


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception as e:  # unhandled -> 500 but logged
        log_event("unhandled_error", level="ERROR", path=request.url.path, error=str(e))
        resp = JSONResponse({"detail": "internal error"}, status_code=500)
    ms = (time.perf_counter() - t0) * 1000
    metrics.observe("http_request", ms)
    metrics.inc("http_requests_total")
    metrics.inc("http_success_total" if resp.status_code < 500 else "http_failed_total")
    return resp


# ------------------------------------------------------------------ models
class LoginIn(BaseModel):
    username: str
    password: str


class RecordIn(BaseModel):
    name: str = Field(..., max_length=200)
    payload: dict


class RestoreIn(BaseModel):
    backup_id: str
    mode: Literal["repair", "full"] = "repair"
    fallback_to_previous: bool = True


class ChaosIn(BaseModel):
    type: Literal["delete_records", "corrupt_records", "storage_outage", "storage_flaky",
                  "corrupt_backup", "clear"]
    count: int = 10
    failure_rate: float = 0.5
    duration_s: float = 5.0
    backup_id: Optional[str] = None


# ------------------------------------------------------------------ auth
@app.post("/auth/login", tags=["auth"])
def auth_login(body: LoginIn):
    res = login(body.username, body.password)
    if not res:
        log_event("login_failed", level="WARNING", actor=body.username)
        raise HTTPException(401, "invalid credentials")
    token, role = res
    log_event("login_success", actor=body.username)
    return {"access_token": token, "token_type": "bearer", "role": role}


# ------------------------------------------------------------------ records (the protected data)
@app.post("/records", status_code=201, tags=["records"])
def create_record(body: RecordIn, user=Depends(require("operator"))):
    payload = json.dumps(body.payload, sort_keys=True)
    rid = db.execute("INSERT INTO records (name,payload,checksum,updated_at) VALUES (?,?,?,?)",
                     (body.name, payload, db.record_checksum(body.name, payload), db.now_iso()))
    return db.query("SELECT * FROM records WHERE id=?", (rid,), one=True)


@app.get("/records", tags=["records"])
def list_records(limit: int = 50, offset: int = 0, user=Depends(current_user)):
    total = db.query("SELECT COUNT(*) n FROM records", one=True)["n"]
    rows = db.query("SELECT * FROM records ORDER BY id LIMIT ? OFFSET ?", (min(limit, 500), offset))
    return {"total": total, "items": rows}


@app.get("/records/{rid}", tags=["records"])
def get_record(rid: int, user=Depends(current_user)):
    r = db.query("SELECT * FROM records WHERE id=?", (rid,), one=True)
    if not r:
        raise HTTPException(404, "record not found")
    r["integrity_ok"] = db.record_checksum(r["name"], r["payload"]) == r["checksum"]
    return r


# ------------------------------------------------------------------ backups
@app.post("/backups", status_code=201, tags=["backups"])
def create_backup(user=Depends(require("operator")), idempotency_key: Optional[str] = Header(None)):
    try:
        b, replay = svc.create_backup(user["username"], idempotency_key)
    except svc.BackupError as e:
        raise HTTPException(503, f"backup storage unavailable: {e}")
    return {**b, "idempotent_replay": replay}


@app.get("/backups", tags=["backups"])
def list_backups(user=Depends(current_user)):
    return db.query("SELECT * FROM backups ORDER BY created_at DESC")


@app.get("/backups/{backup_id}", tags=["backups"])
def get_backup(backup_id: str, user=Depends(current_user)):
    b = db.query("SELECT * FROM backups WHERE id=?", (backup_id,), one=True)
    if not b:
        raise HTTPException(404, "backup not found")
    return b


@app.post("/backups/{backup_id}/verify", tags=["backups"])
def verify(backup_id: str, user=Depends(require("operator"))):
    try:
        rep = svc.verify_backup(backup_id, user["username"])
    except Exception as e:
        raise HTTPException(503, f"verification could not reach storage: {e}")
    if rep is None:
        raise HTTPException(404, "backup not found")
    return rep


# ------------------------------------------------------------------ restore (queued jobs)
@app.post("/restores", status_code=202, tags=["restore"])
def start_restore(body: RestoreIn, user=Depends(require("operator")),
                  idempotency_key: Optional[str] = Header(None)):
    if not db.query("SELECT id FROM backups WHERE id=?", (body.backup_id,), one=True):
        raise HTTPException(404, "backup not found")
    job, replay = svc.enqueue_restore(body.backup_id, body.mode, user["username"], idempotency_key,
                                      body.fallback_to_previous)
    return {**job, "idempotent_replay": replay}


@app.get("/restores/{job_id}", tags=["restore"])
def restore_status(job_id: str, user=Depends(current_user)):
    j = db.query("SELECT * FROM restore_jobs WHERE id=?", (job_id,), one=True)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.get("/restores", tags=["restore"])
def list_restores(user=Depends(current_user)):
    return db.query("SELECT * FROM restore_jobs ORDER BY queued_at DESC")


# ------------------------------------------------------------------ failure injection
@app.post("/chaos", tags=["chaos"])
def chaos(body: ChaosIn, user=Depends(require("admin"))):
    out = {"type": body.type}
    if body.type == "delete_records":
        ids = [r["id"] for r in db.query("SELECT id FROM records")]
        victims = random.sample(ids, min(body.count, len(ids)))
        for i in victims:
            db.execute("DELETE FROM records WHERE id=?", (i,))
        out["deleted_ids"] = sorted(victims)
    elif body.type == "corrupt_records":
        ids = [r["id"] for r in db.query("SELECT id FROM records")]
        victims = random.sample(ids, min(body.count, len(ids)))
        for i in victims:  # change data but NOT its checksum = silent corruption / bit rot
            db.execute("UPDATE records SET payload=? WHERE id=?", ('{"corrupted": "%s"}' % random.random(), i))
        out["corrupted_ids"] = sorted(victims)
    elif body.type == "storage_outage":
        store.faults.down = True
        threading.Timer(body.duration_s, lambda: setattr(store.faults, "down", False)).start()
        out["outage_seconds"] = body.duration_s
    elif body.type == "storage_flaky":
        store.faults.failure_rate = body.failure_rate
        threading.Timer(body.duration_s, lambda: setattr(store.faults, "failure_rate", 0.0)).start()
        out.update(failure_rate=body.failure_rate, seconds=body.duration_s)
    elif body.type == "corrupt_backup":
        b = db.query("SELECT * FROM backups WHERE id=?", (body.backup_id,), one=True)
        if not b:
            raise HTTPException(404, "backup not found")
        path = store.inner.raw_path(b["object_key"])
        data = bytearray(open(path, "rb").read())
        for _ in range(5):
            data[random.randrange(len(data))] ^= 0xFF  # flip bytes on "disk"
        open(path, "wb").write(bytes(data))
        out["backup_id"] = b["id"]
    elif body.type == "clear":
        store.faults.down, store.faults.failure_rate = False, 0.0
    metrics.inc(f"chaos_{body.type}_total")
    log_event("failure_injected", level="WARNING", actor=user["username"], **out)
    return out


# ------------------------------------------------------------------ observability
@app.get("/metrics", tags=["observability"])
def get_metrics(user=Depends(current_user)):
    snap = metrics.snapshot()
    snap["circuit_breaker"] = store.breaker.state
    snap["queue_depth"] = svc.restore_queue.qsize()
    jobs = db.query("SELECT recovery_time_ms FROM restore_jobs WHERE status='COMPLETED'")
    snap["completed_restores"] = len(jobs)
    return snap


@app.get("/logs", tags=["observability"])
def get_logs(limit: int = 100, level: Optional[str] = None, event: Optional[str] = None,
             user=Depends(require("admin"))):
    sql, p = "SELECT * FROM events WHERE 1=1", []
    if level:
        sql += " AND level=?"; p.append(level.upper())
    if event:
        sql += " AND event=?"; p.append(event)
    sql += " ORDER BY id DESC LIMIT ?"; p.append(min(limit, 1000))
    return db.query(sql, tuple(p))


@app.get("/health", tags=["observability"])
def health():
    degraded = store.breaker.state != "CLOSED" or store.faults.down
    return {"status": "degraded" if degraded else "ok", "database": "ok",
            "object_storage": "unavailable" if store.faults.down else "ok",
            "circuit_breaker": store.breaker.state, "queue_depth": svc.restore_queue.qsize()}
