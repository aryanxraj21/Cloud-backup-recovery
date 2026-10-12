# API Documentation

Base URL: `http://localhost:8000` (or the deployed URL). Interactive docs: `/docs` (Swagger), `/openapi.json`.
Auth: `Authorization: Bearer <JWT>` from `/auth/login`. Roles: **viewer** (read) < **operator** (backup/verify/restore) < **admin** (chaos, logs).

| # | Method & path | Role | Purpose |
|---|---|---|---|
| 1 | `POST /auth/login` | public | Get JWT (`{"username","password"}`) |
| 2 | `POST /records` | operator | Create a protected record (checksum stored with it) |
| 3 | `GET /records?limit&offset` | viewer | List records |
| 4 | `GET /records/{id}` | viewer | One record + `integrity_ok` flag |
| 5 | `POST /backups` | operator | Snapshot all records to object storage. Header `Idempotency-Key` makes it safe to retry |
| 6 | `GET /backups` | viewer | List backups |
| 7 | `GET /backups/{id}` | viewer | Backup metadata (size, SHA-256, last verify status) |
| 8 | `POST /backups/{id}/verify` | operator | Check backup file integrity + compare with live DB → missing / corrupted IDs |
| 9 | `POST /restores` | operator | Queue a restore job `{"backup_id","mode":"repair|full","fallback_to_previous":true}` → 202 |
| 10 | `GET /restores/{job_id}` | viewer | Job status, attempts, retries, recovery_time_ms, verified |
| 11 | `GET /restores` | viewer | All restore jobs |
| 12 | `POST /chaos` | admin | Inject failure: `delete_records`, `corrupt_records`, `storage_outage`, `storage_flaky`, `corrupt_backup`, `clear` |
| 13 | `GET /metrics` | viewer | Counters + timing percentiles, breaker state, queue depth |
| 14 | `GET /logs?level&event&limit` | admin | Structured event log |
| 15 | `GET /health` | public | `ok` / `degraded`, storage + breaker state |

## Examples
```http
POST /backups
Authorization: Bearer eyJ...
Idempotency-Key: nightly-2026-10-12

201 {"id":"bkp_20261012093040_f4e74a","status":"COMPLETED","record_count":2485,
     "size_bytes":653156,"manifest_checksum":"9c1e...","idempotent_replay":false}
```
```http
POST /backups/bkp_.../verify
200 {"backup_integrity":"OK","status":"DAMAGE_DETECTED","missing":[334,609,...],"corrupted":[918,1003,...],
     "changed_since_backup":0,"new_since_backup":0,"verify_ms":21.4}
```
```http
POST /restores   {"backup_id":"bkp_...","mode":"repair"}
202 {"id":"rst_17b74f0952","status":"QUEUED",...}
GET  /restores/rst_17b74f0952
200 {"status":"COMPLETED","attempts":1,"storage_retries":0,"records_restored":10,
     "recovery_time_ms":55.3,"verified":1}
```
```http
POST /chaos  {"type":"storage_outage","duration_s":6}
200 {"type":"storage_outage","outage_seconds":6}
```

## Error codes
`401` missing/invalid/expired token · `403` role too low · `404` unknown id · `422` bad body ·
`503` object storage unavailable (backup/verify) – restores are never lost, they are re-queued.

## Restore job states
`QUEUED → RUNNING → COMPLETED` (verified) · `RUNNING → RETRY_SCHEDULED → RUNNING …` (storage down, backoff 1,2,4,8 s, max 6 attempts) · `FAILED` (no intact backup / attempts exhausted).
