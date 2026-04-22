"""Option-chain snapshot built from :class:`InstrumentMeta` rows.

An NFO / BFO option chain for a given underlying + expiry is a dense
strike ladder; strategies typically want:

* ATM strike (closest to spot).
* Calls only / puts only / paired CE + PE at the same strike.
* Strikes within +/- N from ATM (for IV smile, delta hedging, etc.).

:class:`OptionChain` builds this view from the instruments dump rows
already produced by :class:`InstrumentsCache`. Construction is O(N log N)
(sort by strike); all lookups thereafter are O(log N) or O(1).

Deliberately does NOT fetch prices -- callers pair :class:`OptionChain`
with :meth:`KiteClient.quote` / :meth:`KiteClient.ltp` to get option
LTPs, then feed (chain, ltps) into Greek / IV computation.
"""

from __future__ import annotations

import bisect
import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from ml4t.india.core.exceptions import InstrumentNotFoundError, InvalidInputError
from ml4t.india.kite.instruments import InstrumentMeta

OptionType = Literal["CE", "PE"]


@dataclass(frozen=True, slots=True)
class OptionContract:
    """One strike of the chain, with normalized fields.

    Distinct from :class:`InstrumentMeta` -- keeps the options layer
    decoupled from the instruments cache schema. Conversion happens
    in :meth:`OptionChain.from_instruments`.
    """

    tradingsymbol: str
    strike: float
    option_type: OptionType
    instrument_token: int
    lot_size: int
    expiry: dt.date
    exchange: str


class OptionChain:
    """Strike-indexed snapshot of calls + puts for one underlying + expiry.

    Parameters
    ----------
    underlying:
        Root symbol (e.g. ``"NIFTY"``, ``"BANKNIFTY"``, ``"RELIANCE"``).
        Matched against :attr:`InstrumentMeta.name`.
    expiry:
        Expiry date. Must match exactly (Kite stores expiry as the last
        day the contract trades, IST).
    calls, puts:
        Pre-sorted (by strike) lists of :class:`OptionContract`.
    """

    def __init__(
        self,
        underlying: str,
        expiry: dt.date,
        calls: list[OptionContract],
        puts: list[OptionContract],
    ) -> None:
        self.underlying = underlying
        self.expiry = expiry
        self._calls = sorted(calls, key=lambda c: c.strike)
        self._puts = sorted(puts, key=lambda c: c.strike)
        self._call_strikes = [c.strike for c in self._calls]
        self._put_strikes = [p.strike for p in self._puts]

    # ---- builders ---------------------------------------------------

    @classmethod
    def from_instruments(
        cls,
        instruments: Iterable[InstrumentMeta],
        underlying: str,
        expiry: dt.date,
    ) -> OptionChain:
        """Build a chain by filtering an instruments iterable.

        Raises :class:`InstrumentNotFoundError` if no matching options
        exist -- callers should resolve underlying + expiry against
        :meth:`InstrumentsCache.search` first, rather than asking the
        chain to cope with zero rows.
        """
        calls: list[OptionContract] = []
        puts: list[OptionContract] = []
        for inst in instruments:
            if inst.name != underlying or inst.expiry != expiry:
                continue
            if inst.instrument_type not in ("CE", "PE"):
                continue
            contract = OptionContract(
                tradingsymbol=inst.tradingsymbol,
                strike=float(inst.strike),
                option_type=inst.instrument_type,  # type: ignore[arg-type]
                instrument_token=inst.instrument_token,
                lot_size=inst.lot_size,
                expiry=inst.expiry,  # type: ignore[arg-type]
                exchange=inst.exchange,
            )
            if inst.instrument_type == "CE":
                calls.append(contract)
            else:
                puts.append(contract)

        if not calls and not puts:
            raise InstrumentNotFoundError(f"no CE/PE for underlying={underlying!r} expiry={expiry}")
        return cls(underlying, expiry, calls, puts)

    # ---- accessors --------------------------------------------------

    @property
    def calls(self) -> list[OptionContract]:
        return list(self._calls)

    @property
    def puts(self) -> list[OptionContract]:
        return list(self._puts)

    @property
    def strikes(self) -> list[float]:
        """Union of all call + put strikes, sorted ascending."""
        # In the standard chain these are identical, but guard against
        # asymmetric listings (auction / delisted legs) by taking the union.
        return sorted(set(self._call_strikes) | set(self._put_strikes))

    # ---- lookups ----------------------------------------------------

    def atm_strike(self, spot: float) -> float:
        """Strike closest to ``spot`` (calls + puts unioned).

        Ties broken toward the LOWER strike, matching how NSE publishes
        the "At-the-Money" leg for indices.
        """
        strikes = self.strikes
        if not strikes:
            raise InstrumentNotFoundError("chain is empty")
        idx = bisect.bisect_left(strikes, spot)
        if idx == 0:
            return strikes[0]
        if idx == len(strikes):
            return strikes[-1]
        below = strikes[idx - 1]
        above = strikes[idx]
        # Tie goes to below (lower strike wins at equidistance).
        return above if (above - spot) < (spot - below) else below

    def get(self, strike: float, option_type: OptionType) -> OptionContract:
        """Return the contract at exactly ``strike`` + ``option_type``.

        Raises :class:`InstrumentNotFoundError` if the strike/type is
        not listed.
        """
        if option_type not in ("CE", "PE"):
            raise InvalidInputError(f"option_type must be 'CE' or 'PE', got {option_type!r}")
        legs = self._calls if option_type == "CE" else self._puts
        strikes = self._call_strikes if option_type == "CE" else self._put_strikes
        idx = bisect.bisect_left(strikes, strike)
        if idx < len(strikes) and strikes[idx] == strike:
            return legs[idx]
        raise InstrumentNotFoundError(
            f"{self.underlying} {self.expiry} {strike} {option_type} not listed"
        )

    def around_atm(
        self,
        spot: float,
        count: int,
    ) -> tuple[list[OptionContract], list[OptionContract]]:
        """Return ``(calls, puts)`` within +/- ``count`` strikes of ATM.

        For NIFTY with 50-point strikes and count=3, returns 6 calls + 6 puts
        (three above ATM, three below). If the chain hits its edge before
        reaching ``count``, returns what's available (no padding).
        """
        if count < 0:
            raise InvalidInputError(f"count must be >= 0, got {count}")
        atm = self.atm_strike(spot)

        def _window(legs: list[OptionContract], strikes: list[float]) -> list[OptionContract]:
            if not strikes:
                return []
            center = bisect.bisect_left(strikes, atm)
            lo = max(0, center - count)
            hi = min(len(legs), center + count + 1)
            return legs[lo:hi]

        return _window(self._calls, self._call_strikes), _window(self._puts, self._put_strikes)

    # ---- meta -------------------------------------------------------

    def __len__(self) -> int:
        return len(self._calls) + len(self._puts)

    def __repr__(self) -> str:
        return (
            f"OptionChain(underlying={self.underlying!r}, "
            f"expiry={self.expiry}, calls={len(self._calls)}, "
            f"puts={len(self._puts)})"
        )


__all__ = ["OptionChain", "OptionContract", "OptionType"]
