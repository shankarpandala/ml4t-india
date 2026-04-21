"""Tests for :mod:`ml4t.india.kite.rate_limit`.

Split across three axes:

* Synchronous :class:`TokenBucket` -- basic deducts, refill math,
  timeout, bad-input guardrails.
* Asynchronous :class:`AsyncTokenBucket` -- same surface but async.
* :class:`KiteRateLimiter` composite -- per-category + global bucket
  interaction, unknown-category fallback, non-blocking refund.

The sync tests use ``time.monotonic`` indirectly (no mocking) because
the bucket's refill is monotonic-clock driven; tests that would
otherwise take several seconds pass very large ``rate`` values so the
bucket refills almost instantly.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from ml4t.india.kite.rate_limit import (
    KITE_GLOBAL_LIMIT,
    KITE_RATE_LIMITS,
    AsyncTokenBucket,
    KiteRateLimiter,
    TokenBucket,
)

# ----------------------------------------------------------------------
# TokenBucket (sync)
# ----------------------------------------------------------------------


class TestTokenBucketConstruction:
    def test_initial_state_is_full(self) -> None:
        bucket = TokenBucket(rate=10.0, capacity=10.0)
        # try_acquire all 10 tokens should succeed immediately.
        for _ in range(10):
            assert bucket.try_acquire()

    def test_negative_rate_rejected(self) -> None:
        with pytest.raises(ValueError, match="rate"):
            TokenBucket(rate=-1.0, capacity=1.0)

    def test_zero_rate_rejected(self) -> None:
        with pytest.raises(ValueError, match="rate"):
            TokenBucket(rate=0.0, capacity=1.0)

    def test_tiny_capacity_rejected(self) -> None:
        with pytest.raises(ValueError, match="capacity"):
            TokenBucket(rate=1.0, capacity=0.5)


class TestTokenBucketBasicOps:
    def test_try_acquire_drains_and_refuses(self) -> None:
        """Drain exactly `capacity` tokens, then the next try must fail."""
        bucket = TokenBucket(rate=1.0, capacity=3.0)
        assert bucket.try_acquire()
        assert bucket.try_acquire()
        assert bucket.try_acquire()
        assert not bucket.try_acquire()

    def test_acquire_blocks_and_succeeds_after_refill(self) -> None:
        """With rate=200/s, a single-token wait after drain takes ~5ms."""
        bucket = TokenBucket(rate=200.0, capacity=1.0)
        assert bucket.try_acquire()  # drain
        start = time.monotonic()
        bucket.acquire()
        elapsed = time.monotonic() - start
        # At 200 tokens/sec we expect ~5 ms; allow a generous upper bound
        # so CI latency doesn't flake this.
        assert 0 < elapsed < 0.2

    def test_acquire_timeout_raises(self) -> None:
        bucket = TokenBucket(rate=0.01, capacity=1.0)  # 1 per 100 seconds
        assert bucket.try_acquire()  # drain
        with pytest.raises(TimeoutError):
            bucket.acquire(timeout=0.05)

    def test_acquire_more_than_capacity_raises(self) -> None:
        """Asking for more tokens than the bucket can ever hold is a bug."""
        bucket = TokenBucket(rate=1.0, capacity=2.0)
        with pytest.raises(ValueError, match="capacity"):
            bucket.acquire(tokens=3.0)

    def test_balance_caps_at_capacity(self) -> None:
        """Sitting idle cannot accumulate beyond the configured capacity."""
        bucket = TokenBucket(rate=1000.0, capacity=2.0)
        time.sleep(0.05)  # enough to refill way beyond capacity
        # Can only pull capacity tokens, not more.
        assert bucket.try_acquire()
        assert bucket.try_acquire()
        assert not bucket.try_acquire()


class TestTokenBucketThreadSafety:
    def test_concurrent_acquires_do_not_exceed_capacity(self) -> None:
        """10 threads each try to take 1 token from a full bucket of 5 -- only 5 win."""
        bucket = TokenBucket(rate=0.001, capacity=5.0)  # refill ~never
        wins = []
        lock = threading.Lock()

        def worker() -> None:
            got = bucket.try_acquire()
            with lock:
                wins.append(got)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert wins.count(True) == 5


# ----------------------------------------------------------------------
# AsyncTokenBucket
# ----------------------------------------------------------------------


class TestAsyncTokenBucket:
    @pytest.mark.asyncio
    async def test_try_acquire_drains_and_refuses(self) -> None:
        bucket = AsyncTokenBucket(rate=1.0, capacity=2.0)
        assert await bucket.try_acquire()
        assert await bucket.try_acquire()
        assert not await bucket.try_acquire()

    @pytest.mark.asyncio
    async def test_acquire_blocks_and_succeeds(self) -> None:
        bucket = AsyncTokenBucket(rate=200.0, capacity=1.0)
        await bucket.acquire()
        start = time.monotonic()
        await bucket.acquire()
        elapsed = time.monotonic() - start
        assert 0 < elapsed < 0.2

    @pytest.mark.asyncio
    async def test_acquire_timeout_raises(self) -> None:
        bucket = AsyncTokenBucket(rate=0.01, capacity=1.0)
        await bucket.acquire()  # drain
        with pytest.raises(TimeoutError):
            await bucket.acquire(timeout=0.05)

    @pytest.mark.asyncio
    async def test_concurrent_acquires_limited(self) -> None:
        """20 tasks contend for a 5-token bucket -- only 5 succeed."""
        bucket = AsyncTokenBucket(rate=0.001, capacity=5.0)
        results = await asyncio.gather(
            *[bucket.try_acquire() for _ in range(20)]
        )
        assert results.count(True) == 5


# ----------------------------------------------------------------------
# KiteRateLimiter
# ----------------------------------------------------------------------


class TestKiteRateLimiterDefaults:
    def test_published_limits_match_docs(self) -> None:
        """Guard against accidental edits to the documented Kite ceilings."""
        assert KITE_RATE_LIMITS == {
            "quote": 1.0,
            "historical": 3.0,
            "orders": 10.0,
            "other": 10.0,
        }
        assert KITE_GLOBAL_LIMIT == 10.0

    def test_default_limiter_has_expected_categories(self) -> None:
        rl = KiteRateLimiter()
        # Every published category bucket must exist; `other` is the
        # fallback for unknown categories.
        for cat in ("quote", "historical", "orders", "other"):
            assert rl.try_acquire(cat) is True


class TestKiteRateLimiterBehaviour:
    def test_unknown_category_falls_back_to_other(self) -> None:
        """An unseen category must not raise; it rate-limits at `other`."""
        rl = KiteRateLimiter()
        assert rl.try_acquire("nonexistent_category") is True

    def test_global_bucket_is_enforced(self) -> None:
        """Global cap of 10 req/s is AND-ed with per-category caps."""
        rl = KiteRateLimiter(limits={"quote": 100.0}, global_rate=3.0)
        # quote bucket is 100/s (plenty) but global_rate is only 3
        # meaning only 3 tokens available globally.
        successes = sum(rl.try_acquire("quote") for _ in range(10))
        assert successes == 3

    def test_category_token_refunded_when_global_blocks(self) -> None:
        """Two-phase commit: if global fails, category token is returned."""
        rl = KiteRateLimiter(limits={"quote": 2.0}, global_rate=1.0)
        # First call succeeds on both buckets.
        assert rl.try_acquire("quote") is True
        # Second call: quote has one left (ok) but global is empty
        # -- the quote token must be refunded so it's still there for
        # a hypothetical future global refill. We verify the "refund"
        # indirectly by asserting quote can still be drained.
        assert rl.try_acquire("quote") is False
        # Internal state check: quote bucket should not be at zero.
        # Use the public API; with global exhausted the second refusal
        # above was category-refunded, so one call should still think
        # the category side has a token -- but since global is empty,
        # it still returns False. Instead, assert refund worked by
        # making global refill a lot and trying again:
        rl._global = TokenBucket(rate=100.0, capacity=100.0)  # noqa: SLF001
        time.sleep(0.02)  # let global refill
        assert rl.try_acquire("quote") is True

    def test_acquire_blocks_and_returns(self) -> None:
        """The blocking path uses the same two-bucket logic."""
        # fast rates so the blocking call returns quickly
        rl = KiteRateLimiter(
            limits={
                "quote": 500.0,
                "historical": 500.0,
                "orders": 500.0,
                "other": 500.0,
            },
            global_rate=500.0,
        )
        start = time.monotonic()
        rl.acquire("quote")
        assert time.monotonic() - start < 0.2
