from __future__ import annotations

import copy
import csv
import io
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands

from commands.moderation import can_use_moderation_action, moderation_denial
from config.config import (
    AUTOMATION_SCHEDULES_FILE,
    AUTOROLE_SETTINGS_FILE,
    COMMUNITY_SETTINGS_FILE,
    GIVEAWAY_SETTINGS_FILE,
    LEVEL_SETTINGS_FILE,
    MOD_HISTORY_FILE,
    MOD_PERMISSIONS_FILE,
    NETWORK_SETTINGS_FILE,
    PREMIUM_BACKUPS_FILE,
    QUIZ_SETTINGS_FILE,
    REACTION_ROLES_FILE,
    REPORT_SETTINGS_FILE,
    REVIEW_SETTINGS_FILE,
    SECURITY_SETTINGS_FILE,
    SETTINGS_FILE,
    TICKET_SETTINGS_FILE,
    VERIFICATION_SETTINGS_FILE,
    WELCOME_SETTINGS_FILE,
)
from core.premium import has_plan, premium_check, resolve_entitlement
from storage.json_store import load_json, save_json

BRAND = 0x7567EE
SUCCESS = 0x45C486
WARNING = 0xF0B232
MAX_BACKUPS = 10

CONFIG_SOURCES: dict[str, str] = {
    "activity": SETTINGS_FILE,
    "quizzes": QUIZ_SETTINGS_FILE,
    "community": COMMUNITY_SETTINGS_FILE,
    "giveaways": GIVEAWAY_SETTINGS_FILE,
    "welcomes": WELCOME_SETTINGS_FILE,
    "levels": LEVEL_SETTINGS_FILE,
    "autoroles": AUTOROLE_SETTINGS_FILE,
    "reaction_roles": REACTION_ROLES_FILE,
    "verification": VERIFICATION_SETTINGS_FILE,
    "tickets": TICKET_SETTINGS_FILE,
    "reports": REPORT_SETTINGS_FILE,
    "reviews": REVIEW_SETTINGS_FILE,
    "moderation_permissions": MOD_PERMISSIONS_FILE,
    "security": SECURITY_SETTINGS_FILE,
    "automation": AUTOMATION_SCHEDULES_FILE,
}

CASE_ACTIONS = (
    "warn",
    "unwarn",
    "timeout",
    "untimeout",
    "kick",
    "ban",
    "unban",
    "clear",
    "nickname",
    "role_add",
    "role_remove",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _staff_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if (
            interaction.guild
            and isinstance(interaction.user, discord.Member)
            and can_use_moderation_action(interaction.user, "warn")
        ):
            return True
        raise app_commands.CheckFailure(moderation_denial("warn"))

    return app_commands.check(predicate)


def _case_rows(guild_id: int) -> list[dict[str, Any]]:
    data = load_json(MOD_HISTORY_FILE) or {}
    guild_data = data.get(str(guild_id), {}) if isinstance(data, dict) else {}
    rows: list[dict[str, Any]] = []
    if not isinstance(guild_data, dict):
        return rows
    for target_id, records in guild_data.items():
        if not isinstance(records, list):
            continue
        for record in records:
            if isinstance(record, dict):
                rows.append({**record, "target_id": str(target_id)})
    rows.sort(
        key=lambda row: (
            _parse_datetime(row.get("timestamp"))
            or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )
    return rows


def filter_case_rows(
    guild_id: int,
    *,
    days: int | None = None,
    member_id: int | None = None,
    moderator_id: int | None = None,
    action: str | None = None,
) -> list[dict[str, Any]]:
    threshold = _now() - timedelta(days=max(1, min(3650, int(days)))) if days else None
    wanted_action = str(action or "").strip().lower()
    rows = []
    for row in _case_rows(guild_id):
        when = _parse_datetime(row.get("timestamp"))
        if threshold and (when is None or when < threshold):
            continue
        if member_id is not None and str(row.get("target_id")) != str(member_id):
            continue
        if moderator_id is not None and str(row.get("moderator_id")) != str(
            moderator_id
        ):
            continue
        if wanted_action and str(row.get("action", "")).lower() != wanted_action:
            continue
        rows.append(row)
    return rows


def _case_line(row: dict[str, Any]) -> str:
    when = _parse_datetime(row.get("timestamp"))
    timestamp = discord.utils.format_dt(when, "R") if when else "Unknown time"
    action = str(row.get("action") or "Action").replace("_", " ").title()
    reason = discord.utils.escape_markdown(
        str(row.get("reason") or row.get("details") or "No reason recorded")
    )[:180]
    return (
        f"**{action}** · <@{row.get('target_id')}> · {timestamp}\n"
        f"{reason} · by <@{row.get('moderator_id')}>"
    )


def _case_description(rows: list[dict[str, Any]], empty_message: str) -> str:
    if not rows:
        return empty_message
    lines: list[str] = []
    used = 0
    for row in rows:
        line = _case_line(row)
        extra = len(line) + (2 if lines else 0)
        if used + extra > 3900:
            break
        lines.append(line)
        used += extra
    return "\n\n".join(lines)


def case_export_csv(guild: discord.Guild, rows: list[dict[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["Rallybit moderation case export"])
    writer.writerow(
        ["Server", guild.name, "Server ID", guild.id, "Exported at", _now().isoformat()]
    )
    writer.writerow([])
    writer.writerow(
        [
            "Timestamp",
            "Action",
            "Member ID",
            "Moderator ID",
            "Moderator",
            "Reason",
            "Details",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.get("timestamp", ""),
                row.get("action", ""),
                row.get("target_id", ""),
                row.get("moderator_id", ""),
                row.get("moderator_name", ""),
                row.get("reason", ""),
                row.get("details", ""),
            ]
        )
    return stream.getvalue().encode("utf-8-sig")


def _backups() -> dict[str, Any]:
    data = load_json(PREMIUM_BACKUPS_FILE) or {}
    return data if isinstance(data, dict) else {}


def _guild_section(path: str, guild_id: int) -> Any:
    data = load_json(path) or {}
    if not isinstance(data, dict):
        return None
    return copy.deepcopy(data.get(str(guild_id)))


def _server_structure(guild: discord.Guild) -> dict[str, Any]:
    return {
        "channels": [
            {
                "id": str(channel.id),
                "name": channel.name,
                "type": str(channel.type),
                "category_id": str(channel.category_id)
                if getattr(channel, "category_id", None)
                else None,
                "position": channel.position,
            }
            for channel in guild.channels
        ],
        "roles": [
            {
                "id": str(role.id),
                "name": role.name,
                "position": role.position,
                "permissions": str(role.permissions.value),
            }
            for role in guild.roles
            if not role.is_default() and not role.managed
        ],
    }


def create_config_backup(
    guild: discord.Guild,
    actor_id: int,
    label: str = "",
) -> dict[str, Any]:
    data = _backups()
    guild_backups = data.setdefault(str(guild.id), {})
    if not isinstance(guild_backups, dict):
        guild_backups = {}
        data[str(guild.id)] = guild_backups
    backup_id = uuid.uuid4().hex[:8].upper()
    settings = {
        name: _guild_section(path, guild.id) for name, path in CONFIG_SOURCES.items()
    }
    record = {
        "id": backup_id,
        "label": str(label or "Manual snapshot").strip()[:80],
        "created_at": _now().isoformat(),
        "created_by": str(actor_id),
        "settings": settings,
        "structure": _server_structure(guild),
    }
    guild_backups[backup_id] = record
    ordered = sorted(
        guild_backups.values(),
        key=lambda item: (
            str(item.get("created_at", "")) if isinstance(item, dict) else ""
        ),
        reverse=True,
    )[:MAX_BACKUPS]
    data[str(guild.id)] = {
        str(item["id"]): item
        for item in ordered
        if isinstance(item, dict) and item.get("id")
    }
    if not save_json(PREMIUM_BACKUPS_FILE, data):
        raise RuntimeError("The configuration backup could not be saved.")
    return record


def get_config_backup(guild_id: int, backup_id: str) -> dict[str, Any] | None:
    guild_backups = _backups().get(str(guild_id), {})
    if not isinstance(guild_backups, dict):
        return None
    record = guild_backups.get(str(backup_id).strip().upper())
    return record if isinstance(record, dict) else None


def list_config_backups(guild_id: int) -> list[dict[str, Any]]:
    records = _backups().get(str(guild_id), {})
    if not isinstance(records, dict):
        return []
    return sorted(
        [record for record in records.values() if isinstance(record, dict)],
        key=lambda item: str(item.get("created_at", "")),
        reverse=True,
    )


def delete_config_backup(guild_id: int, backup_id: str) -> bool:
    data = _backups()
    guild_backups = data.get(str(guild_id), {})
    if not isinstance(guild_backups, dict):
        return False
    if guild_backups.pop(str(backup_id).strip().upper(), None) is None:
        return False
    data[str(guild_id)] = guild_backups
    if not save_json(PREMIUM_BACKUPS_FILE, data):
        raise RuntimeError("The backup index could not be saved.")
    return True


def config_drift(guild: discord.Guild, backup: dict[str, Any]) -> dict[str, Any]:
    structure = backup.get("structure", {})
    if not isinstance(structure, dict):
        structure = {}
    old_channels = {
        str(row.get("id"))
        for row in structure.get("channels", [])
        if isinstance(row, dict)
    }
    old_roles = {
        str(row.get("id"))
        for row in structure.get("roles", [])
        if isinstance(row, dict)
    }
    current_channels = {str(channel.id) for channel in guild.channels}
    current_roles = {
        str(role.id)
        for role in guild.roles
        if not role.is_default() and not role.managed
    }
    saved_settings = backup.get("settings", {})
    if not isinstance(saved_settings, dict):
        saved_settings = {}
    changed_modules = [
        name
        for name, path in CONFIG_SOURCES.items()
        if saved_settings.get(name) != _guild_section(path, guild.id)
    ]
    return {
        "deleted_channels": len(old_channels - current_channels),
        "new_channels": len(current_channels - old_channels),
        "deleted_roles": len(old_roles - current_roles),
        "new_roles": len(current_roles - old_roles),
        "changed_modules": changed_modules,
    }


def restore_config_backup(guild_id: int, backup: dict[str, Any]) -> int:
    saved_settings = backup.get("settings", {})
    if not isinstance(saved_settings, dict):
        raise TypeError("That backup does not contain restorable settings.")
    originals = {
        name: _guild_section(path, guild_id) for name, path in CONFIG_SOURCES.items()
    }
    changed: list[str] = []
    try:
        for name, path in CONFIG_SOURCES.items():
            data = load_json(path) or {}
            if not isinstance(data, dict):
                data = {}
            if saved_settings.get(name) is None:
                data.pop(str(guild_id), None)
            else:
                data[str(guild_id)] = copy.deepcopy(saved_settings[name])
            if not save_json(path, data):
                raise RuntimeError(f"Could not restore {name} settings.")
            changed.append(name)
    except Exception:
        for name in changed:
            path = CONFIG_SOURCES[name]
            data = load_json(path) or {}
            if not isinstance(data, dict):
                data = {}
            if originals[name] is None:
                data.pop(str(guild_id), None)
            else:
                data[str(guild_id)] = originals[name]
            save_json(path, data)
        raise
    return len(changed)


def _network_settings() -> dict[str, Any]:
    data = load_json(NETWORK_SETTINGS_FILE) or {}
    return data if isinstance(data, dict) else {}


def _owned_guilds(interaction: discord.Interaction) -> list[discord.Guild]:
    return sorted(
        [
            guild
            for guild in interaction.client.guilds
            if guild.owner_id == interaction.user.id
        ],
        key=lambda guild: guild.name.lower(),
    )


def _network_owner_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None or interaction.user.id != guild.owner_id:
            raise app_commands.CheckFailure(
                "Network operations can only be run by the server owner."
            )
        entitlement = resolve_entitlement(
            user_id=interaction.user.id,
            guild_id=guild.id,
            guild_owner_id=guild.owner_id,
        )
        if not has_plan(entitlement, "network"):
            raise app_commands.CheckFailure(
                "This command requires the **Network** plan. Paid plans are coming soon."
            )
        return True

    return app_commands.check(predicate)


def setup_premium_operation_commands(tree: app_commands.CommandTree) -> None:
    cases = app_commands.Group(
        name="case",
        description="Search and export the server's moderation case history.",
    )
    backups = app_commands.Group(
        name="backup",
        description="Create and restore Rallybit configuration snapshots.",
    )
    network = app_commands.Group(
        name="network",
        description="Operate every Discord server you own from one place.",
    )
    action_choices = [
        app_commands.Choice(name=value.replace("_", " ").title(), value=value)
        for value in CASE_ACTIONS
    ]

    @cases.command(
        name="member", description="View a member's complete moderation record."
    )
    @app_commands.guild_only()
    @_staff_check()
    @premium_check("community")
    @app_commands.describe(
        member="Member whose cases you want to inspect",
        limit="Number of recent actions to show",
    )
    async def case_member(
        interaction: discord.Interaction,
        member: discord.Member,
        limit: app_commands.Range[int, 1, 20] = 10,
    ) -> None:
        assert interaction.guild is not None
        rows = filter_case_rows(interaction.guild.id, member_id=member.id)[:limit]
        embed = discord.Embed(
            title=f"Case file · {member.display_name}",
            description=_case_description(
                rows,
                "No moderation actions are recorded for this member.",
            ),
            color=BRAND,
            timestamp=_now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"{len(rows)} shown · Community plan")
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @cases.command(
        name="recent",
        description="Search recent moderation actions by type or moderator.",
    )
    @app_commands.guild_only()
    @_staff_check()
    @premium_check("community")
    @app_commands.choices(action=action_choices)
    async def case_recent(
        interaction: discord.Interaction,
        action: app_commands.Choice[str] | None = None,
        moderator: discord.Member | None = None,
        limit: app_commands.Range[int, 1, 20] = 10,
    ) -> None:
        assert interaction.guild is not None
        rows = filter_case_rows(
            interaction.guild.id,
            moderator_id=moderator.id if moderator else None,
            action=action.value if action else None,
        )[:limit]
        embed = discord.Embed(
            title="Recent moderation cases",
            description=_case_description(
                rows,
                "No moderation actions match those filters.",
            ),
            color=BRAND,
            timestamp=_now(),
        )
        embed.set_footer(text=f"{len(rows)} shown · Community plan")
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @cases.command(
        name="stats",
        description="See moderation workload, action types, and top moderators.",
    )
    @app_commands.guild_only()
    @_staff_check()
    @premium_check("community")
    async def case_stats(
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 365] = 30,
    ) -> None:
        assert interaction.guild is not None
        rows = filter_case_rows(interaction.guild.id, days=days)
        actions = Counter(
            str(row.get("action") or "unknown").replace("_", " ").title()
            for row in rows
        )
        moderators = Counter(str(row.get("moderator_id") or "unknown") for row in rows)
        action_lines = (
            "\n".join(
                f"**{name}:** {count:,}" for name, count in actions.most_common(6)
            )
            or "No actions"
        )
        moderator_lines = (
            "\n".join(
                f"<@{user_id}> · {count:,}"
                for user_id, count in moderators.most_common(5)
            )
            or "No moderators"
        )
        embed = discord.Embed(
            title=f"Case workload · {days} days",
            color=BRAND,
            timestamp=_now(),
        )
        embed.add_field(
            name="Recorded actions", value=f"**{len(rows):,}** total", inline=False
        )
        embed.add_field(name="Action breakdown", value=action_lines, inline=True)
        embed.add_field(name="Moderator workload", value=moderator_lines, inline=True)
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @cases.command(
        name="export",
        description="Export filtered moderation cases as a CSV audit file.",
    )
    @app_commands.guild_only()
    @_staff_check()
    @premium_check("pro")
    @app_commands.choices(action=action_choices)
    async def case_export(
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 365] = 90,
        member: discord.Member | None = None,
        moderator: discord.Member | None = None,
        action: app_commands.Choice[str] | None = None,
    ) -> None:
        assert interaction.guild is not None
        rows = filter_case_rows(
            interaction.guild.id,
            days=days,
            member_id=member.id if member else None,
            moderator_id=moderator.id if moderator else None,
            action=action.value if action else None,
        )
        payload = case_export_csv(interaction.guild, rows)
        await interaction.response.send_message(
            content=f"Exported **{len(rows):,}** matching moderation actions.",
            file=discord.File(
                io.BytesIO(payload),
                filename=f"rallybit-cases-{interaction.guild.id}-{_now():%Y%m%d}.csv",
            ),
            ephemeral=True,
        )

    @backups.command(
        name="create",
        description="Save a restorable snapshot of every Rallybit server setting.",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @premium_check("pro")
    async def backup_create(
        interaction: discord.Interaction,
        label: app_commands.Range[str, 1, 80] | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        record = create_config_backup(
            interaction.guild,
            interaction.user.id,
            str(label or ""),
        )
        configured = sum(value is not None for value in record["settings"].values())
        structure = record["structure"]
        embed = discord.Embed(
            title="Configuration backup created",
            description=f"Backup ID: `{record['id']}`",
            color=SUCCESS,
            timestamp=_now(),
        )
        embed.add_field(name="Label", value=record["label"], inline=False)
        embed.add_field(
            name="Rallybit modules", value=f"{configured} configured", inline=True
        )
        embed.add_field(
            name="Structure manifest",
            value=f"{len(structure['channels'])} channels · {len(structure['roles'])} roles",
            inline=True,
        )
        embed.set_footer(
            text=f"Rallybit retains the latest {MAX_BACKUPS} backups per server"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @backups.command(
        name="list", description="List the server's retained configuration backups."
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @premium_check("pro")
    async def backup_list(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        records = list_config_backups(interaction.guild.id)
        lines = []
        for record in records:
            created = _parse_datetime(record.get("created_at"))
            when = discord.utils.format_dt(created, "R") if created else "Unknown time"
            lines.append(
                f"`{record.get('id')}` · **{record.get('label', 'Snapshot')}** · "
                f"{when} · by <@{record.get('created_by')}>"
            )
        embed = discord.Embed(
            title="Configuration backups",
            description="\n".join(lines)
            if lines
            else "No configuration backups exist yet.",
            color=BRAND,
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @backups.command(
        name="inspect", description="Inspect a backup before restoring it."
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @premium_check("pro")
    async def backup_inspect(
        interaction: discord.Interaction,
        backup_id: str,
    ) -> None:
        assert interaction.guild is not None
        record = get_config_backup(interaction.guild.id, backup_id)
        if not record:
            await interaction.response.send_message(
                "That backup was not found.", ephemeral=True
            )
            return
        configured = [
            name.replace("_", " ").title()
            for name, value in record.get("settings", {}).items()
            if value is not None
        ]
        structure = record.get("structure", {})
        created = _parse_datetime(record.get("created_at"))
        embed = discord.Embed(
            title=f"Backup {record['id']}",
            description=record.get("label"),
            color=BRAND,
        )
        embed.add_field(
            name="Created",
            value=discord.utils.format_dt(created, "F") if created else "Unknown",
            inline=False,
        )
        embed.add_field(
            name="Restorable modules",
            value=", ".join(configured) if configured else "No configured modules",
            inline=False,
        )
        embed.add_field(
            name="Structure manifest",
            value=(
                f"{len(structure.get('channels', []))} channels · "
                f"{len(structure.get('roles', []))} roles"
            ),
            inline=False,
        )
        embed.set_footer(
            text="Restore changes Rallybit settings only; it never deletes Discord channels or roles"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @backups.command(
        name="drift",
        description="Compare current settings, channels, and roles with a backup.",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @premium_check("pro")
    async def backup_drift(interaction: discord.Interaction, backup_id: str) -> None:
        assert interaction.guild is not None
        record = get_config_backup(interaction.guild.id, backup_id)
        if not record:
            await interaction.response.send_message(
                "That backup was not found.", ephemeral=True
            )
            return
        drift = config_drift(interaction.guild, record)
        modules = (
            ", ".join(
                name.replace("_", " ").title() for name in drift["changed_modules"]
            )
            or "No Rallybit setting changes"
        )
        embed = discord.Embed(
            title=f"Configuration drift · {record['id']}",
            color=WARNING,
            timestamp=_now(),
        )
        embed.add_field(name="Rallybit settings changed", value=modules, inline=False)
        embed.add_field(
            name="Channels",
            value=f"{drift['new_channels']} added · {drift['deleted_channels']} missing",
            inline=True,
        )
        embed.add_field(
            name="Roles",
            value=f"{drift['new_roles']} added · {drift['deleted_roles']} missing",
            inline=True,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @backups.command(
        name="restore", description="Restore every Rallybit setting from a backup."
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @premium_check("pro")
    async def backup_restore(
        interaction: discord.Interaction,
        backup_id: str,
        confirm: bool,
    ) -> None:
        assert interaction.guild is not None
        if not confirm:
            await interaction.response.send_message(
                "Nothing was changed. Set `confirm` to `True` after inspecting the backup and drift report.",
                ephemeral=True,
            )
            return
        record = get_config_backup(interaction.guild.id, backup_id)
        if not record:
            await interaction.response.send_message(
                "That backup was not found.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        restored = restore_config_backup(interaction.guild.id, record)
        await interaction.followup.send(
            embed=discord.Embed(
                title="Configuration restored",
                description=(
                    f"Restored **{restored} Rallybit modules** from `{record['id']}`. "
                    "Discord channels and roles were not deleted or recreated."
                ),
                color=SUCCESS,
                timestamp=_now(),
            ),
            ephemeral=True,
        )

    @backups.command(
        name="delete", description="Permanently delete a retained configuration backup."
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @premium_check("pro")
    async def backup_delete(
        interaction: discord.Interaction,
        backup_id: str,
        confirm: bool,
    ) -> None:
        if not interaction.guild or not confirm:
            await interaction.response.send_message(
                "Nothing was deleted. Set `confirm` to `True` to delete the backup.",
                ephemeral=True,
            )
            return
        removed = delete_config_backup(interaction.guild.id, backup_id)
        await interaction.response.send_message(
            "Backup deleted." if removed else "That backup was not found.",
            ephemeral=True,
        )

    @network.command(
        name="channel",
        description="Set this owned server's Network announcement destination.",
    )
    @app_commands.guild_only()
    @_network_owner_check()
    async def network_channel(
        interaction: discord.Interaction,
        destination: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        data = _network_settings()
        if destination is None:
            data.pop(str(interaction.guild.id), None)
            message = "Network announcements are disabled for this server."
        else:
            permissions = destination.permissions_for(interaction.guild.me)
            if not permissions.send_messages or not permissions.embed_links:
                await interaction.response.send_message(
                    "Rallybit needs Send Messages and Embed Links in that channel.",
                    ephemeral=True,
                )
                return
            data[str(interaction.guild.id)] = {
                "channel_id": str(destination.id),
                "configured_by": str(interaction.user.id),
                "updated_at": _now().isoformat(),
            }
            message = f"Network announcements will be sent to {destination.mention}."
        if not save_json(NETWORK_SETTINGS_FILE, data):
            await interaction.response.send_message(
                "The Network destination could not be saved.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(message, ephemeral=True)

    @network.command(
        name="overview",
        description="View every owned server and its Network readiness.",
    )
    @app_commands.guild_only()
    @_network_owner_check()
    async def network_overview(interaction: discord.Interaction) -> None:
        owned = _owned_guilds(interaction)
        settings = _network_settings()
        lines = []
        ready = 0
        for guild in owned[:25]:
            record = settings.get(str(guild.id), {})
            channel_id = record.get("channel_id") if isinstance(record, dict) else None
            channel = (
                guild.get_channel(int(channel_id))
                if str(channel_id or "").isdigit()
                else None
            )
            if isinstance(channel, discord.TextChannel):
                ready += 1
                destination = f"#{channel.name}"
            else:
                destination = "Not configured"
            lines.append(
                f"**{discord.utils.escape_markdown(guild.name)}** · "
                f"{guild.member_count or 0:,} members · {destination}"
            )
        embed = discord.Embed(
            title="Network operations",
            description="\n".join(lines)
            if lines
            else "Rallybit is not in any other servers you own.",
            color=BRAND,
            timestamp=_now(),
        )
        embed.add_field(name="Owned servers", value=f"{len(owned):,}", inline=True)
        embed.add_field(name="Broadcast ready", value=f"{ready:,}", inline=True)
        embed.set_footer(text="Only servers you own are included")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @network.command(
        name="broadcast",
        description="Send one announcement to every configured server you own.",
    )
    @app_commands.guild_only()
    @_network_owner_check()
    async def network_broadcast(
        interaction: discord.Interaction,
        title: app_commands.Range[str, 1, 100],
        message: app_commands.Range[str, 1, 2000],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        settings = _network_settings()
        sent: list[str] = []
        failed: list[str] = []
        embed = discord.Embed(
            title=str(title),
            description=str(message),
            color=BRAND,
            timestamp=_now(),
        )
        embed.set_footer(text=f"Network announcement · Sent by {interaction.user}")
        for guild in _owned_guilds(interaction):
            record = settings.get(str(guild.id), {})
            channel_id = record.get("channel_id") if isinstance(record, dict) else None
            channel = (
                guild.get_channel(int(channel_id))
                if str(channel_id or "").isdigit()
                else None
            )
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                await channel.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                sent.append(guild.name)
            except discord.HTTPException:
                failed.append(guild.name)
        result = discord.Embed(
            title="Network broadcast complete",
            color=SUCCESS if sent else WARNING,
            timestamp=_now(),
        )
        result.add_field(name="Delivered", value=f"{len(sent):,} servers", inline=True)
        result.add_field(name="Failed", value=f"{len(failed):,} servers", inline=True)
        if failed:
            result.add_field(
                name="Could not deliver",
                value=", ".join(
                    discord.utils.escape_markdown(name) for name in failed[:15]
                ),
                inline=False,
            )
        if not sent:
            result.description = (
                "No configured owned server accepted the announcement. "
                "Run `/network channel` in each destination server first."
            )
        await interaction.followup.send(embed=result, ephemeral=True)

    @network.command(
        name="export",
        description="Export owned-server size and announcement readiness as CSV.",
    )
    @app_commands.guild_only()
    @_network_owner_check()
    async def network_export(interaction: discord.Interaction) -> None:
        settings = _network_settings()
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(
            [
                "Server",
                "Server ID",
                "Members",
                "Announcement channel ID",
                "Ready",
            ]
        )
        owned = _owned_guilds(interaction)
        for guild in owned:
            record = settings.get(str(guild.id), {})
            channel_id = record.get("channel_id") if isinstance(record, dict) else ""
            channel = (
                guild.get_channel(int(channel_id))
                if str(channel_id or "").isdigit()
                else None
            )
            writer.writerow(
                [
                    guild.name,
                    guild.id,
                    guild.member_count or 0,
                    channel_id or "",
                    isinstance(channel, discord.TextChannel),
                ]
            )
        await interaction.response.send_message(
            content=f"Exported **{len(owned):,}** owned servers.",
            file=discord.File(
                io.BytesIO(stream.getvalue().encode("utf-8-sig")),
                filename=f"rallybit-network-{interaction.user.id}-{_now():%Y%m%d}.csv",
            ),
            ephemeral=True,
        )

    tree.add_command(cases)
    tree.add_command(backups)
    tree.add_command(network)
