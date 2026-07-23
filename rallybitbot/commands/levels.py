from __future__ import annotations

import math
import random
import time
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands

from config.config import LEVEL_SETTINGS_FILE, LEVEL_STATS_FILE
from storage.json_store import load_json, save_json

BRAND = 0x5865F2
COOLDOWNS: dict[tuple[int, int], float] = {}


def _settings(guild_id: int) -> dict[str, Any]:
    all_data = load_json(LEVEL_SETTINGS_FILE) or {}
    defaults = {
        "enabled": False,
        "xp_min": 15,
        "xp_max": 25,
        "cooldown_seconds": 60,
        "announce_channel_id": None,
        "announce_message": "🎉 {user} reached **level {level}**!",
        "reward_roles": {},
        "ignored_channel_ids": [],
        "ignored_role_ids": [],
    }
    saved = all_data.get(str(guild_id), {})
    if isinstance(saved, dict):
        defaults.update(saved)
    return defaults


def _save_settings(guild_id: int, settings: dict[str, Any]) -> None:
    data = load_json(LEVEL_SETTINGS_FILE) or {}
    data[str(guild_id)] = settings
    save_json(LEVEL_SETTINGS_FILE, data)


def _stats() -> dict[str, Any]:
    data = load_json(LEVEL_STATS_FILE) or {}
    return data if isinstance(data, dict) else {}


def _save_stats(data: dict[str, Any]) -> None:
    save_json(LEVEL_STATS_FILE, data)


def level_from_xp(xp: int) -> int:
    return max(0, int(math.sqrt(max(0, xp) / 100)))


def xp_for_level(level: int) -> int:
    return max(0, int(level) ** 2 * 100)


def _member_record(guild_id: int, user_id: int, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    data = _stats()
    guild_data = data.setdefault(str(guild_id), {})
    record = guild_data.setdefault(str(user_id), {
        "xp": 0,
        "level": 0,
        "messages": 0,
        "display_name": name,
        "last_message_at": None,
    })
    record["display_name"] = name
    return data, record


async def _apply_reward_roles(member: discord.Member, level: int, settings: dict[str, Any]) -> list[discord.Role]:
    added: list[discord.Role] = []
    rewards = settings.get("reward_roles", {})
    if not isinstance(rewards, dict):
        return added
    me = member.guild.me
    for level_text, role_id in rewards.items():
        try:
            required_level = int(level_text)
            role = member.guild.get_role(int(role_id))
        except (TypeError, ValueError):
            continue
        if required_level <= level and role and role not in member.roles and not role.managed and me and role < me.top_role:
            try:
                await member.add_roles(role, reason=f"Rallybit level {required_level} reward")
                added.append(role)
            except discord.HTTPException:
                pass
    return added


async def on_message(message: discord.Message) -> None:
    if not message.guild or message.author.bot or not isinstance(message.author, discord.Member):
        return
    settings = _settings(message.guild.id)
    if not settings.get("enabled"):
        return
    if message.channel.id in {int(x) for x in settings.get("ignored_channel_ids", []) if str(x).isdigit()}:
        return
    ignored_roles = {int(x) for x in settings.get("ignored_role_ids", []) if str(x).isdigit()}
    if ignored_roles.intersection(role.id for role in message.author.roles):
        return
    key = (message.guild.id, message.author.id)
    now = time.time()
    cooldown = max(10, int(settings.get("cooldown_seconds", 60)))
    if now - COOLDOWNS.get(key, 0) < cooldown:
        return
    COOLDOWNS[key] = now

    data, record = _member_record(message.guild.id, message.author.id, message.author.display_name)
    old_level = level_from_xp(int(record.get("xp", 0)))
    try:
        xp_gain = random.randint(int(settings.get("xp_min", 15)), int(settings.get("xp_max", 25)))
    except (TypeError, ValueError):
        xp_gain = random.randint(15, 25)
    record["xp"] = int(record.get("xp", 0)) + max(1, xp_gain)
    record["messages"] = int(record.get("messages", 0)) + 1
    record["last_message_at"] = datetime.now(timezone.utc).isoformat()
    new_level = level_from_xp(int(record["xp"]))
    record["level"] = new_level
    data[str(message.guild.id)][str(message.author.id)] = record
    _save_stats(data)

    if new_level > old_level:
        rewards = await _apply_reward_roles(message.author, new_level, settings)
        channel_id = settings.get("announce_channel_id")
        channel = message.guild.get_channel(int(channel_id)) if channel_id else message.channel
        if isinstance(channel, discord.abc.Messageable):
            text = str(settings.get("announce_message", "🎉 {user} reached **level {level}**!"))
            text = text.replace("{user}", message.author.mention).replace("{username}", message.author.display_name).replace("{level}", str(new_level))
            if rewards:
                text += "\nReward unlocked: " + ", ".join(role.mention for role in rewards)
            try:
                await channel.send(text, allowed_mentions=discord.AllowedMentions(users=[message.author], roles=False, everyone=False))
            except discord.HTTPException:
                pass


def _rank_embed(member: discord.Member, record: dict[str, Any], rank: int | None = None) -> discord.Embed:
    xp = int(record.get("xp", 0))
    level = level_from_xp(xp)
    current_floor = xp_for_level(level)
    next_floor = xp_for_level(level + 1)
    progress = xp - current_floor
    needed = max(1, next_floor - current_floor)
    filled = min(12, round(progress / needed * 12))
    bar = "▰" * filled + "▱" * (12 - filled)
    embed = discord.Embed(title=f"{member.display_name}'s rank", color=member.color.value or BRAND)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=f"**{level}**", inline=True)
    embed.add_field(name="XP", value=f"**{xp:,}**", inline=True)
    embed.add_field(name="Rank", value=f"**#{rank}**" if rank else "Unranked", inline=True)
    embed.add_field(name="Progress", value=f"`{bar}`\n{progress:,} / {needed:,} XP", inline=False)
    embed.set_footer(text=f"{int(record.get('messages', 0)):,} XP-earning messages")
    return embed


def setup_level_commands(tree: app_commands.CommandTree) -> None:
    group = app_commands.Group(name="level", description="Server levelling, XP and reward roles.")

    @group.command(name="rank", description="Show a member's level and XP.")
    async def rank(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        member = member or interaction.user  # type: ignore[assignment]
        guild_data = _stats().get(str(interaction.guild.id), {})
        record = guild_data.get(str(member.id), {"xp": 0, "messages": 0})
        sorted_ids = sorted(guild_data, key=lambda uid: int(guild_data[uid].get("xp", 0)), reverse=True)
        rank_number = sorted_ids.index(str(member.id)) + 1 if str(member.id) in sorted_ids else None
        await interaction.response.send_message(embed=_rank_embed(member, record, rank_number))

    @group.command(name="leaderboard", description="Show the server XP leaderboard.")
    async def leaderboard(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        guild_data = _stats().get(str(interaction.guild.id), {})
        rows = sorted(guild_data.items(), key=lambda item: int(item[1].get("xp", 0)), reverse=True)[:15]
        if not rows:
            await interaction.response.send_message("No XP has been earned yet.", ephemeral=True); return
        lines = []
        for index, (uid, record) in enumerate(rows, 1):
            lines.append(f"**{index}.** <@{uid}> — Level **{level_from_xp(int(record.get('xp', 0)))}** · {int(record.get('xp', 0)):,} XP")
        await interaction.response.send_message(embed=discord.Embed(title="🏆 Level leaderboard", description="\n".join(lines), color=BRAND))

    @group.command(name="setup", description="Enable or configure server levelling.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(
        interaction: discord.Interaction,
        enabled: bool,
        announce_channel: discord.TextChannel | None = None,
        cooldown_seconds: app_commands.Range[int, 10, 600] = 60,
        xp_min: app_commands.Range[int, 1, 100] = 15,
        xp_max: app_commands.Range[int, 1, 200] = 25,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        if xp_max < xp_min:
            await interaction.response.send_message("XP maximum must be greater than or equal to XP minimum.", ephemeral=True); return
        cfg = _settings(interaction.guild.id)
        cfg.update({"enabled": enabled, "announce_channel_id": announce_channel.id if announce_channel else None, "cooldown_seconds": cooldown_seconds, "xp_min": xp_min, "xp_max": xp_max})
        _save_settings(interaction.guild.id, cfg)
        await interaction.response.send_message(f"Levelling is now **{'enabled' if enabled else 'disabled'}**.", ephemeral=True)

    reward = app_commands.Group(name="reward", description="Configure level reward roles.", parent=group)

    @reward.command(name="add", description="Give a role when members reach a level.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def reward_add(interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000], role: discord.Role) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        if role.is_default() or role.managed or (interaction.guild.me and role >= interaction.guild.me.top_role):
            await interaction.response.send_message("Rallybit cannot assign that role. Move its bot role higher.", ephemeral=True); return
        cfg = _settings(interaction.guild.id)
        rewards = cfg.setdefault("reward_roles", {})
        rewards[str(level)] = role.id
        _save_settings(interaction.guild.id, cfg)
        await interaction.response.send_message(f"{role.mention} will be awarded at level **{level}**.", ephemeral=True)

    @reward.command(name="remove", description="Remove a level reward.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def reward_remove(interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000]) -> None:
        if interaction.guild:
            cfg = _settings(interaction.guild.id); cfg.setdefault("reward_roles", {}).pop(str(level), None); _save_settings(interaction.guild.id, cfg)
        await interaction.response.send_message(f"Level {level} reward removed.", ephemeral=True)

    @reward.command(name="list", description="List configured level rewards.")
    async def reward_list(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        rewards = _settings(interaction.guild.id).get("reward_roles", {})
        if not rewards:
            await interaction.response.send_message("No reward roles are configured.", ephemeral=True); return
        lines = [f"Level **{level}** → <@&{role_id}>" for level, role_id in sorted(rewards.items(), key=lambda x: int(x[0]))]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="setxp", description="Set a member's XP.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setxp(interaction: discord.Interaction, member: discord.Member, xp: app_commands.Range[int, 0, 100000000]) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        data, record = _member_record(interaction.guild.id, member.id, member.display_name)
        record["xp"] = xp; record["level"] = level_from_xp(xp)
        data[str(interaction.guild.id)][str(member.id)] = record; _save_stats(data)
        await _apply_reward_roles(member, record["level"], _settings(interaction.guild.id))
        await interaction.response.send_message(f"Set {member.mention} to **{xp:,} XP** (level {record['level']}).", ephemeral=True)

    tree.add_command(group)
