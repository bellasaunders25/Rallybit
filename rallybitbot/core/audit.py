from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any, Iterable

import discord

from config.config import AUDIT_EVENTS_FILE, AUDIT_SETTINGS_FILE
from storage.json_store import load_json, save_json

AUDIT_EVENT_TYPES = (
    "commands",
    "configuration",
    "moderation",
    "members",
    "messages",
    "roles",
    "channels",
    "voice",
    "tickets",
    "reports",
    "staff",
    "security",
)
AUDIT_EVENT_LABELS = {
    "commands": "Commands",
    "configuration": "Configuration",
    "moderation": "Moderation",
    "members": "Members",
    "messages": "Messages",
    "roles": "Roles",
    "channels": "Channels",
    "voice": "Voice",
    "tickets": "Tickets",
    "reports": "Reports",
    "staff": "Staff operations",
    "security": "Security",
}
AUDIT_COLOURS = {
    "commands": 0x7567EE,
    "configuration": 0x6C8CFF,
    "moderation": 0xF06A70,
    "members": 0x45C486,
    "messages": 0xE6B85C,
    "roles": 0xB47AEA,
    "channels": 0x56B6C2,
    "voice": 0x52A8FF,
    "tickets": 0x7567EE,
    "reports": 0xF08A6A,
    "staff": 0x45C486,
    "security": 0xF06A70,
}
MAX_AUDIT_EVENTS = 1500
_AUDIT_WRITE_LOCK = RLock()


def default_audit_settings() -> dict[str, Any]:
    return {
        "enabled": True,
        "default_channel_id": None,
        "channel_ids": {},
        "enabled_events": {event: True for event in AUDIT_EVENT_TYPES},
    }


def get_audit_settings(guild_id: int) -> dict[str, Any]:
    data = load_json(AUDIT_SETTINGS_FILE) or {}
    saved = data.get(str(guild_id), {}) if isinstance(data, dict) else {}
    settings = default_audit_settings()
    if isinstance(saved, dict):
        settings.update({key: value for key, value in saved.items() if key not in {"channel_ids", "enabled_events"}})
        if isinstance(saved.get("channel_ids"), dict):
            settings["channel_ids"].update(saved["channel_ids"])
        if isinstance(saved.get("enabled_events"), dict):
            settings["enabled_events"].update(saved["enabled_events"])
    return settings


def save_audit_settings(guild_id: int, settings: dict[str, Any]) -> bool:
    data = load_json(AUDIT_SETTINGS_FILE) or {}
    if not isinstance(data, dict):
        data = {}
    data[str(guild_id)] = settings
    return save_json(AUDIT_SETTINGS_FILE, data)


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _record_event(guild_id: int, event: dict[str, Any]) -> None:
    with _AUDIT_WRITE_LOCK:
        data = load_json(AUDIT_EVENTS_FILE) or {}
        if not isinstance(data, dict):
            data = {}
        rows = data.setdefault(str(guild_id), [])
        if not isinstance(rows, list):
            rows = []
        rows.append(event)
        data[str(guild_id)] = rows[-MAX_AUDIT_EVENTS:]
        save_json(AUDIT_EVENTS_FILE, data)


def _channel_for_event(guild: discord.Guild, event_type: str, settings: dict[str, Any]) -> discord.TextChannel | None:
    channel_id = settings.get("channel_ids", {}).get(event_type) or settings.get("default_channel_id")
    channel = guild.get_channel(int(channel_id or 0))
    return channel if isinstance(channel, discord.TextChannel) else None


async def emit_audit_event(
    guild: discord.Guild,
    event_type: str,
    title: str,
    description: str,
    *,
    actor: discord.abc.User | None = None,
    target: Any = None,
    channel: discord.abc.GuildChannel | None = None,
    fields: Iterable[tuple[str, Any, bool]] = (),
) -> bool:
    event_type = event_type if event_type in AUDIT_EVENT_TYPES else "configuration"
    settings = get_audit_settings(guild.id)
    if not settings.get("enabled", True) or not settings.get("enabled_events", {}).get(event_type, True):
        return False
    created = datetime.now(timezone.utc)
    safe_title = _clean_text(title, 256) or AUDIT_EVENT_LABELS[event_type]
    safe_description = _clean_text(description, 4000) or "No additional details were provided."
    event = {
        "timestamp": created.isoformat(),
        "type": event_type,
        "title": safe_title,
        "description": safe_description,
        "actor_id": str(getattr(actor, "id", "") or ""),
        "actor_name": _clean_text(getattr(actor, "display_name", actor), 100),
        "target": _clean_text(target, 250),
        "channel_id": str(getattr(channel, "id", "") or ""),
        "channel_name": _clean_text(getattr(channel, "name", ""), 100),
    }
    _record_event(guild.id, event)
    destination = _channel_for_event(guild, event_type, settings)
    if destination is None or guild.me is None:
        return False
    permissions = destination.permissions_for(guild.me)
    if not permissions.send_messages or not permissions.embed_links:
        return False
    embed = discord.Embed(
        title=safe_title,
        description=safe_description,
        colour=AUDIT_COLOURS[event_type],
        timestamp=created,
    )
    embed.set_author(name=f"Rallybit Logs · {AUDIT_EVENT_LABELS[event_type]}")
    if actor is not None:
        embed.add_field(
            name="Performed by",
            value=f"{getattr(actor, 'mention', str(actor))}\n`{getattr(actor, 'id', 'Unknown')}`",
            inline=True,
        )
    if target not in (None, ""):
        embed.add_field(name="Target", value=_clean_text(target, 1024), inline=True)
    if channel is not None:
        embed.add_field(name="Channel", value=getattr(channel, "mention", f"#{channel.name}"), inline=True)
    for name, value, inline in list(fields)[:20]:
        if str(value or "").strip():
            embed.add_field(name=_clean_text(name, 256), value=_clean_text(value, 1024), inline=bool(inline))
    embed.set_footer(text=f"Server ID: {guild.id}")
    try:
        await destination.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def audit_command_interaction(interaction: discord.Interaction) -> None:
    if interaction.guild is None or interaction.command is None:
        return
    qualified_name = getattr(interaction.command, "qualified_name", getattr(interaction.command, "name", "command"))
    await emit_audit_event(
        interaction.guild,
        "commands",
        "Command used",
        f"`/{qualified_name}` was used.",
        actor=interaction.user,
        channel=interaction.channel if isinstance(interaction.channel, discord.abc.GuildChannel) else None,
    )
    root = str(qualified_name).split(" ", 1)[0]
    operational_type = (
        "tickets" if root == "ticket" else
        "reports" if root in {"report", "review"} else
        "staff" if root in {"loa", "roa", "clockin", "clockout", "break", "shift", "timesheet", "staffhours", "duty", "forceclockout", "staff"} else
        "security" if root.startswith("security") else
        "moderation" if root in {"mod", "role", "channel", "snipe", "editsnipe", "clearsnipes"} else
        None
    )
    if operational_type:
        await emit_audit_event(
            interaction.guild,
            operational_type,
            f"{AUDIT_EVENT_LABELS[operational_type]} command",
            f"`/{qualified_name}` was invoked.",
            actor=interaction.user,
            channel=interaction.channel if isinstance(interaction.channel, discord.abc.GuildChannel) else None,
        )
