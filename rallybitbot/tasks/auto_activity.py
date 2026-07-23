from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from discord.ext import tasks

from config.config import ACTIVE_CHECKS_FILE, BANNED_SERVERS_FILE
from core.logging import get_guild_settings, set_guild_settings
from storage.json_store import load_json

_bot = None

@tasks.loop(minutes=1)
async def auto_activity_loop():
    if _bot is None:
        return
    banned = load_json(BANNED_SERVERS_FILE) or {}
    persisted = load_json(ACTIVE_CHECKS_FILE) or {}
    now = datetime.utcnow()
    from commands.activity import active_guild_checks, run_activitycheck

    for guild in list(_bot.guilds):
        if str(guild.id) in banned or str(guild.id) in persisted or guild.id in active_guild_checks:
            continue
        settings = get_guild_settings(guild.id)
        if not settings.get("auto_enabled"):
            continue
        channel_id = settings.get("auto_channel") or settings.get("channel_id")
        try:
            channel = guild.get_channel(int(channel_id)) if channel_id else None
        except (TypeError, ValueError):
            channel = None
        if channel is None or guild.me is None:
            continue
        try:
            interval = max(1, min(168, int(settings.get("auto_hours", 1))))
            last = datetime.fromisoformat(str(settings.get("last_auto_check", "2000-01-01T00:00:00")))
        except (TypeError, ValueError):
            last = datetime(2000, 1, 1)
            interval = 1
        if now - last < timedelta(hours=interval):
            continue

        # Save the run time before starting so a restart cannot duplicate the check.
        settings["last_auto_check"] = now.isoformat()
        set_guild_settings(guild.id, settings)
        active_guild_checks[guild.id] = asyncio.Event()

        async def launch_auto_check(target_guild=guild, target_channel=channel):
            try:
                await run_activitycheck(target_guild, target_channel, target_guild.me)
            except Exception as exc:
                active_guild_checks.pop(target_guild.id, None)
                print(f"[AUTO] {target_guild.id}: {exc}")

        asyncio.create_task(launch_auto_check(), name=f"rallybit-auto-{guild.id}")

def setup_auto_activity(bot):
    global _bot
    _bot = bot
    if not auto_activity_loop.is_running():
        auto_activity_loop.start()
