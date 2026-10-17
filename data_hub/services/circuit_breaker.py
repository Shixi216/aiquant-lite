"""Circuit breaker for third-party data providers.

第三方数据源熔断器（2026-08-30 新增，用户指定 P3 层）：

设计目标：外部 Provider 连续故障时，不应每个请求都重新撞一次
（东财 SSL 连续故障实例：每次都浪费 3~25 秒）。每个 Provider
独立维护状态机：

    HEALTHY ──失败──▶ DEGRADED ──连续失败──▶ OPEN
       ▲                                          │
       │              成功（试探）                 │ 熔断窗口到期
       └────────── HALF_OPEN ◀────────────────────┘

状态语义：
- HEALTHY: 正常，放行所有请求
- DEGRADED: 失败过但未到阈值，放行（计数观察）
- OPEN: 熔断打开，直接短路跳过（不发起真实请求）
- HALF_OPEN: 熔断窗口到期，放行一次试探请求；
  成功→恢复 HEALTHY，失败→回到 OPEN 并刷新窗口

线程安全：进程内单例按 provider 名注册，锁保护状态变更。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum


class CircuitState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitStats:
    consecutive_failures: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_failure_at: float | None = None
    last_success_at: float | None = None
    opened_at: float | None = None


class CircuitBreaker:
    """Per-provider failure gate."""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        open_timeout_seconds: float = 300.0,
        success_threshold: int = 1,
        clock: callable | None = None,
    ) -> None:
        self._name = name
        self._failure_threshold = max(1, failure_threshold)
        self._open_timeout_seconds = max(1.0, open_timeout_seconds)
        self._success_threshold = max(1, success_threshold)
        self._clock = clock or time.monotonic
        self._state = CircuitState.HEALTHY
        self._stats = CircuitStats()
        self._half_open_successes = 0
        self._lock = threading.Lock()

    # ---- 查询 ----

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    @property
    def stats(self) -> CircuitStats:
        with self._lock:
            return CircuitStats(**self._stats.__dict__)

    def allow_request(self) -> bool:
        """是否放行一次真实请求；OPEN 时短路返回 False。"""
        with self._lock:
            if self._state is CircuitState.OPEN:
                # 熔断窗口到期 → 转 HALF_OPEN 放行一次试探
                if (
                    self._stats.opened_at is not None
                    and (self._clock() - self._stats.opened_at)
                    >= self._open_timeout_seconds
                ):
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_successes = 0
                    return True
                return False
            return True

    # ---- 结果记录 ----

    def record_success(self) -> None:
        with self._lock:
            self._stats.total_successes += 1
            self._stats.last_success_at = self._clock()
            if self._state is CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self._success_threshold:
                    self._state = CircuitState.HEALTHY
                    self._stats.consecutive_failures = 0
                    self._stats.opened_at = None
            elif self._state is CircuitState.DEGRADED:
                # 单次成功不清零连续失败计数（需连续恢复），
                # 但连续 2 次成功视为回暖
                self._stats.consecutive_failures = max(
                    0, self._stats.consecutive_failures - 1
                )
                if self._stats.consecutive_failures == 0:
                    self._state = CircuitState.HEALTHY
            elif self._state is CircuitState.HEALTHY:
                self._stats.consecutive_failures = 0

    def record_failure(self, error: str = "") -> None:
        with self._lock:
            self._stats.total_failures += 1
            self._stats.last_failure_at = self._clock()
            self._stats.consecutive_failures += 1
            if self._state is CircuitState.HALF_OPEN:
                # 试探失败 → 重新熔断，刷新窗口
                self._state = CircuitState.OPEN
                self._stats.opened_at = self._clock()
                self._half_open_successes = 0
            elif self._state is CircuitState.HEALTHY:
                if self._stats.consecutive_failures >= self._failure_threshold:
                    self._state = CircuitState.OPEN
                    self._stats.opened_at = self._clock()
            # DEGRADED 状态：连续失败累积到阈值同样打开
            elif (
                self._state is CircuitState.DEGRADED
                and self._stats.consecutive_failures >= self._failure_threshold
            ):
                self._state = CircuitState.OPEN
                self._stats.opened_at = self._clock()

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.HEALTHY
            self._stats = CircuitStats()
            self._half_open_successes = 0

    def describe(self) -> dict:
        s = self.stats
        return {
            "provider": self._name,
            "state": self._state.value,
            "consecutive_failures": s.consecutive_failures,
            "total_successes": s.total_successes,
            "total_failures": s.total_failures,
            "opened_at": s.opened_at,
            "open_timeout_seconds": self._open_timeout_seconds,
        }


# 进程内注册表：provider 名 → 熔断器单例
_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def get_breaker(name: str, **kwargs) -> CircuitBreaker:
    """取（或创建）指定 provider 的熔断器单例。"""
    with _BREAKERS_LOCK:
        breaker = _BREAKERS.get(name)
        if breaker is None:
            breaker = CircuitBreaker(name, **kwargs)
            _BREAKERS[name] = breaker
        return breaker


def breakers_snapshot() -> list[dict]:
    """所有熔断器状态快照（供运维/看门狗 L3 使用）。"""
    with _BREAKERS_LOCK:
        names = list(_BREAKERS)
    return [get_breaker(name).describe() for name in sorted(names)]


__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "breakers_snapshot",
    "get_breaker",
]
