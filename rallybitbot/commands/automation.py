from __future__ import annotations

import asyncio
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import discord
from discord import app_commands
from discord.ext import tasks

from config.config import AUTOMATION_SCHEDULES_FILE
from storage.json_store import load_json, save_json

KINDS = ("activity", "quiz", "pulse", "icebreaker", "giveaway")


def _all() -> dict[str, Any]:
    data = load_json(AUTOMATION_SCHEDULES_FILE) or {}
    return data if isinstance(data, dict) else {}


def _guild(guild_id: int) -> dict[str, Any]:
    data = _all().get(str(guild_id), {})
    return data if isinstance(data, dict) else {}


def _save_guild(guild_id: int, schedules: dict[str, Any]) -> None:
    data = _all(); data[str(guild_id)] = schedules; save_json(AUTOMATION_SCHEDULES_FILE, data)


def _next_time(interval_minutes: int) -> float:
    return time.time() + max(5, interval_minutes) * 60


async def run_schedule(bot: discord.Client, guild: discord.Guild, schedule_id: str, schedule: dict[str, Any]) -> str:
    channel = guild.get_channel(int(schedule.get("channel_id") or 0))
    if not isinstance(channel, discord.TextChannel):
        raise RuntimeError("The configured channel no longer exists.")
    kind = str(schedule.get("kind", ""))
    role = guild.get_role(int(schedule.get("ping_role_id") or 0)) if schedule.get("ping_role_id") else None
    options = schedule.get("options", {}) if isinstance(schedule.get("options"), dict) else {}
    starter = bot.user
    if starter is None:
        raise RuntimeError("Rallybit is not ready.")

    if kind == "activity":
        from commands.activity import get_guild_settings, launch_activity_check
        settings = get_guild_settings(guild.id)
        if role:
            settings["ping_target"] = role.mention
        await launch_activity_check(guild, channel, starter, settings_override=settings)
        return f"Activity check started in #{channel.name}."
    if kind == "quiz":
        from commands.quizzes import start_quiz
        await start_quiz(guild, channel, starter, str(options.get("category", "mixed")), int(options.get("duration_seconds", 30)), True, role)
        return f"Quiz started in #{channel.name}."
    if kind == "pulse":
        from commands.community import start_pulse
        await start_pulse(guild, channel, starter, int(options.get("duration_minutes", 10)), str(options.get("message") or "How is everyone feeling right now?"), role)
        return f"Pulse started in #{channel.name}."
    if kind == "icebreaker":
        from commands.community import ICEBREAKERS
        category = str(options.get("category", "casual"))
        pool = ICEBREAKERS.get(category, sum(ICEBREAKERS.values(), []))
        prompt = random.choice(pool)
        content = f"{role.mention} " if role and (role.mentionable or channel.permissions_for(guild.me).mention_everyone) else ""
        embed = discord.Embed(title="💬 Conversation starter", description=f"## {prompt}", color=0x5865F2)
        await channel.send(content=content or None, embed=embed, allowed_mentions=discord.AllowedMentions(roles=[role] if content and role else False, users=False, everyone=False))
        return f"Icebreaker posted in #{channel.name}."
    if kind == "giveaway":
        from commands.giveaways import start_giveaway
        prize = str(options.get("message") or "Community giveaway")
        await start_giveaway(guild, channel, starter, prize, int(options.get("duration_minutes", 60)), int(options.get("winners", 1)), None, role)
        return f"Giveaway started in #{channel.name}."
    raise RuntimeError("Unknown automation type.")


@tasks.loop(seconds=30)
async def automation_loop(bot: discord.Client) -> None:
    data = _all()
    now = time.time()
    changed = False
    for guild_id, schedules in list(data.items()):
        if not str(guild_id).isdigit() or not isinstance(schedules, dict):
            continue
        guild = bot.get_guild(int(guild_id))
        if guild is None:
            continue
        for schedule_id, schedule in list(schedules.items()):
            if not isinstance(schedule, dict) or not schedule.get("enabled", True):
                continue
            try:
                next_run = float(schedule.get("next_run", 0))
            except (TypeError, ValueError):
                next_run = 0
            if next_run > now:
                continue
            try:
                result = await run_schedule(bot, guild, schedule_id, schedule)
                schedule["last_result"] = result
                schedule["last_error"] = None
                schedule["last_run"] = datetime.now(timezone.utc).isoformat()
                schedule["next_run"] = _next_time(int(schedule.get("interval_minutes", 60)))
            except Exception as exc:
                # Conflicting live sessions are retried soon without destroying the schedule.
                schedule["last_error"] = str(exc)[:500]
                schedule["next_run"] = time.time() + min(300, max(60, int(schedule.get("interval_minutes", 60)) * 10))
            schedules[schedule_id] = schedule
            changed = True
        data[guild_id] = schedules
    if changed:
        save_json(AUTOMATION_SCHEDULES_FILE, data)


@automation_loop.before_loop
async def before_automation() -> None:
    from core.bot import client
    await client.wait_until_ready()
    await asyncio.sleep(10)


def setup_automation_task(bot: discord.Client) -> None:
    if not automation_loop.is_running():
        automation_loop.start(bot)


def setup_automation_commands(tree: app_commands.CommandTree) -> None:
    group = app_commands.Group(name="automation", description="Run community tools automatically in multiple channels.")

    kind_choices = [app_commands.Choice(name=x.title(), value=x) for x in KINDS]

    @group.command(name="add", description="Add an independent recurring schedule for a channel.")
    @app_commands.choices(kind=kind_choices)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        channel: discord.TextChannel,
        interval_minutes: app_commands.Range[int, 5, 10080],
        ping_role: discord.Role | None = None,
        message_or_prize: str | None = None,
        category: str = "mixed",
        duration: app_commands.Range[int, 1, 1440] = 30,
        winners: app_commands.Range[int, 1, 20] = 1,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        schedule_id = uuid.uuid4().hex[:8].upper()
        schedules = _guild(interaction.guild.id)
        schedules[schedule_id] = {
            "schedule_id": schedule_id,
            "kind": kind.value,
            "channel_id": channel.id,
            "interval_minutes": interval_minutes,
            "ping_role_id": ping_role.id if ping_role else None,
            "enabled": True,
            "next_run": _next_time(interval_minutes),
            "created_by": str(interaction.user.id),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "options": {
                "message": message_or_prize,
                "category": category,
                "duration_minutes": duration,
                "duration_seconds": max(15, min(120, duration)),
                "winners": winners,
            },
        }
        _save_guild(interaction.guild.id, schedules)
        await interaction.response.send_message(
            f"Created `{schedule_id}`: **{kind.name}** in {channel.mention} every **{interval_minutes} minutes**.\nYou can add more schedules for other channels.",
            ephemeral=True,
        )

    @group.command(name="remove", description="Delete an automation schedule.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(interaction: discord.Interaction, schedule_id: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        schedules = _guild(interaction.guild.id); removed = schedules.pop(schedule_id.upper(), None); _save_guild(interaction.guild.id, schedules)
        await interaction.response.send_message("Schedule removed." if removed else "That schedule was not found.", ephemeral=True)

    @group.command(name="toggle", description="Enable or pause an automation schedule.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def toggle(interaction: discord.Interaction, schedule_id: str, enabled: bool) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        schedules = _guild(interaction.guild.id); schedule = schedules.get(schedule_id.upper())
        if not isinstance(schedule, dict):
            await interaction.response.send_message("That schedule was not found.", ephemeral=True); return
        schedule["enabled"] = enabled
        if enabled: schedule["next_run"] = _next_time(int(schedule.get("interval_minutes", 60)))
        schedules[schedule_id.upper()] = schedule; _save_guild(interaction.guild.id, schedules)
        await interaction.response.send_message(f"Schedule `{schedule_id.upper()}` is now **{'enabled' if enabled else 'paused'}**.", ephemeral=True)

    @group.command(name="list", description="List every automation schedule for this server.")
    async def list_cmd(interaction: discord.Interaction) -> None:
        schedules = _guild(interaction.guild.id) if interaction.guild else {}
        if not schedules:
            await interaction.response.send_message("No multi-channel automations are configured.", ephemeral=True); return
        lines = []
        for sid, schedule in schedules.items():
            status = "🟢" if schedule.get("enabled", True) else "⏸️"
            lines.append(f"{status} `{sid}` • **{str(schedule.get('kind')).title()}** • <#{schedule.get('channel_id')}> • every {schedule.get('interval_minutes')}m • next <t:{int(float(schedule.get('next_run', 0)))}:R>")
        await interaction.response.send_message("\n".join(lines[:40]), ephemeral=True)

    @group.command(name="run", description="Run a configured schedule immediately.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def run(interaction: discord.Interaction, schedule_id: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        schedule = _guild(interaction.guild.id).get(schedule_id.upper())
        if not isinstance(schedule, dict):
            await interaction.response.send_message("That schedule was not found.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await run_schedule(interaction.client, interaction.guild, schedule_id.upper(), schedule)
            await interaction.followup.send(result, ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Could not run it: {exc}", ephemeral=True)

    @group.command(name="clear", description="Delete all automation schedules for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def clear(interaction: discord.Interaction) -> None:
        if interaction.guild: _save_guild(interaction.guild.id, {})
        await interaction.response.send_message("All automation schedules were deleted.", ephemeral=True)

    tree.add_command(group)
