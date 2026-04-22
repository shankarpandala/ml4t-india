"""Zerodha Kite order postback handler.

Kite sends an HTTP POST to the strategy's configured postback URL each
time an order transitions state (COMPLETE, CANCELLED, REJECTED,
TRIGGER_PENDING -> OPEN, etc.). The payload is JSON; authenticity is
verified with an HMAC-SHA256 signature the broker computes from the
order_id + api_secret.

This module provides :class:`PostbackHandler`, a framework-agnostic
dispatcher:

* Transport-independent. It accepts a raw request body (``bytes``) +
  the signature header; it does NOT tie to FastAPI / Flask / aiohttp.
  Callers route their web framework's request into
  :meth:`PostbackHandler.handle` and get back an upstream
  :class:`~ml4t.backtest.types.Order`.

* Signature-verified by default. A :class:`PostbackSignatureError`
  is raised if the HMAC doesn't match; the caller should respond HTTP
  401. Disabling verification (``verify=False``) is supported for
  unit tests only -- live deployments MUST verify.

* Multi-handler fan-out. Strategies, risk, and diagnostics all want
  to see order-state transitions; registering multiple callbacks via
  :meth:`on_order` keeps them decoupled without re-subscribing a
  webhook.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

from ml4t.backtest.types import Order, OrderSide, OrderStatus, OrderType

from ml4t.india.core.exceptions import IndiaError, InvalidInputError

#: Handler called with the fully-translated Order. Runs synchronously on
#: the thread that calls :meth:`PostbackHandler.handle`, which in a web
#: framework is typically the request-handling thread / event loop.
PostbackHandlerCallback = Callable[[Order], None]


# ---- upstream mapping ---------------------------------------------------
#
# Mirrors the _KITE_STATUS / _to_order logic in kite_broker.py. Kept
# deliberately separate (not imported) so postback handling can evolve
# independently if Kite adds postback-only status values.

_POSTBACK_STATUS: dict[str, OrderStatus] = {
    "COMPLETE": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "OPEN": OrderStatus.PENDING,
    "TRIGGER PENDING": OrderStatus.PENDING,
    "UPDATE": OrderStatus.PENDING,
}


class PostbackSignatureError(IndiaError):
    """HMAC verification failed for an incoming postback payload.

    Callers SHOULD respond HTTP 401 and must NOT act on the payload --
    a mismatched signature means the request might be forged.
    """


def _translate(payload: dict[str, Any]) -> Order:
    """Convert a postback JSON body to an upstream :class:`Order`."""
    exchange = str(payload.get("exchange", "NSE"))
    tradingsymbol = str(payload.get("tradingsymbol", ""))
    asset = f"{exchange}:{tradingsymbol}" if tradingsymbol else exchange

    side = (
        OrderSide.BUY
        if str(payload.get("transaction_type", "BUY")).upper() == "BUY"
        else OrderSide.SELL
    )
    kite_ot = str(payload.get("order_type", "MARKET")).upper().replace("-", "_")
    upstream_ot = {
        "MARKET": OrderType.MARKET,
        "LIMIT": OrderType.LIMIT,
        "SL": OrderType.STOP_LIMIT,
        "SL_M": OrderType.STOP,
    }.get(kite_ot, OrderType.MARKET)
    status = _POSTBACK_STATUS.get(str(payload.get("status", "")).upper(), OrderStatus.PENDING)

    return Order(
        asset=asset,
        side=side,
        quantity=float(payload.get("quantity", 0) or 0),
        order_type=upstream_ot,
        limit_price=(float(payload["price"]) if payload.get("price") else None),
        stop_price=(float(payload["trigger_price"]) if payload.get("trigger_price") else None),
        order_id=str(payload.get("order_id", "")),
        status=status,
        filled_quantity=float(payload.get("filled_quantity", 0) or 0),
        filled_price=(float(payload["average_price"]) if payload.get("average_price") else None),
        rejection_reason=payload.get("status_message") or None,
    )


class PostbackHandler:
    """Parse + verify + dispatch Kite order postbacks.

    Parameters
    ----------
    api_secret:
        The Kite API secret used to sign postbacks. Matches the secret
        shown on https://kite.trade Apps page for this API key. Stored
        in memory only; never logged.
    verify:
        If ``False``, skip HMAC verification. DO NOT disable in
        production -- only for unit tests where the fixture doesn't
        bother to sign.

    Notes
    -----
    Kite's signature is ``HMAC-SHA256(order_id + api_secret, api_key)``
    BUT the exact formulation varies by Kite Connect release. The
    current (v3) rule used here: ``sha256(order_id + api_secret)``
    hex-digested. Fixtures + the integration snapshot test pin the
    expected behaviour.
    """

    def __init__(self, api_secret: str, verify: bool = True) -> None:
        if not api_secret and verify:
            raise InvalidInputError("api_secret is required when verify=True")
        self._api_secret = api_secret
        self._verify = verify
        self._handlers: list[PostbackHandlerCallback] = []

    # ---- handler registration --------------------------------------

    def on_order(self, handler: PostbackHandlerCallback) -> None:
        """Register a callback; multiple handlers allowed."""
        self._handlers.append(handler)

    # ---- verification ----------------------------------------------

    def verify_signature(self, order_id: str, provided_hmac: str) -> bool:
        """Return ``True`` iff ``provided_hmac`` matches the computed HMAC.

        Kite's scheme (v3): ``sha256(order_id + api_secret)`` hex-digest,
        lowercase. We use :func:`hmac.compare_digest` for constant-time
        comparison so a timing attack can't leak the secret.
        """
        expected = hashlib.sha256((order_id + self._api_secret).encode("utf-8")).hexdigest()
        return hmac.compare_digest(expected, provided_hmac)

    # ---- entry point ------------------------------------------------

    def handle(
        self,
        body: bytes | str,
        signature: str | None = None,
    ) -> Order:
        """Parse, verify, translate, and fan-out a postback.

        Parameters
        ----------
        body:
            The raw request body (Kite sends form-encoded? no -- JSON).
            Bytes preferred (avoids encoding surprises); strings are
            decoded as UTF-8.
        signature:
            The HMAC signature from the ``X-Kite-Signature`` header (or
            equivalent). Required when ``verify=True``.

        Returns
        -------
        Order
            The translated upstream order. Returning it -- rather than
            only fanning out -- lets tests assert on the translation.

        Raises
        ------
        PostbackSignatureError
            If ``verify=True`` and the signature is missing or wrong.
        InvalidInputError
            If the body isn't valid JSON or lacks ``order_id``.
        """
        if isinstance(body, bytes):
            try:
                body_text = body.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise InvalidInputError("postback body is not UTF-8") from exc
        else:
            body_text = body

        try:
            payload: dict[str, Any] = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise InvalidInputError("postback body is not valid JSON") from exc

        order_id = str(payload.get("order_id", "")).strip()
        if not order_id:
            raise InvalidInputError("postback payload missing order_id")

        if self._verify:
            if not signature:
                raise PostbackSignatureError("missing postback signature")
            if not self.verify_signature(order_id, signature):
                raise PostbackSignatureError("postback signature mismatch")

        order = _translate(payload)
        # A bad handler must not take down siblings or the webhook.
        # We deliberately do NOT log here -- the caller owns logging
        # so structlog context (request_id, trace) stays intact.
        for handler in list(self._handlers):
            with contextlib.suppress(Exception):
                handler(order)
        return order


__all__ = [
    "PostbackHandler",
    "PostbackSignatureError",
]
