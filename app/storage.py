"""Object storage for backup files.

LocalObjectStore  - folder that behaves like a bucket (used locally / in the demo)
S3ObjectStore     - AWS S3 / any S3-compatible bucket (set BACKUP_BUCKET to enable)
ResilientStore    - wraps either one with retry + circuit breaker

Both stores support fault injection (outage / flaky / latency) for the experiments.
"""
import os
import random
import time

from .resilience import CircuitBreaker, retry


class StorageUnavailable(Exception):
    pass


class FaultInjector:
    def __init__(self):
        self.down = False          # full outage
        self.failure_rate = 0.0    # transient 503s
        self.latency_ms = 0

    def check(self):
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        if self.down:
            raise StorageUnavailable("object storage unreachable (injected outage)")
        if self.failure_rate and random.random() < self.failure_rate:
            raise StorageUnavailable("transient storage error 503 (injected)")


class LocalObjectStore:
    def __init__(self, root="storage/bucket"):
        self.root = root
        self.faults = FaultInjector()
        os.makedirs(root, exist_ok=True)

    def _path(self, key):
        p = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def put(self, key, data: bytes):
        self.faults.check()
        tmp = self._path(key) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, self._path(key))  # atomic write: never a half-written backup

    def get(self, key) -> bytes:
        self.faults.check()
        with open(self._path(key), "rb") as f:
            return f.read()

    def raw_path(self, key):
        return self._path(key)


class S3ObjectStore:
    def __init__(self, bucket):
        import boto3  # only needed in the cloud deployment
        self.s3 = boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT_URL") or None)
        self.bucket = bucket
        self.faults = FaultInjector()

    def put(self, key, data: bytes):
        self.faults.check()
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key) -> bytes:
        self.faults.check()
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()


class ResilientStore:
    def __init__(self, inner):
        self.inner = inner
        self.breaker = CircuitBreaker("object-storage", failure_threshold=3, reset_timeout=3.0)

    @property
    def faults(self):
        return self.inner.faults

    def put(self, key, data):
        return retry(lambda: self.breaker.call(self.inner.put, key, data),
                     retry_on=(StorageUnavailable, OSError), label=f"put:{key}")

    def get(self, key):
        return retry(lambda: self.breaker.call(self.inner.get, key),
                     retry_on=(StorageUnavailable,), label=f"get:{key}")


def build_store():
    bucket = os.getenv("BACKUP_BUCKET")
    inner = S3ObjectStore(bucket) if bucket else LocalObjectStore(os.getenv("LOCAL_BUCKET_DIR", "storage/bucket"))
    return ResilientStore(inner)


store = build_store()
