from __future__ import annotations

import os
import threading

from config.config import DISCORD_TOKEN
from core.api import start_api
from core.bot import client


def main() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not configured. See .env.example.")
    print("=" * 58)
    print(" Rallybit 7.2.2 — Discord community activity toolkit")
    print("=" * 58)
    api_thread = threading.Thread(target=start_api, args=(client,), daemon=True, name="rallybit-api")
    api_thread.start()
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
