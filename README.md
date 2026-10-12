# Cloud Backup Verification and Recovery System (P26)

Cloud Application and Development – individual project. Category: Fault Tolerance & Resilience.

The service protects a set of records. It takes checksummed backups into object storage, lets an admin
deliberately delete/corrupt data or break the storage dependency, detects the damage, and restores the data
through a queued, atomic, verified recovery job — measuring recovery time and retries.

## Run locally (2 minutes)
```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
# open http://localhost:8000/docs  (Swagger UI)
```
Default users (change via env): `admin/admin123`, `operator/operator123`, `viewer/viewer123`.

## Run as containers (API + S3-compatible MinIO)
```bash
docker compose up --build
```

## Deploy to the cloud (Render, free tier)
1. Push this repo to GitHub.
2. render.com → New → Blueprint → select the repo (uses `render.yaml` + `Dockerfile`).
3. Open `https://<your-app>.onrender.com/docs`.
To use AWS S3 instead of the local bucket, set `BACKUP_BUCKET` (+ AWS credentials) as env vars.

## Experiments
```bash
python scripts/failure_experiment.py     # failure injection + recovery  -> results/failure_experiment.json
python scripts/load_test.py              # low / medium / high load       -> results/load_test.json
python scripts/make_charts.py            # charts for the report
```

## Project layout
| Path | What it does |
|---|---|
| `app/main.py` | REST API (FastAPI), chaos endpoint, metrics/logs/health |
| `app/auth.py` | JWT login + role-based access (viewer < operator < admin) |
| `app/db.py` | SQL schema, transactions, checksums, password hashing |
| `app/backup_service.py` | backup, verify, restore queue + worker, failover |
| `app/resilience.py` | retry with exponential backoff, circuit breaker |
| `app/storage.py` | object storage (local bucket / S3) + fault injection |
| `app/observability.py` | JSON logs, events table, metrics |
| `docs/API.md` | API documentation |
| `docs/architecture.png` | architecture diagram |

## Demo script for the viva (≈3 min)
1. `POST /auth/login` as admin → Authorize in Swagger.
2. `POST /backups` (show Idempotency-Key replay), `GET /backups`.
3. `POST /chaos` `{"type":"corrupt_records","count":10}` and `{"type":"delete_records","count":10}`.
4. `POST /backups/{id}/verify` → shows missing + corrupted IDs.
5. `POST /chaos` `{"type":"storage_outage","duration_s":8}` then `POST /restores` → `GET /restores/{id}` shows
   `RETRY_SCHEDULED`, `/health` shows `degraded` + breaker `OPEN`, while `GET /records` still works.
6. After the outage the job becomes `COMPLETED`, `verified=1`; verify again → `HEALTHY`.
7. Show `GET /logs?level=WARNING`, `GET /metrics` (recovery_time, retries, circuit counters).
