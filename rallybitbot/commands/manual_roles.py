from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands

from config.config import TEMPORARY_ROLES_FILE
from core.logging import log_server_event
from storage.json_store import load_json, save_json

BRAND = 0x7567EE
SUCCESS = 0x45C486
SYNC_SECONDS = 30
UNIT_DAYS = {"days": 1, "weeks": 7, "months": 30, "years": 365}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _records() -> list[dict[str, Any]]:
    data = load_json(TEMPORARY_ROLES_FILE) or {}
    rows = data.get("records", []) if isinstance(data, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _save_records(records: list[dict[str, Any]]) -> bool:
    return save_json(TEMPORARY_ROLES_FILE, {"records": records})


def _actor_can_manage(actor: discord.Member, target: discord.Member, role: discord.Role) -> str | None:
    guild = actor.guild
    me = guild.me
    if role.is_default():
        return "The @everyone role cannot be assigned or removed."
    if role.managed:
        return "That role is managed by Discord or an integration."
    if actor.id != guild.owner_id:
        if role >= actor.top_role:
            return "That role must be below your highest role."
        if target.id != actor.id and target.top_role >= actor.top_role:
            return "You cannot manage a member whose highest role is equal to or above yours."
    if me is None:
        return "Rallybit's server member could not be found."
    if role >= me.top_role:
        return "Move Rallybit's role above the role you want it to manage."
    if target.id != me.id and target.top_role >= me.top_role:
        return "Rallybit cannot manage that member because their highest role is equal to or above its own."
    return None


def _countdown_label(expires_at: datetime, now: datetime | None = None) -> str:
    seconds = max(0, math.ceil((expires_at - (now or _now())).total_seconds()))
    if seconds >= 86400:
        return f"{math.ceil(seconds / 86400)}d"
    if seconds >= 3600:
        return f"{math.ceil(seconds / 3600)}h"
    if seconds >= 60:
        return f"{math.ceil(seconds / 60)}m"
    return f"{seconds}s"


def _countdown_nick(base_name: str, expires_at: datetime, now: datetime | None = None) -> str:
    suffix = f" | {_countdown_label(expires_at, now)}"
    base = " ".join(str(base_name).split()).strip() or "Member"
    return f"{base[: max(1, 32 - len(suffix))]}{suffix}"[:32]


def _member_records(records: list[dict[str, Any]], guild_id: int, member_id: int) -> list[dict[str, Any]]:
    return [
        row for row in records
        if str(row.get("guild_id")) == str(guild_id) and str(row.get("member_id")) == str(member_id)
    ]


def _record_token(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("guild_id", "")),
        str(row.get("member_id", "")),
        str(row.get("role_id", "")),
        str(row.get("expires_at", "")),
    )


async def _refresh_member_nickname(
    member: discord.Member,
    records: list[dict[str, Any]],
    *,
    original_nick: str | None = None,
) -> None:
    active = [row for row in _member_records(records, member.guild.id, member.id) if _parse_time(row.get("expires_at"))]
    if active:
        nearest = min(active, key=lambda row: _parse_time(row.get("expires_at")) or datetime.max.replace(tzinfo=timezone.utc))
        expiry = _parse_time(nearest.get("expires_at"))
        if expiry is None:
            return
        desired = _countdown_nick(str(nearest.get("base_name") or member.display_name), expiry)
    else:
        desired = original_nick
    if member.nick == desired:
        return
    try:
        await member.edit(nick=desired, reason="Rallybit temporary role countdown")
    except (discord.Forbidden, discord.HTTPException):
        pass


def _drop_temp_record(
    records: list[dict[str, Any]], guild_id: int, member_id: int, role_id: int
) -> tuple[list[dict[str, Any]], str | None, bool]:
    removed: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for row in records:
        if (
            str(row.get("guild_id")) == str(guild_id)
            and str(row.get("member_id")) == str(member_id)
            and str(row.get("role_id")) == str(role_id)
        ):
            removed.append(row)
        else:
            kept.append(row)
    original = removed[0].get("original_nick") if removed else None
    return kept, original if isinstance(original, str) else None, bool(removed)


async def process_temporary_roles(bot: discord.Client) -> dict[str, int]:
    records = _records()
    removals: set[tuple[str, str, str, str]] = set()
    affected: dict[tuple[int, int], str | None] = {}
    removed = 0
    now = _now()
    for row in records:
        try:
            guild_id = int(row.get("guild_id"))
            member_id = int(row.get("member_id"))
            role_id = int(row.get("role_id"))
        except (TypeError, ValueError):
            removals.add(_record_token(row))
            continue
        guild = bot.get_guild(guild_id)
        member = guild.get_member(member_id) if guild else None
        role = guild.get_role(role_id) if guild else None
        expiry = _parse_time(row.get("expires_at"))
        if guild is None or member is None or role is None or expiry is None:
            affected[(guild_id, member_id)] = row.get("original_nick") if isinstance(row.get("original_nick"), str) else None
            removals.add(_record_token(row))
            continue
        if role not in member.roles:
            affected[(guild_id, member_id)] = row.get("original_nick") if isinstance(row.get("original_nick"), str) else None
            removals.add(_record_token(row))
            continue
        if expiry <= now:
            me = guild.me
            if me is None or role.managed or role >= me.top_role or (member.id != me.id and member.top_role >= me.top_role):
                continue
            try:
                await member.remove_roles(role, reason="Rallybit temporary role expired")
            except (discord.Forbidden, discord.HTTPException):
                continue
            removed += 1
            removals.add(_record_token(row))
            affected[(guild_id, member_id)] = row.get("original_nick") if isinstance(row.get("original_nick"), str) else None
            log_server_event(guild_id, f"Temporary role {role.name} expired for {member}.")
        else:
            affected.setdefault((guild_id, member_id), row.get("original_nick") if isinstance(row.get("original_nick"), str) else None)
    # Reload before saving so a command that created or extended a timer while
    # Discord was processing an expiry is not overwritten by this pass.
    latest = _records()
    kept = [row for row in latest if _record_token(row) not in removals]
    if kept != latest:
        _save_records(kept)
    for (guild_id, member_id), original_nick in affected.items():
        guild = bot.get_guild(guild_id)
        member = guild.get_member(member_id) if guild else None
        if member:
            await _refresh_member_nickname(member, kept, original_nick=original_nick)
    return {"active": len(kept), "removed": removed}


async def temporary_role_sync_loop(bot: discord.Client) -> None:
    while not bot.is_closed():
        try:
            await process_temporary_roles(bot)
        except Exception as exc:
            print(f"[TEMP ROLES] Synchronisation failed: {exc!r}")
        await asyncio.sleep(SYNC_SECONDS)


def setup_manual_role_commands(tree: app_commands.CommandTree) -> None:
    role_group = app_commands.Group(name="role", description="Safely give, remove, or temporarily assign roles.")

    @role_group.command(name="give", description="Give a member a role below your highest role.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.describe(member="Member receiving the role", role="Role to give", reason="Optional audit-log reason")
    async def role_give(
        interaction: discord.Interaction,
        member: discord.Member,
        role: discord.Role,
        reason: app_commands.Range[str, 1, 300] | None = None,
    ) -> None:
        assert interaction.guild is not None and isinstance(interaction.user, discord.Member)
        denial = _actor_can_manage(interaction.user, member, role)
        if denial:
            await interaction.response.send_message(denial, ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("Use Discord's integration settings to manage bot roles.", ephemeral=True)
            return
        records = _records()
        records, original_nick, was_temporary = _drop_temp_record(records, interaction.guild.id, member.id, role.id)
        if role not in member.roles:
            try:
                await member.add_roles(role, reason=str(reason or f"Role given by {interaction.user} ({interaction.user.id})"))
            except (discord.Forbidden, discord.HTTPException):
                await interaction.response.send_message("Discord rejected that role assignment. Check Rallybit's role position.", ephemeral=True)
                return
        if was_temporary:
            _save_records(records)
            await _refresh_member_nickname(member, records, original_nick=original_nick)
        log_server_event(interaction.guild.id, f"{interaction.user} gave {role.name} to {member}.")
        await interaction.response.send_message(
            f"{role.mention} is now assigned to {member.mention}." + (" The temporary timer was converted to permanent." if was_temporary else ""),
            ephemeral=True,
        )

    @role_group.command(name="remove", description="Remove a role below your highest role from a member.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.describe(member="Member losing the role", role="Role to remove", reason="Optional audit-log reason")
    async def role_remove(
        interaction: discord.Interaction,
        member: discord.Member,
        role: discord.Role,
        reason: app_commands.Range[str, 1, 300] | None = None,
    ) -> None:
        assert interaction.guild is not None and isinstance(interaction.user, discord.Member)
        denial = _actor_can_manage(interaction.user, member, role)
        if denial:
            await interaction.response.send_message(denial, ephemeral=True)
            return
        records = _records()
        records, original_nick, was_temporary = _drop_temp_record(records, interaction.guild.id, member.id, role.id)
        if role in member.roles:
            try:
                await member.remove_roles(role, reason=str(reason or f"Role removed by {interaction.user} ({interaction.user.id})"))
            except (discord.Forbidden, discord.HTTPException):
                await interaction.response.send_message("Discord rejected that role removal. Check Rallybit's role position.", ephemeral=True)
                return
        if was_temporary:
            _save_records(records)
            await _refresh_member_nickname(member, records, original_nick=original_nick)
        log_server_event(interaction.guild.id, f"{interaction.user} removed {role.name} from {member}.")
        await interaction.response.send_message(f"{role.mention} was removed from {member.mention}.", ephemeral=True)

    @role_group.command(name="temp", description="Give a timed role with a live nickname countdown.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.choices(unit=[
        app_commands.Choice(name="Days", value="days"),
        app_commands.Choice(name="Weeks", value="weeks"),
        app_commands.Choice(name="Months (30 days)", value="months"),
        app_commands.Choice(name="Years (365 days)", value="years"),
    ])
    @app_commands.describe(
        member="Member receiving the temporary role",
        role="Role to assign temporarily",
        amount="Number of selected time units",
        unit="Days, weeks, 30-day months, or 365-day years",
        nickname="Optional base nickname shown before the countdown",
        reason="Optional audit-log reason",
    )
    async def role_temp(
        interaction: discord.Interaction,
        member: discord.Member,
        role: discord.Role,
        amount: app_commands.Range[int, 1, 365],
        unit: app_commands.Choice[str],
        nickname: app_commands.Range[str, 1, 32] | None = None,
        reason: app_commands.Range[str, 1, 300] | None = None,
    ) -> None:
        assert interaction.guild is not None and isinstance(interaction.user, discord.Member)
        denial = _actor_can_manage(interaction.user, member, role)
        if denial:
            await interaction.response.send_message(denial, ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("Temporary roles and nickname countdowns can only be used for members.", ephemeral=True)
            return
        if not interaction.guild.me or not interaction.guild.me.guild_permissions.manage_nicknames:
            await interaction.response.send_message("Rallybit needs Manage Nicknames to display the live countdown.", ephemeral=True)
            return
        if nickname is not None and not interaction.user.guild_permissions.manage_nicknames:
            await interaction.response.send_message("You need Manage Nicknames to choose a custom base nickname.", ephemeral=True)
            return
        if unit.value == "years" and int(amount) > 10:
            await interaction.response.send_message("Temporary roles can last at most 10 years.", ephemeral=True)
            return
        duration = timedelta(days=int(amount) * UNIT_DAYS[unit.value])
        expires = _now() + duration
        records = _records()
        existing_member = _member_records(records, interaction.guild.id, member.id)
        existing_role = next((row for row in existing_member if str(row.get("role_id")) == str(role.id)), None)
        if role in member.roles and existing_role is None:
            await interaction.response.send_message(
                "That member already has this role permanently. Remove it first if you want it to become temporary.", ephemeral=True
            )
            return
        original_nick = existing_member[0].get("original_nick") if existing_member else member.nick
        base_name = " ".join(str(nickname or (existing_member[0].get("base_name") if existing_member else member.display_name)).split())
        record = {
            "guild_id": str(interaction.guild.id),
            "member_id": str(member.id),
            "role_id": str(role.id),
            "created_at": _now().isoformat(),
            "expires_at": expires.isoformat(),
            "assigned_by": str(interaction.user.id),
            "original_nick": original_nick,
            "base_name": base_name[:32],
            "reason": str(reason or "")[:300],
        }
        if existing_role is not None:
            records[records.index(existing_role)] = record
        else:
            records.append(record)
        if not _save_records(records):
            await interaction.response.send_message("The temporary-role timer could not be saved, so no role was changed.", ephemeral=True)
            return
        if role not in member.roles:
            try:
                await member.add_roles(role, reason=str(reason or f"Temporary role by {interaction.user} ({interaction.user.id})"))
            except (discord.Forbidden, discord.HTTPException):
                records, _, _ = _drop_temp_record(records, interaction.guild.id, member.id, role.id)
                _save_records(records)
                await interaction.response.send_message("Discord rejected that role assignment. Check Rallybit's role position.", ephemeral=True)
                return
        await _refresh_member_nickname(member, records, original_nick=original_nick if isinstance(original_nick, str) else None)
        log_server_event(interaction.guild.id, f"{interaction.user} gave {role.name} to {member} until {expires.isoformat()}.")
        embed = discord.Embed(
            title="Temporary role assigned",
            description=f"{role.mention} was assigned to {member.mention} until {discord.utils.format_dt(expires, 'F')} ({discord.utils.format_dt(expires, 'R')}).",
            color=SUCCESS,
        )
        embed.add_field(name="Nickname countdown", value=_countdown_nick(base_name, expires), inline=False)
        embed.set_footer(text="Months use 30 days; years use 365 days")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    tree.add_command(role_group)
