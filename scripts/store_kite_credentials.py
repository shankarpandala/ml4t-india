"""One-time credential setup for ml4t-india integration tests.

Stores Kite Connect credentials in the OS keychain:
  - Windows: Windows Credential Manager (search "ml4t-india")
  - Linux:   GNOME Keyring / libsecret
  - macOS:   Keychain Access

Usage:
    python scripts/store_kite_credentials.py             # first-time setup
    python scripts/store_kite_credentials.py --refresh   # daily token refresh
    python scripts/store_kite_credentials.py --auto-setup# OPT-IN auto-login secrets
    python scripts/store_kite_credentials.py --verify    # print masked values
    python scripts/store_kite_credentials.py --clear     # delete all entries
"""

from __future__ import annotations

import argparse
import getpass
import sys

try:
    import keyring
    import keyring.errors
except ImportError:
    print("ERROR: keyring not installed. Run: pip install keyring>=25")
    sys.exit(1)

_SERVICE = "ml4t-india"
_ALL_KEYS = ["kite_api_key", "kite_api_secret", "kite_request_token", "kite_access_token"]

# OPT-IN automated-login secrets (password + TOTP seed). Stored ONLY in the
# OS keychain, never written to disk, argv, or logs. See `--auto-setup`.
# SECURITY: co-locating the password and the TOTP seed on one host defeats
# the two-party 2FA split and is strictly weaker than manual login.
_AUTO_LOGIN_KEYS = ["kite_user_id", "kite_password", "kite_totp_secret"]


def _mask(value: str) -> str:
    if len(value) <= 6:
        return "***"
    return value[:3] + "*" * (len(value) - 6) + value[-3:]


def _get(key: str) -> str | None:
    return keyring.get_password(_SERVICE, key)


def _set(key: str, value: str) -> None:
    keyring.set_password(_SERVICE, key, value)


def _delete(key: str) -> bool:
    try:
        keyring.delete_password(_SERVICE, key)
        return True
    except keyring.errors.PasswordDeleteError:
        return False


def cmd_store(refresh_only: bool = False) -> None:
    if not refresh_only:
        print("=== ml4t-india Kite credential setup ===")
        print("Credentials go to the OS keychain — nothing is written to disk or git.\n")

        api_key = getpass.getpass("Kite API key: ").strip()
        if not api_key:
            print("ERROR: API key cannot be empty.")
            sys.exit(1)
        api_secret = getpass.getpass("Kite API secret: ").strip()
        if not api_secret:
            print("ERROR: API secret cannot be empty.")
            sys.exit(1)

        _set("kite_api_key", api_key)
        _set("kite_api_secret", api_secret)
        print("\nAPI key and secret stored in keychain.")

        try:
            from ml4t.india.kite.auth import login_url

            url = login_url(api_key)
        except ImportError:
            url = f"https://kite.trade/connect/login?api_key={api_key}&v=3"

        print("\nOpen this URL in your browser to log in:")
        print(f"  {url}")
        print(
            "\nAfter login you are redirected to your app URL with ?request_token=XXX in the URL."
        )
        print("Copy that token and paste it below.\n")
    else:
        print("=== Daily token refresh ===")

    api_key = _get("kite_api_key")
    api_secret = _get("kite_api_secret")
    if not api_key or not api_secret:
        print("ERROR: API key/secret not found. Run without --refresh first.")
        sys.exit(1)

    request_token = getpass.getpass("Request token (from browser redirect URL): ").strip()
    if not request_token:
        print("ERROR: Request token cannot be empty.")
        sys.exit(1)
    _set("kite_request_token", request_token)

    print("Generating access token...")
    try:
        from ml4t.india.kite.auth import generate_session

        record = generate_session(api_key, api_secret, request_token)
        access_token = record.access_token
    except ImportError:
        from kiteconnect import KiteConnect

        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(request_token=request_token, api_secret=api_secret)
        access_token = data["access_token"]

    _set("kite_access_token", access_token)
    print("Access token stored. Valid until ~06:00 IST tomorrow.")
    print("\nRun: pytest -m integration -v")


def cmd_auto_setup() -> None:
    """Capture OPT-IN automated-login secrets straight into the keychain.

    Prompts for user_id, password, and TOTP secret via getpass (never
    echoed) and stores each in the OS keychain. NOTHING is written to disk,
    argv, or logs.
    """
    print("=== ml4t-india automated-login setup (OPT-IN) ===")
    print(
        "SECURITY WARNING: this stores your Kite password AND your TOTP seed\n"
        "together in the OS keychain. That co-locates both 2FA factors on one\n"
        "host and is STRICTLY WEAKER than the default manual login. Only do\n"
        "this on a machine you control. Nothing is written to disk or git.\n"
    )

    user_id = getpass.getpass("Kite user id (client code): ").strip()
    if not user_id:
        print("ERROR: user id cannot be empty.")
        sys.exit(1)
    password = getpass.getpass("Kite password: ").strip()
    if not password:
        print("ERROR: password cannot be empty.")
        sys.exit(1)
    totp_secret = getpass.getpass("Kite TOTP secret (base32 seed, not a code): ").strip()
    if not totp_secret:
        print("ERROR: TOTP secret cannot be empty.")
        sys.exit(1)

    _set("kite_user_id", user_id)
    _set("kite_password", password)
    _set("kite_totp_secret", totp_secret)

    if not (_get("kite_api_key") and _get("kite_api_secret")):
        print(
            "\nuser_id/password/TOTP stored. NOTE: kite_api_key/kite_api_secret\n"
            "are not yet set -- run `python scripts/store_kite_credentials.py`\n"
            "(or pass --api-key/--api-secret to `ml4t-india login`)."
        )
    else:
        print("\nAutomated-login secrets stored in keychain.")
    print("Then log in headlessly with: ml4t-india login --method auto")


def cmd_verify() -> None:
    print("=== Stored credentials (masked) ===")
    all_present = True
    for key in _ALL_KEYS:
        value = _get(key)
        if value:
            print(f"  {key}: {_mask(value)}")
        else:
            print(f"  {key}: NOT SET")
            all_present = False
    print("\n--- automated-login secrets (OPT-IN) ---")
    for key in _AUTO_LOGIN_KEYS:
        value = _get(key)
        print(f"  {key}: {_mask(value) if value else 'NOT SET'}")
    if all_present:
        print("\nAll integration credentials stored. Ready: pytest -m integration -v")
    else:
        print("\nSome credentials missing. Run: python scripts/store_kite_credentials.py")


def cmd_clear() -> None:
    print("Clearing all ml4t-india credentials from keychain...")
    for key in _ALL_KEYS + _AUTO_LOGIN_KEYS:
        if _delete(key):
            print(f"  Deleted: {key}")
        else:
            print(f"  Not found (already clear): {key}")
    print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage ml4t-india Kite credentials in the OS keychain."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--refresh", action="store_true", help="Refresh the daily access token only")
    group.add_argument(
        "--auto-setup",
        action="store_true",
        help="OPT-IN: store user_id/password/TOTP secret for `login --method auto`",
    )
    group.add_argument("--verify", action="store_true", help="Print masked stored credentials")
    group.add_argument("--clear", action="store_true", help="Delete all stored credentials")
    args = parser.parse_args()

    if args.verify:
        cmd_verify()
    elif args.clear:
        cmd_clear()
    elif args.auto_setup:
        cmd_auto_setup()
    elif args.refresh:
        cmd_store(refresh_only=True)
    else:
        cmd_store(refresh_only=False)


if __name__ == "__main__":
    main()
