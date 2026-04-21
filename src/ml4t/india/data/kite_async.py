"""Asyncio variant of :class:`KiteProvider`.

Delegates to the sync provider via :func:`asyncio.to_thread` so the
resolution + fetch + frame-assembly logic is defined exactly once.
kiteconnect is a sync SDK; a native-async rewrite would add complexity
without removing blocking I/O.
"""

from __future__ import annotations

import polars as pl

from ml4t.india.data.kite import KiteProvider


class KiteAsyncProvider:
    """Asyncio facade over :class:`KiteProvider`.

    Structurally satisfies :class:`ml4t.data.providers.async_base.AsyncBaseProvider`
    on the :meth:`fetch_ohlcv_async` method name. Inherit from that class
    in a later phase if we need the full upstream async pipeline
    (currently each async call goes straight through the sync path).
    """

    def __init__(self, sync: KiteProvider) -> None:
        self._sync = sync

    @property
    def name(self) -> str:
        return self._sync.name

    async def fetch_ohlcv_async(
        self,
        symbol: str,
        start: str,
        end: str,
        frequency: str = "daily",
    ) -> pl.DataFrame:
        # Delegate to the sync method via the upstream BaseProvider
        # async wrapper which already handles thread dispatch.
        return await self._sync.fetch_ohlcv_async(symbol, start, end, frequency)


__all__ = ["KiteAsyncProvider"]
