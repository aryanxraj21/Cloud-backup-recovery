"""Failure-injection experiment (this is also the live demo script).

Usage: python scripts/failure_experiment.py [--base http://localhost:8000]
Writes results/failure_experiment.json
"""
import argparse
import hashlib
import json
import random
import time

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8000")
ap.add_argument("--records", type=int, default=1000)
args = ap.parse_args()

c = httpx.Client(base_url=args.base, timeout=30)
tok = c.post("/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
results = {}


def say(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fingerprint():
    """Hash of every (id, checksum) pair - proves the whole dataset is identical."""
    items, off = [], 0
    while True:
        page = c.get("/records", params={"limit": 500, "offset": off}, headers=H).json()
        items += [(r["id"], r["checksum"], r["payload"]) for r in page["items"]]
        off += 500
        if off >= page["total"]:
            break
    return hashlib.sha256(json.dumps(sorted(items)).encode()).hexdigest(), len(items)


def wait_job(job_id, timeout=60):
    t0 = time.time()
    seen = []
    while time.time() - t0 < timeout:
        j = c.get(f"/restores/{job_id}", headers=H).json()
        if not seen or seen[-1] != j["status"]:
            seen.append(j["status"])
        if j["status"] in ("COMPLETED", "FAILED"):
            j["status_history"] = seen
            return j
        time.sleep(0.2)
    raise TimeoutError(job_id)


def backup(key=None):
    h = dict(H)
    if key:
        h["Idempotency-Key"] = key
    return c.post("/backups", headers=h).json()


# ---------------------------------------------------------------- setup
say(f"Seeding {args.records} records ...")
existing = c.get("/records", params={"limit": 1}, headers=H).json()["total"]
for i in range(existing, args.records):
    c.post("/records", json={"name": f"customer-{i:05d}",
                             "payload": {"balance": round(random.uniform(0, 1e5), 2), "tier": random.choice("ABC"),
                                         "notes": "x" * random.randint(20, 200)}}, headers=H)

# ---------------------------------------------------------------- E: idempotency
k = f"nightly-{int(time.time())}"
b1, b2 = backup(k), backup(k)
results["idempotency"] = {"first_id": b1["id"], "second_id": b2["id"], "same_backup": b1["id"] == b2["id"],
                          "second_was_replay": b2["idempotent_replay"]}
say(f"Idempotency: two POST /backups with same key -> {b1['id']} / {b2['id']} (replay={b2['idempotent_replay']})")
base_backup = b1
good_fp, n = fingerprint()
say(f"Baseline dataset fingerprint {good_fp[:16]}... over {n} records")

# ---------------------------------------------------------------- A: delete + corrupt, sizes
results["scenario_A"] = []
for size in [10, 50, 100, 200]:
    half = size // 2
    c.post("/chaos", json={"type": "delete_records", "count": half}, headers=H)
    c.post("/chaos", json={"type": "corrupt_records", "count": size - half}, headers=H)
    t_det = time.perf_counter()
    rep = c.post(f"/backups/{base_backup['id']}/verify", headers=H).json()
    detect_ms = (time.perf_counter() - t_det) * 1000
    t0 = time.perf_counter()
    job = c.post("/restores", json={"backup_id": base_backup["id"], "mode": "repair"}, headers=H).json()
    j = wait_job(job["id"])
    e2e = (time.perf_counter() - t0) * 1000
    after = c.post(f"/backups/{base_backup['id']}/verify", headers=H).json()
    fp, _ = fingerprint()
    row = {"damaged": size, "missing_detected": len(rep["missing"]), "corrupted_detected": len(rep["corrupted"]),
           "detect_ms": round(detect_ms, 1), "recovery_time_ms_server": round(j["recovery_time_ms"], 1),
           "recovery_time_ms_client": round(e2e, 1), "records_restored": j["records_restored"],
           "verified": bool(j["verified"]), "post_verify_status": after["status"],
           "dataset_identical_to_baseline": fp == good_fp}
    results["scenario_A"].append(row)
    say(f"A[{size}] detected {row['missing_detected']} missing + {row['corrupted_detected']} corrupted; "
        f"restored {row['records_restored']} in {row['recovery_time_ms_server']} ms; identical={fp == good_fp}")

# ---------------------------------------------------------------- B: flaky storage (retry)
c.post("/chaos", json={"type": "corrupt_records", "count": 30}, headers=H)
c.post("/chaos", json={"type": "storage_flaky", "failure_rate": 0.5, "duration_s": 20}, headers=H)
runs = []
for _ in range(5):
    c.post("/chaos", json={"type": "corrupt_records", "count": 10}, headers=H)
    job = c.post("/restores", json={"backup_id": base_backup["id"]}, headers=H).json()
    runs.append(wait_job(job["id"]))
c.post("/chaos", json={"type": "clear"}, headers=H)
fp, _ = fingerprint()
results["scenario_B"] = {
    "failure_rate": 0.5,
    "runs": [{"status": r["status"], "attempts": r["attempts"], "storage_retries": r["storage_retries"],
              "recovery_time_ms": round(r["recovery_time_ms"] or 0, 1)} for r in runs],
    "dataset_identical_to_baseline": fp == good_fp}
say(f"B flaky storage 50%: retries per job {[r['storage_retries'] for r in runs]}, all completed="
    f"{all(r['status'] == 'COMPLETED' for r in runs)}")

# ---------------------------------------------------------------- C: full outage (circuit breaker + queue)
c.post("/chaos", json={"type": "corrupt_records", "count": 25}, headers=H)
c.post("/chaos", json={"type": "storage_outage", "duration_s": 6}, headers=H)
t0 = time.perf_counter()
job = c.post("/restores", json={"backup_id": base_backup["id"]}, headers=H).json()
reads_ok = reads_fail = 0
health_seen = set()
breaker_seen = set()
while time.perf_counter() - t0 < 5.5:
    r = c.get("/records", params={"limit": 20}, headers=H)
    reads_ok += r.status_code == 200
    reads_fail += r.status_code != 200
    h = c.get("/health").json()
    health_seen.add(h["status"]); breaker_seen.add(h["circuit_breaker"])
    time.sleep(0.1)
j = wait_job(job["id"])
total = (time.perf_counter() - t0) * 1000
fp, _ = fingerprint()
results["scenario_C"] = {"outage_s": 6, "job_status_history": j["status_history"], "attempts": j["attempts"],
                         "recovery_time_ms": round(j["recovery_time_ms"], 1), "client_e2e_ms": round(total, 1),
                         "reads_served_during_outage": reads_ok, "reads_failed_during_outage": reads_fail,
                         "health_states_seen": sorted(health_seen), "breaker_states_seen": sorted(breaker_seen),
                         "dataset_identical_to_baseline": fp == good_fp}
say(f"C outage 6s: job went {j['status_history']} after {j['attempts']} attempts, RTO {j['recovery_time_ms']:.0f} ms; "
    f"reads OK during outage {reads_ok}/{reads_ok + reads_fail}; breaker {sorted(breaker_seen)}")

# ---------------------------------------------------------------- D: corrupted backup -> failover
time.sleep(3.5)  # let breaker cool down
newer = backup()
c.post("/chaos", json={"type": "corrupt_backup", "backup_id": newer["id"]}, headers=H)
vr = c.post(f"/backups/{newer['id']}/verify", headers=H).json()
c.post("/chaos", json={"type": "delete_records", "count": 20}, headers=H)
job = c.post("/restores", json={"backup_id": newer["id"]}, headers=H).json()
j = wait_job(job["id"])
fp, _ = fingerprint()
results["scenario_D"] = {"requested_backup": newer["id"], "verify_result": vr.get("backup_integrity"),
                         "used_backup": j["used_backup_id"], "status": j["status"],
                         "recovery_time_ms": round(j["recovery_time_ms"] or 0, 1),
                         "dataset_identical_to_baseline": fp == good_fp}
say(f"D corrupted backup {newer['id']} -> verify says {vr.get('backup_integrity')}; restore failed over to "
    f"{j['used_backup_id']} ({j['status']})")

results["final_metrics"] = c.get("/metrics", headers=H).json()
results["failure_logs"] = [e for e in c.get("/logs", params={"limit": 1000}, headers=H).json()
                           if e["level"] in ("WARNING", "ERROR")][:40]
json.dump(results, open("results/failure_experiment.json", "w"), indent=2)
say("Saved results/failure_experiment.json")
