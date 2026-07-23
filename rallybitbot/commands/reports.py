from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands

from commands.moderation import can_use_moderation_action, moderation_denial
from config.config import REPORT_SETTINGS_FILE, REPORTS_FILE
from core.logging import log_server_event
from storage.json_store import load_json, save_json

BRAND = 0x5865F2
REPORT_STATUSES = ("Pending", "Claimed", "Under Review", "Resolved", "Dismissed", "Closed")
OPEN_REPORT_STATUSES = {"Pending", "Claimed", "Under Review"}


def _reports() -> dict[str, Any]:
    value = load_json(REPORTS_FILE) or {}
    return value if isinstance(value, dict) else {}


def _save_reports(value: dict[str, Any]) -> None:
    save_json(REPORTS_FILE, value)


def _settings(guild_id: int) -> dict[str, Any]:
    data = load_json(REPORT_SETTINGS_FILE) or {}
    saved = data.get(str(guild_id), {}) if isinstance(data, dict) else {}
    return saved if isinstance(saved, dict) else {}


def _save_settings(guild_id: int, settings: dict[str, Any]) -> None:
    data = load_json(REPORT_SETTINGS_FILE) or {}
    if not isinstance(data, dict):
        data = {}
    data[str(guild_id)] = settings
    save_json(REPORT_SETTINGS_FILE, data)


def _record(guild_id: int, report_id: str) -> dict[str, Any] | None:
    guild_reports = _reports().get(str(guild_id), {})
    value = guild_reports.get(report_id.upper()) if isinstance(guild_reports, dict) else None
    return value if isinstance(value, dict) else None


def _staff_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild and isinstance(interaction.user, discord.Member) and can_use_moderation_action(interaction.user, "warn"):
            return True
        raise app_commands.CheckFailure(moderation_denial("warn"))
    return app_commands.check(predicate)


def _report_embed(report_id: str, record: dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title=f"Member report {report_id}",
        description=str(record.get("reason", "No reason provided"))[:4096],
        color=BRAND,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Reported member", value=f"<@{record.get('target_id')}> (`{record.get('target_id')}`)", inline=True)
    embed.add_field(name="Submitted by", value=f"<@{record.get('reporter_id')}> (`{record.get('reporter_id')}`)", inline=True)
    embed.add_field(name="Status", value=str(record.get("status", "Pending")), inline=True)
    if record.get("details"):
        embed.add_field(name="Additional information", value=str(record["details"])[:1024], inline=False)
    if record.get("evidence"):
        embed.add_field(name="Evidence", value=str(record["evidence"])[:1024], inline=False)
    if record.get("claimed_by"):
        embed.add_field(name="Assigned staff", value=f"<@{record['claimed_by']}>", inline=True)
    if record.get("resolution"):
        embed.add_field(name="Latest update", value=str(record["resolution"])[:1024], inline=False)
    embed.set_footer(text=f"Report ID: {report_id}")
    return embed


def setup_report_commands(tree: app_commands.CommandTree) -> None:
    group = app_commands.Group(name="report", description="Submit and manage member reports with the moderation team.")

    @group.command(name="user", description="Report a member to the server moderation team.")
    @app_commands.describe(member="Member being reported", reason="Rule violation or concern", details="Additional context", evidence="Message link or evidence URL")
    async def report_user(interaction: discord.Interaction, member: discord.Member, reason: str, details: str | None = None, evidence: str | None = None) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this command in a server.", ephemeral=True)
            return
        if member.bot or member.id == interaction.user.id:
            await interaction.response.send_message("You cannot report yourself or a bot account.", ephemeral=True)
            return
        channel_id = _settings(interaction.guild.id).get("channel_id")
        channel = interaction.guild.get_channel(int(channel_id or 0))
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Reports are not configured yet. Ask an administrator to run `/report settings`.", ephemeral=True)
            return
        report_id = f"RPT-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "report_id": report_id,
            "target_id": str(member.id),
            "reporter_id": str(interaction.user.id),
            "reason": reason.strip()[:1000],
            "details": (details or "").strip()[:1500],
            "evidence": (evidence or "").strip()[:1000],
            "status": "Pending",
            "claimed_by": None,
            "resolution": None,
            "created_at": now,
            "updated_at": now,
            "updated_by": str(interaction.user.id),
        }
        try:
            await channel.send(embed=_report_embed(report_id, record), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await interaction.response.send_message("I could not deliver that report to the review channel. Ask an administrator to check my channel permissions.", ephemeral=True)
            return
        data = _reports()
        data.setdefault(str(interaction.guild.id), {})[report_id] = record
        _save_reports(data)
        log_server_event(interaction.guild.id, f"Report {report_id} submitted against {member} by {interaction.user}.")
        await interaction.response.send_message(f"Your report was submitted privately. Reference: `{report_id}`", ephemeral=True)

    @group.command(name="settings", description="Choose the staff channel that receives member reports.")
    @_staff_check()
    async def report_settings(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not interaction.guild:
            return
        _save_settings(interaction.guild.id, {"channel_id": str(channel.id)})
        await interaction.response.send_message(f"New reports will be sent to {channel.mention}.", ephemeral=True)

    @group.command(name="list", description="View reports that are awaiting staff action.")
    @_staff_check()
    async def report_list(interaction: discord.Interaction) -> None:
        rows = _reports().get(str(interaction.guild_id), {})
        open_rows = [(rid, row) for rid, row in rows.items() if isinstance(row, dict) and row.get("status") in OPEN_REPORT_STATUSES]
        open_rows.sort(key=lambda item: str(item[1].get("created_at", "")), reverse=True)
        lines = [f"`{rid}` • **{row.get('status', 'Pending')}** • <@{row.get('target_id')}> • {str(row.get('reason', ''))[:90]}" for rid, row in open_rows[:20]]
        await interaction.response.send_message("\n".join(lines) if lines else "There are no open reports.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @group.command(name="claim", description="Claim a report for investigation.")
    @_staff_check()
    async def report_claim(interaction: discord.Interaction, report_id: str) -> None:
        data = _reports(); record = data.get(str(interaction.guild_id), {}).get(report_id.upper())
        if not isinstance(record, dict):
            await interaction.response.send_message("That report was not found.", ephemeral=True); return
        if record.get("claimed_by") and str(record["claimed_by"]) != str(interaction.user.id):
            await interaction.response.send_message(f"That report is already claimed by <@{record['claimed_by']}>.", ephemeral=True); return
        record.update({"claimed_by": str(interaction.user.id), "status": "Claimed", "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": str(interaction.user.id)})
        _save_reports(data)
        await interaction.response.send_message(f"You claimed `{report_id.upper()}`.", ephemeral=True)

    @group.command(name="setstatus", description="Update a report's moderation status.")
    @app_commands.choices(status=[app_commands.Choice(name=value, value=value) for value in REPORT_STATUSES])
    @_staff_check()
    async def report_setstatus(interaction: discord.Interaction, report_id: str, status: app_commands.Choice[str], note: str | None = None) -> None:
        data = _reports(); record = data.get(str(interaction.guild_id), {}).get(report_id.upper())
        if not isinstance(record, dict):
            await interaction.response.send_message("That report was not found.", ephemeral=True); return
        record.update({"status": status.value, "resolution": (note or record.get("resolution")), "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": str(interaction.user.id)})
        if status.value == "Claimed" and not record.get("claimed_by"):
            record["claimed_by"] = str(interaction.user.id)
        _save_reports(data)
        await interaction.response.send_message(f"`{report_id.upper()}` is now **{status.value}**.", ephemeral=True)

    @group.command(name="close", description="Close a report with an optional resolution note.")
    @_staff_check()
    async def report_close(interaction: discord.Interaction, report_id: str, resolution: str | None = None) -> None:
        data = _reports(); record = data.get(str(interaction.guild_id), {}).get(report_id.upper())
        if not isinstance(record, dict):
            await interaction.response.send_message("That report was not found.", ephemeral=True); return
        record.update({"status": "Closed", "resolution": (resolution or "Closed by staff")[:1000], "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": str(interaction.user.id)})
        _save_reports(data)
        await interaction.response.send_message(f"`{report_id.upper()}` was closed.", ephemeral=True)

    @group.command(name="reopen", description="Reopen a previously closed report.")
    @_staff_check()
    async def report_reopen(interaction: discord.Interaction, report_id: str) -> None:
        data = _reports(); record = data.get(str(interaction.guild_id), {}).get(report_id.upper())
        if not isinstance(record, dict):
            await interaction.response.send_message("That report was not found.", ephemeral=True); return
        record.update({"status": "Under Review", "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": str(interaction.user.id)})
        _save_reports(data)
        await interaction.response.send_message(f"`{report_id.upper()}` was reopened.", ephemeral=True)

    @group.command(name="history", description="View reports submitted against a member.")
    @_staff_check()
    async def report_history(interaction: discord.Interaction, member: discord.Member) -> None:
        rows = _reports().get(str(interaction.guild_id), {})
        history = [(rid, row) for rid, row in rows.items() if isinstance(row, dict) and str(row.get("target_id")) == str(member.id)]
        history.sort(key=lambda item: str(item[1].get("created_at", "")), reverse=True)
        lines = [f"`{rid}` • **{row.get('status', 'Pending')}** • {str(row.get('reason', ''))[:100]}" for rid, row in history[:20]]
        await interaction.response.send_message("\n".join(lines) if lines else f"No reports were found for {member.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @group.command(name="status", description="View the current status of a report you submitted.")
    async def report_status(interaction: discord.Interaction, report_id: str) -> None:
        record = _record(interaction.guild_id or 0, report_id)
        if not record:
            await interaction.response.send_message("That report was not found.", ephemeral=True); return
        is_staff = isinstance(interaction.user, discord.Member) and can_use_moderation_action(interaction.user, "warn")
        if str(record.get("reporter_id")) != str(interaction.user.id) and not is_staff:
            await interaction.response.send_message("You can only view reports you submitted.", ephemeral=True); return
        await interaction.response.send_message(embed=_report_embed(report_id.upper(), record), ephemeral=True)

    tree.add_command(group)
