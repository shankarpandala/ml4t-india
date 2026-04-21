"""Thread-safe token-bucket rate limiters for the Zerodha Kite API.

Kite enforces per-endpoint and global rate ceilings; the published limits
(https://kite.trade/docs/connect/v3/exceptions/#rate-limiting) are, as of
2026-04-21:

    * quote        : 1  request / second
    * historical   : 3  requests / second
    * orders       : 10 requests / second
    * global       : 10 requests / second
    * orders       : 400 / minute and 5000 / day (hard limits)

This module provides:

* :class:`TokenBucket` -- a minimal thread-safe synchronous token bucket.
* :class:`AsyncTokenBucket` -- its asyncio cousin.
* :class:`KiteRateLimiter` -- a composite that keeps one bucket per Kite
  endpoint category plus the global bucket, exposing a single
  ``acquire(category)`` entry point for call sites.

Design choices
--------------

* **Monotonic time.** All refill arithmetic uses :func:`time.monotonic`
  so wall-clock jumps (NTP corrections, container suspend) cannot cause
  accidental burst credits.
* **No separate refill thread.** Each :meth:`acquire` recomputes the
  token balance against elapsed time; the bucket is pure state plus a
  lock. That makes instances cheap, safe to pickle, and easy to reason
  about in free-threaded CPython (no background thread to debug).
* **`threading.Lock` not `RLock`.** Re-entrant locking is a yellow flag
  for sharing a single bucket across call chains. If you need it you
  are probably sharing a bucket somewhere you shouldn't.
* **Global + per-category = AND.** A caller must acquire from BOTH the
  category bucket AND the global bucket before making the request. This
  models Kite's actual enforcement: the per-endpoint limit is a ceiling
  on that endpoint, and the 10 req/s global is a separate ceiling on
  the whole key. Exceeding either triggers a 429 / Rate-Limit response.

Buckets exist primarily to SHAPE traffic, not to poll. Callers are
expected to block on :meth:`acquire` rather than loop on
:meth:`try_acquire`; the latter exists for specialised cases (test
harnesses, non-blocking probes).
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class TokenBucket:
    """Thread-safe synchronous token bucket.

    Parameters
    ----------
    rate:
        Tokens added per second. For Kite's quote endpoint, ``rate=1.0``
        (1 request per second).
    capacity:
        Maximum tokens that accumulate if the bucket is idle. Usually
        equal to ``rate`` for Kite's per-second limits; set higher to
        allow short bursts. Must be >= 1.0 (so at least one request
        fits).

    Notes
    -----
    Each call to :meth:`acquire` or :meth:`try_acquire` first updates
    the bucket's token balance by adding ``(now - last_refill) * rate``,
    capped at ``capacity``, then tries to deduct one token. The lock
    covers that read-modify-write sequence.
    """

    rate: float
    capacity: float
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: threading.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError(f"rate must be positive, got {self.rate}")
        if self.capacity < 1.0:
            raise ValueError(f"capacity must be >= 1.0, got {self.capacity}")
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    # ---- internal: refill balance against elapsed wall time ---------

    def _refill_locked(self) -> None:
        """Must be called with ``_lock`` held."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now

    # ---- public API --------------------------------------------------

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking. Return ``True`` iff enough tokens were available."""
        with self._lock:
            self._refill_locked()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def acquire(self, tokens: float = 1.0, timeout: float | None = None) -> None:
        """Block until ``tokens`` are available, or raise ``TimeoutError``.

        Parameters
        ----------
        tokens:
            Tokens to deduct. Defaults to 1, which is the common case.
        timeout:
            Maximum seconds to wait; ``None`` means wait indefinitely.

        Raises
        ------
        TimeoutError
            If the wait exceeded ``timeout``. The bucket is NOT charged
            when this happens -- the caller can retry cleanly.
        ValueError
            If ``tokens`` > :attr:`capacity`; such a request can never
            be satisfied and blocking forever would hide the bug.
        """
        if tokens > self.capacity:
            raise ValueError(
                f"requested {tokens} tokens > capacity {self.capacity}; "
                "bump `capacity` if you truly need this burst size"
            )
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                # Time needed for the bucket to have enough tokens.
                shortfall = tokens - self._tokens
                wait_for = shortfall / self.rate
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"rate-limit wait exceeded timeout of {timeout}s"
                    )
                wait_for = min(wait_for, remaining)
            time.sleep(wait_for)


@dataclass(slots=True)
class AsyncTokenBucket:
    """Asyncio analogue of :class:`TokenBucket`.

    Shares the state-plus-lock design. The lock is an
    :class:`asyncio.Lock` created lazily on first use, because asyncio
    primitives require a running event loop and we want the constructor
    to work outside of one (for easier dependency injection in tests).
    """

    rate: float
    capacity: float
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError(f"rate must be positive, got {self.rate}")
        if self.capacity < 1.0:
            raise ValueError(f"capacity must be >= 1.0, got {self.capacity}")
        self._tokens = self.capacity
        self._last_refill = time.monotonic()

    def _ensure_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now

    async def try_acquire(self, tokens: float = 1.0) -> bool:
        lock = self._ensure_lock()
        async with lock:
            self._refill_locked()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    async def acquire(
        self, tokens: float = 1.0, timeout: float | None = None
    ) -> None:
        if tokens > self.capacity:
            raise ValueError(
                f"requested {tokens} tokens > capacity {self.capacity}"
            )
        lock = self._ensure_lock()
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            async with lock:
                self._refill_locked()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                shortfall = tokens - self._tokens
                wait_for = shortfall / self.rate
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"rate-limit wait exceeded timeout of {timeout}s"
                    )
                wait_for = min(wait_for, remaining)
            await asyncio.sleep(wait_for)


# ---- Kite-specific composite -----------------------------------------


#: Published Zerodha Kite rate ceilings (requests per second per category).
#: Update only after confirming the SDK / docs were updated; a
#: contract-test pin at ``tests/contracts`` would catch regressions.
KITE_RATE_LIMITS: dict[str, float] = {
    "quote": 1.0,
    "historical": 3.0,
    "orders": 10.0,
    "other": 10.0,
}

#: Kite's global per-API-key ceiling, enforced independently of per-
#: category limits. Kite rejects requests that exceed EITHER bucket.
KITE_GLOBAL_LIMIT: float = 10.0


class KiteRateLimiter:
    """Holds one :class:`TokenBucket` per Kite category plus a global bucket.

    Call sites invoke :meth:`acquire` with the category name (``quote``,
    ``historical``, ``orders``, etc.) before hitting the corresponding
    SDK method. The limiter then draws one token from the category
    bucket AND one from the global bucket, which models Kite's actual
    enforcement.

    Unknown categories fall into the ``other`` bucket, not an exception,
    because the Kite SDK exposes dozens of methods and we would rather
    rate-limit them at the general ceiling than forget to limit a new
    one.

    Parameters
    ----------
    limits:
        Optional override of :data:`KITE_RATE_LIMITS`. Pass through
        test fixtures to avoid real-time delays.
    global_rate:
        Override :data:`KITE_GLOBAL_LIMIT`. Default is 10 req/s.
    """

    __slots__ = ("_buckets", "_global")

    def __init__(
        self,
        limits: dict[str, float] | None = None,
        global_rate: float = KITE_GLOBAL_LIMIT,
    ) -> None:
        rates = dict(KITE_RATE_LIMITS)
        if limits is not None:
            rates.update(limits)
        # Capacity matches rate: 1 second of burst for per-second ceilings.
        self._buckets: dict[str, TokenBucket] = {
            name: TokenBucket(rate=r, capacity=r) for name, r in rates.items()
        }
        self._global = TokenBucket(rate=global_rate, capacity=global_rate)

    def _bucket_for(self, category: str) -> TokenBucket:
        return self._buckets.get(category, self._buckets["other"])

    def acquire(self, category: str = "other", timeout: float | None = None) -> None:
        """Block until both the category bucket AND the global bucket have a token."""
        # Category first: a slow category bucket will usually be the
        # binding constraint and acquiring it first keeps the global
        # bucket's "reserved" window small when many callers contend.
        self._bucket_for(category).acquire(timeout=timeout)
        self._global.acquire(timeout=timeout)

    def try_acquire(self, category: str = "other") -> bool:
        """Non-blocking. Return ``True`` iff both buckets had a token.

        If the category bucket succeeds but the global bucket is empty,
        the category token IS refunded so the caller is not charged for
        a request they could not make.
        """
        if not self._bucket_for(category).try_acquire():
            return False
        if self._global.try_acquire():
            return True
        # Refund the category token by crediting the bucket directly.
        # Using the private attribute is intentional -- this is a
        # two-phase commit across buckets we own.
        bucket = self._bucket_for(category)
        with bucket._lock:  # noqa: SLF001
            bucket._tokens = min(bucket.capacity, bucket._tokens + 1.0)
        return False


__all__ = [
    "AsyncTokenBucket",
    "KITE_GLOBAL_LIMIT",
    "KITE_RATE_LIMITS",
    "KiteRateLimiter",
    "TokenBucket",
]
