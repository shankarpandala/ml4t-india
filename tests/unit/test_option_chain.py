"""Tests for :class:`ml4t.india.options.chain.OptionChain`."""

from __future__ import annotations

import datetime as dt

import pytest

from ml4t.india.core.exceptions import InstrumentNotFoundError, InvalidInputError
from ml4t.india.kite.instruments import InstrumentMeta
from ml4t.india.options import OptionChain, OptionContract

_EXPIRY = dt.date(2026, 4, 24)


def _opt(
    strike: float,
    option_type: str,
    name: str = "NIFTY",
    expiry: dt.date = _EXPIRY,
    token: int | None = None,
) -> InstrumentMeta:
    return InstrumentMeta(
        instrument_token=token if token is not None else int(strike * 10),
        exchange_token=0,
        tradingsymbol=f"NIFTY26APR{int(strike)}{option_type}",
        name=name,
        last_price=0.0,
        expiry=expiry,
        strike=strike,
        tick_size=0.05,
        lot_size=50,
        instrument_type=option_type,
        segment="NFO-OPT",
        exchange="NFO",
    )


@pytest.fixture
def chain() -> OptionChain:
    instruments = [_opt(s, t) for s in [24800, 24900, 25000, 25100, 25200] for t in ("CE", "PE")]
    return OptionChain.from_instruments(instruments, "NIFTY", _EXPIRY)


# ---- construction -----------------------------------------------------


class TestConstruction:
    def test_from_instruments_filters_by_name(self) -> None:
        mixed = [
            _opt(25000, "CE"),
            _opt(25000, "PE"),
            _opt(25000, "CE", name="BANKNIFTY", token=1000001),
        ]
        chain = OptionChain.from_instruments(mixed, "NIFTY", _EXPIRY)
        assert len(chain) == 2  # only NIFTY rows

    def test_from_instruments_filters_by_expiry(self) -> None:
        other = dt.date(2026, 5, 29)
        mixed = [
            _opt(25000, "CE", expiry=_EXPIRY),
            _opt(25000, "CE", expiry=other, token=123),
        ]
        chain = OptionChain.from_instruments(mixed, "NIFTY", _EXPIRY)
        assert len(chain) == 1

    def test_empty_raises(self) -> None:
        with pytest.raises(InstrumentNotFoundError, match="no CE/PE"):
            OptionChain.from_instruments([], "NIFTY", _EXPIRY)

    def test_calls_and_puts_sorted(self, chain: OptionChain) -> None:
        assert [c.strike for c in chain.calls] == [24800, 24900, 25000, 25100, 25200]
        assert [p.strike for p in chain.puts] == [24800, 24900, 25000, 25100, 25200]


# ---- ATM lookup -------------------------------------------------------


class TestAtmStrike:
    def test_spot_between_strikes_picks_nearest_above(self, chain: OptionChain) -> None:
        # 25070 is closer to 25100 than 25000.
        assert chain.atm_strike(25070) == 25100

    def test_spot_between_strikes_picks_nearest_below(self, chain: OptionChain) -> None:
        assert chain.atm_strike(25030) == 25000

    def test_spot_exactly_at_strike(self, chain: OptionChain) -> None:
        assert chain.atm_strike(25000) == 25000

    def test_spot_below_all_strikes(self, chain: OptionChain) -> None:
        assert chain.atm_strike(20000) == 24800

    def test_spot_above_all_strikes(self, chain: OptionChain) -> None:
        assert chain.atm_strike(30000) == 25200

    def test_tie_breaks_toward_lower(self, chain: OptionChain) -> None:
        # 24950 is exactly between 24900 and 25000 -> lower wins.
        assert chain.atm_strike(24950) == 24900


# ---- get --------------------------------------------------------------


class TestGet:
    def test_returns_exact_contract(self, chain: OptionChain) -> None:
        ce = chain.get(25000, "CE")
        assert isinstance(ce, OptionContract)
        assert ce.option_type == "CE"
        assert ce.strike == 25000

    def test_missing_strike_raises(self, chain: OptionChain) -> None:
        with pytest.raises(InstrumentNotFoundError):
            chain.get(99999, "CE")

    def test_invalid_option_type_raises(self, chain: OptionChain) -> None:
        with pytest.raises(InvalidInputError, match="option_type"):
            chain.get(25000, "X")  # type: ignore[arg-type]


# ---- around_atm -------------------------------------------------------


class TestAroundAtm:
    def test_window_of_one(self, chain: OptionChain) -> None:
        calls, puts = chain.around_atm(25000, count=1)
        assert [c.strike for c in calls] == [24900, 25000, 25100]
        assert [p.strike for p in puts] == [24900, 25000, 25100]

    def test_window_of_zero_returns_atm_only(self, chain: OptionChain) -> None:
        calls, puts = chain.around_atm(25000, count=0)
        assert [c.strike for c in calls] == [25000]
        assert [p.strike for p in puts] == [25000]

    def test_window_clamped_at_edges(self, chain: OptionChain) -> None:
        calls, _ = chain.around_atm(24800, count=10)
        # Only 5 strikes exist; no padding.
        assert [c.strike for c in calls] == [24800, 24900, 25000, 25100, 25200]

    def test_negative_count_rejected(self, chain: OptionChain) -> None:
        with pytest.raises(InvalidInputError, match="count"):
            chain.around_atm(25000, count=-1)


# ---- misc -------------------------------------------------------------


class TestMisc:
    def test_repr(self, chain: OptionChain) -> None:
        text = repr(chain)
        assert "NIFTY" in text
        assert "2026-04-24" in text
        assert "calls=5" in text
