from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .database import Database
from .proxy import load_mtproto_proxy, telethon_client_options


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the Telegram login session used by the downloader.")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args()
    try:
        from telethon import connection
        from telethon.sync import TelegramClient
    except ImportError as exc:
        raise SystemExit("Telethon is not installed. Run: python -m pip install -r requirements.txt") from exc

    config = load_config(args.config.resolve())
    db = Database(config.database_file)
    mtproxy = load_mtproto_proxy(db)
    print("Telegram will ask for your phone number, login code, and 2FA password if enabled.")
    if mtproxy.enabled:
        print(f"Using MTProto proxy {mtproxy.host}:{mtproxy.port}.")
    with TelegramClient(
        str(config.session_file), config.api_id, config.api_hash,
        **telethon_client_options(mtproxy, connection),
    ) as client:
        user = client.get_me()
        print(f"Session ready for {user.first_name or ''} (@{user.username or 'no_username'}).")


if __name__ == "__main__":
    main()
