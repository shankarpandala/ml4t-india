"""Unified facade over ``kiteconnect.KiteConnect`` for ml4t-india.

:class:`KiteClient` ties together :class:`KiteRateLimiter` (token-bucket
throttling) and :func:`translate` (kiteconnect -> IndiaError mapping) so
downstream modules call a single API that is throttled and has a single
error taxonomy.

:class:`AsyncKiteClient` mirrors every method via :func:`asyncio.to_thread`
so the dispatch logic is defined once.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from kiteconnect import KiteConnect
from kiteconnect import exceptions as kexc

from ml4t.india.kite.errors import translate
from ml4t.india.kite.rate_limit import KiteRateLimiter

# Classify each SDK method so the rate limiter draws from the right bucket.
# Unlisted methods fall through to "other" (10 req/s).
_CATEGORY_FOR: dict[str, str] = {
    "quote": "quote",
    "ltp": "quote",
    "ohlc": "quote",
    "historical_data": "historical",
    "place_order": "orders",
    "modify_order": "orders",
    "cancel_order": "orders",
}


class _KiteSDK(Protocol):
    """Structural SDK shape; real KiteConnect and FakeKiteClient both fit."""

    def set_access_token(self, access_token: str) -> None: ...


class KiteClient:
    """Thread-safe sync facade over ``kiteconnect.KiteConnect``."""

    def __init__(
        self,
        sdk: _KiteSDK,
        rate_limiter: KiteRateLimiter | None = None,
        access_token: str | None = None,
    ) -> None:
        self._sdk = sdk
        self._rate = rate_limiter or KiteRateLimiter()
        if access_token is not None:
            sdk.set_access_token(access_token)

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        access_token: str,
        rate_limiter: KiteRateLimiter | None = None,
    ) -> KiteClient:
        """Build a :class:`KiteClient` backed by a real ``KiteConnect``."""
        sdk = KiteConnect(api_key=api_key)
        sdk.set_access_token(access_token)
        return cls(sdk=sdk, rate_limiter=rate_limiter)

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Dispatch with rate-limit + error translation."""
        self._rate.acquire(_CATEGORY_FOR.get(method, "other"))
        try:
            return getattr(self._sdk, method)(*args, **kwargs)
        except kexc.KiteException as kite_exc:
            raise translate(kite_exc) from kite_exc

    # ---- wrapped SDK surface ----

    def profile(self) -> dict[str, Any]:
        return self._call("profile")

    def margins(self, segment: str | None = None) -> dict[str, Any]:
        if segment is None:
            return self._call("margins")
        return self._call("margins", segment)

    def instruments(self, exchange: str | None = None) -> list[dict[str, Any]]:
        if exchange is None:
            return self._call("instruments")
        return self._call("instruments", exchange)

    def historical_data(
        self,
        instrument_token: int | str,
        from_date: Any,
        to_date: Any,
        interval: str,
        continuous: bool = False,
        oi: bool = False,
    ) -> list[dict[str, Any]]:
        return self._call(
            "historical_data",
            instrument_token,
            from_date,
            to_date,
            interval,
            continuous=continuous,
            oi=oi,
        )

    def quote(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        return self._call("quote", instruments)

    def ltp(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        return self._call("ltp", instruments)

    def ohlc(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        return self._call("ohlc", instruments)

    def place_order(
        self,
        variety: str,
        *,
        tradingsymbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        product: str,
        order_type: str,
        **kwargs: Any,
    ) -> str:
        return self._call(
            "place_order",
            variety,
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=transaction_type,
            quantity=quantity,
            product=product,
            order_type=order_type,
            **kwargs,
        )

    def cancel_order(self, variety: str, order_id: str, **kwargs: Any) -> str:
        return self._call("cancel_order", variety, order_id, **kwargs)

    def orders(self) -> list[dict[str, Any]]:
        return self._call("orders")

    def positions(self) -> dict[str, list[dict[str, Any]]]:
        return self._call("positions")


class AsyncKiteClient:
    """Asyncio twin that delegates to :class:`KiteClient` via to_thread."""

    def __init__(self, sync: KiteClient) -> None:
        self._sync = sync

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        access_token: str,
        rate_limiter: KiteRateLimiter | None = None,
    ) -> AsyncKiteClient:
        return cls(KiteClient.from_api_key(api_key, access_token, rate_limiter))

    async def profile(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._sync.profile)

    async def margins(self, segment: str | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self._sync.margins, segment)

    async def instruments(
        self, exchange: str | None = None
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._sync.instruments, exchange)

    async def historical_data(
        self,
        instrument_token: int | str,
        from_date: Any,
        to_date: Any,
        interval: str,
        continuous: bool = False,
        oi: bool = False,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._sync.historical_data,
            instrument_token,
            from_date,
            to_date,
            interval,
            continuous,
            oi,
        )

    async def quote(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        return await asyncio.to_thread(self._sync.quote, instruments)

    async def ltp(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        return await asyncio.to_thread(self._sync.ltp, instruments)

    async def ohlc(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        return await asyncio.to_thread(self._sync.ohlc, instruments)

    async def place_order(
        self,
        variety: str,
        *,
        tradingsymbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        product: str,
        order_type: str,
        **kwargs: Any,
    ) -> str:
        return await asyncio.to_thread(
            lambda: self._sync.place_order(
                variety,
                tradingsymbol=tradingsymbol,
                exchange=exchange,
                transaction_type=transaction_type,
                quantity=quantity,
                product=product,
                order_type=order_type,
                **kwargs,
            )
        )

    async def cancel_order(
        self, variety: str, order_id: str, **kwargs: Any
    ) -> str:
        return await asyncio.to_thread(
            lambda: self._sync.cancel_order(variety, order_id, **kwargs)
        )

    async def orders(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._sync.orders)

    async def positions(self) -> dict[str, list[dict[str, Any]]]:
        return await asyncio.to_thread(self._sync.positions)


__all__ = [
    "AsyncKiteClient",
    "KiteClient",
]
