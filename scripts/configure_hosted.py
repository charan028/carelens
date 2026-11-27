"""Set up hosted mode with a Supabase (or any PostgreSQL) database, step by step.

    .venv/bin/python scripts/configure_hosted.py

Asks for the connection line and the password separately, so nothing has to be
edited by hand or pasted as several terminal lines. It tests the connection
and saves the settings to infra/.env, which scripts/run_demo.sh then uses.
infra/.env is in .gitignore and must never be committed.
"""
from __future__ import annotations

import getpass
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "infra" / ".env"


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def existing_settings() -> dict[str, str]:
    settings: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            match = re.match(r"^(?:export )?([A-Z_]+)='(.*)'$", line.strip())
            if match:
                settings[match.group(1)] = match.group(2)
    return settings


def read_address() -> tuple[str, str, int, str]:
    """Returns (user, host, port, database) from the pasted connection line."""
    print("\nIn Supabase press Connect, set Type to URI and Method to Session pooler,")
    print("and copy the line. Leave the [YOUR-...PASSWORD] placeholder in it exactly as shown.\n")
    while True:
        raw = ask("Paste the Session pooler line").strip("'\"` ")
        if raw.lower().startswith("psql"):
            print("  That is a psql command. Set Type to URI in the Connect window and copy that line.\n")
            continue
        if not re.match(r"^postgres(ql)?://", raw):
            print("  It should start with postgresql:// . Copy the whole line and try again.\n")
            continue
        # Read the parts by pattern: the [YOUR-PASSWORD] placeholder (or a password
        # with special characters) makes a strict URL parser give up.
        match = re.match(r"^postgres(?:ql)?://([^:@/]+)(?::.*)?@([^@/:]+)(?::(\d+))?/([^?\s]+)", raw)
        if not match:
            print("  I couldn't read that line. Copy it again without changing it.\n")
            continue
        user, host, database = match.group(1), match.group(2), match.group(4)
        port = int(match.group(3) or 5432)
        if host.startswith("db.") and host.endswith(".supabase.co"):
            print("  That is the Direct connection address, which most networks (and Render) cannot reach.")
            print("  In the Connect window change Method to Session pooler and copy that line.\n")
            continue
        if not host.endswith(".pooler.supabase.com"):
            if ask(f"  '{host}' is not a Supabase pooler address. Use it anyway? (y/n)", "n").lower() != "y":
                continue
        return user, host, port, database


def test_connection(url: str) -> str | None:
    try:
        import psycopg
    except ImportError:
        return "the database driver is not installed; run ./scripts/run_demo.sh once first"
    try:
        with psycopg.connect(url, connect_timeout=10) as connection:
            connection.execute("SELECT 1")
        return None
    except Exception as error:
        return str(error).strip().splitlines()[0][:300]


def main() -> int:
    saved = existing_settings()
    print("CareLens hosted-mode setup. Synthetic data only.")
    user, host, port, database = read_address()

    while True:
        password = getpass.getpass("\nDatabase password (typing stays hidden): ")
        if not password or re.search(r"YOUR-.*PASSWORD", password):
            print("  Enter the database password you set in Supabase.")
            continue
        # Percent-encode, so a password with @ : / # or spaces is safe in the address.
        url = f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{database}"
        print(f"\nTesting the connection to {host}:{port} as {user} ...")
        problem = test_connection(url)
        if problem is None:
            print("Connection: OK")
            break
        print(f"Connection FAILED: {problem.replace(password, '***')}")
        if "password authentication failed" in problem:
            print("  The password is wrong. You can reset it in Supabase under Database settings.")
        elif "Tenant or user not found" in problem:
            print("  The user or region in the line is wrong. Copy the Session pooler line again.")
            return main()
        if ask("Try the password again? (y/n)", "y").lower() != "y":
            return 1

    # Pressing Enter accepts the suggestion, so this step cannot get stuck.
    suggestion = saved.get("CARELENS_ACCESS_CODE") or "care-" + secrets.token_hex(3)
    while True:
        code = ask("\nAccess code testers will type. Press Enter to use the one shown", suggestion)
        if re.fullmatch(r"[A-Za-z0-9_-]{6,60}", code):
            break
        print("  Use at least 6 characters: letters, digits, dashes or underscores. No spaces.")
    print(f"Access code: {code}")

    # Keep the same key across runs so open pages are not signed out.
    secret = saved.get("CARELENS_SECRET_KEY") or secrets.token_hex(24)
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text(
        "# Written by scripts/configure_hosted.py. Holds a database password: never commit or share.\n"
        "CARELENS_SANDBOXES='1'\n"
        f"CARELENS_ACCESS_CODE='{code}'\n"
        f"CARELENS_SECRET_KEY='{secret}'\n"
        f"CARELENS_DATABASE_URL='{url}'\n"
    )
    os.chmod(ENV_FILE, 0o600)
    print(f"\nSaved to {ENV_FILE.relative_to(ROOT)} (not tracked by git).")
    print("Try hosted mode locally:    ./scripts/run_demo.sh --hosted")
    print("Plain local demo (default): ./scripts/run_demo.sh")
    print("\nFor Render, paste this same address into CARELENS_DATABASE_URL in the dashboard.")
    print(f"You can read it from {ENV_FILE.relative_to(ROOT)}; it contains your password, so keep it private.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. Nothing was changed.")
        sys.exit(1)
