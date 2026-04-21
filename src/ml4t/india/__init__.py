"""ml4t-india: Algorithmic trading for Indian markets on top of the ml4t-* ecosystem.

Pre-alpha. The public API surface is being built up phase-by-phase; see PR #1
on the repository for the roadmap.

This module provides nothing beyond `__version__` today. Subsequent phases
populate `core`, `kite`, `data`, `backtest`, `live`, `options`, `diagnostic`,
`workflows`, and `cli` subpackages in that order.
"""

from __future__ import annotations

try:
    # Written by hatch-vcs at build time from the current git tag (or from
    # SETUPTOOLS_SCM_PRETEND_VERSION in CI before the first release tag).
    from ml4t.india._version import __version__
except ImportError:  # editable install in a worktree that has never been built
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
