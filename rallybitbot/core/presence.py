from __future__ import annotations

import discord
from discord.ext import tasks

from core.bot_settings import get_bot_settings


_current_presence_idx = 0
VALID_PRESENCE_STATUSES = {"online", "idle", "dnd", "offline"}


def normalise_presence_status(value: object) -> str:
    status = str(value or "online").strip().lower()
    return status if status in VALID_PRESENCE_STATUSES else "online"


def discord_presence_status(value: object) -> discord.Status:
    return {
        "online": discord.Status.online,
        "idle": discord.Status.idle,
        "dnd": discord.Status.dnd,
        # Discord calls the intentionally-offline presence "invisible".
        "offline": discord.Status.invisible,
    }[normalise_presence_status(value)]


async def apply_presence(client: discord.Client, *, advance: bool = False) -> None:
    global _current_presence_idx

    settings = get_bot_settings()
    global_cfg = settings.get("global", {})
    presences = global_cfg.get("presences", [])
    if not isinstance(presences, list) or not presences:
        presences = [{"text": "Rallybit | {servers} servers", "type": "watching"}]

    if _current_presence_idx >= len(presences):
        _current_presence_idx = 0
    presence = presences[_current_presence_idx]
    if not isinstance(presence, dict):
        presence = {"text": "Rallybit", "type": "watching"}

    total_members = sum(guild.member_count or 0 for guild in client.guilds)
    final_text = (
        str(presence.get("text") or "Rallybit")
        .replace("{servers}", f"{len(client.guilds):,}")
        .replace("{members}", f"{total_members:,}")
    )[:128]
    activity_type = {
        "watching": discord.ActivityType.watching,
        "playing": discord.ActivityType.playing,
        "listening": discord.ActivityType.listening,
        "competing": discord.ActivityType.competing,
    }.get(str(presence.get("type") or "watching").lower(), discord.ActivityType.watching)

    await client.change_presence(
        status=discord_presence_status(global_cfg.get("presence_status")),
        activity=discord.Activity(type=activity_type, name=final_text),
    )
    if advance:
        _current_presence_idx = (_current_presence_idx + 1) % len(presences)


@tasks.loop(seconds=60)
async def rotate_presence(client: discord.Client) -> None:
    try:
        await apply_presence(client, advance=True)
    except Exception as exc:
        print(f"[PRESENCE] Unable to update presence: {exc!r}")
