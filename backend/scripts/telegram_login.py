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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine_runtime import Pred, Rule, apply, first_match, pick

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env.local"
STATE_PATH = ROOT / ".telegram_login_state"
SESSION_PATH = ROOT / ".telegram_login_session"


def _raise(exc: BaseException) -> None:
    raise exc


def _load_env() -> None:
    def _parse() -> None:
        for line in ENV_PATH.read_text().splitlines():
            s = line.strip()

            def _set() -> None:
                k, _, v = s.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

            pick(not s or s.startswith("#") or "=" not in s, lambda: None, _set)

    pick(not ENV_PATH.exists(), lambda: None, _parse)


def _upsert_env(key: str, value: str) -> None:
    lines: list[str] = pick(ENV_PATH.exists(), lambda: ENV_PATH.read_text().splitlines(), lambda: [])
    out: list[str] = []
    seen = False
    for line in lines:
        matched = line.startswith(f"{key}=") or line.startswith(f"{key} =")
        pick(matched, lambda: out.append(f"{key}={value}"), lambda: out.append(line))
        seen = seen or matched
    pick(not seen, lambda: out.append(f"{key}={value}"), lambda: None)
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
    pick(
        not api_id or not api_hash,
        lambda: _raise(SystemExit("TELEGRAM_API_ID / TELEGRAM_API_HASH missing in .env.local")),
        lambda: None,
    )

    existing = pick(SESSION_PATH.exists(), lambda: SESSION_PATH.read_text().strip(), lambda: "")
    client = TelegramClient(StringSession(existing), api_id, api_hash)
    await client.connect()

    phone = (args.phone or "").strip()
    code = (args.code or "").strip()
    password = (args.password or "").strip()

    async def _send_code() -> None:
        sent = await client.send_code_request(phone)
        STATE_PATH.write_text(f"{phone}\n{sent.phone_code_hash}\n")
        SESSION_PATH.write_text(client.session.save())
        print(f"CODE_SENT to {phone}")
        print("Paste the Telegram login code ASAP.")
        await client.disconnect()

    async def _sign_in() -> None:
        pick(
            not STATE_PATH.exists(),
            lambda: _raise(SystemExit("No pending code — run with --phone first")),
            lambda: None,
        )
        saved_phone, phone_code_hash = STATE_PATH.read_text().strip().split("\n", 1)
        pick(
            saved_phone.strip() != phone,
            lambda: _raise(SystemExit(f"Phone mismatch (pending {saved_phone!r})")),
            lambda: None,
        )
        try:
            await client.sign_in(
                phone=phone, code=code, phone_code_hash=phone_code_hash.strip()
            )
        except SessionPasswordNeededError:
            async def _need_2fa() -> None:
                SESSION_PATH.write_text(client.session.save())
                print("2FA_REQUIRED — re-run with --password YOUR_2FA_PASSWORD")
                await client.disconnect()

            async def _with_password() -> None:
                await client.sign_in(password=password)

            await pick(not password, _need_2fa, _with_password)

    async def _need_args() -> None:
        _raise(SystemExit("Provide --phone, or --phone + --code"))

    await apply(
        first_match(
            (
                Rule(when=(Pred("phone", "truthy"), Pred("code", "falsey")), action="send"),
                Rule(when=(Pred("phone", "truthy"), Pred("code", "truthy")), action="signin"),
                Rule(when=(), action="need_args"),
            ),
            {"phone": phone, "code": code},
        ).action,
        {
            "send": _send_code,
            "signin": _sign_in,
            "need_args": _need_args,
        },
    )

    async def _finish() -> None:
        pick(
            not await client.is_user_authorized(),
            lambda: _raise(SystemExit("Login failed — not authorized")),
            lambda: None,
        )
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

    # send-code path already disconnected and returned via apply; only finish after sign-in.
    await pick(
        await client.is_connected(),
        _finish,
        lambda: asyncio.sleep(0),
    )


def _cli() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)


pick(__name__ == "__main__", _cli, lambda: None)
