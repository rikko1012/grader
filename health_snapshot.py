"""Single-flight diagnostic refresh; liveness never waits for a database or disk."""
import threading
import time


class HealthSnapshot:
    def __init__(self, ttl=60):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.data = {}
        self.updated = 0.0
        self.attempted = float('-inf')
        self.running = False
        self.error = None

    def read(self, collect):
        now = time.monotonic()
        with self.lock:
            if not self.running and now - self.attempted >= self.ttl:
                self.running = True
                self.attempted = now
                threading.Thread(target=self._refresh, args=(collect,),
                                 name='health-diagnostics', daemon=True).start()
            return {**self.data, 'diagnostics': {
                'pending': self.running,
                'age_s': round(now - self.updated, 1) if self.updated else None,
                'error': self.error,
            }}

    def _refresh(self, collect):
        try:
            data = collect()
            with self.lock:
                self.data = data
                self.updated = time.monotonic()
                self.error = None
        except Exception as exc:
            with self.lock:
                self.error = type(exc).__name__
        finally:
            with self.lock:
                self.running = False
