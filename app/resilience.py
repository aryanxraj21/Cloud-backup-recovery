"""Recovery mechanisms: retry with exponential backoff + circuit breaker."""
import random
import threading
import time

from .observability import log_event, metrics


class CircuitOpenError(Exception):
    pass


class CircuitBreaker:
    """CLOSED -> (N failures) -> OPEN -> (cool-down) -> HALF_OPEN -> success -> CLOSED.

    While OPEN we fail fast instead of hammering a dependency that is down.
    """

    def __init__(self, name, failure_threshold=3, reset_timeout=3.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.state = "CLOSED"
        self.failures = 0
        self.opened_at = 0.0
        self._lock = threading.Lock()

    def _set(self, state):
        if state != self.state:
            log_event("circuit_state_change", level="WARNING" if state == "OPEN" else "INFO",
                      breaker=self.name, old=self.state, new=state)
            metrics.inc(f"circuit_{state.lower()}_total")
            self.state = state

    def call(self, fn, *args, **kwargs):
        with self._lock:
            if self.state == "OPEN":
                if time.time() - self.opened_at >= self.reset_timeout:
                    self._set("HALF_OPEN")
                else:
                    metrics.inc("circuit_rejected_total")
                    raise CircuitOpenError(f"circuit '{self.name}' is OPEN")
        try:
            result = fn(*args, **kwargs)
        except Exception:
            with self._lock:
                self.failures += 1
                if self.state == "HALF_OPEN" or self.failures >= self.failure_threshold:
                    self.opened_at = time.time()
                    self._set("OPEN")
            raise
        with self._lock:
            self.failures = 0
            self._set("CLOSED")
        return result


def retry(fn, attempts=4, base_delay=0.1, max_delay=1.0, retry_on=(Exception,), label="op"):
    """Run fn(); on a retryable error wait base*2^i (+jitter) and try again.

    Returns (result, retries_used). Does not retry when the circuit is open.
    """
    for i in range(attempts):
        try:
            return fn(), i
        except CircuitOpenError:
            raise
        except retry_on as e:
            if i == attempts - 1:
                log_event("retry_exhausted", level="ERROR", op=label, attempts=attempts, error=str(e))
                raise
            delay = min(max_delay, base_delay * (2 ** i)) * (0.5 + random.random() / 2)
            metrics.inc("storage_retries_total")
            log_event("retry", level="WARNING", op=label, attempt=i + 1, wait_s=round(delay, 3), error=str(e))
            time.sleep(delay)
