from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands

from config.config import AFK_STATUS_FILE
from storage.json_store import load_json, save_json


def _data() -> dict[str, Any]:
    value = load_json(AFK_STATUS_FILE) or {}
    return value if isinstance(value, dict) else {}


def setup_afk_commands(tree: app_commands.CommandTree) -> None:
    @tree.command(name="afk", description="Set your AFK status and optional reason.")
    @app_commands.describe(reason="Why you are away")
    async def afk(interaction: discord.Interaction, reason: str = "AFK") -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this command in a server.", ephemeral=True)
            return
        reason = reason.strip()[:300] or "AFK"
        data = _data()
        guild_data = data.setdefault(str(interaction.guild.id), {})
        guild_data[str(interaction.user.id)] = {
            "reason": reason,
            "since": datetime.now(timezone.utc).isoformat(),
        }
        save_json(AFK_STATUS_FILE, data)
        await interaction.response.send_message(f"You are now marked AFK: **{reason}**", ephemeral=True)


async def on_message(message: discord.Message) -> None:
    if not message.guild or message.author.bot:
        return
    data = _data()
    guild_data = data.get(str(message.guild.id), {})
    if not isinstance(guild_data, dict):
        return

    author_key = str(message.author.id)
    was_afk = guild_data.pop(author_key, None)
    if was_afk is not None:
        if guild_data:
            data[str(message.guild.id)] = guild_data
        else:
            data.pop(str(message.guild.id), None)
        save_json(AFK_STATUS_FILE, data)
        try:
            await message.reply("Welcome back. I removed your AFK status.", mention_author=False, delete_after=8)
        except discord.HTTPException:
            pass

    notices: list[str] = []
    for member in message.mentions[:10]:
        record = guild_data.get(str(member.id))
        if not isinstance(record, dict):
            continue
        since_text = ""
        try:
            since = datetime.fromisoformat(str(record.get("since")))
            since_text = f" since <t:{int(since.timestamp())}:R>"
        except (TypeError, ValueError):
            pass
        notices.append(f"**{member.display_name}** is AFK{since_text}: {str(record.get('reason', 'AFK'))[:300]}")
    if notices:
        try:
            await message.reply("\n".join(notices), mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass
