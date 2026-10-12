"""Structured logging + in-memory metrics.

- Every important event goes to logs/app.log (JSON lines) AND the `events` table,
  so it can be read back through GET /logs.
- Metrics are exposed at GET /metrics.
"""
import json
import logging
import os
import statistics
import threading
from collections import defaultdict

from . import db

os.makedirs("logs", exist_ok=True)
_logger = logging.getLogger("backup-system")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    fh = logging.FileHandler("logs/app.log")
    fh.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(fh)


def log_event(event, level="INFO", actor="system", **details):
    entry = {"ts": db.now_iso(), "level": level, "actor": actor, "event": event, **details}
    _logger.log(getattr(logging, level), json.dumps(entry, default=str))
    db.execute("INSERT INTO events (ts,level,actor,event,details) VALUES (?,?,?,?,?)",
               (entry["ts"], level, actor, event, json.dumps(details, default=str)))


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.counters = defaultdict(int)
        self.samples = defaultdict(list)

    def inc(self, name, n=1):
        with self._lock:
            self.counters[name] += n

    def observe(self, name, value):
        with self._lock:
            self.samples[name].append(value)
            if len(self.samples[name]) > 5000:
                self.samples[name] = self.samples[name][-5000:]

    def snapshot(self):
        with self._lock:
            out = {"counters": dict(self.counters), "timings_ms": {}}
            for k, v in self.samples.items():
                if not v:
                    continue
                s = sorted(v)
                out["timings_ms"][k] = {
                    "count": len(s),
                    "avg": round(statistics.mean(s), 2),
                    "p50": round(s[len(s) // 2], 2),
                    "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 2),
                    "max": round(s[-1], 2),
                }
            return out


metrics = Metrics()
