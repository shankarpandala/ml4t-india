# Phase-0 Scaffolding

Phase-0 lays down the extension-only scaffolding that every subsequent
phase builds on. No Zerodha functionality is wired yet; the value of
Phase-0 is that every Phase-1+ commit lands on a solid foundation with
CI, abstract bases, a test double, and upstream-drift guards already
in place.

## Commit map

| Step | Ships |
| ---- | ----- |
| 0.1 | `pyproject.toml`, `README.md`, `AGENTS.md`, draft PR #1 |
| 0.2 | CI matrix across Python 3.12 / 3.13 mainline + 3.14 / 3.13t / 3.14t experimental |
| 0.3 | `ml4t.india.core` &mdash; Kite-wire enums, `IndiaError` hierarchy |
| 0.4 | `ml4t.india.data` &mdash; `IndianOHLCVProvider(BaseProvider)` abstract |
| 0.5 | `ml4t.india.live` &mdash; `IndianBrokerBase`, `IndianTickerFeedBase` abstracts |
| 0.6 | `ml4t.india.kite.FakeKiteClient` &mdash; in-memory test double |
| 0.7 | `tests/contracts/test_upstream_api_snapshot.py` &mdash; drift guard |
| 0.8 | `.github/workflows/upstream-drift.yml` &mdash; weekly cron |
| 0.9 | docs skeleton (this site) + pre-commit config |

## Design-decisions locked in Phase-0

### No `license` field

`pyproject.toml` omits `project.license` and the License classifier on
purpose until the project owner picks one. The upstream ml4t-* packages
are MIT, so we stay compatible with any permissive choice.

### Upstream ml4t-* packages are unpinned

`pyproject.toml` declares `ml4t-data`, `ml4t-engineer`, `ml4t-backtest`,
`ml4t-live`, `ml4t-diagnostic` with no version constraint. The upstream
owns its release cadence; we follow it. Drift is caught by the weekly
`upstream-drift.yml` workflow.

A load-bearing `httpx>=0.27,<1` upper bound ensures our `respx` dev
dependency keeps working &mdash; pip's `--pre` flag would otherwise pull
httpx 1.0.dev* which removed `httpx.BaseTransport` that respx imports.

### Free-threaded Python: experimental, single wheel

`ml4t-india` is pure Python &mdash; no C extensions of our own. One
universal wheel runs on GIL CPython (3.12, 3.13) and on the free-threaded
builds (3.13t, 3.14t). CI matrix includes 3.13t / 3.14t as `experimental`
with `continue-on-error: true` because upstream numpy / polars / numba
wheels for free-threaded ABIs are still rolling out. The classifier
`Programming Language :: Python :: Free Threading :: 3 - Stable` is
intentionally withheld from `pyproject.toml` until both experimental
lanes go green.

### Version via hatch-vcs + `_FOR_ML4T_INDIA` env var

`hatch-vcs` derives version from git tags. Because there are no tags yet,
CI sets `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ML4T_INDIA=0.0.0.dev0`. The
`_FOR_ML4T_INDIA` suffix is load-bearing: the global
`SETUPTOOLS_SCM_PRETEND_VERSION` form would leak into transitive
source-builds (observed breaking `shap` sdist builds on Python 3.14).

### Phase-0 CI is gated to `workflow_dispatch`

Per project owner, CI auto-trigger on push / PR is DISABLED for the
duration of Phase-0. Each commit on this branch is validated locally
against Python 3.12 + 3.13 before push (ruff + pytest). Auto-trigger
is restored in the post-Phase-0 cleanup commit.

## Test counts (local, Python 3.12)

| File | Tests |
| ---- | ----- |
| `tests/test_placeholder.py` | 1 |
| `tests/unit/test_core_constants.py` | 9 |
| `tests/unit/test_core_exceptions.py` | 20 |
| `tests/unit/test_indian_ohlcv_provider.py` | 8 |
| `tests/unit/test_indian_live_base.py` | 6 |
| `tests/unit/test_fake_kite_client.py` | 20 |
| `tests/contracts/test_upstream_api_snapshot.py` | 12 |
| **Total** | **76** |
