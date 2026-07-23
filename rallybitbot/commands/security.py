from __future__ import annotations

import asyncio
import copy
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import discord
from discord import app_commands

from config.config import (
    SECURITY_HISTORY_FILE,
    SECURITY_LOCKDOWN_FILE,
    SECURITY_QUARANTINE_FILE,
    SECURITY_SETTINGS_FILE,
)
from core.checks import bot_can_run
from core.logging import log_server_event
from storage.json_store import load_json, save_json

BRAND = 0x5865F2
SUCCESS = 0x57F287
WARNING = 0xFEE75C
DANGER = 0xED4245
QUARANTINE_ROLE_NAME = "Rallybit Quarantined"
TRAP_DEFAULT_NAME = "do-not-text-here"
HISTORY_LIMIT = 250
INVITE_RE = re.compile(r"(?:discord(?:app)?\.com/invite/|discord\.gg/)[A-Za-z0-9-]+", re.I)
DANGEROUS_PERMISSION_NAMES = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "ban_members",
    "kick_members",
    "manage_webhooks",
)

ACTION_MODULES = {
    "mass_ban",
    "mass_kick",
    "mass_channel_delete",
    "mass_role_delete",
    "anti_emoji_delete",
    "anti_sticker_delete",
}
PUNISHMENT_MODULES = {
    "anti_bot",
    "anti_webhook",
    "anti_guild_update",
    "anti_integration",
    "anti_dangerous_permissions",
    *ACTION_MODULES,
}
ALL_MODULES = (
    "anti_bot",
    "anti_webhook",
    "anti_guild_update",
    "anti_integration",
    "anti_dangerous_permissions",
    "mass_ban",
    "mass_kick",
    "mass_channel_delete",
    "mass_role_delete",
    "anti_emoji_delete",
    "anti_sticker_delete",
    "anti_spam",
    "automod",
    "age_gate",
    "scam_trap",
)

MODULE_CHOICES = [
    app_commands.Choice(name="Unauthorized bot additions", value="anti_bot"),
    app_commands.Choice(name="Webhook protection", value="anti_webhook"),
    app_commands.Choice(name="Server update protection", value="anti_guild_update"),
    app_commands.Choice(name="Integration protection", value="anti_integration"),
    app_commands.Choice(name="Dangerous permission grants", value="anti_dangerous_permissions"),
    app_commands.Choice(name="Mass bans", value="mass_ban"),
    app_commands.Choice(name="Mass kicks", value="mass_kick"),
    app_commands.Choice(name="Mass channel deletion", value="mass_channel_delete"),
    app_commands.Choice(name="Mass role deletion", value="mass_role_delete"),
    app_commands.Choice(name="Mass emoji deletion", value="anti_emoji_delete"),
    app_commands.Choice(name="Mass sticker deletion", value="anti_sticker_delete"),
    app_commands.Choice(name="Anti-spam", value="anti_spam"),
    app_commands.Choice(name="Automod", value="automod"),
    app_commands.Choice(name="Account age gate", value="age_gate"),
    app_commands.Choice(name="Scam trap", value="scam_trap"),
]
THRESHOLD_CHOICES = [choice for choice in MODULE_CHOICES if choice.value in ACTION_MODULES]
PUNISHMENT_CHOICES = [
    app_commands.Choice(name="Ban", value="ban"),
    app_commands.Choice(name="Kick", value="kick"),
    app_commands.Choice(name="Quarantine / strip roles", value="strip"),
]


def _module_defaults() -> dict[str, Any]:
    return {
        "anti_bot": {"enabled": False, "punishment": "kick", "ban_added_bot": True},
        "anti_webhook": {"enabled": False, "punishment": "strip", "delete_created_webhook": True},
        "anti_guild_update": {"enabled": False, "punishment": "strip", "revert": True},
        "anti_integration": {"enabled": False, "punishment": "strip"},
        "anti_dangerous_permissions": {"enabled": False, "punishment": "strip", "revert": True},
        "mass_ban": {"enabled": False, "punishment": "strip", "count": 5, "seconds": 60},
        "mass_kick": {"enabled": False, "punishment": "strip", "count": 5, "seconds": 60},
        "mass_channel_delete": {"enabled": False, "punishment": "strip", "count": 3, "seconds": 60},
        "mass_role_delete": {"enabled": False, "punishment": "strip", "count": 3, "seconds": 60},
        "anti_emoji_delete": {"enabled": False, "punishment": "strip", "count": 5, "seconds": 60},
        "anti_sticker_delete": {"enabled": False, "punishment": "strip", "count": 5, "seconds": 60},
        "anti_spam": {"enabled": False, "message_limit": 6, "window_seconds": 5, "timeout_minutes": 5},
        "automod": {"enabled": False, "block_invites": False, "bad_words": []},
        "age_gate": {
            "enabled": False,
            "min_days": 7,
            "dm_member": True,
            "appeal_url": "",
            "dm_message": "",
        },
        "scam_trap": {
            "enabled": False,
            "channel_id": None,
            "channel_name": TRAP_DEFAULT_NAME,
            "action": "ban",
            "appeal_url": "",
            "warning_message": "",
            "delete_message_seconds": 604800,
            "bypass_trusted_roles": False,
        },
    }


def _default_settings() -> dict[str, Any]:
    return {
        "enabled": True,
        "log_channel_id": None,
        "trusted_role_ids": [],
        "bypass_administrators": False,
        "dm_server_owner": False,
        "event_logging": {
            "member_events": False,
            "message_events": False,
            "voice_events": False,
            "structure_events": False,
        },
        "modules": _module_defaults(),
    }


def _deep_merge(default: Any, saved: Any) -> Any:
    if isinstance(default, dict):
        result = copy.deepcopy(default)
        if isinstance(saved, dict):
            for key, value in saved.items():
                result[key] = _deep_merge(result[key], value) if key in result else copy.deepcopy(value)
        return result
    return copy.deepcopy(saved if saved is not None else default)


def _all_settings() -> dict[str, Any]:
    data = load_json(SECURITY_SETTINGS_FILE, {})
    return data if isinstance(data, dict) else {}


def get_security_settings(guild_id: int) -> dict[str, Any]:
    data = _all_settings()
    settings = _deep_merge(_default_settings(), data.get(str(guild_id), {}))
    if settings != data.get(str(guild_id)):
        data[str(guild_id)] = settings
        save_json(SECURITY_SETTINGS_FILE, data)
    return settings


def save_security_settings(guild_id: int, settings: dict[str, Any]) -> None:
    data = _all_settings()
    data[str(guild_id)] = settings
    save_json(SECURITY_SETTINGS_FILE, data)


def _history_data() -> dict[str, list[dict[str, Any]]]:
    data = load_json(SECURITY_HISTORY_FILE, {})
    return data if isinstance(data, dict) else {}


def _history_for(guild_id: int) -> list[dict[str, Any]]:
    records = _history_data().get(str(guild_id), [])
    return records if isinstance(records, list) else []


def _append_history(guild_id: int, record: dict[str, Any]) -> None:
    data = _history_data()
    records = data.setdefault(str(guild_id), [])
    if not isinstance(records, list):
        records = []
    records.insert(0, record)
    data[str(guild_id)] = records[:HISTORY_LIMIT]
    save_json(SECURITY_HISTORY_FILE, data)


def _display_name(user: discord.abc.User | None) -> str:
    if user is None:
        return "Unknown"
    return f"{user} ({user.id})"


def _module_label(module: str) -> str:
    labels = {
        "scam_trap": "Scam Detection Trap",
        "age_gate": "Account Age Gate",
        "anti_bot": "Unauthorized Bot Protection",
        "anti_webhook": "Webhook Protection",
        "anti_guild_update": "Server Update Protection",
        "anti_integration": "Integration Protection",
        "anti_dangerous_permissions": "Dangerous Permission Protection",
        "anti_spam": "Anti-Spam",
        "automod": "Automod",
        "mass_ban": "Mass Ban Protection",
        "mass_kick": "Mass Kick Protection",
        "mass_channel_delete": "Mass Channel Deletion Protection",
        "mass_role_delete": "Mass Role Deletion Protection",
        "anti_emoji_delete": "Mass Emoji Deletion Protection",
        "anti_sticker_delete": "Mass Sticker Deletion Protection",
    }
    return labels.get(module, module.replace("_", " ").title())


async def _send_security_log(
    guild: discord.Guild,
    *,
    module: str,
    action: str,
    target: discord.abc.User | None = None,
    actor: discord.abc.User | None = None,
    reason: str = "",
    colour: int = DANGER,
    extra: str = "",
) -> None:
    now = datetime.now(timezone.utc)
    record = {
        "timestamp": now.isoformat(),
        "module": module,
        "action": action,
        "target_id": getattr(target, "id", None),
        "target": _display_name(target),
        "actor_id": getattr(actor, "id", None),
        "actor": _display_name(actor),
        "reason": reason,
        "extra": extra,
    }
    _append_history(guild.id, record)
    log_server_event(
        guild.id,
        f"Security [{module}] {action} | target={record['target']} | actor={record['actor']} | {reason}",
        actor if isinstance(actor, discord.Member) else None,
        guild.shard_id,
    )

    settings = get_security_settings(guild.id)
    if settings.get("dm_server_owner", False) and module != "event_log":
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            owner_embed = discord.Embed(
                title=f"🛡️ {_module_label(module)}",
                description=f"**Action:** {action}\n**Reason:** {reason or 'No reason supplied.'}",
                colour=colour,
                timestamp=now,
            )
            if target is not None:
                owner_embed.add_field(name="Target", value=_display_name(target), inline=False)
            if actor is not None:
                owner_embed.add_field(name="Responsible account", value=_display_name(actor), inline=False)
            if extra:
                owner_embed.add_field(name="Details", value=extra[:1024], inline=False)
            await owner.send(embed=owner_embed)
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            pass
    channel_id = settings.get("log_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return
    me = guild.me
    if me is None or not channel.permissions_for(me).send_messages:
        return

    embed = discord.Embed(
        title=f"🛡️ {_module_label(module)}",
        description=f"**Action:** {action}\n**Reason:** {reason or 'No reason supplied.'}",
        colour=colour,
        timestamp=now,
    )
    if target is not None:
        embed.add_field(name="Target", value=f"{target.mention}\n`{target.id}`", inline=True)
    if actor is not None:
        embed.add_field(name="Responsible account", value=f"{actor.mention}\n`{actor.id}`", inline=True)
    if extra:
        embed.add_field(name="Details", value=extra[:1024], inline=False)
    embed.set_footer(text=f"Server ID {guild.id}")
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


def _member_for(guild: discord.Guild, user: discord.abc.User | None) -> discord.Member | None:
    if isinstance(user, discord.Member):
        return user
    return guild.get_member(user.id) if user is not None else None


def _is_trusted_actor(
    guild: discord.Guild,
    user: discord.abc.User | None,
    settings: dict[str, Any] | None = None,
    *,
    trap: bool = False,
) -> bool:
    if user is None:
        return False
    if user.id in {guild.owner_id, getattr(guild.me, "id", 0)}:
        return True
    settings = settings or get_security_settings(guild.id)
    member = _member_for(guild, user)
    if member is None:
        return False
    if not trap and settings.get("bypass_administrators") and member.guild_permissions.administrator:
        return True
    if trap and not settings.get("modules", {}).get("scam_trap", {}).get("bypass_trusted_roles", False):
        return False
    trusted = {int(role_id) for role_id in settings.get("trusted_role_ids", []) if str(role_id).isdigit()}
    return any(role.id in trusted for role in member.roles)


def _hierarchy_error(guild: discord.Guild, target: discord.Member) -> str | None:
    me = guild.me
    if me is None:
        return "Rallybit could not resolve its server member record."
    if target.id == guild.owner_id:
        return "The server owner cannot be moderated."
    if target.top_role >= me.top_role:
        return "Move Rallybit's role above the target member's highest role."
    return None


async def _ensure_quarantine_role(guild: discord.Guild) -> discord.Role:
    role = discord.utils.get(guild.roles, name=QUARANTINE_ROLE_NAME)
    if role is None:
        role = await guild.create_role(
            name=QUARANTINE_ROLE_NAME,
            colour=discord.Colour.dark_grey(),
            reason="Rallybit security quarantine",
        )
    for channel in guild.channels:
        try:
            overwrite = channel.overwrites_for(role)
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                overwrite.send_messages = False
                overwrite.add_reactions = False
                overwrite.create_public_threads = False
                overwrite.create_private_threads = False
                overwrite.send_messages_in_threads = False
            elif isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                overwrite.connect = False
                overwrite.speak = False
            await channel.set_permissions(role, overwrite=overwrite, reason="Rallybit quarantine permissions")
        except (discord.Forbidden, discord.HTTPException):
            continue
    return role


async def _quarantine_member(
    guild: discord.Guild,
    member: discord.Member,
    actor: discord.abc.User | None,
    reason: str,
) -> tuple[bool, str]:
    error = _hierarchy_error(guild, member)
    if error:
        return False, error
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return False, "Rallybit needs Manage Roles to quarantine members."
    try:
        role = await _ensure_quarantine_role(guild)
        removable = [r for r in member.roles if not r.is_default() and not r.managed and r < me.top_role and r.id != role.id]
        records = load_json(SECURITY_QUARANTINE_FILE, {})
        guild_records = records.setdefault(str(guild.id), {})
        guild_records[str(member.id)] = {
            "roles": [r.id for r in removable],
            "actor_id": getattr(actor, "id", None),
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        save_json(SECURITY_QUARANTINE_FILE, records)
        if removable:
            await member.remove_roles(*removable, reason=reason)
        await member.add_roles(role, reason=reason)
        return True, f"Quarantined and removed {len(removable)} manageable role(s)."
    except (discord.Forbidden, discord.HTTPException) as exc:
        return False, f"Discord rejected the quarantine: {exc}"


async def _release_member(guild: discord.Guild, member: discord.Member, reason: str) -> tuple[bool, str]:
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return False, "Rallybit needs Manage Roles to release quarantined members."
    role = discord.utils.get(guild.roles, name=QUARANTINE_ROLE_NAME)
    records = load_json(SECURITY_QUARANTINE_FILE, {})
    guild_records = records.get(str(guild.id), {}) if isinstance(records, dict) else {}
    record = guild_records.get(str(member.id), {}) if isinstance(guild_records, dict) else {}
    role_ids = record.get("roles", []) if isinstance(record, dict) else []
    restore = [guild.get_role(int(role_id)) for role_id in role_ids if str(role_id).isdigit()]
    restore = [r for r in restore if r is not None and not r.managed and r < me.top_role]
    try:
        if role and role in member.roles:
            await member.remove_roles(role, reason=reason)
        if restore:
            await member.add_roles(*restore, reason=reason)
        if isinstance(guild_records, dict):
            guild_records.pop(str(member.id), None)
            records[str(guild.id)] = guild_records
            save_json(SECURITY_QUARANTINE_FILE, records)
        return True, f"Released and restored {len(restore)} saved role(s)."
    except (discord.Forbidden, discord.HTTPException) as exc:
        return False, f"Discord rejected the release: {exc}"


async def _apply_punishment(
    guild: discord.Guild,
    target: discord.abc.User,
    punishment: str,
    reason: str,
    module: str,
    actor: discord.abc.User | None = None,
) -> tuple[bool, str]:
    if target.id in {guild.owner_id, getattr(guild.me, "id", 0)}:
        await _send_security_log(
            guild,
            module=module,
            action="Protection triggered but target is protected",
            target=target,
            actor=actor,
            reason=reason,
            colour=WARNING,
        )
        return False, "The target is the server owner or Rallybit."
    member = _member_for(guild, target)
    me = guild.me
    try:
        if punishment == "ban":
            if me is None or not me.guild_permissions.ban_members:
                return False, "Rallybit needs Ban Members."
            if member and (error := _hierarchy_error(guild, member)):
                return False, error
            await guild.ban(target, reason=reason, delete_message_seconds=0)
            action = "Banned"
        elif punishment == "kick":
            if me is None or not me.guild_permissions.kick_members:
                return False, "Rallybit needs Kick Members."
            if member is None:
                return False, "The account is no longer a server member."
            if error := _hierarchy_error(guild, member):
                return False, error
            await member.kick(reason=reason)
            action = "Kicked"
        else:
            if member is None:
                return False, "The account is no longer a server member."
            success, detail = await _quarantine_member(guild, member, actor, reason)
            if not success:
                return False, detail
            action = "Quarantined"
        await _send_security_log(
            guild,
            module=module,
            action=action,
            target=target,
            actor=actor,
            reason=reason,
            colour=DANGER,
        )
        return True, action
    except (discord.Forbidden, discord.HTTPException) as exc:
        await _send_security_log(
            guild,
            module=module,
            action="Punishment failed",
            target=target,
            actor=actor,
            reason=reason,
            colour=WARNING,
            extra=str(exc),
        )
        return False, f"Discord rejected the action: {exc}"


def _trap_warning(settings: dict[str, Any]) -> str:
    trap = settings["modules"]["scam_trap"]
    custom = str(trap.get("warning_message") or "").strip()
    if custom:
        return custom[:1900]
    action = str(trap.get("action", "ban")).lower()
    action_text = "banned" if action == "ban" else "removed"
    appeal = str(trap.get("appeal_url") or "").strip()
    appeal_line = f"\n\n**Appeals:** {appeal}" if appeal else ""
    return (
        "# ⚠️ SECURITY TRAP — DO NOT TYPE HERE\n"
        "This channel is intentionally left open as a honeypot for compromised accounts and automated scam scripts.\n\n"
        "**Do not send messages, files, links or commands in this channel.**\n"
        f"Any account that posts here will be immediately **{action_text}** and the message will be deleted.\n\n"
        "Legitimate members should mute and ignore this channel. If your account is actioned by mistake, contact the server staff."
        f"{appeal_line}"
    )


async def _create_or_repair_trap(guild: discord.Guild, settings: dict[str, Any]) -> discord.TextChannel:
    trap = settings["modules"]["scam_trap"]
    channel_id = trap.get("channel_id")
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    if not isinstance(channel, discord.TextChannel):
        channel = discord.utils.get(guild.text_channels, name=str(trap.get("channel_name") or TRAP_DEFAULT_NAME))
    me = guild.me
    if me is None:
        raise RuntimeError("Rallybit could not resolve its server member record.")
    everyone_overwrite = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        add_reactions=False,
        create_public_threads=False,
        create_private_threads=False,
        send_messages_in_threads=False,
        use_application_commands=False,
    )
    bot_overwrite = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        manage_messages=True,
        manage_channels=True,
    )
    overwrites = {guild.default_role: everyone_overwrite, me: bot_overwrite}
    if channel is None:
        channel = await guild.create_text_channel(
            name=str(trap.get("channel_name") or TRAP_DEFAULT_NAME)[:100],
            overwrites=overwrites,
            reason="Rallybit scam-detection trap setup",
        )
    else:
        await channel.edit(
            name=str(trap.get("channel_name") or TRAP_DEFAULT_NAME)[:100],
            category=None,
            overwrites=overwrites,
            reason="Rallybit scam-detection trap repair",
        )
    try:
        await channel.edit(position=0, sync_permissions=False, reason="Keep Rallybit security trap above server categories")
    except (discord.Forbidden, discord.HTTPException):
        pass
    try:
        await channel.edit(
            topic="Security honeypot: any member message triggers the configured action. Do not chat here.",
            reason="Rallybit security trap topic",
        )
    except (discord.Forbidden, discord.HTTPException):
        pass

    try:
        for pinned in await channel.pins():
            if pinned.author.id == me.id and "SECURITY TRAP" in (pinned.content or ""):
                await pinned.delete(reason="Replace Rallybit trap warning")
    except (discord.Forbidden, discord.HTTPException):
        pass
    warning = await channel.send(_trap_warning(settings), allowed_mentions=discord.AllowedMentions.none())
    try:
        await warning.pin(reason="Rallybit security trap warning")
    except (discord.Forbidden, discord.HTTPException):
        pass
    trap["channel_id"] = channel.id
    trap["enabled"] = True
    save_security_settings(guild.id, settings)
    return channel


_action_trackers: dict[int, dict[str, dict[int, list[float]]]] = defaultdict(
    lambda: defaultdict(lambda: defaultdict(list))
)
_spam_trackers: dict[int, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))


async def _handle_threshold_action(entry: discord.AuditLogEntry, module: str) -> None:
    guild = entry.guild
    actor = entry.user
    if actor is None or actor.id == getattr(guild.me, "id", 0):
        return
    settings = get_security_settings(guild.id)
    config = settings["modules"].get(module, {})
    if not settings.get("enabled", True) or not config.get("enabled", False):
        return
    if _is_trusted_actor(guild, actor, settings):
        return
    now = time.monotonic()
    seconds = max(5, min(86400, int(config.get("seconds", 60))))
    count_limit = max(1, min(50, int(config.get("count", 3))))
    tracker = _action_trackers[guild.id][module][actor.id]
    tracker.append(now)
    tracker[:] = [stamp for stamp in tracker if now - stamp <= seconds]
    if len(tracker) < count_limit:
        return
    tracker.clear()
    reason = f"Rallybit detected {count_limit}+ {_module_label(module).lower()} actions within {seconds} seconds."
    await _apply_punishment(guild, actor, str(config.get("punishment", "strip")), reason, module, actor)


async def _find_recent_audit_actor(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    target_id: int | None = None,
    *,
    max_age_seconds: int = 20,
) -> discord.abc.User | None:
    """Resolve the actor only from a fresh matching audit entry.

    The freshness check prevents an unrelated older audit entry from being
    treated as the cause of a new gateway event when Discord delivers audit
    records with a short delay.
    """
    cutoff = discord.utils.utcnow() - timedelta(seconds=max(5, max_age_seconds))
    try:
        async for entry in guild.audit_logs(action=action, limit=8):
            if entry.created_at < cutoff:
                continue
            if target_id is None or getattr(entry.target, "id", None) == target_id:
                return entry.user
    except (discord.Forbidden, discord.HTTPException):
        return None
    return None


async def security_on_member_join(member: discord.Member) -> None:
    settings = get_security_settings(member.guild.id)
    if not settings.get("enabled", True):
        return
    modules = settings["modules"]
    if member.bot:
        anti_bot = modules["anti_bot"]
        if not anti_bot.get("enabled", False):
            return
        await asyncio.sleep(1.0)
        actor = await _find_recent_audit_actor(member.guild, discord.AuditLogAction.bot_add, member.id)
        if actor is None or _is_trusted_actor(member.guild, actor, settings):
            return
        if anti_bot.get("ban_added_bot", True):
            try:
                if member.guild.me and member.guild.me.guild_permissions.ban_members and member.top_role < member.guild.me.top_role:
                    await member.ban(reason="Rallybit: unauthorised bot addition", delete_message_seconds=0)
            except (discord.Forbidden, discord.HTTPException):
                pass
        await _apply_punishment(
            member.guild,
            actor,
            str(anti_bot.get("punishment", "kick")),
            f"Unauthorised bot added: {member} ({member.id})",
            "anti_bot",
            actor,
        )
        return

    age_gate = modules["age_gate"]
    if age_gate.get("enabled", False):
        min_days = max(1, min(3650, int(age_gate.get("min_days", 7))))
        account_days = max(0, (discord.utils.utcnow() - member.created_at).days)
        if account_days < min_days:
            if age_gate.get("dm_member", True):
                custom = str(age_gate.get("dm_message") or "").strip()
                appeal = str(age_gate.get("appeal_url") or "").strip()
                message = custom or (
                    f"Your Discord account is too new to join **{member.guild.name}**. "
                    f"This server requires accounts to be at least **{min_days} days old**; yours is **{account_days} days old**."
                )
                if appeal:
                    message += f"\n\nAppeal or contact staff: {appeal}"
                try:
                    await member.send(message)
                except (discord.Forbidden, discord.HTTPException):
                    pass
            try:
                await member.kick(reason=f"Rallybit account age gate: {account_days}d < {min_days}d")
                await _send_security_log(
                    member.guild,
                    module="age_gate",
                    action="Kicked",
                    target=member,
                    reason=f"Account age {account_days} days; minimum {min_days} days.",
                    colour=WARNING,
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                await _send_security_log(
                    member.guild,
                    module="age_gate",
                    action="Kick failed",
                    target=member,
                    reason=f"Account age {account_days} days; minimum {min_days} days.",
                    colour=DANGER,
                    extra=str(exc),
                )
            return

    if settings.get("event_logging", {}).get("member_events", False):
        await _send_security_log(
            member.guild,
            module="event_log",
            action="Member joined",
            target=member,
            reason=f"Account created {discord.utils.format_dt(member.created_at, style='R')}.",
            colour=SUCCESS,
        )


async def security_on_message(message: discord.Message) -> None:
    if message.guild is None or message.author.bot:
        return
    settings = get_security_settings(message.guild.id)
    if not settings.get("enabled", True):
        return
    modules = settings["modules"]
    trap = modules["scam_trap"]
    if trap.get("enabled", False) and trap.get("channel_id") and message.channel.id == int(trap["channel_id"]):
        member = message.author if isinstance(message.author, discord.Member) else message.guild.get_member(message.author.id)
        if member is None:
            return
        if member.id == message.guild.owner_id:
            try:
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass
            await _send_security_log(
                message.guild,
                module="scam_trap",
                action="Owner message removed; owner cannot be actioned",
                target=member,
                reason="A message was sent in the security trap channel.",
                colour=WARNING,
            )
            return
        if _is_trusted_actor(message.guild, member, settings, trap=True):
            try:
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass
            await _send_security_log(
                message.guild,
                module="scam_trap",
                action="Trusted account message removed",
                target=member,
                reason="A trusted account posted in the trap channel; configured bypass prevented punishment.",
                colour=WARNING,
            )
            return
        appeal = str(trap.get("appeal_url") or "").strip()
        action = str(trap.get("action", "ban"))
        try:
            dm = (
                f"Your account posted in the security trap channel in **{message.guild.name}** and was "
                f"{'banned' if action == 'ban' else 'removed'}. The channel warns members not to post because it is used to catch compromised accounts."
            )
            if appeal:
                dm += f"\n\nIf this was a mistake, appeal here: {appeal}"
            await member.send(dm)
        except (discord.Forbidden, discord.HTTPException):
            pass
        content_preview = (message.content or "*No accessible text content*")[:700]
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
        try:
            if action == "kick":
                await member.kick(reason="Rallybit security trap triggered")
                result = "Kicked"
            else:
                delete_seconds = max(0, min(604800, int(trap.get("delete_message_seconds", 604800))))
                await member.ban(reason="Rallybit security trap triggered", delete_message_seconds=delete_seconds)
                result = "Banned"
            await _send_security_log(
                message.guild,
                module="scam_trap",
                action=result,
                target=member,
                reason="Posted in the security trap channel.",
                colour=DANGER,
                extra=f"Channel: {message.channel.mention}\nMessage: {content_preview}",
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            await _send_security_log(
                message.guild,
                module="scam_trap",
                action="Punishment failed",
                target=member,
                reason="Posted in the security trap channel.",
                colour=DANGER,
                extra=str(exc),
            )
        return

    if _is_trusted_actor(message.guild, message.author, settings):
        return

    anti_spam = modules["anti_spam"]
    if anti_spam.get("enabled", False):
        now = time.monotonic()
        window = max(2, min(60, int(anti_spam.get("window_seconds", 5))))
        limit = max(3, min(30, int(anti_spam.get("message_limit", 6))))
        tracker = _spam_trackers[message.guild.id][message.author.id]
        tracker.append(now)
        tracker[:] = [stamp for stamp in tracker if now - stamp <= window]
        if len(tracker) >= limit:
            tracker.clear()
            minutes = max(1, min(40320, int(anti_spam.get("timeout_minutes", 5))))
            member = message.author if isinstance(message.author, discord.Member) else None
            if member:
                try:
                    await member.timeout(timedelta(minutes=minutes), reason="Rallybit anti-spam")
                    try:
                        await message.delete()
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    await _send_security_log(
                        message.guild,
                        module="anti_spam",
                        action="Timed out",
                        target=member,
                        reason=f"Sent {limit}+ messages in {window} seconds.",
                        colour=WARNING,
                        extra=f"Timeout: {minutes} minute(s)",
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    await _send_security_log(
                        message.guild,
                        module="anti_spam",
                        action="Timeout failed",
                        target=member,
                        reason=f"Sent {limit}+ messages in {window} seconds.",
                        colour=DANGER,
                        extra=str(exc),
                    )
            return

    automod = modules["automod"]
    if automod.get("enabled", False) and message.content:
        lowered = message.content.casefold()
        reason = ""
        if automod.get("block_invites", False) and INVITE_RE.search(message.content):
            reason = "Discord invite link"
        if not reason:
            for word in automod.get("bad_words", []):
                clean = str(word).strip().casefold()
                if clean and re.search(rf"(?<!\w){re.escape(clean)}(?!\w)", lowered):
                    reason = f"Blocked term: {clean}"
                    break
        if reason:
            try:
                await message.delete()
                await message.channel.send(
                    f"⚠️ {message.author.mention}, that message was blocked by Rallybit security.",
                    delete_after=6,
                    allowed_mentions=discord.AllowedMentions(users=[message.author], roles=False, everyone=False),
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            await _send_security_log(
                message.guild,
                module="automod",
                action="Message removed",
                target=message.author,
                reason=reason,
                colour=WARNING,
                extra=f"Channel: {message.channel.mention}",
            )


async def security_on_audit_log_entry_create(entry: discord.AuditLogEntry) -> None:
    guild = entry.guild
    settings = get_security_settings(guild.id)
    if not settings.get("enabled", True) or entry.user is None:
        return
    if entry.user.id == getattr(guild.me, "id", 0) or _is_trusted_actor(guild, entry.user, settings):
        return

    action_map = {
        discord.AuditLogAction.ban: "mass_ban",
        discord.AuditLogAction.kick: "mass_kick",
        discord.AuditLogAction.channel_delete: "mass_channel_delete",
        discord.AuditLogAction.role_delete: "mass_role_delete",
        discord.AuditLogAction.emoji_delete: "anti_emoji_delete",
        discord.AuditLogAction.sticker_delete: "anti_sticker_delete",
    }
    module = action_map.get(entry.action)
    if module:
        await _handle_threshold_action(entry, module)
        return

    if entry.action in {
        discord.AuditLogAction.webhook_create,
        discord.AuditLogAction.webhook_update,
        discord.AuditLogAction.webhook_delete,
    }:
        config = settings["modules"]["anti_webhook"]
        if not config.get("enabled", False):
            return
        if entry.action == discord.AuditLogAction.webhook_create and config.get("delete_created_webhook", True):
            target = entry.target
            if isinstance(target, discord.Webhook):
                try:
                    await target.delete(reason="Rallybit: unauthorised webhook")
                except (discord.Forbidden, discord.HTTPException):
                    pass
        await _apply_punishment(
            guild,
            entry.user,
            str(config.get("punishment", "strip")),
            f"Unauthorised webhook action: {entry.action.name}",
            "anti_webhook",
            entry.user,
        )
        return

    if entry.action == discord.AuditLogAction.integration_create:
        config = settings["modules"]["anti_integration"]
        if config.get("enabled", False):
            await _apply_punishment(
                guild,
                entry.user,
                str(config.get("punishment", "strip")),
                "Unauthorised integration created.",
                "anti_integration",
                entry.user,
            )


async def security_on_guild_update(before: discord.Guild, after: discord.Guild) -> None:
    settings = get_security_settings(after.id)
    config = settings["modules"]["anti_guild_update"]
    if not settings.get("enabled", True) or not config.get("enabled", False):
        return
    await asyncio.sleep(0.8)
    actor = await _find_recent_audit_actor(after, discord.AuditLogAction.guild_update, after.id)
    if actor is None or _is_trusted_actor(after, actor, settings):
        return
    changes: list[str] = []
    if before.name != after.name:
        changes.append(f"Name: {before.name!r} → {after.name!r}")
    if before.icon != after.icon:
        changes.append("Server icon changed")
    if before.vanity_url_code != after.vanity_url_code:
        changes.append("Vanity URL changed")
    if config.get("revert", True):
        kwargs: dict[str, Any] = {}
        if before.name != after.name:
            kwargs["name"] = before.name
        if before.icon != after.icon:
            try:
                kwargs["icon"] = await before.icon.read() if before.icon else None
            except (discord.HTTPException, OSError):
                pass
        if kwargs:
            try:
                await after.edit(reason="Rallybit: revert unauthorised server update", **kwargs)
            except (discord.Forbidden, discord.HTTPException):
                pass
    await _apply_punishment(
        after,
        actor,
        str(config.get("punishment", "strip")),
        "Unauthorised server settings update. " + "; ".join(changes),
        "anti_guild_update",
        actor,
    )


async def security_on_member_update(before: discord.Member, after: discord.Member) -> None:
    settings = get_security_settings(after.guild.id)
    if settings.get("event_logging", {}).get("member_events", False):
        changes: list[str] = []
        if before.nick != after.nick:
            changes.append(f"Nickname: {before.nick or before.name!r} → {after.nick or after.name!r}")
        added_log = [role.name for role in after.roles if role not in before.roles]
        removed_log = [role.name for role in before.roles if role not in after.roles]
        if added_log:
            changes.append("Roles added: " + ", ".join(added_log))
        if removed_log:
            changes.append("Roles removed: " + ", ".join(removed_log))
        if changes:
            await _send_security_log(
                after.guild, module="event_log", action="Member updated", target=after,
                reason="; ".join(changes), colour=BRAND,
            )
    config = settings["modules"]["anti_dangerous_permissions"]
    if not settings.get("enabled", True) or not config.get("enabled", False) or before.roles == after.roles:
        return
    added = [role for role in after.roles if role not in before.roles]
    dangerous = [role for role in added if any(getattr(role.permissions, name, False) for name in DANGEROUS_PERMISSION_NAMES)]
    if not dangerous:
        return
    await asyncio.sleep(0.8)
    actor = await _find_recent_audit_actor(after.guild, discord.AuditLogAction.member_role_update, after.id)
    if actor is None or _is_trusted_actor(after.guild, actor, settings):
        return
    if config.get("revert", True):
        removable = [role for role in dangerous if after.guild.me and role < after.guild.me.top_role]
        if removable:
            try:
                await after.remove_roles(*removable, reason="Rallybit: unauthorised dangerous role grant")
            except (discord.Forbidden, discord.HTTPException):
                pass
    role_names = ", ".join(role.name for role in dangerous)
    await _apply_punishment(
        after.guild,
        actor,
        str(config.get("punishment", "strip")),
        f"Attempted to grant dangerous role(s) to {after}: {role_names}",
        "anti_dangerous_permissions",
        actor,
    )


async def security_on_guild_role_update(before: discord.Role, after: discord.Role) -> None:
    settings = get_security_settings(after.guild.id)
    if settings.get("event_logging", {}).get("structure_events", False):
        changes: list[str] = []
        if before.name != after.name:
            changes.append(f"Name: {before.name!r} → {after.name!r}")
        if before.permissions != after.permissions:
            changes.append("Permissions changed")
        if before.colour != after.colour:
            changes.append(f"Colour: {before.colour} → {after.colour}")
        if changes:
            await _send_security_log(after.guild, module="event_log", action="Role updated", reason=f"@{after.name}: " + "; ".join(changes), colour=BRAND)
    config = settings["modules"]["anti_dangerous_permissions"]
    if not settings.get("enabled", True) or not config.get("enabled", False):
        return
    newly_dangerous = [
        name for name in DANGEROUS_PERMISSION_NAMES
        if getattr(after.permissions, name, False) and not getattr(before.permissions, name, False)
    ]
    if not newly_dangerous:
        return
    await asyncio.sleep(0.8)
    actor = await _find_recent_audit_actor(after.guild, discord.AuditLogAction.role_update, after.id)
    if actor is None or _is_trusted_actor(after.guild, actor, settings):
        return
    if config.get("revert", True):
        try:
            await after.edit(permissions=before.permissions, reason="Rallybit: revert dangerous role permission grant")
        except (discord.Forbidden, discord.HTTPException):
            pass
    await _apply_punishment(
        after.guild,
        actor,
        str(config.get("punishment", "strip")),
        f"Added dangerous permissions to role {after.name}: {', '.join(newly_dangerous)}",
        "anti_dangerous_permissions",
        actor,
    )


async def security_on_message_delete(message: discord.Message) -> None:
    if message.guild is None or message.author.bot:
        return
    settings = get_security_settings(message.guild.id)
    if not settings.get("event_logging", {}).get("message_events", False):
        return
    await _send_security_log(
        message.guild,
        module="event_log",
        action="Message deleted",
        target=message.author,
        reason=f"Message deleted in #{getattr(message.channel, 'name', 'unknown')}",
        colour=DANGER,
        extra=(message.content or "*No accessible text content*")[:1000],
    )


async def security_on_message_edit(before: discord.Message, after: discord.Message) -> None:
    if before.guild is None or before.author.bot or before.content == after.content:
        return
    settings = get_security_settings(before.guild.id)
    if not settings.get("event_logging", {}).get("message_events", False):
        return
    await _send_security_log(
        before.guild,
        module="event_log",
        action="Message edited",
        target=before.author,
        reason=f"Message edited in #{getattr(before.channel, 'name', 'unknown')}",
        colour=WARNING,
        extra=f"Before: {(before.content or '*empty*')[:450]}\nAfter: {(after.content or '*empty*')[:450]}",
    )


async def security_on_member_remove(member: discord.Member) -> None:
    settings = get_security_settings(member.guild.id)
    if settings.get("event_logging", {}).get("member_events", False):
        await _send_security_log(
            member.guild,
            module="event_log",
            action="Member left",
            target=member,
            reason="Member left or was removed from the server.",
            colour=WARNING,
        )


async def security_on_member_ban(guild: discord.Guild, user: discord.User | discord.Member) -> None:
    settings = get_security_settings(guild.id)
    if settings.get("event_logging", {}).get("member_events", False):
        await _send_security_log(guild, module="event_log", action="Member banned", target=user, reason="Discord ban event.", colour=DANGER)


async def security_on_member_unban(guild: discord.Guild, user: discord.User) -> None:
    settings = get_security_settings(guild.id)
    if settings.get("event_logging", {}).get("member_events", False):
        await _send_security_log(guild, module="event_log", action="Member unbanned", target=user, reason="Discord unban event.", colour=SUCCESS)


async def security_on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
    settings = get_security_settings(member.guild.id)
    if not settings.get("event_logging", {}).get("voice_events", False) or before.channel == after.channel:
        return
    if before.channel is None:
        action, reason = "Voice joined", f"Joined {after.channel.name if after.channel else 'unknown'}"
    elif after.channel is None:
        action, reason = "Voice left", f"Left {before.channel.name}"
    else:
        action, reason = "Voice moved", f"{before.channel.name} → {after.channel.name}"
    await _send_security_log(member.guild, module="event_log", action=action, target=member, reason=reason, colour=BRAND)


async def security_on_guild_channel_create(channel: discord.abc.GuildChannel) -> None:
    settings = get_security_settings(channel.guild.id)
    if settings.get("event_logging", {}).get("structure_events", False):
        await _send_security_log(channel.guild, module="event_log", action="Channel created", reason=f"#{channel.name} ({channel.id})", colour=SUCCESS)
    records = load_json(SECURITY_QUARANTINE_FILE, {})
    if records.get(str(channel.guild.id)):
        role = discord.utils.get(channel.guild.roles, name=QUARANTINE_ROLE_NAME)
        if role:
            try:
                overwrite = channel.overwrites_for(role)
                if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                    overwrite.send_messages = False
                    overwrite.add_reactions = False
                    overwrite.send_messages_in_threads = False
                elif isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                    overwrite.connect = False
                    overwrite.speak = False
                await channel.set_permissions(role, overwrite=overwrite, reason="Rallybit quarantine permissions")
            except (discord.Forbidden, discord.HTTPException):
                pass


async def security_on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
    settings = get_security_settings(channel.guild.id)
    if settings.get("event_logging", {}).get("structure_events", False):
        await _send_security_log(channel.guild, module="event_log", action="Channel deleted", reason=f"#{channel.name} ({channel.id})", colour=DANGER)
    trap = settings["modules"]["scam_trap"]
    if trap.get("channel_id") == channel.id:
        trap["channel_id"] = None
        trap["enabled"] = False
        save_security_settings(channel.guild.id, settings)


async def security_on_guild_role_create(role: discord.Role) -> None:
    settings = get_security_settings(role.guild.id)
    if settings.get("event_logging", {}).get("structure_events", False):
        await _send_security_log(role.guild, module="event_log", action="Role created", reason=f"@{role.name} ({role.id})", colour=SUCCESS)


async def security_on_guild_role_delete(role: discord.Role) -> None:
    settings = get_security_settings(role.guild.id)
    if settings.get("event_logging", {}).get("structure_events", False):
        await _send_security_log(role.guild, module="event_log", action="Role deleted", reason=f"@{role.name} ({role.id})", colour=DANGER)
    if role.id in settings.get("trusted_role_ids", []):
        settings["trusted_role_ids"] = [role_id for role_id in settings["trusted_role_ids"] if role_id != role.id]
        save_security_settings(role.guild.id, settings)


def _status(value: bool) -> str:
    return "Enabled" if value else "Disabled"


async def _command_guard(interaction: discord.Interaction) -> bool:
    can_run, reason, _ = bot_can_run(interaction)
    if can_run:
        return True
    await interaction.response.send_message(reason, ephemeral=True)
    return False


def setup_security_commands(tree: app_commands.CommandTree) -> None:
    client = tree.client
    # Discord applies a total payload-size limit to each top-level slash command.
    # Keeping the whole security suite below one /security tree can exceed that
    # limit and make Discord reject the command during sync. Split the suite into
    # smaller public command groups while continuing to use one per-guild settings
    # store underneath.
    security = app_commands.Group(name="security", description="Core Rallybit security controls.")
    trap_group = app_commands.Group(name="security-trap", description="Configure the scam-detection trap.")
    age_group = app_commands.Group(name="security-agegate", description="Configure the account-age gate.")
    module_group = app_commands.Group(name="security-modules", description="Configure automatic protection modules.")
    spam_group = app_commands.Group(name="security-antispam", description="Configure rapid-message protection.")
    automod_group = app_commands.Group(name="security-automod", description="Configure invite and term filtering.")
    trust_group = app_commands.Group(name="security-trust", description="Configure trusted security roles.")
    logs_group = app_commands.Group(name="security-logs", description="Configure security logging.")
    antibot_group = app_commands.Group(name="security-antibot", description="Configure bot-add protection.")

    @security.command(name="system", description="Enable or disable all automatic Rallybit security listeners for this server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def system_toggle(interaction: discord.Interaction, enabled: bool) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        settings["enabled"] = enabled
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ Rallybit security system {_status(enabled).lower()}. Individual module settings were preserved.", ephemeral=True)

    @security.command(name="overview", description="View the server's complete security configuration.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def overview(interaction: discord.Interaction) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        modules = settings["modules"]
        enabled = [name for name in ALL_MODULES if modules.get(name, {}).get("enabled", False)]
        embed = discord.Embed(
            title="🛡️ Rallybit Security",
            description=(
                f"**System:** {_status(settings.get('enabled', True))}\n"
                f"**Enabled modules:** {len(enabled)}/{len(ALL_MODULES)}\n"
                f"**Admin bypass:** {_status(settings.get('bypass_administrators', False))}\n"
                f"**Trusted roles:** {len(settings.get('trusted_role_ids', []))}"
            ),
            colour=BRAND,
        )
        for name in ALL_MODULES:
            config = modules.get(name, {})
            details = f"{'✅' if config.get('enabled', False) else '❌'} {_status(config.get('enabled', False))}"
            if name in PUNISHMENT_MODULES:
                details += f"\nAction: `{config.get('punishment', 'strip')}`"
            if name in ACTION_MODULES:
                details += f"\nLimit: `{config.get('count', 3)}` / `{config.get('seconds', 60)}s`"
            if name == "age_gate":
                details += f"\nMinimum: `{config.get('min_days', 7)} days`"
            if name == "scam_trap":
                channel = interaction.guild.get_channel(int(config.get('channel_id'))) if config.get('channel_id') else None
                details += f"\nChannel: {channel.mention if channel else '`Not configured`'}\nAction: `{config.get('action', 'ban')}`"
            embed.add_field(name=_module_label(name), value=details, inline=True)
        log_channel = interaction.guild.get_channel(int(settings["log_channel_id"])) if settings.get("log_channel_id") else None
        embed.set_footer(text=f"Security log: #{log_channel.name}" if log_channel else "Security log channel is not configured")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @security.command(name="audit", description="Scan permissions and security configuration for weaknesses.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def audit(interaction: discord.Interaction) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        guild = interaction.guild
        settings = get_security_settings(guild.id)
        issues: list[str] = []
        me = guild.me
        required = {
            "View Audit Log": bool(me and me.guild_permissions.view_audit_log),
            "Manage Channels": bool(me and me.guild_permissions.manage_channels),
            "Manage Messages": bool(me and me.guild_permissions.manage_messages),
            "Kick Members": bool(me and me.guild_permissions.kick_members),
            "Ban Members": bool(me and me.guild_permissions.ban_members),
            "Moderate Members": bool(me and me.guild_permissions.moderate_members),
            "Manage Roles": bool(me and me.guild_permissions.manage_roles),
            "Manage Webhooks": bool(me and me.guild_permissions.manage_webhooks),
        }
        for label, present in required.items():
            if not present:
                issues.append(f"⚠️ Rallybit lacks **{label}**; related modules cannot act.")
        everyone = guild.default_role.permissions
        for attr, label in (
            ("administrator", "Administrator"),
            ("manage_guild", "Manage Server"),
            ("manage_roles", "Manage Roles"),
            ("manage_channels", "Manage Channels"),
            ("ban_members", "Ban Members"),
            ("kick_members", "Kick Members"),
        ):
            if getattr(everyone, attr, False):
                issues.append(f"🚩 `@everyone` has **{label}**.")
        admin_roles = [role.mention for role in guild.roles if role.permissions.administrator and not role.is_default()]
        if len(admin_roles) > 5:
            issues.append(f"⚠️ This server has **{len(admin_roles)}** Administrator roles.")
        trap = settings["modules"]["scam_trap"]
        if trap.get("enabled", False) and not guild.get_channel(int(trap.get("channel_id") or 0)):
            issues.append("⚠️ The scam trap is enabled but its channel is missing.")
        if not settings.get("log_channel_id"):
            issues.append("ℹ️ No security log channel is configured.")
        embed = discord.Embed(
            title="🔍 Rallybit Security Audit",
            description="\n".join(issues) if issues else "✅ No major configuration weaknesses were found.",
            colour=DANGER if any(line.startswith("🚩") for line in issues) else (WARNING if issues else SUCCESS),
        )
        embed.add_field(name="Bot role", value=f"{me.top_role.mention if me else 'Unavailable'}", inline=True)
        embed.add_field(name="Enabled modules", value=str(sum(1 for module in settings['modules'].values() if module.get('enabled', False))), inline=True)
        embed.add_field(name="Trusted roles", value=str(len(settings.get('trusted_role_ids', []))), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @security.command(name="history", description="View recent security triggers and actions.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def history(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 15] = 10) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        records = _history_for(interaction.guild.id)[: int(limit)]
        if not records:
            return await interaction.response.send_message("No security events have been recorded yet.", ephemeral=True)
        embed = discord.Embed(title="📜 Security Event History", colour=BRAND)
        for record in records:
            try:
                timestamp = int(datetime.fromisoformat(record["timestamp"]).timestamp())
                when = f"<t:{timestamp}:R>"
            except (KeyError, TypeError, ValueError):
                when = "Unknown time"
            embed.add_field(
                name=f"{record.get('action', 'Event')} • {when}",
                value=f"**Module:** {_module_label(str(record.get('module', 'security')))}\n**Target:** {record.get('target', 'Unknown')}\n**Reason:** {record.get('reason', 'None')}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @security.command(name="lockdown", description="Disable or restore basic @everyone chat and voice permissions.")
    @app_commands.checks.has_permissions(administrator=True)
    async def lockdown(interaction: discord.Interaction, enabled: bool) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        guild = interaction.guild
        records = load_json(SECURITY_LOCKDOWN_FILE, {})
        gid = str(guild.id)
        try:
            if enabled:
                if gid not in records:
                    records[gid] = {"permissions": guild.default_role.permissions.value, "timestamp": datetime.now(timezone.utc).isoformat()}
                    save_json(SECURITY_LOCKDOWN_FILE, records)
                permissions = guild.default_role.permissions
                permissions.update(
                    send_messages=False,
                    add_reactions=False,
                    connect=False,
                    create_public_threads=False,
                    create_private_threads=False,
                    send_messages_in_threads=False,
                )
                await guild.default_role.edit(permissions=permissions, reason=f"Rallybit lockdown by {interaction.user}")
                action = "Server locked"
                colour = DANGER
            else:
                saved = records.pop(gid, None)
                if not saved:
                    return await interaction.response.send_message("Rallybit does not have a saved lockdown state for this server.", ephemeral=True)
                await guild.default_role.edit(
                    permissions=discord.Permissions(int(saved["permissions"])),
                    reason=f"Rallybit lockdown restored by {interaction.user}",
                )
                save_json(SECURITY_LOCKDOWN_FILE, records)
                action = "Server unlocked"
                colour = SUCCESS
            await _send_security_log(guild, module="lockdown", action=action, actor=interaction.user, reason="Manual administrator command.", colour=colour)
            await interaction.response.send_message(f"✅ {action}.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.response.send_message(f"Discord rejected the lockdown change: {exc}", ephemeral=True)

    @security.command(name="panic", description="Lock basic permissions and delete all active server invites.")
    @app_commands.checks.has_permissions(administrator=True)
    async def panic(interaction: discord.Interaction, confirmation: str) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        if confirmation.strip().upper() != "PANIC":
            return await interaction.response.send_message("Type `PANIC` in the confirmation field to run this destructive command.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        records = load_json(SECURITY_LOCKDOWN_FILE, {})
        gid = str(guild.id)
        if gid not in records:
            records[gid] = {"permissions": guild.default_role.permissions.value, "timestamp": datetime.now(timezone.utc).isoformat()}
            save_json(SECURITY_LOCKDOWN_FILE, records)
        permissions = guild.default_role.permissions
        permissions.update(send_messages=False, add_reactions=False, connect=False, create_public_threads=False, create_private_threads=False, send_messages_in_threads=False)
        invite_count = 0
        errors: list[str] = []
        try:
            await guild.default_role.edit(permissions=permissions, reason=f"Rallybit panic by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            errors.append(f"Lockdown: {exc}")
        try:
            for invite in await guild.invites():
                await invite.delete(reason=f"Rallybit panic by {interaction.user}")
                invite_count += 1
        except (discord.Forbidden, discord.HTTPException) as exc:
            errors.append(f"Invites: {exc}")
        await _send_security_log(
            guild,
            module="panic",
            action="Panic mode activated",
            actor=interaction.user,
            reason="Manual administrator command.",
            colour=DANGER,
            extra=f"Invites deleted: {invite_count}\nErrors: {'; '.join(errors) if errors else 'None'}",
        )
        await interaction.followup.send(f"🚨 Panic mode completed. Deleted **{invite_count}** invite(s)." + (f"\nWarnings: {'; '.join(errors)}" if errors else ""), ephemeral=True)

    @security.command(name="clearinvites", description="Delete every active invite in the server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def clearinvites(interaction: discord.Interaction, confirmation: bool) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        if not confirmation:
            return await interaction.response.send_message("Set confirmation to true to delete all active invites.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        count = 0
        try:
            for invite in await interaction.guild.invites():
                await invite.delete(reason=f"Rallybit invite purge by {interaction.user}")
                count += 1
            await _send_security_log(interaction.guild, module="invites", action="Invites cleared", actor=interaction.user, reason=f"Deleted {count} active invites.", colour=WARNING)
            await interaction.followup.send(f"✅ Deleted **{count}** active invite(s).", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.followup.send(f"Discord rejected the invite purge: {exc}", ephemeral=True)

    @security.command(name="quarantine", description="Strip manageable roles and isolate a member.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def quarantine(interaction: discord.Interaction, member: discord.Member, reason: str = "Security review") -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        success, detail = await _quarantine_member(interaction.guild, member, interaction.user, f"Rallybit quarantine by {interaction.user}: {reason}")
        if success:
            await _send_security_log(interaction.guild, module="quarantine", action="Quarantined", target=member, actor=interaction.user, reason=reason, colour=WARNING, extra=detail)
        await interaction.response.send_message(("✅ " if success else "❌ ") + detail, ephemeral=True)

    @security.command(name="release", description="Release a member and restore saved manageable roles.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def release(interaction: discord.Interaction, member: discord.Member, reason: str = "Security review complete") -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        success, detail = await _release_member(interaction.guild, member, f"Rallybit release by {interaction.user}: {reason}")
        if success:
            await _send_security_log(interaction.guild, module="quarantine", action="Released", target=member, actor=interaction.user, reason=reason, colour=SUCCESS, extra=detail)
        await interaction.response.send_message(("✅ " if success else "❌ ") + detail, ephemeral=True)

    @security.command(name="purgeuser", description="Soft-ban and immediately unban a member to purge recent messages.")
    @app_commands.checks.has_permissions(ban_members=True)
    async def purgeuser(
        interaction: discord.Interaction,
        member: discord.Member,
        delete_days: app_commands.Range[int, 0, 7] = 7,
        confirmation: str = "",
        reason: str = "Security message purge",
    ) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        if confirmation.strip().upper() != "PURGE":
            return await interaction.response.send_message("Type `PURGE` in the confirmation field. This removes the member and deletes recent messages.", ephemeral=True)
        if error := _hierarchy_error(interaction.guild, member):
            return await interaction.response.send_message(error, ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.guild.ban(member, reason=f"Rallybit purge by {interaction.user}: {reason}", delete_message_seconds=int(delete_days) * 86400)
            await interaction.guild.unban(member, reason=f"Rallybit purge completed by {interaction.user}")
            await _send_security_log(interaction.guild, module="purge", action="Soft-ban purge", target=member, actor=interaction.user, reason=reason, colour=WARNING, extra=f"Deleted up to {delete_days} day(s) of messages; member was unbanned afterwards.")
            await interaction.followup.send(f"✅ Purged recent messages from **{member}** and removed the ban. They must rejoin manually.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.followup.send(f"Discord rejected the purge: {exc}", ephemeral=True)

    @trap_group.command(name="setup", description="Create or update the top-level do-not-text-here security trap.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trap_setup(
        interaction: discord.Interaction,
        action: Literal["ban", "kick"] = "ban",
        channel_name: str = TRAP_DEFAULT_NAME,
        appeal_url: str = "",
        bypass_trusted_roles: bool = False,
        warning_message: str = "",
    ) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        settings = get_security_settings(interaction.guild.id)
        trap = settings["modules"]["scam_trap"]
        trap.update({
            "enabled": True,
            "action": action,
            "channel_name": re.sub(r"[^a-z0-9-]", "-", channel_name.lower()).strip("-")[:100] or TRAP_DEFAULT_NAME,
            "appeal_url": appeal_url.strip()[:500],
            "bypass_trusted_roles": bypass_trusted_roles,
            "warning_message": warning_message.strip()[:1900],
        })
        try:
            channel = await _create_or_repair_trap(interaction.guild, settings)
            await _send_security_log(interaction.guild, module="scam_trap", action="Trap configured", actor=interaction.user, reason=f"Action: {action}; trusted role bypass: {bypass_trusted_roles}", colour=SUCCESS, extra=f"Channel: {channel.mention}")
            await interaction.followup.send(f"✅ Security trap created at {channel.mention} and moved to the top of the server. Any non-exempt human account that posts there will be **{'banned' if action == 'ban' else 'kicked'}**.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
            await interaction.followup.send(f"Could not create the security trap: {exc}", ephemeral=True)

    @trap_group.command(name="repair", description="Repair permissions, position and warning text for the trap channel.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trap_repair(interaction: discord.Interaction) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        settings = get_security_settings(interaction.guild.id)
        settings["modules"]["scam_trap"]["enabled"] = True
        try:
            channel = await _create_or_repair_trap(interaction.guild, settings)
            await interaction.followup.send(f"✅ Repaired {channel.mention}.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
            await interaction.followup.send(f"Could not repair the trap: {exc}", ephemeral=True)

    @trap_group.command(name="disable", description="Disable the trap and optionally delete its channel.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trap_disable(interaction: discord.Interaction, delete_channel: bool = False) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        trap = settings["modules"]["scam_trap"]
        channel = interaction.guild.get_channel(int(trap.get("channel_id") or 0))
        trap["enabled"] = False
        trap["channel_id"] = None
        save_security_settings(interaction.guild.id, settings)
        if delete_channel and isinstance(channel, discord.TextChannel):
            try:
                await channel.delete(reason=f"Rallybit trap disabled by {interaction.user}")
            except (discord.Forbidden, discord.HTTPException) as exc:
                return await interaction.response.send_message(f"The trap was disabled, but Discord rejected channel deletion: {exc}", ephemeral=True)
        await interaction.response.send_message("✅ Security trap disabled." + (" The channel was deleted." if delete_channel else " The channel was left in place."), ephemeral=True)

    @trap_group.command(name="status", description="View the scam-trap configuration.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trap_status(interaction: discord.Interaction) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        trap = get_security_settings(interaction.guild.id)["modules"]["scam_trap"]
        channel = interaction.guild.get_channel(int(trap.get("channel_id") or 0))
        embed = discord.Embed(title="🪤 Security Trap", colour=BRAND)
        embed.description = (
            f"**Status:** {_status(trap.get('enabled', False))}\n"
            f"**Channel:** {channel.mention if channel else 'Not configured'}\n"
            f"**Action:** `{trap.get('action', 'ban')}`\n"
            f"**Trusted-role bypass:** {_status(trap.get('bypass_trusted_roles', False))}\n"
            f"**Appeal URL:** {trap.get('appeal_url') or 'Not configured'}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @age_group.command(name="configure", description="Enable and configure the Discord account-age gate.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def age_configure(
        interaction: discord.Interaction,
        minimum_days: app_commands.Range[int, 1, 3650],
        dm_member: bool = True,
        appeal_url: str = "",
        dm_message: str = "",
    ) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        age = settings["modules"]["age_gate"]
        age.update({
            "enabled": True,
            "min_days": int(minimum_days),
            "dm_member": dm_member,
            "appeal_url": appeal_url.strip()[:500],
            "dm_message": dm_message.strip()[:1500],
        })
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ Account age gate enabled at **{minimum_days} days**. Accounts younger than this are DMed when possible and kicked.", ephemeral=True)

    @age_group.command(name="disable", description="Disable the Discord account-age gate.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def age_disable(interaction: discord.Interaction) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        settings["modules"]["age_gate"]["enabled"] = False
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message("✅ Account age gate disabled.", ephemeral=True)

    @age_group.command(name="status", description="View the account-age gate configuration.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def age_status(interaction: discord.Interaction) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        age = get_security_settings(interaction.guild.id)["modules"]["age_gate"]
        await interaction.response.send_message(
            f"**Account age gate:** {_status(age.get('enabled', False))}\n**Minimum account age:** {age.get('min_days', 7)} days\n**DM before kick:** {_status(age.get('dm_member', True))}\n**Appeal URL:** {age.get('appeal_url') or 'Not configured'}",
            ephemeral=True,
        )

    @module_group.command(name="set", description="Enable or disable a protection module and choose its punishment.")
    @app_commands.choices(module=MODULE_CHOICES, punishment=PUNISHMENT_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def module_set(
        interaction: discord.Interaction,
        module: app_commands.Choice[str],
        enabled: bool,
        punishment: app_commands.Choice[str] | None = None,
    ) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        config = settings["modules"][module.value]
        config["enabled"] = enabled
        if punishment and module.value in PUNISHMENT_MODULES:
            config["punishment"] = punishment.value
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ {_module_label(module.value)} is now **{_status(enabled)}**." + (f" Punishment: `{config.get('punishment')}`." if module.value in PUNISHMENT_MODULES else ""), ephemeral=True)

    @module_group.command(name="threshold", description="Set the action count and time window for an anti-nuke module.")
    @app_commands.choices(module=THRESHOLD_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def module_threshold(
        interaction: discord.Interaction,
        module: app_commands.Choice[str],
        count: app_commands.Range[int, 1, 50],
        seconds: app_commands.Range[int, 5, 86400],
    ) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        config = settings["modules"][module.value]
        config["count"] = int(count)
        config["seconds"] = int(seconds)
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ {_module_label(module.value)} triggers at **{count} actions in {seconds} seconds**.", ephemeral=True)

    @antibot_group.command(name="configure", description="Configure unauthorised bot-add protection.")
    @app_commands.choices(punishment=PUNISHMENT_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def antibot_configure(
        interaction: discord.Interaction,
        enabled: bool,
        punishment: app_commands.Choice[str],
        ban_added_bot: bool = True,
    ) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        config = settings["modules"]["anti_bot"]
        config.update({"enabled": enabled, "punishment": punishment.value, "ban_added_bot": ban_added_bot})
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ Unauthorized bot protection {_status(enabled).lower()}. Bot adder action: `{punishment.value}`; added bot ban: `{ban_added_bot}`.", ephemeral=True)

    @spam_group.command(name="configure", description="Configure rapid-message detection and timeout length.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def spam_configure(
        interaction: discord.Interaction,
        enabled: bool,
        message_limit: app_commands.Range[int, 3, 30] = 6,
        window_seconds: app_commands.Range[int, 2, 60] = 5,
        timeout_minutes: app_commands.Range[int, 1, 40320] = 5,
    ) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        settings["modules"]["anti_spam"].update({
            "enabled": enabled,
            "message_limit": int(message_limit),
            "window_seconds": int(window_seconds),
            "timeout_minutes": int(timeout_minutes),
        })
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ Anti-spam {_status(enabled).lower()}: **{message_limit} messages / {window_seconds}s**, timeout **{timeout_minutes}m**.", ephemeral=True)

    @automod_group.command(name="configure", description="Enable automod and choose whether Discord invites are blocked.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automod_configure(interaction: discord.Interaction, enabled: bool, block_invites: bool = False) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        settings["modules"]["automod"].update({"enabled": enabled, "block_invites": block_invites})
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ Automod {_status(enabled).lower()}. Invite blocking: `{block_invites}`.", ephemeral=True)

    @automod_group.command(name="addterm", description="Add a whole word or phrase to the automod block list.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automod_addterm(interaction: discord.Interaction, term: str) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        clean = " ".join(term.casefold().split())[:100]
        if not clean:
            return await interaction.response.send_message("Enter a non-empty term.", ephemeral=True)
        settings = get_security_settings(interaction.guild.id)
        words = settings["modules"]["automod"].setdefault("bad_words", [])
        if clean not in words:
            words.append(clean)
            words[:] = words[-200:]
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ Added `{clean}` to the automod block list.", ephemeral=True)

    @automod_group.command(name="removeterm", description="Remove a word or phrase from the automod block list.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automod_removeterm(interaction: discord.Interaction, term: str) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        clean = " ".join(term.casefold().split())
        settings = get_security_settings(interaction.guild.id)
        words = settings["modules"]["automod"].setdefault("bad_words", [])
        if clean not in words:
            return await interaction.response.send_message("That term is not in the block list.", ephemeral=True)
        words.remove(clean)
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ Removed `{clean}` from the automod block list.", ephemeral=True)

    @automod_group.command(name="terms", description="View the configured automod block list.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automod_terms(interaction: discord.Interaction) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        words = get_security_settings(interaction.guild.id)["modules"]["automod"].get("bad_words", [])
        text = ", ".join(f"`{word}`" for word in words) if words else "No blocked terms configured."
        await interaction.response.send_message(text[:1900], ephemeral=True)

    @trust_group.command(name="add", description="Allow a role to bypass automatic security punishments.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trust_add(interaction: discord.Interaction, role: discord.Role) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        if role.is_default() or role.managed:
            return await interaction.response.send_message("Choose a normal server role, not @everyone or a managed integration role.", ephemeral=True)
        settings = get_security_settings(interaction.guild.id)
        role_ids = settings.setdefault("trusted_role_ids", [])
        if role.id not in role_ids:
            role_ids.append(role.id)
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ {role.mention} is trusted by automatic security modules.", ephemeral=True)

    @trust_group.command(name="remove", description="Remove a role from the automatic-security trust list.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trust_remove(interaction: discord.Interaction, role: discord.Role) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        settings["trusted_role_ids"] = [role_id for role_id in settings.get("trusted_role_ids", []) if role_id != role.id]
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ {role.mention} was removed from the trust list.", ephemeral=True)

    @trust_group.command(name="list", description="View roles trusted by automatic security modules.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trust_list(interaction: discord.Interaction) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        roles = [interaction.guild.get_role(int(role_id)) for role_id in settings.get("trusted_role_ids", [])]
        roles = [role for role in roles if role is not None]
        await interaction.response.send_message("**Trusted roles:**\n" + ("\n".join(role.mention for role in roles) if roles else "None configured."), ephemeral=True)

    @trust_group.command(name="adminbypass", description="Choose whether all server administrators bypass automatic protection.")
    @app_commands.checks.has_permissions(administrator=True)
    async def trust_adminbypass(interaction: discord.Interaction, enabled: bool) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        settings["bypass_administrators"] = enabled
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ Administrator bypass {_status(enabled).lower()}. The server owner and Rallybit are always protected.", ephemeral=True)

    @logs_group.command(name="channel", description="Set the channel used for security alerts and event logs.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def logs_channel(interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        settings["log_channel_id"] = channel.id if channel else None
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ Security logs will be sent to {channel.mention}." if channel else "✅ Security log channel cleared.", ephemeral=True)

    @logs_group.command(name="ownerdm", description="DM the server owner whenever an automatic security action triggers.")
    @app_commands.checks.has_permissions(administrator=True)
    async def logs_ownerdm(interaction: discord.Interaction, enabled: bool) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        settings["dm_server_owner"] = enabled
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"✅ Server-owner security DMs {_status(enabled).lower()}.", ephemeral=True)

    @logs_group.command(name="events", description="Choose which optional server events are copied to the security log.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def logs_events(
        interaction: discord.Interaction,
        member_events: bool = False,
        message_events: bool = False,
        voice_events: bool = False,
        structure_events: bool = False,
    ) -> None:
        if not await _command_guard(interaction) or interaction.guild is None:
            return
        settings = get_security_settings(interaction.guild.id)
        settings["event_logging"] = {
            "member_events": member_events,
            "message_events": message_events,
            "voice_events": voice_events,
            "structure_events": structure_events,
        }
        save_security_settings(interaction.guild.id, settings)
        await interaction.response.send_message("✅ Optional security event logging updated.", ephemeral=True)

    # Register each security area as its own top-level command group. This keeps
    # every command available to every guild while avoiding Discord's payload
    # limit for a single oversized command tree.
    for command_group in (
        security,
        trap_group,
        age_group,
        module_group,
        spam_group,
        automod_group,
        trust_group,
        logs_group,
        antibot_group,
    ):
        tree.add_command(command_group)

    client.add_listener(security_on_member_join, "on_member_join")
    client.add_listener(security_on_message, "on_message")
    client.add_listener(security_on_audit_log_entry_create, "on_audit_log_entry_create")
    client.add_listener(security_on_guild_update, "on_guild_update")
    client.add_listener(security_on_member_update, "on_member_update")
    client.add_listener(security_on_guild_role_update, "on_guild_role_update")
    client.add_listener(security_on_message_delete, "on_message_delete")
    client.add_listener(security_on_message_edit, "on_message_edit")
    client.add_listener(security_on_member_remove, "on_member_remove")
    client.add_listener(security_on_member_ban, "on_member_ban")
    client.add_listener(security_on_member_unban, "on_member_unban")
    client.add_listener(security_on_voice_state_update, "on_voice_state_update")
    client.add_listener(security_on_guild_channel_create, "on_guild_channel_create")
    client.add_listener(security_on_guild_channel_delete, "on_guild_channel_delete")
    client.add_listener(security_on_guild_role_create, "on_guild_role_create")
    client.add_listener(security_on_guild_role_delete, "on_guild_role_delete")
