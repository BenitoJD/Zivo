"""One-time Telethon login → write StringSession into backend/.env.local.

  python scripts/telegram_login.py --phone +91XXXXXXXXXX
  python scripts/telegram_login.py --phone +91XXXXXXXXXX --code 12345
  python scripts/telegram_login.py --phone +91... --code 12345 --password 2FA
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env.local"
STATE_PATH = ROOT / ".telegram_login_state"
SESSION_PATH = ROOT / ".telegram_login_session"


def _load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _upsert_env(key: str, value: str) -> None:
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()
    out: list[str] = []
    seen = False
    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
            out.append(f"{key}={value}")
            seen = True
        else:
            out.append(line)
    if not seen:
        out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n")


async def main() -> None:
    _load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", default="")
    parser.add_argument("--code", default="")
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.sessions import StringSession

    api_id = int(os.environ.get("TELEGRAM_API_ID") or "0")
    api_hash = (os.environ.get("TELEGRAM_API_HASH") or "").strip()
    if not api_id or not api_hash:
        raise SystemExit("TELEGRAM_API_ID / TELEGRAM_API_HASH missing in .env.local")

    # Reuse same StringSession across --phone and --code steps.
    existing = SESSION_PATH.read_text().strip() if SESSION_PATH.exists() else ""
    client = TelegramClient(StringSession(existing), api_id, api_hash)
    await client.connect()

    phone = (args.phone or "").strip()
    code = (args.code or "").strip()
    password = (args.password or "").strip()

    if phone and not code:
        sent = await client.send_code_request(phone)
        STATE_PATH.write_text(f"{phone}\n{sent.phone_code_hash}\n")
        SESSION_PATH.write_text(client.session.save())
        print(f"CODE_SENT to {phone}")
        print("Paste the Telegram login code ASAP.")
        await client.disconnect()
        return

    if phone and code:
        if not STATE_PATH.exists():
            raise SystemExit("No pending code — run with --phone first")
        saved_phone, phone_code_hash = STATE_PATH.read_text().strip().split("\n", 1)
        if saved_phone.strip() != phone:
            raise SystemExit(f"Phone mismatch (pending {saved_phone!r})")
        try:
            await client.sign_in(
                phone=phone, code=code, phone_code_hash=phone_code_hash.strip()
            )
        except SessionPasswordNeededError:
            if not password:
                SESSION_PATH.write_text(client.session.save())
                print("2FA_REQUIRED — re-run with --password YOUR_2FA_PASSWORD")
                await client.disconnect()
                return
            await client.sign_in(password=password)
    else:
        raise SystemExit("Provide --phone, or --phone + --code")

    if not await client.is_user_authorized():
        raise SystemExit("Login failed — not authorized")

    me = await client.get_me()
    session = client.session.save()
    _upsert_env("TELEGRAM_SESSION", session)
    STATE_PATH.unlink(missing_ok=True)
    SESSION_PATH.unlink(missing_ok=True)
    print("---")
    print(f"logged in as: {getattr(me, 'username', None) or me.id}")
    print(f"TELEGRAM_SESSION written to {ENV_PATH} (len={len(session)})")
    print("---")
    await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
