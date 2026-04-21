"""India-layer abstract base for OHLCV providers.

:class:`IndianOHLCVProvider` extends the upstream template-method class
:class:`ml4t.data.providers.base.BaseProvider` (see that class' docstring
for the full contract) and adds the small set of concerns that are shared
across every Indian broker backend:

* The canonical timezone (:data:`IndianOHLCVProvider.TIMEZONE`) is IST
  regardless of how the broker returns timestamps. Concrete subclasses
  are expected to normalise to this zone before returning a frame.

* :data:`IndianOHLCVProvider.SUPPORTED_EXCHANGES` lets a higher-level
  :class:`ml4t.data.DataManager` route a request to the right provider
  without the caller knowing which broker has coverage of which segment.

Concrete subclasses (``KiteProvider``, future ``NSEBhavcopyProvider``, ...)
override the upstream hooks -- either ``_fetch_and_transform_data`` for
single-step providers or the ``_fetch_raw_data`` + ``_transform_data``
pair for two-step providers. They do **not** override the public
``fetch_ohlcv`` template method; upstream's rate-limit / circuit-breaker /
validation pipeline runs automatically.

Why not implement :class:`ml4t.data.providers.protocols.OHLCVProvider`
directly? Two reasons:

1. Extending :class:`BaseProvider` inherits ~200 lines of tested
   infrastructure (rate-limit mixins, session handling, template method)
   for free -- a protocol implementation would have to duplicate that.

2. The project's "extend existing classes" directive: if upstream adds a
   new method with a default implementation, every subclass picks it up
   automatically.
"""

from __future__ import annotations

from typing import ClassVar

from ml4t.data.providers.base import BaseProvider

from ml4t.india.core.constants import Exchange


class IndianOHLCVProvider(BaseProvider):
    """Abstract base for OHLCV providers scoped to Indian markets.

    This class is intentionally thin; it adds only what is genuinely
    cross-broker. All broker-specific logic belongs in concrete subclasses.

    Class attributes
    ----------------
    TIMEZONE:
        IANA timezone name for India. Applied to output timestamps
        regardless of how the broker returned them. Subclasses SHOULD NOT
        change this; pick the concrete timezone by normalising in
        ``_fetch_and_transform_data`` instead.
    SUPPORTED_EXCHANGES:
        The :class:`~ml4t.india.core.constants.Exchange` codes this provider
        claims to serve. Subclasses override with their actual coverage.
        Default is empty (abstract); attempting to fetch against an
        unsupported exchange is a caller bug that concrete providers may
        raise :class:`~ml4t.india.core.exceptions.InvalidInputError` for.

    Notes on abstractness
    ---------------------
    Upstream :class:`BaseProvider` declares ``name`` as an ``@abstractmethod``
    property. This class does NOT override it, so ``IndianOHLCVProvider``
    remains abstract and cannot be instantiated directly. Concrete
    subclasses must implement ``name``.
    """

    #: IANA timezone for the Indian trading day.
    TIMEZONE: ClassVar[str] = "Asia/Kolkata"

    #: Exchanges this provider serves; subclasses override with their
    #: actual coverage (e.g. ``frozenset({Exchange.NSE, Exchange.BSE})``).
    SUPPORTED_EXCHANGES: ClassVar[frozenset[Exchange]] = frozenset()


__all__ = ["IndianOHLCVProvider"]
