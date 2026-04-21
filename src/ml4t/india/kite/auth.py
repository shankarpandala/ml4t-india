"""Zerodha Kite login flow and on-disk token persistence.

Kite Connect v3 authentication
------------------------------

1. The application generates a login URL from its ``api_key``.
2. The user visits that URL in a browser, logs in, and is redirected to
   the app-registered redirect URI with a ``request_token`` parameter.
3. The application exchanges ``request_token`` + ``api_key`` +
   ``api_secret`` for an ``access_token`` via ``KiteConnect.generate_
   session``.
4. ``access_token`` is valid until approximately **06:00 IST of the
   next calendar day** -- Kite rotates tokens daily for security.

This module wraps steps 1, 3, and a stable on-disk cache for step 4 so
the CLI (``ml4t-india login``) and tests can all read the same token
without touching the kiteconnect SDK directly.

On-disk layout
--------------

Tokens are stored as JSON at ``~/.ml4t/india/token.json`` by default::

    {
      "api_key": "xxx",
      "access_token": "yyy",
      "user_id": "AB1234",
      "login_time": "2026-04-21T09:15:00+05:30",
      "ml4t_india_version": "0.0.0.dev0"
    }

The parent directory is created with mode ``0o700`` and the file itself
with mode ``0o600`` so other users on a shared host cannot read the
token. Writes are atomic: the new JSON lands in a sibling tempfile
which is then ``os.replace``d over the target.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kiteconnect import KiteConnect
from kiteconnect import exceptions as kexc

from ml4t.india.kite.errors import translate

#: ``Asia/Kolkata`` offset used to compute token expiry without pulling
#: in ``pytz``. Kite rotates tokens at approximately 06:00 IST daily.
_IST = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")

#: Hour of day (IST) after which yesterday's token is considered expired.
_DAILY_EXPIRY_HOUR = 6


def default_token_path() -> Path:
    """Return the canonical on-disk location for the Kite token cache.

    Uses ``$ML4T_INDIA_TOKEN_PATH`` if set (handy in tests and CI) and
    falls back to ``~/.ml4t/india/token.json``. The directory is NOT
    created by this function -- :func:`save_token` does that so the
    permissions can be set atomically.
    """
    env_override = os.environ.get("ML4T_INDIA_TOKEN_PATH")
    if env_override:
        return Path(env_override).expanduser()
    return Path.home() / ".ml4t" / "india" / "token.json"


@dataclass
class TokenRecord:
    """Serialisable snapshot of a Zerodha Kite auth session.

    Attributes
    ----------
    api_key:
        The API key (not secret) used to mint this session. Stored so
        a multi-key setup can disambiguate which cache belongs to
        which app without opening the SDK.
    access_token:
        The opaque token the SDK needs for authenticated calls.
    user_id:
        Zerodha client code (e.g. ``AB1234``). Informational.
    login_time:
        When the session was created. Used by :meth:`is_expired` to
        infer the next 06:00 IST rotation boundary.
    ml4t_india_version:
        Captured so a mismatched or ancient cache can be diagnosed
        without reading file metadata. Optional.
    """

    api_key: str
    access_token: str
    user_id: str = ""
    login_time: dt.datetime = field(
        default_factory=lambda: dt.datetime.now(tz=_IST)
    )
    ml4t_india_version: str | None = None

    # ---- serialisation ------------------------------------------------

    def to_json(self) -> str:
        """JSON-serialise the record; timestamps use ISO 8601 with offset."""
        payload = asdict(self)
        payload["login_time"] = self.login_time.isoformat()
        return json.dumps(payload, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> TokenRecord:
        """Parse a JSON string (as produced by :meth:`to_json`).

        Raises
        ------
        ValueError
            If the payload lacks the required fields or timestamps are
            malformed.
        """
        data = json.loads(raw)
        try:
            return cls(
                api_key=data["api_key"],
                access_token=data["access_token"],
                user_id=data.get("user_id", ""),
                login_time=dt.datetime.fromisoformat(data["login_time"]),
                ml4t_india_version=data.get("ml4t_india_version"),
            )
        except KeyError as exc:
            raise ValueError(
                f"token JSON is missing required field: {exc.args[0]}"
            ) from exc

    # ---- expiry logic ------------------------------------------------

    def is_expired(self, now: dt.datetime | None = None) -> bool:
        """Return True if the token has likely been rotated by Kite.

        Kite's docs state tokens are valid until approximately 06:00 IST
        the morning after login. We compute the next 06:00 IST boundary
        from :attr:`login_time` and consider the token expired once the
        clock passes it. False positives (treating a still-valid token
        as expired) only cost a re-login; false negatives would make
        callers hit Kite with a dead token and we'd rather not.

        Parameters
        ----------
        now:
            Test hook; defaults to :func:`datetime.datetime.now(_IST)`.
        """
        if now is None:
            now = dt.datetime.now(tz=_IST)
        # Convert to IST so the arithmetic below is unambiguous regardless
        # of login_time's original offset.
        ist_now = now.astimezone(_IST)
        ist_login = self.login_time.astimezone(_IST)
        # First 06:00 IST at or after login_time is the rotation boundary.
        rotation = ist_login.replace(
            hour=_DAILY_EXPIRY_HOUR, minute=0, second=0, microsecond=0
        )
        if rotation <= ist_login:
            # Logged in AFTER 06:00 today -> next rotation is tomorrow's 06:00.
            rotation = rotation + dt.timedelta(days=1)
        return ist_now >= rotation


# ---- on-disk persistence ----------------------------------------------


def save_token(record: TokenRecord, path: Path | None = None) -> Path:
    """Atomically write ``record`` to disk with restrictive permissions.

    Directories are created with mode ``0o700``; the file itself is
    written to a sibling tempfile and ``os.replace``-renamed into place
    so a concurrent :func:`load_token` never observes a half-written
    file. Returns the path the record landed at.
    """
    target = path or default_token_path()
    target = target.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Tighten parent dir permissions even if the directory already existed.
    # Non-fatal: some filesystems (e.g. Windows) do not support chmod.
    with contextlib.suppress(OSError):
        target.parent.chmod(0o700)

    serialised = record.to_json()
    # tempfile in the same directory so os.replace is atomic on POSIX.
    fd, tmp_name = tempfile.mkstemp(
        prefix=".token-", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialised)
        # Chmod BEFORE rename so no intermediate world-readable state.
        os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp_name, target)
    except Exception:
        # Best-effort cleanup on failure.
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return target


def load_token(path: Path | None = None) -> TokenRecord | None:
    """Read a previously-saved :class:`TokenRecord`, or ``None`` if absent.

    Does NOT check expiry -- that's :meth:`TokenRecord.is_expired`. The
    split is intentional: a CLI that wants to re-use an expired token as
    a hint ("user was last logged in as AB1234") can inspect the record
    even when :meth:`is_expired` is ``True``.

    Raises
    ------
    ValueError
        If the file exists but is unparseable; we surface the bug
        rather than silently re-running the login flow.
    """
    target = (path or default_token_path()).expanduser()
    if not target.exists():
        return None
    raw = target.read_text(encoding="utf-8")
    return TokenRecord.from_json(raw)


# ---- Kite login flow --------------------------------------------------


def login_url(api_key: str) -> str:
    """Return the URL the user should open to initiate Kite login.

    Thin wrapper over :meth:`kiteconnect.KiteConnect.login_url` so the
    kiteconnect SDK stays an implementation detail of this package.
    """
    if not api_key:
        raise ValueError("api_key must be non-empty")
    # KiteConnect is heavy to construct just for a URL, but the SDK
    # doesn't expose a stand-alone helper. Cost is one HTTP session
    # per call; never hits the network.
    return KiteConnect(api_key=api_key).login_url()


def generate_session(
    api_key: str,
    api_secret: str,
    request_token: str,
) -> TokenRecord:
    """Exchange ``request_token`` for an ``access_token`` and return a record.

    Raises
    ------
    ml4t.india.core.exceptions.IndiaError
        Any :class:`kiteconnect.exceptions.KiteException` raised by the
        SDK is translated through :func:`ml4t.india.kite.errors.translate`
        so callers can rely on the India error taxonomy.
    """
    if not api_key:
        raise ValueError("api_key must be non-empty")
    if not api_secret:
        raise ValueError("api_secret must be non-empty")
    if not request_token:
        raise ValueError("request_token must be non-empty")

    kite = KiteConnect(api_key=api_key)
    try:
        response: dict[str, Any] = kite.generate_session(
            request_token=request_token, api_secret=api_secret
        )
    except kexc.KiteException as kite_exc:
        raise translate(kite_exc) from kite_exc

    # kiteconnect returns a dict with access_token, user_id, etc.
    # We capture the fields we care about; everything else is ignored.
    # (If Kite ever drops access_token, KeyError here surfaces loudly
    # rather than silently storing a bad record.)
    return TokenRecord(
        api_key=api_key,
        access_token=response["access_token"],
        user_id=response.get("user_id", ""),
    )


__all__ = [
    "TokenRecord",
    "default_token_path",
    "generate_session",
    "load_token",
    "login_url",
    "save_token",
]
