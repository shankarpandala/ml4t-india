"""Unit tests for the OPT-IN automated Kite login flow.

NO network, NO real credentials. The three web requests are served by an
injected fake ``session`` and ``generate_session`` is monkeypatched so the
SDK boundary is never touched. The TOTP secret below is the canonical RFC
test vector, NOT a real seed.
"""

from __future__ import annotations

import pytest

pyotp = pytest.importorskip("pyotp")

from ml4t.india.core.exceptions import (  # noqa: E402
    IndiaError,
    InvalidInputError,
    NetworkError,
    PermissionDeniedError,
)
from ml4t.india.kite import auth  # noqa: E402
from ml4t.india.kite.auth import TokenRecord, automated_login  # noqa: E402

# RFC 4648 base32 test vector -- a FAKE seed, never a real credential.
FAKE_TOTP_SECRET = "JBSWY3DPEHPK3PXP"
FAKE_API_KEY = "fake_api_key"
FAKE_API_SECRET = "fake_api_secret"
FAKE_USER_ID = "AB1234"
FAKE_PASSWORD = "not-a-real-password"  # noqa: S105 (fake fixture, not a secret)
FAKE_REQUEST_TOKEN = "fake_request_token_xyz"


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, *, status_code=200, json_body=None, headers=None, html=False):
        self.status_code = status_code
        self._json_body = json_body
        self.headers = headers or {}
        self._html = html

    def json(self):
        if self._html or self._json_body is None:
            raise ValueError("No JSON could be decoded")
        return self._json_body


class FakeSession:
    """Programmable fake ``requests.Session`` that records calls."""

    def __init__(self, *, login=None, twofa=None, connect=None):
        # Each may be a single FakeResponse or a list (consumed in order).
        self._responses = {"login": login, "twofa": twofa, "connect": connect}
        self.calls: list[tuple[str, str, dict]] = []

    def _next(self, key):
        resp = self._responses[key]
        if isinstance(resp, list):
            return resp.pop(0)
        return resp

    def post(self, url, data=None, **kwargs):
        key = "login" if url.endswith("/api/login") else "twofa"
        self.calls.append((url, "POST", data or {}))
        return self._next(key)

    def get(self, url, **kwargs):
        self.calls.append((url, "GET", kwargs))
        return self._next("connect")


def _ok_login():
    return FakeResponse(json_body={"status": "success", "data": {"request_id": "req-123"}})


def _ok_twofa():
    return FakeResponse(json_body={"status": "success", "data": {"profile": {}}})


def _ok_connect():
    location = (
        "https://myapp.example/redirect"
        f"?action=login&status=success&request_token={FAKE_REQUEST_TOKEN}"
    )
    return FakeResponse(status_code=302, headers={"Location": location})


@pytest.fixture
def patched_generate_session(monkeypatch):
    """Replace the SDK boundary with a recorder returning a TokenRecord."""
    captured = {}

    def fake_generate_session(api_key, api_secret, request_token):
        captured["api_key"] = api_key
        captured["api_secret"] = api_secret
        captured["request_token"] = request_token
        return TokenRecord(api_key=api_key, access_token="fake_access_token", user_id=FAKE_USER_ID)

    monkeypatch.setattr(auth, "generate_session", fake_generate_session)
    return captured


def test_happy_path_returns_token_record(patched_generate_session):
    session = FakeSession(login=_ok_login(), twofa=_ok_twofa(), connect=_ok_connect())

    record = automated_login(
        FAKE_API_KEY,
        FAKE_API_SECRET,
        FAKE_USER_ID,
        FAKE_PASSWORD,
        FAKE_TOTP_SECRET,
        session=session,
    )

    assert isinstance(record, TokenRecord)
    assert record.access_token == "fake_access_token"
    # request_token was parsed from the 302 Location header and handed to
    # the SDK boundary verbatim.
    assert patched_generate_session["request_token"] == FAKE_REQUEST_TOKEN


def test_twofa_value_is_a_fresh_totp_code(patched_generate_session):
    session = FakeSession(login=_ok_login(), twofa=_ok_twofa(), connect=_ok_connect())

    automated_login(
        FAKE_API_KEY,
        FAKE_API_SECRET,
        FAKE_USER_ID,
        FAKE_PASSWORD,
        FAKE_TOTP_SECRET,
        session=session,
    )

    twofa_call = next(c for c in session.calls if c[0].endswith("/api/twofa"))
    _, _, data = twofa_call
    assert data["twofa_type"] == "totp"
    assert data["user_id"] == FAKE_USER_ID
    assert data["request_id"] == "req-123"
    # The submitted code must be a valid TOTP for the fake seed.
    assert pyotp.TOTP(FAKE_TOTP_SECRET).verify(data["twofa_value"], valid_window=1)


def test_login_page_form_sends_user_and_password(patched_generate_session):
    session = FakeSession(login=_ok_login(), twofa=_ok_twofa(), connect=_ok_connect())

    automated_login(
        FAKE_API_KEY,
        FAKE_API_SECRET,
        FAKE_USER_ID,
        FAKE_PASSWORD,
        FAKE_TOTP_SECRET,
        session=session,
    )

    login_call = next(c for c in session.calls if c[0].endswith("/api/login"))
    _, _, data = login_call
    assert data == {"user_id": FAKE_USER_ID, "password": FAKE_PASSWORD}


def test_invalid_totp_raises_invalid_input(monkeypatch, patched_generate_session):
    # No real waiting: the retry path sleeps before the second attempt.
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    twofa_err = FakeResponse(
        status_code=403,
        json_body={
            "status": "error",
            "error_type": "TwoFAException",
            "message": "Invalid TOTP",
        },
    )
    # Two failures (original + single retry) -> propagates.
    session = FakeSession(login=_ok_login(), twofa=[twofa_err, twofa_err], connect=_ok_connect())

    with pytest.raises(InvalidInputError):
        automated_login(
            FAKE_API_KEY,
            FAKE_API_SECRET,
            FAKE_USER_ID,
            FAKE_PASSWORD,
            FAKE_TOTP_SECRET,
            session=session,
        )


def test_totp_retry_succeeds_on_second_attempt(monkeypatch, patched_generate_session):
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    twofa_err = FakeResponse(
        status_code=403,
        json_body={"status": "error", "error_type": "TwoFAException", "message": "skew"},
    )
    session = FakeSession(login=_ok_login(), twofa=[twofa_err, _ok_twofa()], connect=_ok_connect())

    record = automated_login(
        FAKE_API_KEY,
        FAKE_API_SECRET,
        FAKE_USER_ID,
        FAKE_PASSWORD,
        FAKE_TOTP_SECRET,
        session=session,
    )
    assert isinstance(record, TokenRecord)


def test_bad_password_raises_permission_denied(patched_generate_session):
    login_err = FakeResponse(
        status_code=403,
        json_body={
            "status": "error",
            "error_type": "UserException",
            "message": "Invalid user or password",
        },
    )
    session = FakeSession(login=login_err)

    with pytest.raises(PermissionDeniedError):
        automated_login(
            FAKE_API_KEY,
            FAKE_API_SECRET,
            FAKE_USER_ID,
            FAKE_PASSWORD,
            FAKE_TOTP_SECRET,
            session=session,
        )


def test_rate_limit_raises_network_error(patched_generate_session):
    session = FakeSession(login=FakeResponse(status_code=429, json_body={}))

    with pytest.raises(NetworkError):
        automated_login(
            FAKE_API_KEY,
            FAKE_API_SECRET,
            FAKE_USER_ID,
            FAKE_PASSWORD,
            FAKE_TOTP_SECRET,
            session=session,
        )


def test_html_login_response_raises_india_error(patched_generate_session):
    session = FakeSession(login=FakeResponse(html=True))

    with pytest.raises(IndiaError):
        automated_login(
            FAKE_API_KEY,
            FAKE_API_SECRET,
            FAKE_USER_ID,
            FAKE_PASSWORD,
            FAKE_TOTP_SECRET,
            session=session,
        )


def test_missing_request_token_raises_india_error(patched_generate_session):
    # connect/login returns a 302 with no request_token in the Location.
    bad_connect = FakeResponse(
        status_code=302,
        headers={"Location": "https://myapp.example/redirect?status=success"},
    )
    session = FakeSession(login=_ok_login(), twofa=_ok_twofa(), connect=bad_connect)

    with pytest.raises(IndiaError):
        automated_login(
            FAKE_API_KEY,
            FAKE_API_SECRET,
            FAKE_USER_ID,
            FAKE_PASSWORD,
            FAKE_TOTP_SECRET,
            session=session,
        )


def test_no_secret_appears_in_error_messages(patched_generate_session):
    """Error messages must never echo password or TOTP secret."""
    login_err = FakeResponse(
        status_code=403,
        json_body={"status": "error", "error_type": "UserException", "message": "bad"},
    )
    session = FakeSession(login=login_err)

    with pytest.raises(PermissionDeniedError) as excinfo:
        automated_login(
            FAKE_API_KEY,
            FAKE_API_SECRET,
            FAKE_USER_ID,
            FAKE_PASSWORD,
            FAKE_TOTP_SECRET,
            session=session,
        )
    rendered = str(excinfo.value)
    assert FAKE_PASSWORD not in rendered
    assert FAKE_TOTP_SECRET not in rendered


# --- CLI keychain wiring ----------------------------------------------------


def test_cli_auto_reads_keychain_keys(monkeypatch):
    """The CLI auto path must read the agreed keychain service/key names."""
    pytest.importorskip("keyring")
    from click.testing import CliRunner

    from ml4t.india.cli import main as cli_main

    reads: list[tuple[str, str]] = []
    store = {
        "kite_api_key": FAKE_API_KEY,
        "kite_api_secret": FAKE_API_SECRET,
        "kite_user_id": FAKE_USER_ID,
        "kite_password": FAKE_PASSWORD,
        "kite_totp_secret": FAKE_TOTP_SECRET,
    }

    def fake_get_password(service, key):
        reads.append((service, key))
        return store.get(key)

    import keyring

    monkeypatch.setattr(keyring, "get_password", fake_get_password)

    captured = {}

    def fake_automated_login(*, api_key, api_secret, user_id, password, totp_secret):
        captured.update(
            api_key=api_key,
            api_secret=api_secret,
            user_id=user_id,
            password=password,
            totp_secret=totp_secret,
        )
        return TokenRecord(api_key=api_key, access_token="tok", user_id=user_id)

    monkeypatch.setattr(auth, "automated_login", fake_automated_login)
    # Avoid touching disk.
    saved = {}
    monkeypatch.setattr(cli_main, "save_token", lambda *_a, **_k: saved.setdefault("p", "/tmp/x"))

    runner = CliRunner()
    result = runner.invoke(cli_main.cli, ["login", "--method", "auto"])

    assert result.exit_code == 0, result.output
    # Every expected key was read from the right service.
    assert ("ml4t-india", "kite_user_id") in reads
    assert ("ml4t-india", "kite_password") in reads
    assert ("ml4t-india", "kite_totp_secret") in reads
    assert captured["user_id"] == FAKE_USER_ID
    assert captured["totp_secret"] == FAKE_TOTP_SECRET


def test_cli_auto_missing_secret_errors_friendly(monkeypatch):
    pytest.importorskip("keyring")
    import keyring
    from click.testing import CliRunner

    from ml4t.india.cli import main as cli_main

    # Nothing in the keychain.
    monkeypatch.setattr(keyring, "get_password", lambda *_a, **_k: None)

    runner = CliRunner()
    result = runner.invoke(cli_main.cli, ["login", "--method", "auto"])

    assert result.exit_code == 1
    assert "store_kite_credentials" in result.output
