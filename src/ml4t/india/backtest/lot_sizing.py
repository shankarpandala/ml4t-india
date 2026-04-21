"""Lot-size rounding for F&O and other lot-traded instruments.

Indian futures and options trade in fixed lots (e.g. NIFTY options = 50,
BANKNIFTY options = 15). A strategy that produces a raw share count must
round to the nearest lot before placing the order, or Kite rejects the
request. The helpers below apply that rounding consistently.

The lot size for a given instrument lives on
:class:`~ml4t.india.kite.instruments.InstrumentMeta` and can be looked
up via :meth:`InstrumentsCache.resolve`.
"""

from __future__ import annotations


def round_to_lot(quantity: float, lot_size: int) -> int:
    """Round ``quantity`` to the nearest multiple of ``lot_size``.

    Returns an integer (broker APIs always want integer contract
    counts). Uses banker's-style rounding: 1.5 lots -> 2 lots.

    Parameters
    ----------
    quantity:
        The raw strategy-computed quantity (possibly fractional).
    lot_size:
        The instrument's lot size. Must be >= 1.

    Raises
    ------
    ValueError
        If ``lot_size < 1`` or if the rounded quantity is zero when
        the caller asked for a nonzero position (so strategies cannot
        silently produce no-op orders).
    """
    if lot_size < 1:
        raise ValueError(f"lot_size must be >= 1, got {lot_size}")
    lots = round(quantity / lot_size)
    rounded = int(lots * lot_size)
    if rounded == 0 and quantity != 0:
        raise ValueError(
            f"rounding {quantity} to lot_size={lot_size} produced zero; "
            "either raise the requested quantity or skip the order"
        )
    return rounded


def floor_to_lot(quantity: float, lot_size: int) -> int:
    """Round ``quantity`` DOWN to the nearest multiple of ``lot_size``.

    Useful when the caller has a hard cash budget and cannot afford to
    exceed the requested quantity (e.g. margin headroom checks).
    """
    if lot_size < 1:
        raise ValueError(f"lot_size must be >= 1, got {lot_size}")
    return (int(quantity) // lot_size) * lot_size


__all__ = ["floor_to_lot", "round_to_lot"]
