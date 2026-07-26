from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands

from commands.moderation import can_use_moderation_action, moderation_denial
from config.config import (
    ACTIVITY_AUDIT_FILE,
    MOD_HISTORY_FILE,
    OPEN_TICKETS_FILE,
    STAFF_SHIFTS_FILE,
    TICKET_HISTORY_FILE,
)
from core.premium import PLAN_DEFINITIONS, premium_check, resolve_entitlement
from storage.json_store import load_json, save_json


BRAND = 0x7567EE
SUCCESS = 0x45C486
MAX_SHIFT_HISTORY = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _in_period(value: Any, threshold: datetime) -> bool:
    parsed = _parse_datetime(value)
    return parsed is not None and parsed >= threshold


def _bounded_days(days: int) -> int:
    return max(7, min(365, int(days)))


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _shift_data() -> dict[str, Any]:
    data = load_json(STAFF_SHIFTS_FILE) or {}
    return data if isinstance(data, dict) else {}


def _staff_record(data: dict[str, Any], guild_id: int, user_id: int) -> dict[str, Any]:
    guild_data = data.setdefault(str(guild_id), {})
    record = guild_data.setdefault(str(user_id), {"active": None, "history": []})
    if not isinstance(record, dict):
        record = {"active": None, "history": []}
        guild_data[str(user_id)] = record
    if not isinstance(record.get("history"), list):
        record["history"] = []
    return record


def _staff_allowed(interaction: discord.Interaction) -> bool:
    return bool(
        interaction.guild
        and isinstance(interaction.user, discord.Member)
        and can_use_moderation_action(interaction.user, "warn")
    )


def _staff_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if _staff_allowed(interaction):
            return True
        raise app_commands.CheckFailure(moderation_denial("warn"))

    return app_commands.check(predicate)


def _activity_rows(guild_id: int, threshold: datetime) -> list[dict[str, Any]]:
    data = load_json(ACTIVITY_AUDIT_FILE) or {}
    records = data.get(str(guild_id), {}) if isinstance(data, dict) else {}
    if not isinstance(records, dict):
        return []
    return [record for record in records.values() if isinstance(record, dict) and _in_period(record.get("start_time"), threshold)]


def _ticket_rows(guild_id: int) -> list[dict[str, Any]]:
    data = load_json(TICKET_HISTORY_FILE) or {}
    records = data.get(str(guild_id), []) if isinstance(data, dict) else []
    return [record for record in records if isinstance(record, dict)] if isinstance(records, list) else []


def _moderation_rows(guild_id: int, threshold: datetime) -> list[dict[str, Any]]:
    data = load_json(MOD_HISTORY_FILE) or {}
    guild_data = data.get(str(guild_id), {}) if isinstance(data, dict) else {}
    rows: list[dict[str, Any]] = []
    if isinstance(guild_data, dict):
        for target_id, records in guild_data.items():
            if not isinstance(records, list):
                continue
            for record in records:
                if isinstance(record, dict) and _in_period(record.get("timestamp"), threshold):
                    rows.append({**record, "target_id": str(target_id)})
    return rows


def _staff_rows(guild_id: int, threshold: datetime) -> list[dict[str, Any]]:
    data = _shift_data()
    guild_data = data.get(str(guild_id), {})
    rows: list[dict[str, Any]] = []
    if not isinstance(guild_data, dict):
        return rows
    for user_id, record in guild_data.items():
        if not isinstance(record, dict) or not isinstance(record.get("history"), list):
            continue
        for shift in record["history"]:
            if isinstance(shift, dict) and _in_period(shift.get("ended_at"), threshold):
                rows.append({**shift, "user_id": str(user_id), "display_name": record.get("display_name", user_id)})
    return rows


def collect_insights(guild_id: int, days: int) -> dict[str, Any]:
    period = _bounded_days(days)
    threshold = _now() - timedelta(days=period)
    activities = _activity_rows(guild_id, threshold)
    tickets = _ticket_rows(guild_id)
    moderation = _moderation_rows(guild_id, threshold)
    shifts = _staff_rows(guild_id, threshold)

    closed_keys = {
        (str(row.get("channel_id")), str(row.get("closed_at")))
        for row in tickets
        if _in_period(row.get("closed_at"), threshold)
    }
    deleted_keys = {
        (str(row.get("channel_id")), str(row.get("deleted_at")))
        for row in tickets
        if _in_period(row.get("deleted_at"), threshold)
    }
    open_data = load_json(OPEN_TICKETS_FILE) or {}
    guild_open = open_data.get(str(guild_id), {}) if isinstance(open_data, dict) else {}
    open_count = sum(
        1 for record in guild_open.values()
        if isinstance(record, dict) and str(record.get("status", "open")).lower() == "open"
    ) if isinstance(guild_open, dict) else 0

    return {
        "days": period,
        "threshold": threshold,
        "activities": activities,
        "tickets": tickets,
        "moderation": moderation,
        "shifts": shifts,
        "activity_checks": len(activities),
        "participants": sum(len(row.get("participants", [])) for row in activities if isinstance(row.get("participants", []), list)),
        "tickets_closed": len(closed_keys),
        "tickets_deleted": len(deleted_keys),
        "tickets_open": open_count,
        "moderation_actions": len(moderation),
        "staff_seconds": sum(max(0, int(row.get("seconds", 0) or 0)) for row in shifts),
    }


def _insights_csv(guild: discord.Guild, insights: dict[str, Any]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["Rallybit insights export"])
    writer.writerow(["Server", guild.name, "Server ID", guild.id])
    writer.writerow(["Period (days)", insights["days"], "Exported at", _now().isoformat()])
    writer.writerow([])
    writer.writerow(["Category", "Timestamp", "Actor", "Target", "Status", "Details"])

    for row in insights["activities"]:
        writer.writerow([
            "activity_check", row.get("start_time", ""), row.get("starter_name", row.get("starter_id", "")),
            row.get("channel_name", row.get("channel_id", "")), row.get("status", ""),
            f"participants={len(row.get('participants', [])) if isinstance(row.get('participants'), list) else 0}",
        ])
    threshold = insights["threshold"]
    for row in insights["tickets"]:
        timestamp = row.get("deleted_at") or row.get("closed_at")
        if not _in_period(timestamp, threshold):
            continue
        writer.writerow([
            "ticket", timestamp, row.get("closed_by", row.get("deleted_by", "")), row.get("owner_id", ""),
            "deleted" if row.get("deleted_at") else "closed", row.get("reason", ""),
        ])
    for row in insights["moderation"]:
        writer.writerow([
            "moderation", row.get("timestamp", ""), row.get("moderator_name", row.get("moderator_id", "")),
            row.get("target_id", ""), row.get("action", ""), row.get("reason", row.get("details", "")),
        ])
    for row in insights["shifts"]:
        writer.writerow([
            "staff_shift", row.get("ended_at", ""), row.get("display_name", row.get("user_id", "")), "", "completed",
            f"duration={_format_duration(row.get('seconds', 0))}; note={row.get('note', '')}",
        ])
    return stream.getvalue().encode("utf-8-sig")


def setup_premium_commands(tree: app_commands.CommandTree) -> None:
    premium = app_commands.Group(name="premium", description="View Rallybit plans and preview access.")
    insights = app_commands.Group(name="insights", description="Premium server operations and analytics.")
    staff = app_commands.Group(name="staff", description="Premium staff shift tracking.")

    @premium.command(name="status", description="View the plan currently active in this server.")
    async def premium_status(interaction: discord.Interaction) -> None:
        guild = interaction.guild
        entitlement = resolve_entitlement(
            user_id=interaction.user.id,
            guild_id=guild.id if guild else None,
            guild_owner_id=guild.owner_id if guild else None,
        )
        source_labels = {
            "default": "Free access",
            "server": "Server preview grant",
            "owner": "Server owner subscription",
            "user": "Your subscription",
            "developer": "Developer preview",
        }
        expiry = _parse_datetime(entitlement.get("expires_at"))
        embed = discord.Embed(
            title=f"{entitlement['name']} plan",
            description=source_labels.get(str(entitlement.get("source")), "Rallybit plan"),
            color=BRAND,
            timestamp=_now(),
        )
        embed.add_field(name="Access", value="Active", inline=True)
        embed.add_field(name="Server coverage", value="Unlimited owned servers" if entitlement["unlimited_servers"] else "This server", inline=True)
        embed.add_field(name="Expires", value=discord.utils.format_dt(expiry, "F") if expiry else "No expiration", inline=False)
        embed.set_footer(text="Paid plans are in developer preview and are not available to purchase yet.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @premium.command(name="plans", description="Compare Free, Community, Pro, and Network previews.")
    async def premium_plans(interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Rallybit plans",
            description="Rallybit starts free. Paid plans are coming soon; authorised preview grants already work.",
            color=BRAND,
        )
        embed.add_field(name="Free — £0", value="Every existing Rallybit command.", inline=False)
        embed.add_field(name="Community — £3.99/month", value="Coming soon · Server insights overview.", inline=False)
        embed.add_field(name="Pro — £8.99/month", value="Coming soon · CSV exports and staff shift tools.", inline=False)
        embed.add_field(name="Network — £19.99/month", value="Coming soon · All premium tools across every server you own.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @insights.command(name="overview", description="View activity, ticket, moderation, and staff totals.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @premium_check("community")
    @app_commands.describe(days="Reporting period from 7 to 365 days")
    async def insights_overview(interaction: discord.Interaction, days: app_commands.Range[int, 7, 365] = 30) -> None:
        assert interaction.guild is not None
        data = collect_insights(interaction.guild.id, days)
        embed = discord.Embed(
            title=f"{interaction.guild.name} insights",
            description=f"Operational totals for the last **{data['days']} days**.",
            color=BRAND,
            timestamp=_now(),
        )
        embed.add_field(name="Activity checks", value=f"{data['activity_checks']:,}\n{data['participants']:,} participants", inline=True)
        embed.add_field(name="Tickets", value=f"{data['tickets_closed']:,} closed\n{data['tickets_open']:,} currently open", inline=True)
        embed.add_field(name="Moderation", value=f"{data['moderation_actions']:,} recorded actions", inline=True)
        embed.add_field(name="Staff time", value=_format_duration(data["staff_seconds"]), inline=True)
        embed.add_field(name="Deleted tickets", value=f"{data['tickets_deleted']:,}", inline=True)
        embed.set_footer(text="Community plan preview")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @insights.command(name="export", description="Export detailed operational records as a CSV file.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @premium_check("pro")
    @app_commands.describe(days="Reporting period from 7 to 365 days")
    async def insights_export(interaction: discord.Interaction, days: app_commands.Range[int, 7, 365] = 30) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        data = collect_insights(interaction.guild.id, days)
        payload = _insights_csv(interaction.guild, data)
        filename = f"rallybit-insights-{interaction.guild.id}-{_now():%Y%m%d}.csv"
        await interaction.followup.send(
            content=f"Exported {data['days']} days of Rallybit operational records.",
            file=discord.File(io.BytesIO(payload), filename=filename),
            ephemeral=True,
        )

    @staff.command(name="clockin", description="Start your staff shift.")
    @app_commands.guild_only()
    @_staff_check()
    @premium_check("pro")
    @app_commands.describe(note="Optional note describing your shift")
    async def staff_clockin(interaction: discord.Interaction, note: app_commands.Range[str, 1, 300] | None = None) -> None:
        assert interaction.guild is not None
        data = _shift_data()
        record = _staff_record(data, interaction.guild.id, interaction.user.id)
        if isinstance(record.get("active"), dict):
            await interaction.response.send_message("You already have an active staff shift.", ephemeral=True)
            return
        started = _now()
        record["active"] = {"started_at": started.isoformat(), "note": str(note or "")}
        record["display_name"] = getattr(interaction.user, "display_name", str(interaction.user))
        if not save_json(STAFF_SHIFTS_FILE, data):
            await interaction.response.send_message("The staff shift could not be saved.", ephemeral=True)
            return
        embed = discord.Embed(title="Staff shift started", description=f"Clocked in {discord.utils.format_dt(started, 'R')}.", color=SUCCESS)
        if note:
            embed.add_field(name="Shift note", value=note, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @staff.command(name="clockout", description="Finish your active staff shift.")
    @app_commands.guild_only()
    @_staff_check()
    @premium_check("pro")
    async def staff_clockout(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        data = _shift_data()
        record = _staff_record(data, interaction.guild.id, interaction.user.id)
        active = record.get("active")
        started = _parse_datetime(active.get("started_at")) if isinstance(active, dict) else None
        if started is None:
            await interaction.response.send_message("You do not have an active staff shift.", ephemeral=True)
            return
        ended = _now()
        seconds = max(0, int((ended - started).total_seconds()))
        record["history"].append({
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "seconds": seconds,
            "note": str(active.get("note") or "")[:300],
        })
        record["history"] = record["history"][-MAX_SHIFT_HISTORY:]
        record["active"] = None
        record["display_name"] = getattr(interaction.user, "display_name", str(interaction.user))
        if not save_json(STAFF_SHIFTS_FILE, data):
            await interaction.response.send_message("The completed shift could not be saved.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(title="Staff shift completed", description=f"Recorded **{_format_duration(seconds)}**.", color=SUCCESS),
            ephemeral=True,
        )

    @staff.command(name="status", description="View your own or another staff member's shift status.")
    @app_commands.guild_only()
    @_staff_check()
    @premium_check("pro")
    @app_commands.describe(member="Staff member to inspect; leave empty for yourself")
    async def staff_status(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        assert interaction.guild is not None
        target = member or interaction.user
        data = _shift_data()
        record = _staff_record(data, interaction.guild.id, target.id)
        active = record.get("active")
        started = _parse_datetime(active.get("started_at")) if isinstance(active, dict) else None
        total = sum(max(0, int(row.get("seconds", 0) or 0)) for row in record["history"] if isinstance(row, dict))
        embed = discord.Embed(title=f"Staff status · {getattr(target, 'display_name', target)}", color=BRAND)
        if started:
            embed.description = f"Currently clocked in since {discord.utils.format_dt(started, 'R')}."
            embed.add_field(name="Current shift", value=_format_duration((_now() - started).total_seconds()), inline=True)
        else:
            embed.description = "Not currently clocked in."
        embed.add_field(name="Recorded total", value=_format_duration(total), inline=True)
        embed.add_field(name="Completed shifts", value=f"{len(record['history']):,}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @staff.command(name="leaderboard", description="Rank recorded staff time over a selected period.")
    @app_commands.guild_only()
    @_staff_check()
    @premium_check("pro")
    @app_commands.describe(days="Reporting period from 7 to 365 days")
    async def staff_leaderboard(interaction: discord.Interaction, days: app_commands.Range[int, 7, 365] = 30) -> None:
        assert interaction.guild is not None
        threshold = _now() - timedelta(days=_bounded_days(days))
        rows = _staff_rows(interaction.guild.id, threshold)
        totals: dict[str, dict[str, Any]] = {}
        for row in rows:
            user_id = str(row.get("user_id"))
            entry = totals.setdefault(user_id, {"seconds": 0, "name": row.get("display_name", user_id), "shifts": 0})
            entry["seconds"] += max(0, int(row.get("seconds", 0) or 0))
            entry["shifts"] += 1
        ranking = sorted(totals.items(), key=lambda item: item[1]["seconds"], reverse=True)[:10]
        lines = [
            f"**{index}.** <@{user_id}> · {_format_duration(entry['seconds'])} · {entry['shifts']} shifts"
            for index, (user_id, entry) in enumerate(ranking, start=1)
        ]
        embed = discord.Embed(
            title=f"Staff leaderboard · {_bounded_days(days)} days",
            description="\n".join(lines) if lines else "No completed staff shifts were recorded in this period.",
            color=BRAND,
        )
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    tree.add_command(premium)
    tree.add_command(insights)
    tree.add_command(staff)
