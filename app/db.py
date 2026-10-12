"""Database layer (SQLite locally; swap DATABASE for a managed SQL DB in the cloud).

Why SQL: backups, restore jobs and records have fixed relationships
(backup -> restore_job -> events) and restores must be ATOMIC (all-or-nothing),
which a transactional relational DB gives us for free.
"""
import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "data/app.db")
_lock = threading.RLock()
_conn = None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_conn():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def query(sql, params=(), one=False):
    with _lock:
        cur = get_conn().execute(sql, params)
        rows = cur.fetchall()
    rows = [dict(r) for r in rows]
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    with _lock:
        cur = get_conn().execute(sql, params)
        return cur.lastrowid


class transaction:
    """All-or-nothing block: BEGIN ... COMMIT, ROLLBACK on any error."""

    def __enter__(self):
        _lock.acquire()
        self.c = get_conn()
        self.c.execute("BEGIN IMMEDIATE")
        return self.c

    def __exit__(self, exc_type, exc, tb):
        try:
            self.c.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            _lock.release()
        return False


def record_checksum(name, payload):
    """SHA-256 fingerprint of a record's content (used to detect silent corruption)."""
    body = json.dumps({"name": name, "payload": payload}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def hash_password(pw, salt=None):
    salt = salt or os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${h}"


def verify_password(pw, stored):
    salt, _ = stored.split("$", 1)
    return hash_password(pw, salt) == stored


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','operator','viewer'))
);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    payload TEXT NOT NULL,          -- JSON string
    checksum TEXT NOT NULL,         -- sha256 written together with the data
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backups (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    status TEXT NOT NULL,           -- COMPLETED / FAILED
    record_count INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    object_key TEXT NOT NULL,       -- location in object storage
    manifest_checksum TEXT NOT NULL,-- sha256 of the whole backup object
    idempotency_key TEXT UNIQUE,
    duration_ms REAL,
    last_verified_at TEXT,
    last_verify_status TEXT
);
CREATE TABLE IF NOT EXISTS restore_jobs (
    id TEXT PRIMARY KEY,
    backup_id TEXT NOT NULL REFERENCES backups(id),
    used_backup_id TEXT,
    mode TEXT NOT NULL,             -- repair / full
    fallback INTEGER DEFAULT 1,     -- allow failover to an older intact backup
    status TEXT NOT NULL,           -- QUEUED/RUNNING/RETRY_SCHEDULED/COMPLETED/FAILED
    requested_by TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    queued_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    recovery_time_ms REAL,          -- queued -> verified (measured RTO)
    attempts INTEGER DEFAULT 0,
    storage_retries INTEGER DEFAULT 0,
    records_restored INTEGER DEFAULT 0,
    verified INTEGER DEFAULT 0,
    error TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    actor TEXT,
    event TEXT NOT NULL,
    details TEXT
);
"""


def init_db():
    with _lock:
        get_conn().executescript(SCHEMA)
    seed = {
        "admin": (os.getenv("ADMIN_PASSWORD", "admin123"), "admin"),
        "operator": (os.getenv("OPERATOR_PASSWORD", "operator123"), "operator"),
        "viewer": (os.getenv("VIEWER_PASSWORD", "viewer123"), "viewer"),
    }
    for user, (pw, role) in seed.items():
        if not query("SELECT id FROM users WHERE username=?", (user,), one=True):
            execute("INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",
                    (user, hash_password(pw), role))
