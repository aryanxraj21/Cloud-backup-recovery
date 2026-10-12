"""Core logic: create backups, verify them, and restore through a job queue."""
import hashlib
import json
import queue
import threading
import time
import uuid

from . import db
from .observability import log_event, metrics
from .resilience import CircuitOpenError
from .storage import StorageUnavailable, store


class BackupError(Exception):
    pass


# ---------------------------------------------------------------- backups
def create_backup(actor, idempotency_key=None):
    # Idempotency: the same key always returns the same backup (safe client retries)
    if idempotency_key:
        existing = db.query("SELECT * FROM backups WHERE idempotency_key=?", (idempotency_key,), one=True)
        if existing:
            metrics.inc("idempotent_replays_total")
            log_event("backup_idempotent_replay", actor=actor, backup_id=existing["id"])
            return existing, True

    t0 = time.perf_counter()
    backup_id = "bkp_" + time.strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
    records = db.query("SELECT id,name,payload,checksum,updated_at FROM records ORDER BY id")
    manifest = {"backup_id": backup_id, "created_at": db.now_iso(), "record_count": len(records),
                "records": records}
    blob = json.dumps(manifest, sort_keys=True).encode()
    manifest_checksum = hashlib.sha256(blob).hexdigest()
    key = f"backups/{backup_id}.json"
    try:
        _, retries = store.put(key, blob)
    except (StorageUnavailable, CircuitOpenError) as e:
        metrics.inc("backups_failed_total")
        log_event("backup_failed", level="ERROR", actor=actor, backup_id=backup_id, error=str(e))
        raise BackupError(str(e))
    duration = (time.perf_counter() - t0) * 1000
    db.execute("""INSERT INTO backups (id,created_at,created_by,status,record_count,size_bytes,object_key,
                  manifest_checksum,idempotency_key,duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?)""",
               (backup_id, manifest["created_at"], actor, "COMPLETED", len(records), len(blob), key,
                manifest_checksum, idempotency_key, duration))
    metrics.inc("backups_created_total")
    metrics.observe("backup_duration", duration)
    log_event("backup_created", actor=actor, backup_id=backup_id, records=len(records),
              size_bytes=len(blob), duration_ms=round(duration, 2), storage_retries=retries)
    return db.query("SELECT * FROM backups WHERE id=?", (backup_id,), one=True), False


def load_backup(backup):
    """Download a backup and prove it is intact (sha256 must match what we stored)."""
    blob, retries = store.get(backup["object_key"])
    if hashlib.sha256(blob).hexdigest() != backup["manifest_checksum"]:
        raise BackupError(f"backup {backup['id']} failed integrity check (checksum mismatch)")
    return json.loads(blob), retries


def verify_backup(backup_id, actor):
    """Compare a backup with the live database and report damage."""
    t0 = time.perf_counter()
    backup = db.query("SELECT * FROM backups WHERE id=?", (backup_id,), one=True)
    if not backup:
        return None
    report = {"backup_id": backup_id, "backup_integrity": "OK"}
    try:
        manifest, _ = load_backup(backup)
    except BackupError as e:
        report.update(backup_integrity="CORRUPTED", error=str(e), status="BACKUP_CORRUPTED")
        db.execute("UPDATE backups SET last_verified_at=?, last_verify_status=? WHERE id=?",
                   (db.now_iso(), "BACKUP_CORRUPTED", backup_id))
        metrics.inc("backup_corruption_detected_total")
        log_event("backup_corruption_detected", level="ERROR", actor=actor, backup_id=backup_id)
        return report

    live = {r["id"]: r for r in db.query("SELECT * FROM records")}
    missing, corrupted, changed = [], [], []
    for rec in manifest["records"]:
        cur = live.get(rec["id"])
        if cur is None:
            missing.append(rec["id"])
        elif db.record_checksum(cur["name"], cur["payload"]) != cur["checksum"]:
            corrupted.append(rec["id"])            # data no longer matches its own checksum
        elif cur["checksum"] != rec["checksum"]:
            changed.append(rec["id"])              # legit update after the backup
    backup_ids = {r["id"] for r in manifest["records"]}
    new_since = [i for i in live if i not in backup_ids]
    status = "HEALTHY" if not (missing or corrupted) else "DAMAGE_DETECTED"
    report.update(status=status, records_in_backup=len(backup_ids), live_records=len(live),
                  missing=missing, corrupted=corrupted, changed_since_backup=len(changed),
                  new_since_backup=len(new_since),
                  verify_ms=round((time.perf_counter() - t0) * 1000, 2))
    db.execute("UPDATE backups SET last_verified_at=?, last_verify_status=? WHERE id=?",
               (db.now_iso(), status, backup_id))
    metrics.inc("verifications_total")
    metrics.observe("verify_duration", report["verify_ms"])
    if status != "HEALTHY":
        metrics.inc("damage_detected_total")
        log_event("damage_detected", level="WARNING", actor=actor, backup_id=backup_id,
                  missing=len(missing), corrupted=len(corrupted))
    else:
        log_event("verification_passed", actor=actor, backup_id=backup_id)
    return report


def integrity_scan():
    """Post-restore check: every live record must match its own checksum."""
    bad = [r["id"] for r in db.query("SELECT * FROM records")
           if db.record_checksum(r["name"], r["payload"]) != r["checksum"]]
    return bad


# ---------------------------------------------------------------- restore queue
restore_queue: "queue.Queue[str]" = queue.Queue()
MAX_JOB_ATTEMPTS = 6


def enqueue_restore(backup_id, mode, actor, idempotency_key=None, fallback=True):
    if idempotency_key:
        existing = db.query("SELECT * FROM restore_jobs WHERE idempotency_key=?", (idempotency_key,), one=True)
        if existing:
            metrics.inc("idempotent_replays_total")
            return existing, True
    job_id = "rst_" + uuid.uuid4().hex[:10]
    db.execute("""INSERT INTO restore_jobs (id,backup_id,mode,fallback,status,requested_by,idempotency_key,queued_at)
                  VALUES (?,?,?,?,?,?,?,?)""",
               (job_id, backup_id, mode, int(fallback), "QUEUED", actor, idempotency_key, db.now_iso()))
    _job_clock[job_id] = time.perf_counter()
    restore_queue.put(job_id)
    metrics.inc("restore_jobs_total")
    log_event("restore_queued", actor=actor, job_id=job_id, backup_id=backup_id, mode=mode)
    return db.query("SELECT * FROM restore_jobs WHERE id=?", (job_id,), one=True), False


_job_clock = {}


def _pick_backup(job):
    """Use the requested backup; if it is corrupted, FAIL OVER to the newest older intact one."""
    fallback = bool(job["fallback"])
    req = db.query("SELECT * FROM backups WHERE id=?", (job["backup_id"],), one=True)
    candidates = [req] + (db.query("SELECT * FROM backups WHERE created_at < ? AND status='COMPLETED' "
                                   "ORDER BY created_at DESC", (req["created_at"],)) if fallback else [])
    total_retries, last_err = 0, None
    for b in candidates:
        try:
            manifest, r = load_backup(b)
            total_retries += r
            if b["id"] != req["id"]:
                log_event("backup_failover", level="WARNING", job_id=job["id"], requested=req["id"], used=b["id"],
                          reason=str(last_err))
                metrics.inc("backup_failovers_total")
            return b, manifest, total_retries
        except BackupError as e:
            last_err = e
            log_event("backup_rejected", level="ERROR", job_id=job["id"], backup_id=b["id"], error=str(e))
    raise BackupError(f"no intact backup available: {last_err}")


def _run_job(job_id):
    job = db.query("SELECT * FROM restore_jobs WHERE id=?", (job_id,), one=True)
    attempts = job["attempts"] + 1
    db.execute("UPDATE restore_jobs SET status='RUNNING', attempts=?, started_at=COALESCE(started_at,?) WHERE id=?",
               (attempts, db.now_iso(), job_id))
    try:
        backup, manifest, retries = _pick_backup(job)
    except (StorageUnavailable, CircuitOpenError) as e:
        # Dependency down: keep the job, retry later (graceful degradation - API keeps serving)
        if attempts < MAX_JOB_ATTEMPTS:
            delay = min(8, 1.0 * 2 ** (attempts - 1))
            db.execute("UPDATE restore_jobs SET status='RETRY_SCHEDULED', error=? WHERE id=?", (str(e), job_id))
            log_event("restore_deferred", level="WARNING", job_id=job_id, attempt=attempts,
                      retry_in_s=delay, error=str(e))
            metrics.inc("restore_job_retries_total")
            threading.Timer(delay, restore_queue.put, args=(job_id,)).start()
        else:
            _fail(job_id, f"storage unavailable after {attempts} attempts: {e}")
        return
    except BackupError as e:
        _fail(job_id, str(e))
        return

    snap = {r["id"]: r for r in manifest["records"]}
    restored = 0
    # ATOMIC restore: either every repair is applied or none (no half-restored state)
    with db.transaction() as c:
        if job["mode"] == "full":
            c.execute("DELETE FROM records")
            for r in snap.values():
                c.execute("INSERT INTO records (id,name,payload,checksum,updated_at) VALUES (?,?,?,?,?)",
                          (r["id"], r["name"], r["payload"], r["checksum"], r["updated_at"]))
            restored = len(snap)
        else:  # repair: only touch missing / corrupted rows (smallest blast radius, idempotent)
            live = {r["id"]: r for r in c.execute("SELECT * FROM records").fetchall()}
            for rid, r in snap.items():
                cur = live.get(rid)
                bad = cur is None or db.record_checksum(cur["name"], cur["payload"]) != cur["checksum"]
                if bad:
                    c.execute("INSERT OR REPLACE INTO records (id,name,payload,checksum,updated_at) VALUES (?,?,?,?,?)",
                              (r["id"], r["name"], r["payload"], r["checksum"], r["updated_at"]))
                    restored += 1

    still_bad = integrity_scan()
    live_ids = {r["id"] for r in db.query("SELECT id FROM records")}
    still_missing = [i for i in snap if i not in live_ids]
    verified = not still_bad and not still_missing
    rto = (time.perf_counter() - _job_clock.pop(job_id, time.perf_counter())) * 1000
    db.execute("""UPDATE restore_jobs SET status=?, finished_at=?, recovery_time_ms=?, used_backup_id=?,
                  storage_retries=storage_retries+?, records_restored=?, verified=?, error=NULL WHERE id=?""",
               ("COMPLETED" if verified else "FAILED", db.now_iso(), rto, backup["id"], retries,
                restored, int(verified), job_id))
    metrics.observe("recovery_time", rto)
    metrics.inc("restore_success_total" if verified else "restore_failed_total")
    log_event("restore_completed", job_id=job_id, backup_id=backup["id"], mode=job["mode"],
              records_restored=restored, recovery_time_ms=round(rto, 2), verified=verified,
              storage_retries=retries, attempts=attempts)


def _fail(job_id, msg):
    db.execute("UPDATE restore_jobs SET status='FAILED', finished_at=?, error=? WHERE id=?",
               (db.now_iso(), msg, job_id))
    _job_clock.pop(job_id, None)
    metrics.inc("restore_failed_total")
    log_event("restore_failed", level="ERROR", job_id=job_id, error=msg)


def worker_loop():
    while True:
        job_id = restore_queue.get()
        try:
            _run_job(job_id)
        except Exception as e:  # never let the worker die
            _fail(job_id, f"unexpected: {e}")


def start_worker():
    threading.Thread(target=worker_loop, daemon=True, name="restore-worker").start()
