"""Unit tests for :class:`ml4t.india.kite.fake.FakeKiteClient`.

Tests cover each public surface area:

* Canned-data retrieval (historical_data, instruments, quote, ltp).
* Stateful order lifecycle (place_order allocates an id, cancel_order
  flips status, orders() returns the in-memory book).
* Call recording (every method populates ``calls``).
* Error injection (``set_next_error`` queues an exception for the next
  call).
"""

from __future__ import annotations

import pytest

from ml4t.india.kite import FakeKiteClient
from ml4t.india.kite.fake import RecordedCall


class TestConstructor:
    def test_defaults(self) -> None:
        c = FakeKiteClient()
        assert c.api_key == "fake-api-key"
        assert c.access_token is None
        assert c.calls == []

    def test_override_kwargs(self) -> None:
        c = FakeKiteClient(api_key="k", access_token="t")
        assert c.api_key == "k"
        assert c.access_token == "t"


class TestHistoricalData:
    def test_empty_for_unknown_instrument(self) -> None:
        c = FakeKiteClient()
        assert c.historical_data(999, "2024-01-01", "2024-01-02", "day") == []

    def test_returns_seeded_candles(self) -> None:
        c = FakeKiteClient()
        candles = [
            {"date": "2024-01-01", "open": 100, "high": 101, "low": 99, "close": 100.5}
        ]
        c.set_historical_data("256265", candles)
        out = c.historical_data(256265, "2024-01-01", "2024-01-31", "day")
        assert out == candles

    def test_returns_copy_not_reference(self) -> None:
        c = FakeKiteClient()
        original = [{"date": "2024-01-01", "open": 100}]
        c.set_historical_data("X", original)
        out = c.historical_data("X", None, None, "day")
        out.clear()
        # Internal state unaffected.
        assert c.historical_data("X", None, None, "day") == original


class TestInstruments:
    def test_empty_by_default(self) -> None:
        assert FakeKiteClient().instruments() == []

    def test_returns_seeded_dump(self) -> None:
        c = FakeKiteClient()
        c.set_instruments([
            {"instrument_token": 1, "tradingsymbol": "RELIANCE", "exchange": "NSE"},
            {"instrument_token": 2, "tradingsymbol": "RELIANCE", "exchange": "BSE"},
        ])
        assert len(c.instruments()) == 2

    def test_filters_by_exchange(self) -> None:
        c = FakeKiteClient()
        c.set_instruments([
            {"tradingsymbol": "A", "exchange": "NSE"},
            {"tradingsymbol": "B", "exchange": "BSE"},
            {"tradingsymbol": "C", "exchange": "NSE"},
        ])
        nse = c.instruments(exchange="NSE")
        assert [i["tradingsymbol"] for i in nse] == ["A", "C"]


class TestQuoteAndLtp:
    def test_quote_returns_seeded(self) -> None:
        c = FakeKiteClient()
        c.set_quote("NSE:RELIANCE", {"last_price": 2500.5, "volume": 1_000_000})
        out = c.quote(["NSE:RELIANCE", "NSE:MISSING"])
        assert out == {"NSE:RELIANCE": {"last_price": 2500.5, "volume": 1_000_000}}

    def test_ltp_returns_last_price_only(self) -> None:
        c = FakeKiteClient()
        c.set_quote("NSE:TCS", {"last_price": 4000.0, "volume": 50_000})
        out = c.ltp(["NSE:TCS"])
        assert out == {"NSE:TCS": {"last_price": 4000.0}}


class TestOrderLifecycle:
    def test_place_order_returns_sequential_ids(self) -> None:
        c = FakeKiteClient()
        common = {
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "transaction_type": "BUY",
            "quantity": 1,
            "product": "CNC",
            "order_type": "MARKET",
        }
        id1 = c.place_order("regular", **common)
        id2 = c.place_order("regular", **common)
        assert id1 == "FAKE-000001"
        assert id2 == "FAKE-000002"

    def test_place_order_appends_to_orders_book(self) -> None:
        c = FakeKiteClient()
        c.place_order(
            "regular",
            tradingsymbol="TCS",
            exchange="NSE",
            transaction_type="SELL",
            quantity=10,
            product="MIS",
            order_type="LIMIT",
            price=4000.0,
        )
        orders = c.orders()
        assert len(orders) == 1
        assert orders[0]["tradingsymbol"] == "TCS"
        assert orders[0]["status"] == "COMPLETE"
        assert orders[0]["price"] == 4000.0

    def test_cancel_order_flips_status(self) -> None:
        c = FakeKiteClient()
        order_id = c.place_order(
            "regular",
            tradingsymbol="INFY",
            exchange="NSE",
            transaction_type="BUY",
            quantity=5,
            product="CNC",
            order_type="MARKET",
        )
        returned = c.cancel_order("regular", order_id)
        assert returned == order_id
        assert c.orders()[0]["status"] == "CANCELLED"


class TestCallRecording:
    def test_every_call_is_recorded(self) -> None:
        c = FakeKiteClient()
        c.profile()
        c.ltp(["NSE:RELIANCE"])
        c.margins(segment="equity")

        assert [r.method for r in c.calls] == ["profile", "ltp", "margins"]
        assert c.calls[1].args == (["NSE:RELIANCE"],)
        assert c.calls[2].kwargs == {"segment": "equity"}

    def test_recorded_call_is_dataclass(self) -> None:
        rc = RecordedCall(method="foo", args=(1, 2), kwargs={"k": "v"})
        assert rc.method == "foo"
        assert rc.args == (1, 2)
        assert rc.kwargs == {"k": "v"}


class TestErrorInjection:
    def test_queued_error_raises_on_next_call(self) -> None:
        c = FakeKiteClient()
        c.set_next_error(RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            c.profile()

    def test_error_is_consumed_only_once(self) -> None:
        c = FakeKiteClient()
        c.set_next_error(ValueError("one shot"))
        with pytest.raises(ValueError):
            c.profile()
        # Next call succeeds because the error was already popped.
        assert c.profile()["user_id"] == "FAKE001"

    def test_queued_errors_fifo(self) -> None:
        c = FakeKiteClient()
        c.set_next_error(ValueError("first"))
        c.set_next_error(RuntimeError("second"))
        with pytest.raises(ValueError, match="first"):
            c.profile()
        with pytest.raises(RuntimeError, match="second"):
            c.profile()

    def test_call_still_recorded_even_when_error_raised(self) -> None:
        """Error is raised AFTER the call is logged so tests can still
        assert which method was invoked."""
        c = FakeKiteClient()
        c.set_next_error(RuntimeError("x"))
        with pytest.raises(RuntimeError):
            c.profile()
        assert c.calls[-1].method == "profile"


class TestProfile:
    def test_profile_shape(self) -> None:
        p = FakeKiteClient().profile()
        assert p["user_id"] == "FAKE001"
        assert p["broker"] == "ZERODHA"
        assert "NSE" in p["exchanges"]
