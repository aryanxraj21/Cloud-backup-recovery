"""Builds the final project report PDF from results/ and docs/."""
import json
import subprocess
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

OUT = "results/P26_Cloud_Backup_Verification_and_Recovery_System_Report.pdf"
fe = json.load(open("results/failure_experiment.json"))
lt = json.load(open("results/load_test.json"))
api = json.load(open("results/api_samples.json"))
gitlog = subprocess.run(["git", "log", "--reverse", "--format=%h|%ad|%s", "--date=short"],
                        capture_output=True, text=True).stdout.strip().splitlines()

ss = getSampleStyleSheet()
NAVY = colors.HexColor("#1d3357")
BLUE = colors.HexColor("#355c9a")
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=15, textColor=NAVY, spaceBefore=10, spaceAfter=6)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=12, textColor=BLUE, spaceBefore=8, spaceAfter=4)
B = ParagraphStyle("B", parent=ss["BodyText"], fontSize=10, leading=14, spaceAfter=5)
SM = ParagraphStyle("SM", parent=B, fontSize=8.5, leading=11)
CELL = ParagraphStyle("CELL", parent=B, fontSize=8.5, leading=10.5, spaceAfter=0)
CODE = ParagraphStyle("CODE", parent=ss["Code"], fontSize=7.2, leading=9, backColor=colors.HexColor("#f4f6f9"),
                      borderPadding=5, leftIndent=4, rightIndent=4)
CAP = ParagraphStyle("CAP", parent=B, fontSize=8.5, textColor=colors.grey, alignment=TA_CENTER)

story = []
P = lambda t, s=B: story.append(Paragraph(t, s))


def bullets(items):
    for it in items:
        story.append(Paragraph("&bull;&nbsp; " + it, ParagraphStyle("bl", parent=B, leftIndent=12, spaceAfter=2)))


def table(rows, widths, header=True):
    data = [[Paragraph(str(c) if isinstance(c, str) and c.startswith("<") else escape(str(c)), CELL) for c in r]
            for r in rows]
    t = Table(data, colWidths=[w * cm for w in widths], repeatRows=1 if header else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c5cedb")),
          ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fb")]),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e3eaf5"))]
        data[0] = [Paragraph("<b>%s</b>" % escape(str(c)), CELL) for c in rows[0]]
        t = Table(data, colWidths=[w * cm for w in widths], repeatRows=1)
    t.setStyle(TableStyle(st))
    story.append(t)
    story.append(Spacer(1, 6))


def img(path, width_cm, caption):
    from PIL import Image as PI
    w, h = PI.open(path).size
    story.append(KeepTogether([Image(path, width=width_cm * cm, height=width_cm * cm * h / w),
                               Paragraph(caption, CAP), Spacer(1, 6)]))


def code(text):
    story.append(Preformatted(text, CODE))
    story.append(Spacer(1, 6))


# ------------------------------------------------------------------ title page
story += [Spacer(1, 3 * cm),
          Paragraph("Cloud Application and Development", ParagraphStyle("t0", parent=B, fontSize=13,
                                                                        alignment=TA_CENTER, textColor=BLUE)),
          Spacer(1, 8),
          Paragraph("Cloud Backup Verification and Recovery System",
                    ParagraphStyle("t1", parent=H1, fontSize=24, leading=30, alignment=TA_CENTER)),
          Paragraph("Final Project Report &middot; Project ID P26 &middot; Category: Fault Tolerance &amp; Resilience",
                    ParagraphStyle("t2", parent=B, alignment=TA_CENTER, textColor=colors.grey)),
          Spacer(1, 2 * cm)]
tp = Table([["Student name", "Aryan Raj"], ["SAP / Roll number", "500126035"], ["Section", "B4"],
            ["Email", "aryanxraj21@gmail.com"], ["Project type", "Individual project"],
            ["Submitted to", "Faculty, Cloud Application and Development"], ["Date", "12 October 2026"],
            ["Source code", "Git repository: cloud-backup-recovery (%d commits)" % len(gitlog)]],
           colWidths=[5 * cm, 9 * cm])
tp.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c5cedb")),
                        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#e3eaf5")),
                        ("FONTSIZE", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
story += [tp, PageBreak()]

# ------------------------------------------------------------------ 1 abstract
A = fe["scenario_A"]; C = fe["scenario_C"]; D = fe["scenario_D"]
P("1. Abstract", H1)
P("This project builds a cloud service that protects application data with <b>verifiable backups</b> and "
  "<b>measured recovery</b>. Every record is stored with a SHA-256 checksum; backups are written to object storage "
  "with a whole-file checksum. A fault-injection endpoint deliberately deletes and silently corrupts records, "
  "breaks the storage dependency (full outage or 50% transient errors) and corrupts a backup file. The system "
  "detects every injected fault, restores data through a queued, atomic, self-verifying recovery job, and "
  "uses retry with exponential backoff, a circuit breaker, idempotency keys, job re-queueing and backup failover "
  "to survive dependency failures.")
P("Key results: all %d damaged records in the largest test were detected and repaired in <b>%.0f ms</b>; "
  "a 6-second storage outage was survived with the restore job automatically resuming (RTO %.1f s) while "
  "<b>%d/%d</b> read requests kept succeeding; a corrupted backup was rejected and the restore failed over to the "
  "previous intact backup. After every scenario a SHA-256 fingerprint of the whole dataset matched the "
  "pre-failure baseline, proving no data was corrupted. Load testing at 5, 25 and 100 concurrent users "
  "processed 7,500 requests with <b>0%% errors</b>."
  % (A[-1]["damaged"], A[-1]["recovery_time_ms_server"], C["recovery_time_ms"] / 1000,
     C["reads_served_during_outage"], C["reads_served_during_outage"] + C["reads_failed_during_outage"]))

# ------------------------------------------------------------------ 2 problem
P("2. Problem Statement and Objectives", H1)
P("Backups are only useful if they can be trusted and restored quickly. Many teams discover a backup is "
  "incomplete or corrupted only during an incident. The assigned problem is to design, develop, deploy and "
  "evaluate a cloud application that creates backups, deliberately deletes or corrupts records, and demonstrates "
  "restoration while measuring recovery behaviour, showing cloud-specific behaviour rather than plain CRUD.")
bullets(["Create consistent, checksummed backups in object storage, safely retryable (idempotent).",
         "Detect deleted records, silently corrupted records and corrupted backup files.",
         "Restore atomically (no half-restored state) and verify the result automatically.",
         "Keep working when the storage dependency fails: retry, circuit breaker, queue, graceful degradation, failover.",
         "Measure detection time, recovery time (RTO), retries and success/failure counts; load test three levels.",
         "Secure everything with JWT + role-based access and keep structured logs of all events."])

# ------------------------------------------------------------------ 3 compliance matrix
P("3. Requirement Compliance Matrix", H1)
table([["#", "Requirement (from allocation)", "How it is met", "Section"],
       ["1", "Backups, delete/corrupt, restore, measure", "POST /backups, POST /chaos, POST /restores; recovery_time_ms", "6, 8"],
       ["2", "≥ 5 REST endpoints", "15 endpoints (auth, records, backups, verify, restores, chaos, metrics, logs, health)", "7"],
       ["3", "Proper data model", "5 tables: users, records, backups, restore_jobs, events", "5"],
       ["4", "Authentication / authorization", "JWT (HS256, expiry) + RBAC viewer < operator < admin", "7.2"],
       ["5", "Deployed on cloud/deployable env.", "Docker image, Render blueprint, docker-compose with S3-compatible MinIO", "12"],
       ["6", "Logs of events/failures/actions", "JSON log file + events table, GET /logs", "10"],
       ["7", "≥ 2 metrics in report", "Recovery time, detection time, retries, attempts, latency, throughput, error rate", "8, 9"],
       ["8", "Load test low/medium/high", "5 / 25 / 100 concurrent users, 7,500 requests", "9"],
       ["9", "Architecture diagram", "API, services, DB, queue, object storage, users", "4"],
       ["10", "API docs + report + screenshots", "docs/API.md, Swagger /docs, this report, Section 11", "7, 11"],
       ["11", "Deliberate failure", "Deletion, silent corruption, storage outage, 50% flaky storage, corrupted backup", "8"],
       ["12", "Recovery mechanism", "Retry+backoff, circuit breaker, queue, idempotency, backup, failover, graceful degradation", "6.3"],
       ["13", "Data not corrupted after failure", "Dataset SHA-256 fingerprint identical to baseline after every scenario", "8.5"],
       ["14", "Failure + recovery in logs", "failure_injected, damage_detected, retry, circuit_state_change, restore_deferred, backup_failover, restore_completed", "10"],
       ["15", "Measure recovery time / retries / counts", "recovery_time_ms, storage_retries, attempts, http_success/failed_total", "8"],
       ["C9", "Git repo ≥ 8 commits", "%d meaningful commits" % len(gitlog), "13"]],
      [0.8, 4.2, 9.2, 1.6])

# ------------------------------------------------------------------ 4 architecture
story.append(PageBreak())
P("4. Cloud Architecture", H1)
img("docs/architecture.png", 13.5, "Figure 1 – Cloud architecture: users, API, services, queue, database, object storage, observability")
P("Cloud / architectural components used (requirement: at least 3):", H2)
table([["Component", "Role in the system", "Cloud equivalent"],
       ["Containerised REST API", "Stateless FastAPI service, JWT auth, request metrics", "Render web service / AWS ECS / Cloud Run"],
       ["Object storage", "Durable backup files backups/<id>.json, written atomically", "AWS S3 / MinIO (S3 API); local bucket in demo"],
       ["SQL database", "Records, backup catalogue, jobs, users, events", "SQLite file on volume; Amazon RDS / Cloud SQL in production"],
       ["Job queue + worker", "Asynchronous restores, re-queue with backoff", "In-process queue; SQS / Cloud Tasks in production"],
       ["Circuit breaker + retry", "Protects against storage failures, fails fast when open", "Resilience pattern (like AWS SDK retries / Envoy)"],
       ["Observability", "JSON logs, events table, /metrics, /health", "CloudWatch / Cloud Logging, health checks"]],
      [3.6, 6.6, 5.6])

# ------------------------------------------------------------------ 5 data model
P("5. Technology Choices and Data Model", H1)
P("<b>Stack:</b> Python 3.12, FastAPI (auto OpenAPI docs), SQLite (WAL mode), PyJWT, boto3 for S3, httpx for load "
  "testing, Docker. <b>Why SQL:</b> the entities are relational (a restore job references a backup; events reference "
  "jobs), and a restore must be <b>atomic</b>: either every repaired row is written or none. A transactional SQL "
  "database gives BEGIN/COMMIT/ROLLBACK for free, which NoSQL stores without multi-document transactions do not. "
  "Backup files themselves are large, immutable blobs, so they go to <b>object storage</b>, which is cheaper, more "
  "durable and decoupled from the database it protects (a backup on the same disk as the data is not a backup).")
table([["Entity", "Key fields", "Purpose"],
       ["users", "id, username, password_hash (PBKDF2), role", "Login and RBAC"],
       ["records", "id, name, payload (JSON), checksum (SHA-256), updated_at", "The protected business data"],
       ["backups", "id, created_at, created_by, status, record_count, size_bytes, object_key, manifest_checksum, idempotency_key, duration_ms, last_verify_status", "Backup catalogue; object_key points to storage"],
       ["restore_jobs", "id, backup_id (FK), used_backup_id, mode, fallback, status, attempts, storage_retries, records_restored, recovery_time_ms, verified, error", "Recovery jobs and their measurements"],
       ["events", "id, ts, level, actor, event, details (JSON)", "Audit / failure / recovery log"]],
      [2.4, 8.4, 5.0])

# ------------------------------------------------------------------ 6 design
P("6. How the System Works", H1)
P("6.1 Backup", H2)
P("A backup reads all records, writes a JSON manifest (records + their checksums) to object storage via an "
  "atomic write (temp file then rename) and stores the SHA-256 of the whole file in the catalogue. An "
  "<i>Idempotency-Key</i> header guarantees that a client retrying the same request gets the same backup instead of a "
  "duplicate (verified in the experiment: two calls returned <b>%s</b> both times)." % fe["idempotency"]["first_id"])
P("6.2 Verification (detection)", H2)
P("Verify first re-downloads the backup and checks its SHA-256 (detects a corrupted backup). Then for every record in "
  "the backup it checks the live DB: <b>missing</b> (deleted), <b>corrupted</b> (data no longer matches its own "
  "checksum = silent corruption / bit rot), or legitimately <b>changed</b> since the backup.")
P("6.3 Recovery and failure handling", H2)
table([["Mechanism", "Where", "What it does"],
       ["Queue", "POST /restores → worker", "Restore runs asynchronously; API returns 202 immediately and stays responsive"],
       ["Retry + exponential backoff + jitter", "storage get/put", "Up to 4 tries, 0.1→0.2→0.4 s (+jitter) for transient 503s"],
       ["Circuit breaker", "storage client", "Opens after 3 consecutive failures, fails fast for 3 s, then half-open probe"],
       ["Job re-queue", "worker", "If storage is down, job → RETRY_SCHEDULED and re-runs after 1, 2, 4, 8 s (max 6 attempts)"],
       ["Atomic transaction", "worker", "All repairs in one DB transaction; error = rollback, never half-restored"],
       ["Repair mode (idempotent)", "worker", "Only rewrites missing/corrupted rows; running it twice is harmless"],
       ["Backup failover", "worker", "If the chosen backup fails its checksum, use the newest older intact backup"],
       ["Graceful degradation", "API", "Reads/writes keep working while storage is down; /health reports 'degraded'"],
       ["Post-restore verification", "worker", "Full integrity scan; job is COMPLETED only if verified = true"]],
      [4.0, 3.4, 8.4])

# ------------------------------------------------------------------ 7 API
P("7. REST API and Security", H1)
P("7.1 Endpoints", H2)
table([["Method & path", "Role", "Description"],
       ["POST /auth/login", "public", "Returns JWT"], ["POST /records", "operator", "Create record (checksum stored)"],
       ["GET /records, GET /records/{id}", "viewer", "List / get with integrity flag"],
       ["POST /backups", "operator", "Create backup (Idempotency-Key)"],
       ["GET /backups, GET /backups/{id}", "viewer", "Backup catalogue"],
       ["POST /backups/{id}/verify", "operator", "Detect missing / corrupted records and backup corruption"],
       ["POST /restores, GET /restores/{id}, GET /restores", "operator / viewer", "Queue restore, poll status"],
       ["POST /chaos", "admin", "Inject failures"], ["GET /metrics", "viewer", "Metrics"],
       ["GET /logs", "admin", "Event logs"], ["GET /health", "public", "Health and breaker state"]],
      [6.0, 2.6, 7.2])
P("Full request/response documentation is in <i>docs/API.md</i> and interactively at <i>/docs</i> (Swagger UI).", SM)
P("7.2 Authentication and authorization", H2)
P("Users log in with a password (stored as salted PBKDF2-SHA256, 100k iterations) and receive a JWT signed with "
  "HS256 that expires after 1 hour. Every protected endpoint checks the token and the role rank "
  "viewer &lt; operator &lt; admin. Destructive fault injection and log access are admin-only. Tested: a request "
  "without a token returns 401 and a viewer calling /chaos returns 403 (Section 11).")

# ------------------------------------------------------------------ 8 failure experiment
story.append(PageBreak())
P("8. Failure Experiments and Results", H1)
P("All experiments are automated in <i>scripts/failure_experiment.py</i> on a dataset of 1,000 records "
  "(results/failure_experiment.json). Before the faults, a SHA-256 fingerprint of every (id, checksum, payload) "
  "was taken as the baseline.")
P("8.1 Scenario A – deleted and silently corrupted records", H2)
table([["Damaged", "Missing found", "Corrupted found", "Detect (ms)", "Recovery (ms)", "Restored", "Verified", "Identical to baseline"]] +
      [[r["damaged"], r["missing_detected"], r["corrupted_detected"], r["detect_ms"], r["recovery_time_ms_server"],
        r["records_restored"], r["verified"], r["dataset_identical_to_baseline"]] for r in A],
      [1.6, 1.9, 2.1, 1.8, 2.0, 1.7, 1.6, 3.1])
img("results/chart_recovery.png", 13, "Figure 2 – Detection and recovery time vs number of damaged records")
P("Detection was 100% accurate at every size. Recovery time stays roughly flat (~25–35 ms) because the cost is "
  "dominated by downloading and checksumming the backup, not by the number of rows repaired.")

B_ = fe["scenario_B"]
P("8.2 Scenario B – flaky storage (50% transient failures)", H2)
table([["Job", "Status", "Job attempts", "Storage retries", "Recovery time (ms)"]] +
      [[i + 1, r["status"], r["attempts"], r["storage_retries"], r["recovery_time_ms"]] for i, r in enumerate(B_["runs"])],
      [2, 3, 3, 3, 4])
img("results/chart_flaky.png", 12, "Figure 3 – Restores while half of all storage calls fail")
P("All 5 jobs completed and verified. Three jobs succeeded after one in-call retry (~100–135 ms). In two jobs several "
  "consecutive failures opened the circuit breaker; instead of failing, the job was re-queued and succeeded on "
  "attempt 3 and 4 (3.6 s and 7.7 s). Dataset identical to baseline: <b>%s</b>." % B_["dataset_identical_to_baseline"])

P("8.3 Scenario C – full storage outage for 6 seconds (mandatory demo failure)", H2)
table([["Measure", "Result"],
       ["Restore job state history", " → ".join(C["job_status_history"])],
       ["Job attempts until success", C["attempts"]],
       ["Recovery time (queued → verified)", "%.2f s" % (C["recovery_time_ms"] / 1000)],
       ["Circuit breaker states observed", ", ".join(C["breaker_states_seen"])],
       ["/health states observed", ", ".join(C["health_states_seen"])],
       ["Read requests during outage", "%d succeeded / %d failed" % (C["reads_served_during_outage"], C["reads_failed_during_outage"])],
       ["Dataset identical to baseline", C["dataset_identical_to_baseline"]]], [7, 8.8])
P("The breaker opened after repeated failures, so the worker stopped hammering the dead dependency. The job was "
  "kept in the queue (RETRY_SCHEDULED) with backoff, and resumed automatically once storage returned. Users never "
  "saw an error: the API degraded gracefully (health = degraded) but kept serving data.")

P("8.4 Scenario D – corrupted backup file → failover", H2)
table([["Measure", "Result"], ["Requested backup", D["requested_backup"]], ["Verify result", D["verify_result"]],
       ["Backup actually used", D["used_backup"]], ["Job status", D["status"]],
       ["Recovery time (ms)", D["recovery_time_ms"]], ["Dataset identical to baseline", D["dataset_identical_to_baseline"]]],
      [7, 8.8])
P("Bytes were flipped inside the newest backup file. The checksum check rejected it (a restore from it would have "
  "written garbage), and the worker failed over to the previous intact backup.")
P("8.5 Proof that data is not corrupted after failures", H2)
P("After every scenario the full dataset fingerprint was recomputed and compared to the baseline: "
  "<b>identical in 100% of runs</b> (A×4, B, C, D). Additionally each restore job ends with an integrity scan of every "
  "record (checksum recomputation) and only reports COMPLETED with verified = 1 if zero records are missing or "
  "corrupted. The restore itself runs in a single transaction, so a crash mid-restore rolls back completely.")

# ------------------------------------------------------------------ metrics
m = fe["final_metrics"]; cnt = m["counters"]; tm = m["timings_ms"]
P("8.6 Collected metrics (from GET /metrics at the end of the experiment)", H2)
table([["Metric", "Value"],
       ["Recovery time (all restores)", "avg %.1f ms, p50 %.1f ms, max %.1f ms (n=%d)" % (
           tm["recovery_time"]["avg"], tm["recovery_time"]["p50"], tm["recovery_time"]["max"], tm["recovery_time"]["count"])],
       ["Backup creation time", "avg %.1f ms" % tm["backup_duration"]["avg"]],
       ["Verification time", "avg %.1f ms" % tm["verify_duration"]["avg"]],
       ["Restores successful / failed", "%d / %d" % (cnt.get("restore_success_total", 0), cnt.get("restore_failed_total", 0))],
       ["Storage retries (in-call)", cnt.get("storage_retries_total", 0)],
       ["Restore job re-queues", cnt.get("restore_job_retries_total", 0)],
       ["Circuit opened / rejected calls", "%d / %d" % (cnt.get("circuit_open_total", 0), cnt.get("circuit_rejected_total", 0))],
       ["Backup failovers", cnt.get("backup_failovers_total", 0)],
       ["Idempotent replays", cnt.get("idempotent_replays_total", 0)],
       ["HTTP successful / failed requests", "%d / %d" % (cnt.get("http_success_total", 0), cnt.get("http_failed_total", 0))]],
      [7, 8.8])

# ------------------------------------------------------------------ 9 load test
story.append(PageBreak())
P("9. Load Testing", H1)
P("<i>scripts/load_test.py</i> sends a realistic mix (60% list records, 15% get record, 20% create record, 5% list "
  "backups) with an async client at three workload levels against a single API instance (1 worker, local).")
table([["Level", "Users", "Requests", "Throughput (req/s)", "Avg (ms)", "p50", "p95", "p99", "Errors"]] +
      [[r["level"], r["concurrent_users"], r["requests"], r["throughput_rps"], r["avg_ms"], r["p50_ms"], r["p95_ms"],
        r["p99_ms"], "%d (%.1f%%)" % (r["errors"], r["error_rate_pct"])] for r in lt],
      [1.6, 1.3, 1.7, 2.6, 1.6, 1.4, 1.6, 1.6, 2.2])
img("results/chart_load.png", 13, "Figure 4 – Latency percentiles and throughput at low / medium / high load")
P("<b>Analysis.</b> No request failed at any level. Latency grows with concurrency (p95 %.0f → %.0f → %.0f ms) and "
  "throughput drops from %.0f to %.0f req/s at 100 users because a single process serialises writes to the SQLite "
  "file and the event loop is shared by all users. This is the expected bottleneck; the API is stateless, so in the "
  "cloud it scales horizontally (more containers behind the load balancer) once the database is moved to a managed "
  "SQL service that handles concurrent writers."
  % (lt[0]["p95_ms"], lt[1]["p95_ms"], lt[2]["p95_ms"], lt[0]["throughput_rps"], lt[2]["throughput_rps"]))

# ------------------------------------------------------------------ 10 logs
P("10. Logging", H1)
P("Every important action is written as one JSON line to <i>logs/app.log</i> and to the <i>events</i> table "
  "(queried by GET /logs). Excerpt of warning/error events recorded during the experiment:")
lines = []
for e in fe["failure_logs"][:22][::-1]:
    d = e["details"]
    lines.append("%s %-7s %-22s %s" % (e["ts"][11:23], e["level"], e["event"], d[:78] + ("…" if len(d) > 78 else "")))
code("\n".join(lines))

# ------------------------------------------------------------------ 11 api screenshots
P("11. API Execution Output (captured responses)", H1)
P("The following request/response pairs were captured from the running service during the demo flow "
  "(results/api_samples.json). Long fields are shortened.", SM)


def short(v):
    if isinstance(v, str) and len(v) > 48:
        return v[:45] + "..."
    if isinstance(v, list) and len(v) > 8:
        return v[:8] + ["..."]
    if isinstance(v, dict):
        return {k: short(x) for k, x in list(v.items())[:14]}
    return v


for s in api:
    body = s["body"]
    if isinstance(body, dict) and "timings_ms" in body:
        body = {"counters": dict(list(body["counters"].items())[:10]), "circuit_breaker": body["circuit_breaker"],
                "queue_depth": body["queue_depth"], "timings_ms": {"recovery_time": body["timings_ms"].get("recovery_time")}}
    txt = json.dumps(short(body), indent=1)
    story.append(KeepTogether([Paragraph("<b>%s</b> &nbsp; <font color='#355c9a'>%s</font> &rarr; HTTP %d"
                                         % (escape(s["label"]), escape(s["req"]), s["status"]), SM),
                               Preformatted(txt[:1400], CODE), Spacer(1, 5)]))

# ------------------------------------------------------------------ 12 deployment
P("12. Deployment", H1)
P("The service is packaged as a Docker image (<i>Dockerfile</i>, health check on /health, config via environment "
  "variables, data on a mounted volume). Two deployment paths are provided:")
bullets(["<b>Render (cloud):</b> <i>render.yaml</i> blueprint – a web service built from the Dockerfile, auto-generated "
         "JWT secret, health-check path /health. Deploy: push repo to GitHub → Render → New Blueprint.",
         "<b>docker compose:</b> API container + MinIO (S3-compatible object storage) – the backup files go to a real "
         "S3 API bucket, the same code works against AWS S3 by setting BACKUP_BUCKET and AWS credentials.",
         "Secrets (JWT_SECRET, user passwords) come from environment variables, never from the code."])

# ------------------------------------------------------------------ 13 git
P("13. Source Code and Git History", H1)
table([["Commit", "Date", "Message"]] + [l.split("|", 2) for l in gitlog], [2, 2.4, 11.4])

# ------------------------------------------------------------------ 14 limitations
P("14. Limitations and Future Work", H1)
bullets(["SQLite allows one writer at a time; move to managed PostgreSQL (RDS / Cloud SQL) for multi-instance scaling.",
         "The job queue is in-process; a managed queue (SQS) would survive container restarts.",
         "Backups are full snapshots; incremental backups and scheduled backups (cron) would reduce cost.",
         "Add backup encryption at rest (S3 SSE / KMS) and cross-region replication for disaster recovery.",
         "Export metrics to Prometheus/CloudWatch with alerting on damage_detected and circuit_open events."])

P("15. Conclusion", H1)
P("The system meets every project-specific and common requirement: it creates verifiable backups, deliberately "
  "deletes and corrupts data and breaks its own storage dependency, detects every fault, and recovers using retry, "
  "circuit breaker, queueing, idempotency, failover and graceful degradation. Recovery is measured (tens of "
  "milliseconds for data damage, ~%.1f s through a 6 s outage), logged, and proven correct by dataset fingerprints "
  "identical to the pre-failure baseline." % (C["recovery_time_ms"] / 1000))


def footer(c, d):
    c.saveState()
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.grey)
    c.drawString(2 * cm, 1.2 * cm, "P26 – Cloud Backup Verification and Recovery System – Aryan Raj (500126035)")
    c.drawRightString(A4[0] - 2 * cm, 1.2 * cm, "Page %d" % d.page)
    c.restoreState()


doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                        bottomMargin=1.8 * cm, title="Cloud Backup Verification and Recovery System – P26",
                        author="Aryan Raj")
doc.build(story, onFirstPage=lambda c, d: None, onLaterPages=footer)
print(OUT)
