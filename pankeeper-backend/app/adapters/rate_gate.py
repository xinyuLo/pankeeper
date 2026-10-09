from __future__ import annotations

import threading
import time

class RateLimited(Exception):
    pass

class CircuitOpen(Exception):
    pass

class RateGate:
    def __init__(self, name: str, min_interval: float = 1.0, cooldown: float = 30.0, max_consecutive_fail: int = 3):
        self.name = name
        self.min_interval = min_interval
        self.cooldown = cooldown
        self.max_consecutive_fail = max_consecutive_fail
        self._lock = threading.Lock()
        self._last = 0.0
        self._fail_streak = 0
        self._open_until = 0.0

    def wait(self) -> float:
        with self._lock:
            now = time.monotonic()
            if now < self._open_until:
                raise CircuitOpen(f"{self.name} 连续失败熔断中，约 {self._open_until - now:.0f}s 后自动恢复")
            delay = self.min_interval - (now - self._last)
            if delay > 0:
                time.sleep(delay)
                waited = delay
            else:
                waited = 0.0
            self._last = time.monotonic()
            return waited

    def on_success(self) -> None:
        with self._lock:
            self._fail_streak = 0

    def on_rate_limited(self) -> float:
        with self._lock:
            self._fail_streak += 1
            backoff = min(60.0, self.min_interval * (2**min(self._fail_streak, 5)))
            self._open_until = time.monotonic() + backoff
            return backoff

    def on_failure(self) -> float:
        with self._lock:
            self._fail_streak += 1
            if self._fail_streak >= self.max_consecutive_fail:
                self._open_until = time.monotonic() + self.cooldown
                self._fail_streak = 0
                return self.cooldown
            return 0.0
