"""One-time Telethon login → print StringSession for TELEGRAM_SESSION."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Load backend/.env.local
env_path = Path(__file__).resolve().parents[1] / ".env.local"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    api_id = int(os.environ.get("TELEGRAM_API_ID") or "0")
    api_hash = (os.environ.get("TELEGRAM_API_HASH") or "").strip()
    if not api_id or not api_hash:
        raise SystemExit("TELEGRAM_API_ID / TELEGRAM_API_HASH missing in .env.local")

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()
    me = await client.get_me()
    session = client.session.save()
    print("---")
    print(f"logged in as: {getattr(me, 'username', None) or me.id}")
    print("TELEGRAM_SESSION (copy into backend/.env.local):")
    print(session)
    print("---")
    await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
