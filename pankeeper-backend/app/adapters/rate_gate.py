"""每网盘一个限速门：串行锁 + 最小间隔 + 退避 + 连败熔断。

参考项目（bdsavepro / quark-auto-save）都没有全局限速器，这是 PanKeeper 补的短板；
间隔与熔断阈值取自 docs/research/ 实测值：百度 1-2s、夸克 0.5-1s、115 >=800ms。
"""
from __future__ import annotations

import threading
import time


class RateLimited(Exception):
    """上游明确返回限频信号（如 115 HTTP 406）。"""


class CircuitOpen(Exception):
    """熔断中：连续失败过多，直接拒绝请求，等冷却结束。"""


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
        """请求前调用：熔断检查 + 串行间隔。返回实际等待秒数（日志用）。"""
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
        """限频信号：退避翻倍并熔断一小段。返回建议退避秒数。"""
        with self._lock:
            self._fail_streak += 1
            backoff = min(60.0, self.min_interval * (2**min(self._fail_streak, 5)))
            self._open_until = time.monotonic() + backoff
            return backoff

    def on_failure(self) -> float:
        """一般失败：连败到阈值熔断 cooldown 秒。"""
        with self._lock:
            self._fail_streak += 1
            if self._fail_streak >= self.max_consecutive_fail:
                self._open_until = time.monotonic() + self.cooldown
                self._fail_streak = 0
                return self.cooldown
            return 0.0
