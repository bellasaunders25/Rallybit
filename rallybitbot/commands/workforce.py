from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands

from config.config import (
    STAFF_REQUESTS_FILE,
    STAFF_SHIFTS_FILE,
    WORKFORCE_SETTINGS_FILE,
)
from core.audit import emit_audit_event
from storage.json_store import load_json, save_json

BRAND = 0x7567EE
SUCCESS = 0x45C486
DANGER = 0xF06A70
WARNING = 0xE6B85C
MAX_SHIFT_HISTORY = 300
REQUEST_STATUSES = ("Pending", "Approved", "Denied", "Cancelled", "Ended")


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


def _format_duration(seconds: int | float) -> str:
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def default_workforce_settings() -> dict[str, Any]:
    return {
        "staff_role_ids": [],
        "hr_role_ids": [],
        "request_channel_id": None,
        "history_channel_id": None,
        "shift_log_channel_id": None,
        "max_loa_days": 90,
        "max_roa_days": 30,
    }


def get_workforce_settings(guild_id: int) -> dict[str, Any]:
    data = load_json(WORKFORCE_SETTINGS_FILE) or {}
    saved = data.get(str(guild_id), {}) if isinstance(data, dict) else {}
    settings = default_workforce_settings()
    if isinstance(saved, dict):
        settings.update(saved)
    for key in ("staff_role_ids", "hr_role_ids"):
        if not isinstance(settings.get(key), list):
            settings[key] = []
        settings[key] = [str(value) for value in settings[key] if str(value).isdigit()]
    return settings


def save_workforce_settings(guild_id: int, settings: dict[str, Any]) -> bool:
    data = load_json(WORKFORCE_SETTINGS_FILE) or {}
    if not isinstance(data, dict):
        data = {}
    data[str(guild_id)] = settings
    return save_json(WORKFORCE_SETTINGS_FILE, data)


def _member_has_role(member: discord.Member, role_ids: list[str]) -> bool:
    allowed = set(map(str, role_ids))
    return any(str(role.id) in allowed for role in member.roles)


def is_hr_member(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    settings = get_workforce_settings(member.guild.id)
    roles = settings["hr_role_ids"]
    return _member_has_role(member, roles) if roles else member.guild_permissions.manage_guild


def is_staff_member(member: discord.Member) -> bool:
    if is_hr_member(member):
        return True
    settings = get_workforce_settings(member.guild.id)
    roles = settings["staff_role_ids"]
    return _member_has_role(member, roles) if roles else member.guild_permissions.manage_messages


def _staff_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.Member) and is_staff_member(interaction.user):
            return True
        raise app_commands.CheckFailure("You do not have a configured staff role for that command.")

    return app_commands.check(predicate)


def _hr_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.Member) and is_hr_member(interaction.user):
            return True
        raise app_commands.CheckFailure("You do not have a configured HR role for that command.")

    return app_commands.check(predicate)


def _request_data() -> dict[str, Any]:
    data = load_json(STAFF_REQUESTS_FILE) or {}
    return data if isinstance(data, dict) else {}


def _save_request_data(data: dict[str, Any]) -> bool:
    return save_json(STAFF_REQUESTS_FILE, data)


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


def _new_shift_id() -> str:
    return f"SH-{uuid.uuid4().hex[:8].upper()}"


def _ensure_shift_ids(record: dict[str, Any]) -> bool:
    """Give legacy completed shifts stable IDs without changing their totals."""
    changed = False
    used: set[str] = set()
    for row in record.get("history", []):
        if not isinstance(row, dict):
            continue
        shift_id = str(row.get("shift_id") or "").strip().upper()
        if not shift_id or shift_id in used:
            shift_id = _new_shift_id()
            while shift_id in used:
                shift_id = _new_shift_id()
            row["shift_id"] = shift_id
            changed = True
        elif row.get("shift_id") != shift_id:
            row["shift_id"] = shift_id
            changed = True
        used.add(shift_id)
    return changed


def _remove_completed_shift(record: dict[str, Any], shift_id: str) -> dict[str, Any] | None:
    wanted = str(shift_id or "").strip().upper()
    for index, row in enumerate(record.get("history", [])):
        if isinstance(row, dict) and str(row.get("shift_id") or "").strip().upper() == wanted:
            return record["history"].pop(index)
    return None


def _clear_shift_account(
    data: dict[str, Any], guild_id: int, user_id: int, *, include_active: bool
) -> tuple[int, bool] | None:
    guild_data = data.get(str(guild_id))
    if not isinstance(guild_data, dict):
        return None
    record = guild_data.get(str(user_id))
    if not isinstance(record, dict):
        return None
    history = record.get("history")
    history_count = len([row for row in history if isinstance(row, dict)]) if isinstance(history, list) else 0
    active_removed = include_active and isinstance(record.get("active"), dict)
    if include_active:
        guild_data.pop(str(user_id), None)
    else:
        record["history"] = []
        if not isinstance(record.get("active"), dict):
            guild_data.pop(str(user_id), None)
    if not guild_data:
        data.pop(str(guild_id), None)
    return history_count, active_removed


def _active_seconds(active: dict[str, Any], end: datetime | None = None) -> int:
    end = end or _now()
    started = _parse_datetime(active.get("started_at"))
    if started is None:
        return 0
    break_seconds = max(0, int(active.get("break_seconds", 0) or 0))
    break_started = _parse_datetime(active.get("break_started_at"))
    if break_started:
        break_seconds += max(0, int((end - break_started).total_seconds()))
    return max(0, int((end - started).total_seconds()) - break_seconds)


async def _send_channel_embed(
    guild: discord.Guild,
    channel_id: Any,
    embed: discord.Embed,
    *,
    content: str | None = None,
    roles: list[discord.Role] | None = None,
) -> bool:
    channel = guild.get_channel(int(channel_id or 0))
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=roles or False, users=False, everyone=False),
        )
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


def _request_embed(record: dict[str, Any]) -> discord.Embed:
    request_type = str(record.get("type", "LOA")).upper()
    status = str(record.get("status", "Pending"))
    colours = {"Pending": WARNING, "Approved": SUCCESS, "Denied": DANGER, "Cancelled": 0x80848E, "Ended": BRAND}
    embed = discord.Embed(
        title=f"{request_type} request · {record.get('request_id')}",
        description=str(record.get("reason") or "No reason provided")[:4096],
        colour=colours.get(status, BRAND),
        timestamp=_now(),
    )
    embed.add_field(name="Member", value=f"<@{record.get('user_id')}>\n`{record.get('user_id')}`", inline=True)
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Duration", value=f"{record.get('duration_days', 0)} day(s)", inline=True)
    start = _parse_datetime(record.get("start_at"))
    end = _parse_datetime(record.get("end_at"))
    if start and end:
        embed.add_field(name="Dates", value=f"{discord.utils.format_dt(start, 'D')} → {discord.utils.format_dt(end, 'D')}", inline=False)
    if record.get("review_note"):
        embed.add_field(name="HR note", value=str(record["review_note"])[:1024], inline=False)
    embed.set_footer(text=f"Rallybit Staff Operations · {record.get('request_id')}")
    return embed


async def _publish_request_event(guild: discord.Guild, record: dict[str, Any], action: str, actor: discord.abc.User) -> None:
    settings = get_workforce_settings(guild.id)
    embed = _request_embed(record)
    embed.title = f"{action} · {record.get('request_id')}"
    if action.endswith("requested"):
        hr_roles = [role for role_id in settings["hr_role_ids"] if (role := guild.get_role(int(role_id))) is not None]
        content = " ".join(role.mention for role in hr_roles) or None
        await _send_channel_embed(guild, settings.get("request_channel_id"), embed, content=content, roles=hr_roles)
    await _send_channel_embed(guild, settings.get("history_channel_id"), embed)
    await emit_audit_event(
        guild,
        "staff",
        action,
        f"{record.get('type')} request `{record.get('request_id')}` is now **{record.get('status')}**.",
        actor=actor,
        target=f"<@{record.get('user_id')}> (`{record.get('user_id')}`)",
    )


def _find_request(guild_id: int, request_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    data = _request_data()
    record = data.get(str(guild_id), {}).get(request_id.upper())
    return (data, record) if isinstance(record, dict) else None


def _latest_member_request(guild_id: int, user_id: int, kind: str, request_id: str | None = None) -> dict[str, Any] | None:
    rows = _request_data().get(str(guild_id), {})
    if not isinstance(rows, dict):
        return None
    if request_id:
        row = rows.get(request_id.upper())
        return row if isinstance(row, dict) and str(row.get("user_id")) == str(user_id) and row.get("type") == kind else None
    matches = [row for row in rows.values() if isinstance(row, dict) and str(row.get("user_id")) == str(user_id) and row.get("type") == kind]
    matches.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
    return matches[0] if matches else None


async def _create_request(interaction: discord.Interaction, kind: str, duration_days: int, reason: str, start_date: str | None) -> None:
    assert interaction.guild is not None
    settings = get_workforce_settings(interaction.guild.id)
    maximum = int(settings.get(f"max_{kind.lower()}_days", 90))
    if duration_days < 1 or duration_days > maximum:
        await interaction.response.send_message(f"{kind} requests must be between 1 and {maximum} days.", ephemeral=True)
        return
    if start_date:
        try:
            start = datetime.strptime(start_date.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            await interaction.response.send_message("Start date must use `YYYY-MM-DD`.", ephemeral=True)
            return
    else:
        start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    request_id = f"{kind}-{uuid.uuid4().hex[:8].upper()}"
    record = {
        "request_id": request_id,
        "type": kind,
        "user_id": str(interaction.user.id),
        "user_name": getattr(interaction.user, "display_name", str(interaction.user)),
        "reason": reason.strip()[:2000],
        "duration_days": duration_days,
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(days=duration_days)).isoformat(),
        "status": "Pending",
        "created_at": _now().isoformat(),
        "updated_at": _now().isoformat(),
        "updated_by": str(interaction.user.id),
    }
    data = _request_data()
    data.setdefault(str(interaction.guild.id), {})[request_id] = record
    _save_request_data(data)
    await _publish_request_event(interaction.guild, record, f"{kind} requested", interaction.user)
    await interaction.response.send_message(embed=_request_embed(record), ephemeral=True)


async def _request_status(interaction: discord.Interaction, kind: str, request_id: str | None) -> None:
    record = _latest_member_request(interaction.guild_id or 0, interaction.user.id, kind, request_id)
    if record is None:
        await interaction.response.send_message(f"No matching {kind} request was found.", ephemeral=True)
        return
    await interaction.response.send_message(embed=_request_embed(record), ephemeral=True)


async def _cancel_request(interaction: discord.Interaction, kind: str, request_id: str) -> None:
    found = _find_request(interaction.guild_id or 0, request_id)
    if found is None:
        await interaction.response.send_message("That request was not found.", ephemeral=True)
        return
    data, record = found
    if record.get("type") != kind or str(record.get("user_id")) != str(interaction.user.id):
        await interaction.response.send_message("You can only cancel your own matching request.", ephemeral=True)
        return
    if record.get("status") not in {"Pending", "Approved"}:
        await interaction.response.send_message("That request can no longer be cancelled.", ephemeral=True)
        return
    record.update({"status": "Cancelled", "updated_at": _now().isoformat(), "updated_by": str(interaction.user.id)})
    _save_request_data(data)
    await _publish_request_event(interaction.guild, record, f"{kind} cancelled", interaction.user)
    await interaction.response.send_message(f"`{request_id.upper()}` was cancelled.", ephemeral=True)


async def _set_request_status(interaction: discord.Interaction, kind: str, request_id: str, status: str, note: str | None) -> None:
    found = _find_request(interaction.guild_id or 0, request_id)
    if found is None:
        await interaction.response.send_message("That request was not found.", ephemeral=True)
        return
    data, record = found
    if record.get("type") != kind:
        await interaction.response.send_message(f"That is not a {kind} request.", ephemeral=True)
        return
    record.update({
        "status": status,
        "review_note": (note or "")[:1000],
        "updated_at": _now().isoformat(),
        "updated_by": str(interaction.user.id),
    })
    _save_request_data(data)
    await _publish_request_event(interaction.guild, record, f"{kind} {status.lower()}", interaction.user)
    await interaction.response.send_message(embed=_request_embed(record), ephemeral=True)


async def _end_request(interaction: discord.Interaction, kind: str, request_id: str) -> None:
    found = _find_request(interaction.guild_id or 0, request_id)
    if found is None:
        await interaction.response.send_message("That request was not found.", ephemeral=True)
        return
    data, record = found
    if record.get("type") != kind:
        await interaction.response.send_message(f"That is not a {kind} request.", ephemeral=True)
        return
    if str(record.get("user_id")) != str(interaction.user.id) and not (isinstance(interaction.user, discord.Member) and is_hr_member(interaction.user)):
        await interaction.response.send_message("Only the request owner or HR can end it.", ephemeral=True)
        return
    if record.get("status") != "Approved":
        await interaction.response.send_message("Only an approved request can be ended early.", ephemeral=True)
        return
    record.update({"status": "Ended", "ended_at": _now().isoformat(), "updated_at": _now().isoformat(), "updated_by": str(interaction.user.id)})
    _save_request_data(data)
    await _publish_request_event(interaction.guild, record, f"{kind} ended", interaction.user)
    await interaction.response.send_message(f"`{request_id.upper()}` was ended early.", ephemeral=True)


async def _update_request_settings(
    interaction: discord.Interaction,
    kind: str,
    request_channel: discord.TextChannel,
    history_channel: discord.TextChannel,
    staff_role: discord.Role,
    hr_role: discord.Role,
    maximum_days: int,
    shift_log_channel: discord.TextChannel | None,
) -> None:
    assert interaction.guild is not None
    settings = get_workforce_settings(interaction.guild.id)
    settings.update({
        "request_channel_id": str(request_channel.id),
        "history_channel_id": str(history_channel.id),
        "shift_log_channel_id": str(shift_log_channel.id) if shift_log_channel else settings.get("shift_log_channel_id"),
        "staff_role_ids": [str(staff_role.id)],
        "hr_role_ids": [str(hr_role.id)],
        f"max_{kind.lower()}_days": maximum_days,
    })
    save_workforce_settings(interaction.guild.id, settings)
    await interaction.response.send_message(f"{kind}, staff access, HR access, and history logging are configured.", ephemeral=True)


async def _shift_log(guild: discord.Guild, title: str, member: discord.Member, description: str, colour: int, actor: discord.abc.User | None = None) -> None:
    embed = discord.Embed(title=title, description=description, colour=colour, timestamp=_now())
    embed.add_field(name="Employee", value=f"{member.mention}\n`{member.id}`", inline=True)
    embed.set_footer(text="Rallybit Staff Operations")
    settings = get_workforce_settings(guild.id)
    await _send_channel_embed(guild, settings.get("shift_log_channel_id"), embed)
    await emit_audit_event(guild, "staff", title, description, actor=actor or member, target=f"{member} (`{member.id}`)")


async def _clock_out_member(guild: discord.Guild, member: discord.Member, actor: discord.abc.User) -> tuple[int, datetime, datetime] | None:
    data = _shift_data()
    record = _staff_record(data, guild.id, member.id)
    active = record.get("active")
    started = _parse_datetime(active.get("started_at")) if isinstance(active, dict) else None
    if started is None:
        return None
    ended = _now()
    seconds = _active_seconds(active, ended)
    break_seconds = max(0, int((ended - started).total_seconds()) - seconds)
    record["history"].append({
        "shift_id": _new_shift_id(),
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "seconds": seconds,
        "break_seconds": break_seconds,
        "note": str(active.get("note") or "")[:300],
        "clocked_out_by": str(actor.id),
    })
    record["history"] = record["history"][-MAX_SHIFT_HISTORY:]
    record["active"] = None
    record["display_name"] = member.display_name
    save_json(STAFF_SHIFTS_FILE, data)
    return seconds, started, ended


def setup_workforce_commands(tree: app_commands.CommandTree) -> None:
    loa = app_commands.Group(name="loa", description="Request and manage staff leave of absence.")
    roa = app_commands.Group(name="roa", description="Request and manage staff release of activity.")
    breaks = app_commands.Group(name="break", description="Pause and resume your active staff shift.")
    shifts = app_commands.Group(name="shifts", description="Inspect and correct staff shift records.")

    @loa.command(name="request", description="Submit a Leave of Absence request.")
    @_staff_check()
    async def loa_request(interaction: discord.Interaction, duration_days: int, reason: str, start_date: str | None = None) -> None:
        await _create_request(interaction, "LOA", duration_days, reason, start_date)

    @loa.command(name="status", description="View the status of your LOA request.")
    @_staff_check()
    async def loa_status(interaction: discord.Interaction, request_id: str | None = None) -> None:
        await _request_status(interaction, "LOA", request_id)

    @loa.command(name="cancel", description="Cancel your pending or approved LOA.")
    @_staff_check()
    async def loa_cancel(interaction: discord.Interaction, request_id: str) -> None:
        await _cancel_request(interaction, "LOA", request_id)

    @loa.command(name="setstatus", description="Update an LOA request status.")
    @_hr_check()
    @app_commands.choices(status=[app_commands.Choice(name=value, value=value) for value in ("Pending", "Approved", "Denied")])
    async def loa_setstatus(interaction: discord.Interaction, request_id: str, status: app_commands.Choice[str], note: str | None = None) -> None:
        await _set_request_status(interaction, "LOA", request_id, status.value, note)

    @loa.command(name="end", description="End an approved LOA early.")
    @_staff_check()
    async def loa_end(interaction: discord.Interaction, request_id: str) -> None:
        await _end_request(interaction, "LOA", request_id)

    @loa.command(name="settings", description="Configure LOA channels, access roles, duration, and logging.")
    @_hr_check()
    async def loa_settings(interaction: discord.Interaction, request_channel: discord.TextChannel, history_channel: discord.TextChannel, staff_role: discord.Role, hr_role: discord.Role, maximum_days: app_commands.Range[int, 1, 365] = 90, shift_log_channel: discord.TextChannel | None = None) -> None:
        await _update_request_settings(interaction, "LOA", request_channel, history_channel, staff_role, hr_role, maximum_days, shift_log_channel)

    @roa.command(name="request", description="Submit a Release of Activity request.")
    @_staff_check()
    async def roa_request(interaction: discord.Interaction, duration_days: int, reason: str, start_date: str | None = None) -> None:
        await _create_request(interaction, "ROA", duration_days, reason, start_date)

    @roa.command(name="status", description="View the status of your ROA request.")
    @_staff_check()
    async def roa_status(interaction: discord.Interaction, request_id: str | None = None) -> None:
        await _request_status(interaction, "ROA", request_id)

    @roa.command(name="cancel", description="Cancel your pending or approved ROA.")
    @_staff_check()
    async def roa_cancel(interaction: discord.Interaction, request_id: str) -> None:
        await _cancel_request(interaction, "ROA", request_id)

    @roa.command(name="setstatus", description="Update an ROA request status.")
    @_hr_check()
    @app_commands.choices(status=[app_commands.Choice(name=value, value=value) for value in ("Pending", "Approved", "Denied")])
    async def roa_setstatus(interaction: discord.Interaction, request_id: str, status: app_commands.Choice[str], note: str | None = None) -> None:
        await _set_request_status(interaction, "ROA", request_id, status.value, note)

    @roa.command(name="end", description="End an approved ROA early.")
    @_staff_check()
    async def roa_end(interaction: discord.Interaction, request_id: str) -> None:
        await _end_request(interaction, "ROA", request_id)

    @roa.command(name="settings", description="Configure ROA channels, access roles, duration, and logging.")
    @_hr_check()
    async def roa_settings(interaction: discord.Interaction, request_channel: discord.TextChannel, history_channel: discord.TextChannel, staff_role: discord.Role, hr_role: discord.Role, maximum_days: app_commands.Range[int, 1, 365] = 30, shift_log_channel: discord.TextChannel | None = None) -> None:
        await _update_request_settings(interaction, "ROA", request_channel, history_channel, staff_role, hr_role, maximum_days, shift_log_channel)

    @tree.command(name="clockin", description="Start your staff work shift.")
    @app_commands.guild_only()
    @_staff_check()
    async def clockin(interaction: discord.Interaction, note: str | None = None) -> None:
        assert interaction.guild is not None and isinstance(interaction.user, discord.Member)
        data = _shift_data()
        record = _staff_record(data, interaction.guild.id, interaction.user.id)
        if isinstance(record.get("active"), dict):
            await interaction.response.send_message("You are already clocked in.", ephemeral=True)
            return
        started = _now()
        record["active"] = {"started_at": started.isoformat(), "note": str(note or "")[:300], "break_seconds": 0, "break_started_at": None}
        record["display_name"] = interaction.user.display_name
        save_json(STAFF_SHIFTS_FILE, data)
        embed = discord.Embed(title="Clocked In Successfully", description="You have officially started your shift.", colour=SUCCESS, timestamp=started)
        embed.add_field(name="Employee", value=interaction.user.mention, inline=True)
        embed.add_field(name="Clock In", value=discord.utils.format_dt(started, "F"), inline=True)
        embed.add_field(name="Status", value="On Duty", inline=True)
        embed.add_field(name="Shift guidance", value="Remain active, respond to assigned work, and follow staff protocols.", inline=False)
        await interaction.response.send_message(embed=embed)
        await _shift_log(interaction.guild, "Staff Clock In", interaction.user, f"Clocked in {discord.utils.format_dt(started, 'F')}.\nStatus: **On Duty**", SUCCESS)

    @tree.command(name="clockout", description="End your active staff work shift.")
    @app_commands.guild_only()
    @_staff_check()
    async def clockout(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None and isinstance(interaction.user, discord.Member)
        result = await _clock_out_member(interaction.guild, interaction.user, interaction.user)
        if result is None:
            await interaction.response.send_message("You are not currently clocked in.", ephemeral=True)
            return
        seconds, started, ended = result
        embed = discord.Embed(title="Clocked Out Successfully", description="Your shift has been ended.", colour=DANGER, timestamp=ended)
        embed.add_field(name="Employee", value=interaction.user.mention, inline=True)
        embed.add_field(name="Total Time Worked", value=_format_duration(seconds), inline=True)
        embed.add_field(name="Status", value="Off Duty", inline=True)
        await interaction.response.send_message(embed=embed)
        await _shift_log(interaction.guild, "Staff Clock Out", interaction.user, f"Clock In: {discord.utils.format_dt(started, 'F')}\nClock Out: {discord.utils.format_dt(ended, 'F')}\nTime Worked: **{_format_duration(seconds)}**\nStatus: **Off Duty**", DANGER)

    @breaks.command(name="start", description="Begin a break and pause your active shift timer.")
    @_staff_check()
    async def break_start(interaction: discord.Interaction) -> None:
        data = _shift_data(); record = _staff_record(data, interaction.guild_id or 0, interaction.user.id); active = record.get("active")
        if not isinstance(active, dict):
            await interaction.response.send_message("Clock in before starting a break.", ephemeral=True); return
        if active.get("break_started_at"):
            await interaction.response.send_message("You are already on a break.", ephemeral=True); return
        active["break_started_at"] = _now().isoformat(); save_json(STAFF_SHIFTS_FILE, data)
        await interaction.response.send_message("Your break has started and the shift timer is paused.")
        if interaction.guild and isinstance(interaction.user, discord.Member):
            await _shift_log(interaction.guild, "Staff Break Started", interaction.user, "Shift timer paused.", WARNING)

    @breaks.command(name="end", description="End your break and resume the active shift timer.")
    @_staff_check()
    async def break_end(interaction: discord.Interaction) -> None:
        data = _shift_data(); record = _staff_record(data, interaction.guild_id or 0, interaction.user.id); active = record.get("active")
        break_started = _parse_datetime(active.get("break_started_at")) if isinstance(active, dict) else None
        if not isinstance(active, dict) or break_started is None:
            await interaction.response.send_message("You do not have an active break.", ephemeral=True); return
        seconds = max(0, int((_now() - break_started).total_seconds()))
        active["break_seconds"] = int(active.get("break_seconds", 0) or 0) + seconds
        active["break_started_at"] = None; save_json(STAFF_SHIFTS_FILE, data)
        await interaction.response.send_message(f"Break ended after **{_format_duration(seconds)}**. Your shift timer has resumed.")
        if interaction.guild and isinstance(interaction.user, discord.Member):
            await _shift_log(interaction.guild, "Staff Break Ended", interaction.user, f"Break duration: **{_format_duration(seconds)}**.", SUCCESS)

    @tree.command(name="shift", description="View your current shift duration and break state.")
    @_staff_check()
    async def shift(interaction: discord.Interaction) -> None:
        record = _staff_record(_shift_data(), interaction.guild_id or 0, interaction.user.id); active = record.get("active")
        if not isinstance(active, dict):
            await interaction.response.send_message("You are currently **Off Duty**.", ephemeral=True); return
        started = _parse_datetime(active.get("started_at")); on_break = bool(active.get("break_started_at"))
        embed = discord.Embed(title="Current staff shift", colour=WARNING if on_break else SUCCESS)
        embed.add_field(name="Status", value="On Break" if on_break else "On Duty", inline=True)
        embed.add_field(name="Worked", value=_format_duration(_active_seconds(active)), inline=True)
        if started:
            embed.add_field(name="Clocked in", value=discord.utils.format_dt(started, "R"), inline=True)
        await interaction.response.send_message(embed=embed)

    @tree.command(name="timesheet", description="View your worked hours during the last seven days.")
    @_staff_check()
    async def timesheet(interaction: discord.Interaction) -> None:
        record = _staff_record(_shift_data(), interaction.guild_id or 0, interaction.user.id)
        threshold = _now() - timedelta(days=7)
        rows = [row for row in record["history"] if isinstance(row, dict) and (_parse_datetime(row.get("ended_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= threshold]
        total = sum(max(0, int(row.get("seconds", 0) or 0)) for row in rows)
        lines = [f"{discord.utils.format_dt(_parse_datetime(row.get('ended_at')), 'D')} · **{_format_duration(row.get('seconds', 0))}**" for row in rows[-10:] if _parse_datetime(row.get("ended_at"))]
        embed = discord.Embed(title="Weekly timesheet", description="\n".join(lines) if lines else "No completed shifts in the last seven days.", colour=BRAND)
        embed.add_field(name="Total worked", value=_format_duration(total), inline=True)
        embed.add_field(name="Completed shifts", value=str(len(rows)), inline=True)
        await interaction.response.send_message(embed=embed)

    @tree.command(name="staffhours", description="View another staff member's recorded hours. HR access required.")
    @_hr_check()
    async def staffhours(interaction: discord.Interaction, member: discord.Member, days: app_commands.Range[int, 1, 365] = 30) -> None:
        record = _staff_record(_shift_data(), interaction.guild_id or 0, member.id); threshold = _now() - timedelta(days=days)
        rows = [row for row in record["history"] if isinstance(row, dict) and (_parse_datetime(row.get("ended_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= threshold]
        total = sum(max(0, int(row.get("seconds", 0) or 0)) for row in rows)
        await interaction.response.send_message(embed=discord.Embed(title=f"Staff hours · {member.display_name}", description=f"**{_format_duration(total)}** across **{len(rows)}** shifts in the last {days} days.", colour=BRAND))

    @tree.command(name="duty", description="Display every staff member currently on duty.")
    @_staff_check()
    async def duty(interaction: discord.Interaction) -> None:
        guild_rows = _shift_data().get(str(interaction.guild_id), {}); lines = []
        if isinstance(guild_rows, dict):
            for user_id, record in guild_rows.items():
                active = record.get("active") if isinstance(record, dict) else None
                if isinstance(active, dict):
                    status = "On Break" if active.get("break_started_at") else "On Duty"
                    lines.append(f"<@{user_id}> · **{status}** · {_format_duration(_active_seconds(active))}")
        await interaction.response.send_message(embed=discord.Embed(title="Staff currently on duty", description="\n".join(lines) if lines else "No staff members are currently clocked in.", colour=SUCCESS), allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name="forceclockout", description="Force a staff member off duty. HR access required.")
    @_hr_check()
    async def forceclockout(interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
        assert interaction.guild is not None
        result = await _clock_out_member(interaction.guild, member, interaction.user)
        if result is None:
            await interaction.response.send_message(f"{member.mention} is not clocked in.", ephemeral=True); return
        seconds, _, _ = result
        await interaction.response.send_message(f"{member.mention} was forced off duty after **{_format_duration(seconds)}**.", ephemeral=True)
        await _shift_log(interaction.guild, "Staff Forced Clock Out", member, f"Forced off duty by {interaction.user.mention}.\nTime Worked: **{_format_duration(seconds)}**\nReason: {reason or 'No reason provided'}", DANGER, actor=interaction.user)

    @shifts.command(name="list", description="List a staff member's completed shifts and record IDs. HR access required.")
    @app_commands.guild_only()
    @_hr_check()
    async def shifts_list(
        interaction: discord.Interaction,
        member: discord.Member,
        limit: app_commands.Range[int, 1, 20] = 10,
    ) -> None:
        assert interaction.guild is not None
        data = _shift_data()
        guild_data = data.get(str(interaction.guild.id), {})
        record = guild_data.get(str(member.id)) if isinstance(guild_data, dict) else None
        if not isinstance(record, dict):
            await interaction.response.send_message(f"{member.mention} has no saved shift account.", ephemeral=True)
            return
        if not isinstance(record.get("history"), list):
            record["history"] = []
        if _ensure_shift_ids(record) and not save_json(STAFF_SHIFTS_FILE, data):
            await interaction.response.send_message("Rallybit could not assign IDs to the saved shifts.", ephemeral=True)
            return
        rows = [row for row in record["history"] if isinstance(row, dict)]
        lines: list[str] = []
        for row in reversed(rows[-int(limit):]):
            ended = _parse_datetime(row.get("ended_at"))
            date = discord.utils.format_dt(ended, "D") if ended else "Unknown date"
            lines.append(f"`{row.get('shift_id')}` · {date} · **{_format_duration(row.get('seconds', 0))}**")
        embed = discord.Embed(
            title=f"Shift records · {member.display_name}",
            description="\n".join(lines) if lines else "No completed shifts are saved for this account.",
            colour=BRAND,
        )
        embed.add_field(name="Completed shifts", value=str(len(rows)), inline=True)
        embed.add_field(name="Active shift", value="Yes" if isinstance(record.get("active"), dict) else "No", inline=True)
        embed.set_footer(text="Use /shifts remove with a listed shift ID to correct one entry")
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @shifts.command(name="remove", description="Remove one completed shift by its record ID. HR access required.")
    @app_commands.guild_only()
    @_hr_check()
    async def shifts_remove(
        interaction: discord.Interaction,
        member: discord.Member,
        shift_id: str,
        reason: app_commands.Range[str, 1, 300],
    ) -> None:
        assert interaction.guild is not None
        data = _shift_data()
        guild_data = data.get(str(interaction.guild.id), {})
        record = guild_data.get(str(member.id)) if isinstance(guild_data, dict) else None
        if not isinstance(record, dict):
            await interaction.response.send_message(f"{member.mention} has no saved shift account.", ephemeral=True)
            return
        if not isinstance(record.get("history"), list):
            record["history"] = []
        ids_added = _ensure_shift_ids(record)
        removed = _remove_completed_shift(record, shift_id)
        if removed is None:
            if ids_added:
                save_json(STAFF_SHIFTS_FILE, data)
            await interaction.response.send_message("That completed shift ID was not found. Run `/shifts list` to copy the current ID.", ephemeral=True)
            return
        if not record["history"] and not isinstance(record.get("active"), dict):
            guild_data.pop(str(member.id), None)
            if not guild_data:
                data.pop(str(interaction.guild.id), None)
        if not save_json(STAFF_SHIFTS_FILE, data):
            await interaction.response.send_message("The shift could not be removed from storage.", ephemeral=True)
            return
        removed_id = str(removed.get("shift_id") or shift_id).upper()
        duration = _format_duration(removed.get("seconds", 0))
        await interaction.response.send_message(f"Removed shift `{removed_id}` from {member.mention} (**{duration}**).", ephemeral=True)
        await _shift_log(
            interaction.guild,
            "Staff Shift Removed",
            member,
            f"Shift `{removed_id}` was removed by {interaction.user.mention}.\nRecorded time: **{duration}**\nReason: {reason}",
            DANGER,
            actor=interaction.user,
        )

    @shifts.command(name="clear", description="Clear a staff member's saved shift account. HR access required.")
    @app_commands.guild_only()
    @_hr_check()
    async def shifts_clear(
        interaction: discord.Interaction,
        member: discord.Member,
        reason: app_commands.Range[str, 1, 300],
        include_active: bool = False,
        confirm: bool = False,
    ) -> None:
        assert interaction.guild is not None
        if not confirm:
            await interaction.response.send_message(
                "Nothing was removed. Run the command again with `confirm:True` after checking the member and `include_active` choice.",
                ephemeral=True,
            )
            return
        data = _shift_data()
        result = _clear_shift_account(data, interaction.guild.id, member.id, include_active=include_active)
        if result is None:
            await interaction.response.send_message(f"{member.mention} has no saved shift account.", ephemeral=True)
            return
        history_count, active_removed = result
        if history_count == 0 and not active_removed:
            await interaction.response.send_message(
                f"{member.mention} has no completed shifts to clear." + (" Their active shift was kept." if not include_active else ""),
                ephemeral=True,
            )
            return
        if not save_json(STAFF_SHIFTS_FILE, data):
            await interaction.response.send_message("The shift account could not be cleared from storage.", ephemeral=True)
            return
        active_note = " The active shift was also removed." if active_removed else ""
        await interaction.response.send_message(
            f"Cleared **{history_count}** completed shift(s) from {member.mention}.{active_note}",
            ephemeral=True,
        )
        await _shift_log(
            interaction.guild,
            "Staff Shift Account Cleared",
            member,
            f"{history_count} completed shift(s) were removed by {interaction.user.mention}.\nActive shift removed: **{'Yes' if active_removed else 'No'}**\nReason: {reason}",
            DANGER,
            actor=interaction.user,
        )

    tree.add_command(loa)
    tree.add_command(roa)
    tree.add_command(breaks)
    tree.add_command(shifts)
