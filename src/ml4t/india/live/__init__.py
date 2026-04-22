""":mod:`ml4t.india.live` -- live-trading adapters for Indian brokers.

Every concrete broker (``KiteBroker`` in Phase 2; future ``UpstoxBroker``,
``AngelOneBroker``, ``FivePaisaBroker``) extends :class:`IndianBrokerBase`,
an abstract class that structurally satisfies the upstream
:class:`ml4t.live.protocols.AsyncBrokerProtocol`. Every ticker feed extends
:class:`IndianTickerFeedBase`, which likewise satisfies
:class:`ml4t.live.protocols.DataFeedProtocol`.

Implementing the upstream protocols once at this layer rather than on each
concrete broker means:

* Cross-broker invariants (product / exchange mapping, lot rounding,
  charges wiring) live in one place.
* When upstream evolves the protocol, we add a default implementation
  here and every concrete broker inherits it automatically.
"""

from __future__ import annotations

from ml4t.india.live.angelone_broker import AngelOneBroker
from ml4t.india.live.base import IndianBrokerBase
from ml4t.india.live.feed_base import IndianTickerFeedBase
from ml4t.india.live.fivepaisa_broker import FivePaisaBroker
from ml4t.india.live.kite_broker import KiteBroker
from ml4t.india.live.kite_ticker_feed import KiteTickerFeed
from ml4t.india.live.postbacks import PostbackHandler, PostbackSignatureError
from ml4t.india.live.upstox_broker import UpstoxBroker

__all__ = [
    "AngelOneBroker",
    "FivePaisaBroker",
    "IndianBrokerBase",
    "IndianTickerFeedBase",
    "KiteBroker",
    "KiteTickerFeed",
    "PostbackHandler",
    "PostbackSignatureError",
    "UpstoxBroker",
]
