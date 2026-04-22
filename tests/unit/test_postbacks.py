"""Tests for :class:`ml4t.india.live.postbacks.PostbackHandler`."""

from __future__ import annotations

import hashlib
import json

import pytest
from ml4t.backtest.types import Order, OrderSide, OrderStatus, OrderType

from ml4t.india.core.exceptions import InvalidInputError
from ml4t.india.live.postbacks import PostbackHandler, PostbackSignatureError


def _sign(order_id: str, api_secret: str) -> str:
    return hashlib.sha256((order_id + api_secret).encode("utf-8")).hexdigest()


def _make_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "order_id": "200101000000001",
        "status": "COMPLETE",
        "exchange": "NSE",
        "tradingsymbol": "RELIANCE",
        "transaction_type": "BUY",
        "order_type": "MARKET",
        "quantity": 10,
        "filled_quantity": 10,
        "price": 0,
        "trigger_price": 0,
        "average_price": 2500.50,
        "status_message": None,
    }
    base.update(overrides)
    return base


# ---- verification ------------------------------------------------------


class TestSignatureVerification:
    def test_valid_signature_accepted(self) -> None:
        handler = PostbackHandler(api_secret="s3cret")
        assert handler.verify_signature("200101000000001", _sign("200101000000001", "s3cret"))

    def test_invalid_signature_rejected(self) -> None:
        handler = PostbackHandler(api_secret="s3cret")
        assert not handler.verify_signature("200101000000001", "deadbeef")

    def test_missing_signature_rejected(self) -> None:
        handler = PostbackHandler(api_secret="s3cret")
        body = json.dumps(_make_payload()).encode()
        with pytest.raises(PostbackSignatureError, match="missing"):
            handler.handle(body)

    def test_wrong_signature_rejected(self) -> None:
        handler = PostbackHandler(api_secret="s3cret")
        body = json.dumps(_make_payload()).encode()
        with pytest.raises(PostbackSignatureError, match="mismatch"):
            handler.handle(body, signature="wronghex")

    def test_verify_false_skips_hmac(self) -> None:
        handler = PostbackHandler(api_secret="s3cret", verify=False)
        body = json.dumps(_make_payload()).encode()
        order = handler.handle(body)  # no signature, no error
        assert order.order_id == "200101000000001"

    def test_verify_true_requires_api_secret(self) -> None:
        with pytest.raises(InvalidInputError, match="api_secret"):
            PostbackHandler(api_secret="", verify=True)


# ---- payload parsing ---------------------------------------------------


class TestPayloadParsing:
    def test_missing_order_id_raises(self) -> None:
        handler = PostbackHandler(api_secret="s3cret", verify=False)
        body = json.dumps({"status": "COMPLETE"}).encode()
        with pytest.raises(InvalidInputError, match="order_id"):
            handler.handle(body)

    def test_empty_order_id_raises(self) -> None:
        handler = PostbackHandler(api_secret="s3cret", verify=False)
        body = json.dumps({"order_id": "   "}).encode()
        with pytest.raises(InvalidInputError, match="order_id"):
            handler.handle(body)

    def test_invalid_json_raises(self) -> None:
        handler = PostbackHandler(api_secret="s3cret", verify=False)
        with pytest.raises(InvalidInputError, match="valid JSON"):
            handler.handle(b"not json")

    def test_bytes_and_str_both_accepted(self) -> None:
        handler = PostbackHandler(api_secret="s3cret", verify=False)
        body_dict = _make_payload()
        order_bytes = handler.handle(json.dumps(body_dict).encode())
        order_str = handler.handle(json.dumps(body_dict))
        assert order_bytes.order_id == order_str.order_id

    def test_non_utf8_bytes_rejected(self) -> None:
        handler = PostbackHandler(api_secret="s3cret", verify=False)
        with pytest.raises(InvalidInputError, match="UTF-8"):
            handler.handle(b"\xff\xfe")


# ---- translation --------------------------------------------------------


class TestTranslation:
    def _handle(self, payload: dict[str, object]) -> Order:
        handler = PostbackHandler(api_secret="s3cret", verify=False)
        return handler.handle(json.dumps(payload).encode())

    def test_filled_order_translation(self) -> None:
        order = self._handle(_make_payload())
        assert order.order_id == "200101000000001"
        assert order.asset == "NSE:RELIANCE"
        assert order.side == OrderSide.BUY
        assert order.order_type == OrderType.MARKET
        assert order.status == OrderStatus.FILLED
        assert order.quantity == 10
        assert order.filled_quantity == 10
        assert order.filled_price == pytest.approx(2500.50)

    def test_cancelled(self) -> None:
        order = self._handle(_make_payload(status="CANCELLED", filled_quantity=0, average_price=0))
        assert order.status == OrderStatus.CANCELLED

    def test_rejected_with_reason(self) -> None:
        order = self._handle(_make_payload(status="REJECTED", status_message="insufficient margin"))
        assert order.status == OrderStatus.REJECTED
        assert order.rejection_reason == "insufficient margin"

    def test_pending_variants(self) -> None:
        for st in ("OPEN", "TRIGGER PENDING", "UPDATE"):
            order = self._handle(_make_payload(status=st, filled_quantity=0, average_price=0))
            assert order.status == OrderStatus.PENDING, st

    def test_sell_side(self) -> None:
        order = self._handle(_make_payload(transaction_type="SELL"))
        assert order.side == OrderSide.SELL

    def test_limit_order_with_price(self) -> None:
        order = self._handle(_make_payload(order_type="LIMIT", price=2500.0))
        assert order.order_type == OrderType.LIMIT
        assert order.limit_price == pytest.approx(2500.0)

    def test_sl_m_maps_to_stop(self) -> None:
        order = self._handle(_make_payload(order_type="SL-M", trigger_price=2450.0))
        assert order.order_type == OrderType.STOP
        assert order.stop_price == pytest.approx(2450.0)


# ---- fan-out ------------------------------------------------------------


class TestFanOut:
    def test_all_handlers_called(self) -> None:
        handler = PostbackHandler(api_secret="s3cret", verify=False)
        seen_a: list[str] = []
        seen_b: list[str] = []
        handler.on_order(lambda o: seen_a.append(o.order_id))
        handler.on_order(lambda o: seen_b.append(o.order_id))

        body = json.dumps(_make_payload()).encode()
        handler.handle(body)
        assert seen_a == ["200101000000001"]
        assert seen_b == ["200101000000001"]

    def test_bad_handler_isolated(self) -> None:
        handler = PostbackHandler(api_secret="s3cret", verify=False)
        good_seen: list[str] = []

        def bad(_: Order) -> None:
            raise RuntimeError("boom")

        handler.on_order(bad)
        handler.on_order(lambda o: good_seen.append(o.order_id))
        handler.handle(json.dumps(_make_payload()).encode())
        assert good_seen == ["200101000000001"]


# ---- signed end-to-end -------------------------------------------------


class TestSignedEndToEnd:
    def test_signature_round_trip(self) -> None:
        handler = PostbackHandler(api_secret="s3cret")
        seen: list[Order] = []
        handler.on_order(seen.append)

        payload = _make_payload(order_id="230101000000009")
        body = json.dumps(payload).encode()
        signature = _sign("230101000000009", "s3cret")

        order = handler.handle(body, signature=signature)
        assert order.order_id == "230101000000009"
        assert seen[0] is order
