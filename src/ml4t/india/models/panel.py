"""Assemble upstream panel batches from India long-format frames.

Latent-factor models (PCA / RP-PCA / IPCA) do not consume the long
``(timestamp, symbol, ...)`` polars frame that
:class:`~ml4t.india.data.KiteProvider` and :mod:`ml4t.engineer` produce.
They require a *stable-entity panel* --
:class:`ml4t.models.PersistentPanelBatch` -- holding a dense ``(T, N)``
returns matrix and an optional ``(T, N, F)`` characteristics tensor, with
parallel ``timestamps`` / ``asset_ids`` axes.

:func:`build_persistent_panel` is the bridge: it pivots a long frame into
that batch via the upstream ``persistent_panel_batch_from_long_frame``
builder, deriving close-to-close returns when the frame does not already
carry a returns column. This is what lets
:meth:`ml4t.india.workflows.research.ResearchPipeline.run` drive the
``nse_cash_long_only`` (PCA) preset on real multi-symbol OHLCV without
tripping ``TypeError: PCA requires PersistentPanelBatch input``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import polars as pl

from ml4t.india.core.exceptions import InvalidInputError

_RETURN_COL = "returns"


def build_persistent_panel(
    frame: pl.DataFrame,
    *,
    return_col: str | None = None,
    feature_cols: Sequence[str] = (),
    timestamp_col: str = "timestamp",
    entity_col: str = "symbol",
    price_col: str = "close",
) -> Any:
    """Build a ``PersistentPanelBatch`` from a long OHLCV+features frame.

    Parameters
    ----------
    frame:
        Long-format frame with one row per ``(timestamp, entity)``. Must
        carry ``timestamp_col`` and ``entity_col``; needs ``price_col``
        only when ``return_col`` is None (returns are derived from it).
    return_col:
        Name of an existing returns column. When ``None`` (default),
        close-to-close simple returns are computed per entity from
        ``price_col``; the first bar of each entity is null and becomes
        ``NaN`` in the panel, which the upstream latent-factor models
        treat as missing.
    feature_cols:
        Candidate characteristic columns. Only those actually present in
        ``frame`` are forwarded as the ``(T, N, F)`` characteristics
        tensor; a missing feature (e.g. a feature-engineering step that
        silently degraded) is dropped rather than raising, so PCA still
        runs on the returns panel alone.
    timestamp_col, entity_col, price_col:
        Column names. Defaults match the India provider schema.

    Returns
    -------
    ml4t.models.PersistentPanelBatch
        The stable-entity panel ready for ``LatentFactorForecastPipeline``.

    Raises
    ------
    InvalidInputError
        If a required column is absent from ``frame``.
    """
    from ml4t.models import persistent_panel_batch_from_long_frame

    for required in (timestamp_col, entity_col):
        if required not in frame.columns:
            raise InvalidInputError(
                f"panel frame is missing required column {required!r}; have {frame.columns}"
            )

    work = frame
    resolved_return = return_col
    if resolved_return is None:
        if price_col not in frame.columns:
            raise InvalidInputError(
                f"cannot derive returns: frame has no {price_col!r} column "
                f"and no explicit return_col; have {frame.columns}"
            )
        work = frame.sort([entity_col, timestamp_col]).with_columns(
            pl.col(price_col).pct_change().over(entity_col).alias(_RETURN_COL)
        )
        resolved_return = _RETURN_COL
    elif resolved_return not in frame.columns:
        raise InvalidInputError(
            f"return_col {resolved_return!r} not in frame; have {frame.columns}"
        )

    present_features = [c for c in feature_cols if c in work.columns]
    return persistent_panel_batch_from_long_frame(
        work,
        return_col=resolved_return,
        feature_cols=present_features,
        timestamp_col=timestamp_col,
        entity_col=entity_col,
    )


__all__ = ["build_persistent_panel"]
