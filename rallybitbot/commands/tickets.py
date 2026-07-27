from __future__ import annotations

import asyncio
import io
import re
import unicodedata
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands

from config.config import (
    OPEN_TICKETS_FILE,
    TICKET_HISTORY_FILE,
    TICKET_PANELS_FILE,
    TICKET_SETTINGS_FILE,
)
from core.logging import log_server_event
from storage.json_store import load_json, save_json

BRAND = 0x7C6CFF
GREEN = 0x57F287
RED = 0xED4245
TICKET_STATUSES = ("Open", "Claimed", "Pending", "Waiting for User", "Resolved", "Closed")
TICKET_PRIORITIES = ("Low", "Medium", "High", "Critical")
LEGACY_WELCOME = "Thanks for opening a ticket, {user}. Please describe how the team can help."
DEFAULT_WELCOME = "Thanks for reaching out, {user}. A team member will be with you shortly. Please explain what you need help with and include any useful context."
_DELETE_TASKS: dict[int, asyncio.Task[None]] = {}
MAX_PANEL_OPTIONS = 25
DEFAULT_PANEL_DESCRIPTION = "Choose the ticket type that best matches what you need. Your conversation will be private."
DEFAULT_OPTION_DESCRIPTION = "Speak privately with the support team."


def _settings(guild_id: int) -> dict[str, Any]:
    data = load_json(TICKET_SETTINGS_FILE) or {}
    defaults = {
        "default_category_id": None,
        "log_channel_id": None,
        "support_role_ids": [],
        "one_ticket_per_member": True,
        "transcript_limit": 500,
        "auto_delete_minutes": 0,
        "ticket_name": "ticket-{username}",
        "welcome_message": DEFAULT_WELCOME,
    }
    saved = data.get(str(guild_id), {})
    if isinstance(saved, dict): defaults.update(saved)
    return defaults


def _save_settings(guild_id: int, settings: dict[str, Any]) -> None:
    data = load_json(TICKET_SETTINGS_FILE) or {}; data[str(guild_id)] = settings; save_json(TICKET_SETTINGS_FILE, data)


def _panels() -> dict[str, Any]:
    data = load_json(TICKET_PANELS_FILE) or {}; return data if isinstance(data, dict) else {}


def _save_panels(data: dict[str, Any]) -> None:
    save_json(TICKET_PANELS_FILE, data)


def ticket_panel_by_message(guild_id: int, message_id: int) -> tuple[str, dict[str, Any]] | None:
    guild_panels = _panels().get(str(guild_id), {})
    if not isinstance(guild_panels, dict):
        return None
    for panel_id, panel in guild_panels.items():
        if isinstance(panel, dict) and str(panel.get("message_id")) == str(message_id):
            return str(panel_id), deepcopy(panel)
    return None


def _open() -> dict[str, Any]:
    data = load_json(OPEN_TICKETS_FILE) or {}; return data if isinstance(data, dict) else {}


def _save_open(data: dict[str, Any]) -> None:
    save_json(OPEN_TICKETS_FILE, data)


def _record_history(guild_id: int, record: dict[str, Any]) -> None:
    data = load_json(TICKET_HISTORY_FILE) or {}; rows = data.setdefault(str(guild_id), []); rows.append(record); data[str(guild_id)] = rows[-500:]; save_json(TICKET_HISTORY_FILE, data)


def _safe_channel_name(template: str, member: discord.Member, number: int = 1) -> str:
    name = template.replace("{username}", member.name).replace("{displayname}", member.display_name).replace("{id}", str(member.id)).replace("{number}", str(number))
    name = re.sub(r"[^a-zA-Z0-9-_]", "-", name.lower())
    name = re.sub(r"-+", "-", name).strip("-")
    return (name or f"ticket-{member.id}")[:90]


def _member_open_ticket(guild_id: int, user_id: int) -> tuple[str, dict[str, Any]] | None:
    guild_data = _open().get(str(guild_id), {})
    if not isinstance(guild_data, dict): return None
    for channel_id, record in guild_data.items():
        if isinstance(record, dict) and str(record.get("owner_id")) == str(user_id) and record.get("status", "Open") != "Closed":
            return channel_id, record
    return None


def _ticket_record(guild_id: int, channel_id: int) -> dict[str, Any] | None:
    record = _open().get(str(guild_id), {}).get(str(channel_id))
    return record if isinstance(record, dict) else None


def _allowed_ticket_category_ids(guild_id: int) -> set[int]:
    values: set[int] = set()
    default_id = _settings(guild_id).get("default_category_id")
    if str(default_id).isdigit():
        values.add(int(default_id))
    panels = _panels().get(str(guild_id), {})
    if isinstance(panels, dict):
        for panel in panels.values():
            if not isinstance(panel, dict):
                continue
            for option in _panel_options(panel):
                category_id = option.get("category_id")
                if str(category_id).isdigit():
                    values.add(int(category_id))
    return values


def _support_roles(guild: discord.Guild, role_ids: list[Any]) -> list[discord.Role]:
    result = []
    seen_ids: set[int] = set()
    for value in role_ids:
        try: role_id = int(value)
        except (TypeError, ValueError): role = None
        else: role = guild.get_role(role_id)
        if role and role.id not in seen_ids and not role.is_default():
            seen_ids.add(role.id)
            result.append(role)
    return result


def _ticket_support_role_ids(guild_id: int, record: dict[str, Any] | None) -> set[int]:
    values = list(_settings(guild_id).get("support_role_ids", []))
    if record:
        panel_id = str(record.get("panel_id") or "")
        panel = _panels().get(str(guild_id), {}).get(panel_id)
        if isinstance(panel, dict):
            values.extend(panel.get("support_role_ids", []))
            option = _panel_option(panel, str(record.get("option_id") or ""))
            if option:
                values.extend(option.get("support_role_ids", []))
    role_ids: set[int] = set()
    for value in values:
        try:
            role_ids.add(int(value))
        except (TypeError, ValueError):
            continue
    return role_ids


def _is_ticket_staff(member: discord.abc.User, record: dict[str, Any] | None) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator or member.guild_permissions.manage_messages:
        return True
    support_role_ids = _ticket_support_role_ids(member.guild.id, record)
    return any(role.id in support_role_ids for role in member.roles)


def _ticket_staff_denial() -> str:
    return "You need a configured ticket support role or the Manage Messages permission to use this action."


def _guild_icon_url(guild: discord.Guild) -> str | None:
    return str(guild.icon.url) if guild.icon else None


def _set_guild_author(embed: discord.Embed, guild: discord.Guild, label: str) -> str | None:
    icon_url = _guild_icon_url(guild)
    if icon_url:
        embed.set_author(name=f"{guild.name} • {label}", icon_url=icon_url)
    else:
        embed.set_author(name=f"{guild.name} • {label}")
    return icon_url


def _panel_workload(guild_id: int, panel_id: str) -> int:
    guild_tickets = _open().get(str(guild_id), {})
    if not isinstance(guild_tickets, dict):
        return 0
    return sum(
        1
        for record in guild_tickets.values()
        if isinstance(record, dict)
        and str(record.get("panel_id")) == panel_id
        and record.get("status", "Open") != "Closed"
    )


def _safe_media_url(value: Any) -> str | None:
    url = str(value or "").strip()
    return url[:2048] if url.lower().startswith("https://") else None


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _panel_colour(value: Any) -> int:
    raw = str(value or "").strip().lower().removeprefix("#").removeprefix("0x")
    if re.fullmatch(r"[0-9a-f]{6}", raw):
        return int(raw, 16)
    return BRAND


def _normalise_panel_option(option: dict[str, Any], fallback_id: str) -> dict[str, Any]:
    role_ids = option.get("support_role_ids", [])
    if not isinstance(role_ids, list):
        role_ids = [role_ids] if role_ids else []
    option_id = re.sub(r"[^A-Z0-9_-]", "", str(option.get("option_id") or fallback_id).upper())[:32]
    name = str(option.get("name") or "Support").strip()[:100] or "Support"
    return {
        "option_id": option_id or fallback_id,
        "name": name,
        "description": str(option.get("description") or DEFAULT_OPTION_DESCRIPTION).strip()[:100] or DEFAULT_OPTION_DESCRIPTION,
        "emoji": str(option.get("emoji") or "").strip()[:100],
        "category_id": option.get("category_id"),
        "support_role_ids": list(dict.fromkeys(str(value) for value in role_ids if str(value).isdigit())),
        "ticket_name": str(option.get("ticket_name") or "").strip()[:90],
        "ticket_title": str(option.get("ticket_title") or f"{name} ticket").strip()[:256] or f"{name} ticket",
        "welcome_message": str(option.get("welcome_message") or DEFAULT_WELCOME).strip()[:4000] or DEFAULT_WELCOME,
    }


def _panel_options(panel: dict[str, Any]) -> list[dict[str, Any]]:
    configured = panel.get("options")
    result: list[dict[str, Any]] = []
    if isinstance(configured, list):
        for index, option in enumerate(configured[:MAX_PANEL_OPTIONS], start=1):
            if isinstance(option, dict):
                result.append(_normalise_panel_option(option, f"OPTION-{index}"))
    if result:
        return result
    legacy = {
        "option_id": "DEFAULT",
        "name": panel.get("name") or panel.get("button_label") or "Support",
        "description": panel.get("option_description") or DEFAULT_OPTION_DESCRIPTION,
        "emoji": panel.get("button_emoji") or "🎫",
        "category_id": panel.get("category_id"),
        "support_role_ids": panel.get("support_role_ids", []),
        "ticket_name": panel.get("ticket_name") or "",
        "ticket_title": panel.get("ticket_title") or "Support ticket",
        "welcome_message": panel.get("welcome_message") or DEFAULT_WELCOME,
    }
    return [_normalise_panel_option(legacy, "DEFAULT")]


def _panel_option(panel: dict[str, Any], option_id: str) -> dict[str, Any] | None:
    options = _panel_options(panel)
    if not option_id:
        return options[0] if len(options) == 1 else None
    target = option_id.upper()
    return next((option for option in options if str(option.get("option_id", "")).upper() == target), None)


def _effective_ticket_panel(panel: dict[str, Any], option_id: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    option = _panel_option(panel, str(option_id or ""))
    if not option:
        raise RuntimeError("That ticket option no longer exists. Refresh the panel and try again.")
    effective = dict(panel)
    effective.update(option)
    common_roles = panel.get("support_role_ids", []) if isinstance(panel.get("support_role_ids"), list) else []
    effective["support_role_ids"] = list(dict.fromkeys([*common_roles, *option.get("support_role_ids", [])]))
    return effective, option


def _select_option_emoji(value: Any) -> discord.PartialEmoji | None:
    raw = str(value or "").strip()
    custom_emoji = re.fullmatch(r"<a?:[A-Za-z0-9_]+:\d+>", raw)
    has_unicode_symbol = any(
        unicodedata.category(character) == "So" or character == "\u20e3"
        for character in raw
    )
    if not raw or (not custom_emoji and not has_unicode_symbol):
        return None
    try:
        return discord.PartialEmoji.from_str(raw)
    except (TypeError, ValueError):
        return None


def _ticket_panel_embeds(guild: discord.Guild, panel_id: str, panel: dict[str, Any]) -> list[discord.Embed]:
    active = _panel_workload(guild.id, panel_id)
    workload = "No tickets waiting" if active == 0 else f"{active} active ticket{'s' if active != 1 else ''}"
    description = str(panel.get("description") or DEFAULT_PANEL_DESCRIPTION).strip()[:1800]
    if _as_bool(panel.get("show_workload"), True):
        description += f"\n\n**Current workload**\n{workload}"
    if _as_bool(panel.get("show_guidance"), True):
        description += "\n\n**Before opening**\nChoose the closest option, include useful IDs or screenshots, and avoid duplicate tickets."
    embed = discord.Embed(
        title=str(panel.get("title") or "Support centre")[:256],
        description=description[:4096],
        color=_panel_colour(panel.get("color")),
        timestamp=discord.utils.utcnow() if _as_bool(panel.get("show_timestamp"), True) else None,
    )
    author_name = str(panel.get("author_name") or f"{guild.name} • Support centre").strip()[:256]
    author_icon = _safe_media_url(panel.get("author_icon_url")) or _guild_icon_url(guild)
    if _as_bool(panel.get("show_author"), True) and author_name:
        if author_icon:
            embed.set_author(name=author_name, icon_url=author_icon)
        else:
            embed.set_author(name=author_name)
    footer_text = str(panel.get("footer_text") or f"Rallybit Tickets • Panel {panel_id}").strip()[:2048]
    used_characters = len(embed.title or "") + len(embed.description or "")
    used_characters += len(author_name) if _as_bool(panel.get("show_author"), True) else 0
    used_characters += len(footer_text)
    if _as_bool(panel.get("show_option_details"), True):
        for option in _panel_options(panel):
            emoji = str(option.get("emoji") or "").strip()
            heading = f"{emoji} {option['name']}".strip()[:180]
            field_value = str(option["description"])[:100]
            if used_characters + len(heading) + len(field_value) > 5900:
                break
            embed.add_field(name=heading, value=field_value, inline=False)
            used_characters += len(heading) + len(field_value)
    thumbnail_url = _safe_media_url(panel.get("thumbnail_url"))
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    image_url = _safe_media_url(panel.get("image_url"))
    if image_url:
        embed.set_image(url=image_url)
    footer_icon = _safe_media_url(panel.get("footer_icon_url"))
    if footer_text:
        if footer_icon:
            embed.set_footer(text=footer_text, icon_url=footer_icon)
        else:
            embed.set_footer(text=footer_text)
    embeds: list[discord.Embed] = []
    header_image = _safe_media_url(panel.get("header_image_url"))
    if header_image:
        header = discord.Embed(color=_panel_colour(panel.get("color")))
        header.set_image(url=header_image)
        embeds.append(header)
    embeds.append(embed)
    return embeds


def _ticket_panel_embed(guild: discord.Guild, panel_id: str, panel: dict[str, Any]) -> discord.Embed:
    """Compatibility helper for callers that expect the content embed only."""
    return _ticket_panel_embeds(guild, panel_id, panel)[-1]


def _ticket_welcome_embed(
    guild: discord.Guild,
    channel: discord.TextChannel,
    member: discord.Member,
    panel: dict[str, Any],
    welcome: str,
    case_id: str,
    reason: str | None,
) -> discord.Embed:
    ticket_type = str(panel.get("name") or "Support")
    embed = discord.Embed(
        title="Welcome to your ticket",
        description=welcome[:4000],
        color=BRAND,
        timestamp=discord.utils.utcnow(),
    )
    icon_url = _set_guild_author(embed, guild, "Support desk")
    embed.add_field(name="Case ID", value=f"`{case_id}`", inline=True)
    embed.add_field(name="Category", value=ticket_type[:1024], inline=True)
    embed.add_field(name="Created by", value=member.mention, inline=True)
    if reason:
        embed.add_field(name="Request summary", value=reason.strip()[:1024], inline=False)
    embed.add_field(
        name="What happens next",
        value="Share the relevant details below. A team member can claim the ticket and will reply here as soon as possible.",
        inline=False,
    )
    if icon_url:
        embed.set_thumbnail(url=icon_url)
    embed.set_footer(text=f"{channel.name} • Use the controls below to manage this ticket")
    return embed


def _ticket_closed_embed(
    channel: discord.TextChannel,
    owner: discord.Member | None,
    closer: discord.abc.User,
    record: dict[str, Any],
    reason: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="Ticket closed",
        description="This support request has been closed. A transcript is attached for reference.",
        color=RED,
        timestamp=discord.utils.utcnow(),
    )
    icon_url = _set_guild_author(embed, channel.guild, "Support desk")
    embed.add_field(name="Case ID", value=f"`{record.get('case_id') or channel.name}`", inline=True)
    embed.add_field(name="Created by", value=owner.mention if owner else str(record.get("owner_id")), inline=True)
    embed.add_field(name="Closed by", value=closer.mention, inline=True)
    embed.add_field(name="Closure reason", value=reason[:1024], inline=False)
    if icon_url:
        embed.set_thumbnail(url=icon_url)
    embed.set_footer(text="Rallybit Tickets • Transcript archive")
    return embed


def _ticket_deleted_embed(
    channel: discord.TextChannel,
    owner: discord.Member | None,
    deleter: discord.abc.User,
    record: dict[str, Any],
    reason: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="Ticket deleted",
        description="This ticket channel was permanently deleted. A final transcript is attached for reference.",
        color=RED,
        timestamp=discord.utils.utcnow(),
    )
    icon_url = _set_guild_author(embed, channel.guild, "Support desk")
    embed.add_field(name="Case ID", value=f"`{record.get('case_id') or channel.name}`", inline=True)
    embed.add_field(name="Created by", value=owner.mention if owner else str(record.get("owner_id")), inline=True)
    embed.add_field(name="Deleted by", value=deleter.mention, inline=True)
    embed.add_field(name="Deletion reason", value=reason[:1024], inline=False)
    if icon_url:
        embed.set_thumbnail(url=icon_url)
    embed.set_footer(text="Rallybit Tickets • Final transcript")
    return embed


async def _make_transcript(channel: discord.TextChannel, limit: int = 500) -> discord.File:
    lines = [f"Rallybit ticket transcript: #{channel.name}", f"Server: {channel.guild.name} ({channel.guild.id})", "=" * 80, ""]
    try:
        messages = [message async for message in channel.history(limit=max(1, min(limit, 2000)), oldest_first=True)]
    except discord.HTTPException:
        messages = []
    for message in messages:
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        content = message.clean_content or ""
        if message.attachments:
            content += ("\n" if content else "") + "Attachments: " + ", ".join(a.url for a in message.attachments)
        if message.embeds and not content:
            content = "[Embed message]"
        lines.append(f"[{timestamp}] {message.author} ({message.author.id}): {content}")
    payload = "\n".join(lines).encode("utf-8", errors="replace")
    return discord.File(io.BytesIO(payload), filename=f"transcript-{channel.name}.txt")


def _cancel_ticket_deletion(channel_id: int) -> None:
    task = _DELETE_TASKS.pop(channel_id, None)
    if task and task is not asyncio.current_task() and not task.done():
        task.cancel()


def _schedule_ticket_deletion(channel: discord.TextChannel, delete_at: str) -> None:
    _cancel_ticket_deletion(channel.id)

    async def runner() -> None:
        try:
            scheduled = datetime.fromisoformat(delete_at)
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            delay = max(0.0, (scheduled - datetime.now(timezone.utc)).total_seconds())
            await asyncio.sleep(delay)
            record = _ticket_record(channel.guild.id, channel.id)
            if not record or record.get("status") != "Closed" or record.get("delete_at") != delete_at:
                return
            actor = channel.guild.me
            if actor:
                await delete_ticket(channel, actor, "Automatic deletion after the configured close delay")
        except asyncio.CancelledError:
            raise
        except (ValueError, discord.HTTPException):
            pass
        finally:
            current = _DELETE_TASKS.get(channel.id)
            if current is asyncio.current_task():
                _DELETE_TASKS.pop(channel.id, None)

    _DELETE_TASKS[channel.id] = asyncio.create_task(runner(), name=f"rallybit:delete-ticket:{channel.id}")


async def delete_ticket(channel: discord.TextChannel, deleter: discord.abc.User, reason: str) -> bool:
    data = _open(); guild_data = data.get(str(channel.guild.id), {})
    record = guild_data.get(str(channel.id)) if isinstance(guild_data, dict) else None
    if not isinstance(record, dict):
        return False
    settings = _settings(channel.guild.id)
    transcript = await _make_transcript(channel, int(settings.get("transcript_limit", 500)))
    owner = channel.guild.get_member(int(record.get("owner_id", 0)))
    log_channel = channel.guild.get_channel(int(settings.get("log_channel_id") or 0))
    embed = _ticket_deleted_embed(channel, owner, deleter, record, reason)
    if isinstance(log_channel, discord.TextChannel):
        try:
            await log_channel.send(embed=embed, file=transcript)
        except discord.HTTPException:
            pass
    elif owner:
        try:
            await owner.send(embed=embed, file=transcript)
        except discord.HTTPException:
            pass
    deleted_at = datetime.now(timezone.utc).isoformat()
    await channel.delete(reason=f"Ticket deleted by {deleter}: {reason}")
    _record_history(channel.guild.id, {
        "channel_id": str(channel.id), "channel_name": channel.name, "owner_id": str(record.get("owner_id")),
        "panel_id": record.get("panel_id"), "claimed_by": record.get("claimed_by"), "deleted_by": str(deleter.id),
        "reason": reason, "created_at": record.get("created_at"), "closed_at": record.get("closed_at"), "deleted_at": deleted_at,
    })
    guild_data.pop(str(channel.id), None); data[str(channel.guild.id)] = guild_data; _save_open(data)
    _cancel_ticket_deletion(channel.id)
    await _update_ticket_panel_message(channel.guild, str(record.get("panel_id") or ""))
    log_server_event(channel.guild.id, f"Ticket #{channel.name} deleted by {deleter}.")
    return True


async def _send_claim_announcement(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(f"Ticket claimed by {interaction.user.mention}.")
    try:
        message = await interaction.original_response()
        await message.pin(reason=f"Ticket claimed by {interaction.user}")
    except discord.HTTPException:
        pass


async def close_ticket(channel: discord.TextChannel, closer: discord.abc.User, reason: str) -> bool:
    data = _open(); guild_data = data.get(str(channel.guild.id), {})
    record = guild_data.get(str(channel.id)) if isinstance(guild_data, dict) else None
    if not isinstance(record, dict): return False
    settings = _settings(channel.guild.id)
    transcript = await _make_transcript(channel, int(settings.get("transcript_limit", 500)))
    owner = channel.guild.get_member(int(record.get("owner_id", 0)))
    log_channel = channel.guild.get_channel(int(settings.get("log_channel_id") or 0))
    embed = _ticket_closed_embed(channel, owner, closer, record, reason)
    if isinstance(log_channel, discord.TextChannel):
        try: await log_channel.send(embed=embed, file=transcript)
        except discord.HTTPException: pass
    elif owner:
        try: await owner.send(embed=embed, file=transcript)
        except discord.HTTPException: pass
    closed_at_dt = datetime.now(timezone.utc)
    closed_at = closed_at_dt.isoformat()
    auto_delete_minutes = max(0, min(10080, int(settings.get("auto_delete_minutes", 0) or 0)))
    delete_at_dt = closed_at_dt + timedelta(minutes=auto_delete_minutes) if auto_delete_minutes else None
    delete_at = delete_at_dt.isoformat() if delete_at_dt else None
    _record_history(channel.guild.id, {
        "channel_id": str(channel.id), "channel_name": channel.name, "owner_id": str(record.get("owner_id")),
        "panel_id": record.get("panel_id"), "claimed_by": record.get("claimed_by"), "closed_by": str(closer.id),
        "reason": reason, "created_at": record.get("created_at"), "closed_at": closed_at,
    })
    record.update({
        "status": "Closed", "closed_by": str(closer.id), "closed_at": closed_at,
        "close_reason": reason[:1000], "delete_at": delete_at, "updated_at": closed_at, "updated_by": str(closer.id),
    })
    guild_data[str(channel.id)] = record; data[str(channel.guild.id)] = guild_data; _save_open(data)
    if delete_at:
        _schedule_ticket_deletion(channel, delete_at)
    await _update_ticket_panel_message(channel.guild, str(record.get("panel_id") or ""))
    try:
        if owner:
            await channel.set_permissions(owner, view_channel=True, send_messages=False, read_message_history=True, reason=f"Ticket closed by {closer}")
        closed_name = channel.name if channel.name.startswith("closed-") else f"closed-{channel.name}"
        await channel.edit(name=closed_name[:100], reason=f"Ticket closed by {closer}: {reason}")
        deletion_note = f" It will be deleted <t:{int(delete_at_dt.timestamp())}:R>." if delete_at_dt else " Staff can delete it with the button below."
        notice = discord.Embed(
            title="This ticket is now closed",
            description=f"The conversation is read-only for the ticket creator. Staff can reopen it with `/ticket reopen`.{deletion_note}",
            color=RED,
            timestamp=discord.utils.utcnow(),
        )
        notice.set_footer(text=f"Case {record.get('case_id') or channel.name}")
        await channel.send(embed=notice)
    except discord.HTTPException: pass
    log_server_event(channel.guild.id, f"Ticket #{channel.name} closed by {closer}.")
    return True


async def create_ticket(
    guild: discord.Guild,
    member: discord.Member,
    panel_id: str,
    category_override: discord.CategoryChannel | None = None,
    reason: str | None = None,
    option_id: str | None = None,
) -> discord.TextChannel:
    panels = _panels(); panel = panels.get(str(guild.id), {}).get(panel_id)
    if not isinstance(panel, dict) and category_override is not None:
        panel = {
            "name": "Direct request", "category_id": category_override.id,
            "ticket_title": "Support ticket", "support_role_ids": [],
        }
    if not isinstance(panel, dict): raise RuntimeError("That ticket panel no longer exists.")
    if category_override is None:
        panel, selected_option = _effective_ticket_panel(panel, option_id)
    else:
        selected_option = {"option_id": "DIRECT"}
    settings = _settings(guild.id)
    if settings.get("one_ticket_per_member"):
        existing = _member_open_ticket(guild.id, member.id)
        if existing:
            channel = guild.get_channel(int(existing[0]))
            if isinstance(channel, discord.TextChannel): raise RuntimeError(f"You already have an open ticket: {channel.mention}")
    category_id = category_override.id if category_override else panel.get("category_id") or settings.get("default_category_id")
    category = guild.get_channel(int(category_id or 0))
    if not isinstance(category, discord.CategoryChannel): raise RuntimeError("The ticket category is not configured or no longer exists.")
    me = guild.me
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
    }
    role_ids = list(settings.get("support_role_ids", [])) + list(panel.get("support_role_ids", []))
    support_roles = _support_roles(guild, role_ids)
    for role in support_roles:
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
    if me:
        overwrites[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_messages=True)
    open_data = _open(); guild_open = open_data.setdefault(str(guild.id), {})
    number = len(guild_open) + 1
    channel_name = _safe_channel_name(str(panel.get("ticket_name") or settings.get("ticket_name", "ticket-{username}")), member, number)
    channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites, topic=f"Rallybit ticket owner={member.id} panel={panel_id}", reason=f"Ticket opened by {member}")
    case_id = f"T-{uuid.uuid4().hex[:8].upper()}"
    guild_open[str(channel.id)] = {
        "owner_id": str(member.id), "panel_id": panel_id,
        "option_id": str(selected_option.get("option_id") or ""), "claimed_by": None,
        "status": "Open", "priority": "Medium", "reason": (reason or "").strip()[:1000],
        "case_id": case_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    open_data[str(guild.id)] = guild_open; _save_open(open_data)
    welcome = str(panel.get("welcome_message") or settings.get("welcome_message") or DEFAULT_WELCOME)
    if welcome.strip() == LEGACY_WELCOME:
        welcome = DEFAULT_WELCOME
    welcome = welcome.replace("{user}", member.mention).replace("{username}", member.display_name).replace("{server}", guild.name)
    embed = _ticket_welcome_embed(guild, channel, member, panel, welcome, case_id, reason)
    mentions = " ".join(role.mention for role in support_roles)
    try:
        control_message = await channel.send(content=f"{member.mention} {mentions}".strip(), embed=embed, view=TicketControlView(guild.id, channel.id), allowed_mentions=discord.AllowedMentions(users=[member], roles=support_roles, everyone=False))
        guild_open[str(channel.id)]["control_message_id"] = str(control_message.id)
        open_data[str(guild.id)] = guild_open
        _save_open(open_data)
    except Exception:
        guild_open.pop(str(channel.id), None)
        open_data[str(guild.id)] = guild_open
        _save_open(open_data)
        try:
            await channel.delete(reason="Rallybit ticket setup failed; rolling back empty channel")
        except Exception:
            pass
        raise
    await _update_ticket_panel_message(guild, panel_id)
    log_server_event(guild.id, f"Ticket #{channel.name} opened by {member}.")
    return channel


class TicketPanelSelect(discord.ui.Select):
    def __init__(self, guild_id: int, panel_id: str, panel: dict[str, Any]) -> None:
        self.guild_id = guild_id
        self.panel_id = panel_id
        select_options = []
        for option in _panel_options(panel):
            select_options.append(discord.SelectOption(
                label=str(option["name"])[:100],
                value=str(option["option_id"])[:100],
                description=str(option["description"])[:100],
                emoji=_select_option_emoji(option.get("emoji")),
            ))
        super().__init__(
            placeholder=str(panel.get("select_placeholder") or "Select a ticket type…")[:150],
            min_values=1,
            max_values=1,
            options=select_options,
            custom_id=f"rallybit:ticket:select:{guild_id}:{panel_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or interaction.guild.id != self.guild_id or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This ticket panel is unavailable.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            channel = await create_ticket(
                interaction.guild,
                interaction.user,
                self.panel_id,
                option_id=self.values[0],
            )
            await interaction.followup.send(f"Your ticket is ready: {channel.mention}", ephemeral=True)
        except (RuntimeError, discord.HTTPException) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self, guild_id: int, panel_id: str, panel: dict[str, Any]) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketPanelSelect(guild_id, panel_id, panel))


class TicketCloseReasonModal(discord.ui.Modal, title="Close ticket"):
    reason = discord.ui.TextInput(
        label="Reason for closing",
        placeholder="Explain why this ticket is being closed",
        style=discord.TextStyle.paragraph,
        min_length=3,
        max_length=1000,
        required=True,
    )

    def __init__(self, guild_id: int, channel_id: int) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This ticket channel is no longer available.", ephemeral=True)
            return
        record = _ticket_record(self.guild_id, self.channel_id)
        if not record:
            await interaction.response.send_message("This ticket is no longer tracked.", ephemeral=True)
            return
        owner_id = int(record.get("owner_id", 0))
        if interaction.user.id != owner_id and not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message("Only the ticket owner or staff can close this ticket.", ephemeral=True)
            return
        if record.get("status") == "Closed":
            await interaction.response.send_message("This ticket is already closed.", ephemeral=True)
            return
        await interaction.response.send_message("Closing this ticket…", ephemeral=True)
        await close_ticket(interaction.channel, interaction.user, str(self.reason.value).strip())


class TicketDeleteConfirmView(discord.ui.View):
    def __init__(self, requester_id: int, guild_id: int, channel_id: int) -> None:
        super().__init__(timeout=30)
        self.requester_id = requester_id
        self.guild_id = guild_id
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message("Only the staff member who opened this confirmation can use it.", ephemeral=True)
        return False

    @discord.ui.button(label="Delete permanently", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This ticket channel is no longer available.", ephemeral=True)
            return
        record = _ticket_record(self.guild_id, self.channel_id)
        if not record:
            await interaction.response.send_message("This ticket is no longer tracked.", ephemeral=True)
            return
        if not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message(_ticket_staff_denial(), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await delete_ticket(interaction.channel, interaction.user, "Deleted manually by ticket staff")
            if not deleted:
                await interaction.followup.send("This ticket is no longer tracked.", ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send("The channel could not be deleted. Check Rallybit's Manage Channels permission.", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Ticket deletion cancelled.", embed=None, view=None)
        self.stop()


class TicketControlView(discord.ui.View):
    def __init__(self, guild_id: int, channel_id: int) -> None:
        super().__init__(timeout=None)
        self.guild_id = guild_id; self.channel_id = channel_id
        for label, emoji, style, action in [
            ("Claim", "🙋", discord.ButtonStyle.secondary, "claim"),
            ("Transcript", "📄", discord.ButtonStyle.secondary, "transcript"),
            ("Close", "🔒", discord.ButtonStyle.danger, "close"),
            ("Delete", "🗑️", discord.ButtonStyle.danger, "delete"),
        ]:
            button = discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"rallybit:ticket:{action}:{guild_id}:{channel_id}")
            button.callback = getattr(self, action)  # type: ignore[assignment]
            self.add_item(button)

    def _record(self) -> dict[str, Any] | None:
        return _open().get(str(self.guild_id), {}).get(str(self.channel_id))

    async def claim(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel): return
        data = _open(); record = data.get(str(self.guild_id), {}).get(str(self.channel_id))
        if not isinstance(record, dict):
            await interaction.response.send_message("This ticket is no longer tracked.", ephemeral=True); return
        if not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message(_ticket_staff_denial(), ephemeral=True); return
        if record.get("claimed_by") and str(record["claimed_by"]) != str(interaction.user.id):
            await interaction.response.send_message(f"This ticket is already claimed by <@{record['claimed_by']}>", ephemeral=True); return
        record.update({"claimed_by": str(interaction.user.id), "status": "Claimed", "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": str(interaction.user.id)})
        data[str(self.guild_id)][str(self.channel_id)] = record; _save_open(data)
        await _send_claim_announcement(interaction)

    async def transcript(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel): return
        record = self._record(); owner_id = int(record.get("owner_id", 0)) if record else 0
        if interaction.user.id != owner_id and not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message("Only the ticket owner or staff can export this transcript.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        file = await _make_transcript(interaction.channel, int(_settings(self.guild_id).get("transcript_limit", 500)))
        await interaction.followup.send(file=file, ephemeral=True)

    async def close(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel): return
        record = self._record(); owner_id = int(record.get("owner_id", 0)) if record else 0
        if interaction.user.id != owner_id and not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message("Only the ticket owner or staff can close this ticket.", ephemeral=True); return
        await interaction.response.send_modal(TicketCloseReasonModal(self.guild_id, self.channel_id))

    async def delete(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel): return
        record = self._record()
        if not record:
            await interaction.response.send_message("This ticket is no longer tracked.", ephemeral=True); return
        if not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message(_ticket_staff_denial(), ephemeral=True); return
        embed = discord.Embed(
            title="Delete this ticket permanently?",
            description="A final transcript will be archived before the channel is deleted. This action cannot be undone.",
            color=RED,
        )
        embed.set_footer(text=f"Case {record.get('case_id') or interaction.channel.name}")
        await interaction.response.send_message(
            embed=embed,
            view=TicketDeleteConfirmView(interaction.user.id, self.guild_id, self.channel_id),
            ephemeral=True,
        )


async def _refresh_ticket_panel_message(
    guild: discord.Guild,
    panel_id: str,
    panel: dict[str, Any],
    view: TicketPanelView,
) -> bool:
    channel = guild.get_channel(int(panel.get("channel_id") or 0))
    if not isinstance(channel, discord.TextChannel) or not str(panel.get("message_id") or "").isdigit():
        return False
    try:
        message = await channel.fetch_message(int(panel["message_id"]))
        await message.edit(content=None, embeds=_ticket_panel_embeds(guild, panel_id, panel), view=view)
        return True
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return False


async def _update_ticket_panel_message(guild: discord.Guild, panel_id: str) -> bool:
    panel = _panels().get(str(guild.id), {}).get(panel_id)
    if not isinstance(panel, dict):
        return False
    view = TicketPanelView(guild.id, panel_id, panel)
    return await _refresh_ticket_panel_message(guild, panel_id, panel, view)


def _has_ticket_controls(message: discord.Message, channel_id: int) -> bool:
    prefix = "rallybit:ticket:"
    suffix = f":{channel_id}"
    return any(
        str(getattr(item, "custom_id", "")).startswith(prefix)
        and str(getattr(item, "custom_id", "")).endswith(suffix)
        for row in message.components
        for item in getattr(row, "children", [])
    )


async def _refresh_ticket_control_message(
    channel: discord.TextChannel,
    view: TicketControlView,
    message_id: Any,
) -> int | None:
    message: discord.Message | None = None
    if str(message_id or "").isdigit():
        try:
            message = await channel.fetch_message(int(message_id))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            message = None
    if message is None:
        try:
            async for candidate in channel.history(limit=100, oldest_first=True):
                if _has_ticket_controls(candidate, channel.id):
                    message = candidate
                    break
        except (discord.Forbidden, discord.HTTPException):
            return None
    if message is None:
        return None
    try:
        await message.edit(view=view)
        return message.id
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return None


async def restore_ticket_views(bot: discord.Client) -> int:
    restored = 0
    panels = _panels()
    for guild_id, guild_panels in panels.items():
        if not str(guild_id).isdigit() or not isinstance(guild_panels, dict): continue
        for panel_id, panel in guild_panels.items():
            if isinstance(panel, dict):
                view = TicketPanelView(int(guild_id), panel_id, panel)
                bot.add_view(view)
                guild = bot.get_guild(int(guild_id))
                if guild:
                    await _refresh_ticket_panel_message(guild, panel_id, panel, view)
                restored += 1
    open_data = _open()
    open_data_changed = False
    for guild_id, channels in open_data.items():
        if not str(guild_id).isdigit() or not isinstance(channels, dict): continue
        for channel_id, record in channels.items():
            if str(channel_id).isdigit():
                numeric_channel_id = int(channel_id)
                view = TicketControlView(int(guild_id), numeric_channel_id)
                bot.add_view(view); restored += 1
                guild = bot.get_guild(int(guild_id))
                channel = guild.get_channel(numeric_channel_id) if guild else None
                if isinstance(record, dict) and isinstance(channel, discord.TextChannel):
                    control_message_id = await _refresh_ticket_control_message(channel, view, record.get("control_message_id"))
                    if control_message_id and str(record.get("control_message_id")) != str(control_message_id):
                        record["control_message_id"] = str(control_message_id)
                        open_data_changed = True
                    if record.get("status") == "Closed" and record.get("delete_at"):
                        _schedule_ticket_deletion(channel, str(record["delete_at"]))
    if open_data_changed:
        _save_open(open_data)
    return restored



async def create_ticket_panel(
    guild: discord.Guild,
    channel: discord.TextChannel,
    category: discord.CategoryChannel,
    name: str,
    title: str = "How can we help?",
    description: str = "Open a private ticket to speak with the support team. Choose the button below when you are ready.",
    support_role: discord.Role | None = None,
    button_label: str = "Open ticket",
    *,
    option_description: str = DEFAULT_OPTION_DESCRIPTION,
    option_emoji: str = "🎫",
    select_placeholder: str = "Select a ticket type…",
    options: list[dict[str, Any]] | None = None,
    color: str = "#7C6CFF",
    author_name: str = "",
    author_icon_url: str = "",
    header_image_url: str = "",
    thumbnail_url: str = "",
    image_url: str = "",
    footer_text: str = "",
    footer_icon_url: str = "",
    show_author: bool = True,
    show_option_details: bool = True,
    show_workload: bool = True,
    show_guidance: bool = True,
    show_timestamp: bool = True,
) -> str:
    media_values = {
        "author icon": author_icon_url,
        "header image": header_image_url,
        "thumbnail": thumbnail_url,
        "body image": image_url,
        "footer icon": footer_icon_url,
    }
    invalid_media = [label for label, value in media_values.items() if str(value or "").strip() and not _safe_media_url(value)]
    if invalid_media:
        raise RuntimeError(f"The {invalid_media[0]} must be a public HTTPS URL.")
    panel_id = uuid.uuid4().hex[:8].upper()
    configured_options = options or [{
        "name": name,
        "description": option_description,
        "emoji": option_emoji,
        "category_id": category.id,
        "support_role_ids": [support_role.id] if support_role else [],
        "ticket_title": f"{name[:100]} ticket",
        "welcome_message": DEFAULT_WELCOME,
    }]
    normalised_options: list[dict[str, Any]] = []
    for index, option in enumerate(configured_options[:MAX_PANEL_OPTIONS], start=1):
        if not isinstance(option, dict):
            continue
        candidate = dict(option)
        candidate.setdefault("category_id", category.id)
        candidate.setdefault("support_role_ids", [support_role.id] if support_role else [])
        if not str(candidate.get("option_id") or "").strip():
            candidate["option_id"] = uuid.uuid4().hex[:8].upper()
        normalised_options.append(_normalise_panel_option(candidate, f"OPTION-{index}"))
    if not normalised_options:
        raise RuntimeError("Add at least one ticket option before publishing the panel.")
    panel = {
        "panel_id": panel_id, "name": name[:80], "channel_id": channel.id, "message_id": None,
        "category_id": category.id, "title": title[:256], "description": description[:4000],
        "button_label": button_label[:80], "button_emoji": option_emoji[:100],
        "support_role_ids": [support_role.id] if support_role else [],
        "ticket_title": f"{name[:80]} ticket", "welcome_message": DEFAULT_WELCOME,
        "select_placeholder": select_placeholder.strip()[:150] or "Select a ticket type…",
        "options": normalised_options,
        "color": f"#{_panel_colour(color):06X}",
        "author_name": author_name.strip()[:256],
        "author_icon_url": _safe_media_url(author_icon_url),
        "header_image_url": _safe_media_url(header_image_url),
        "thumbnail_url": _safe_media_url(thumbnail_url),
        "image_url": _safe_media_url(image_url),
        "footer_text": footer_text.strip()[:2048],
        "footer_icon_url": _safe_media_url(footer_icon_url),
        "show_author": bool(show_author), "show_option_details": bool(show_option_details),
        "show_workload": bool(show_workload), "show_guidance": bool(show_guidance),
        "show_timestamp": bool(show_timestamp),
    }
    view = TicketPanelView(guild.id, panel_id, panel)
    message = await channel.send(embeds=_ticket_panel_embeds(guild, panel_id, panel), view=view)
    panel["message_id"] = message.id
    data = _panels(); data.setdefault(str(guild.id), {})[panel_id] = panel; _save_panels(data)
    from core.bot import client
    client.add_view(view)
    return panel_id


async def update_ticket_panel(
    guild: discord.Guild,
    panel_id: str,
    *,
    name: str,
    title: str,
    description: str,
    options: list[dict[str, Any]],
    select_placeholder: str,
    color: str,
    author_name: str = "",
    author_icon_url: str = "",
    header_image_url: str = "",
    thumbnail_url: str = "",
    image_url: str = "",
    footer_text: str = "",
    footer_icon_url: str = "",
    show_author: bool = True,
    show_option_details: bool = True,
    show_workload: bool = True,
    show_guidance: bool = True,
    show_timestamp: bool = True,
) -> dict[str, Any]:
    panel_id = panel_id.upper()
    data = _panels()
    existing = data.get(str(guild.id), {}).get(panel_id)
    if not isinstance(existing, dict):
        raise RuntimeError("That ticket panel is no longer available.")
    media_values = {
        "author icon": author_icon_url,
        "header image": header_image_url,
        "thumbnail": thumbnail_url,
        "body image": image_url,
        "footer icon": footer_icon_url,
    }
    invalid_media = [
        label
        for label, value in media_values.items()
        if str(value or "").strip() and not _safe_media_url(value)
    ]
    if invalid_media:
        raise RuntimeError(f"The {invalid_media[0]} must be a public HTTPS URL.")
    previous_options = {
        str(option.get("option_id") or "").upper(): option
        for option in _panel_options(existing)
        if str(option.get("option_id") or "").strip()
    }
    normalised_options: list[dict[str, Any]] = []
    for index, option in enumerate(options[:MAX_PANEL_OPTIONS], start=1):
        if not isinstance(option, dict):
            continue
        option_id = str(option.get("option_id") or "").strip().upper()
        candidate = {**previous_options.get(option_id, {}), **option}
        if not str(candidate.get("option_id") or "").strip():
            candidate["option_id"] = uuid.uuid4().hex[:8].upper()
        normalised_options.append(_normalise_panel_option(candidate, f"OPTION-{index}"))
    if not normalised_options:
        raise RuntimeError("A ticket panel must contain at least one dropdown option.")
    previous = deepcopy(existing)
    first_option = normalised_options[0]
    updated = deepcopy(existing)
    updated.update({
        "name": name.strip()[:80] or str(first_option["name"])[:80],
        "category_id": first_option.get("category_id"),
        "title": title.strip()[:256],
        "description": description.strip()[:4000],
        "support_role_ids": list(first_option.get("support_role_ids", [])),
        "select_placeholder": select_placeholder.strip()[:150] or "Select a ticket type…",
        "options": normalised_options,
        "color": f"#{_panel_colour(color):06X}",
        "author_name": author_name.strip()[:256],
        "author_icon_url": _safe_media_url(author_icon_url),
        "header_image_url": _safe_media_url(header_image_url),
        "thumbnail_url": _safe_media_url(thumbnail_url),
        "image_url": _safe_media_url(image_url),
        "footer_text": footer_text.strip()[:2048],
        "footer_icon_url": _safe_media_url(footer_icon_url),
        "show_author": bool(show_author),
        "show_option_details": bool(show_option_details),
        "show_workload": bool(show_workload),
        "show_guidance": bool(show_guidance),
        "show_timestamp": bool(show_timestamp),
    })
    data.setdefault(str(guild.id), {})[panel_id] = updated
    _save_panels(data)
    view = TicketPanelView(guild.id, panel_id, updated)
    if not await _refresh_ticket_panel_message(guild, panel_id, updated, view):
        data[str(guild.id)][panel_id] = previous
        _save_panels(data)
        await _refresh_ticket_panel_message(guild, panel_id, previous, TicketPanelView(guild.id, panel_id, previous))
        raise RuntimeError("Rallybit could not update the original ticket message, so the saved panel was restored.")
    return updated


async def add_ticket_panel_option(
    guild: discord.Guild,
    panel_id: str,
    *,
    name: str,
    description: str,
    emoji: str,
    category: discord.CategoryChannel,
    support_role: discord.Role | None = None,
) -> str:
    data = _panels()
    panel = data.get(str(guild.id), {}).get(panel_id.upper())
    if not isinstance(panel, dict):
        raise RuntimeError("That ticket panel was not found.")
    options = _panel_options(panel)
    if len(options) >= MAX_PANEL_OPTIONS:
        raise RuntimeError("A ticket dropdown can contain at most 25 options.")
    option = _normalise_panel_option({
        "option_id": uuid.uuid4().hex[:8].upper(),
        "name": name,
        "description": description,
        "emoji": emoji,
        "category_id": category.id,
        "support_role_ids": [support_role.id] if support_role else [],
        "ticket_title": f"{name[:100]} ticket",
        "welcome_message": DEFAULT_WELCOME,
    }, f"OPTION-{len(options) + 1}")
    options.append(option)
    panel["options"] = options
    data[str(guild.id)][panel_id.upper()] = panel
    _save_panels(data)
    await _update_ticket_panel_message(guild, panel_id.upper())
    return str(option["option_id"])


async def remove_ticket_panel_option(guild: discord.Guild, panel_id: str, option_id: str) -> bool:
    data = _panels()
    panel = data.get(str(guild.id), {}).get(panel_id.upper())
    if not isinstance(panel, dict):
        raise RuntimeError("That ticket panel was not found.")
    options = _panel_options(panel)
    if len(options) <= 1:
        raise RuntimeError("A ticket panel must keep at least one option.")
    remaining = [option for option in options if str(option["option_id"]).upper() != option_id.upper()]
    if len(remaining) == len(options):
        return False
    panel["options"] = remaining
    data[str(guild.id)][panel_id.upper()] = panel
    _save_panels(data)
    await _update_ticket_panel_message(guild, panel_id.upper())
    return True

def setup_ticket_commands(tree: app_commands.CommandTree) -> None:
    group = app_commands.Group(name="ticket", description="Ticket Tool-style support tickets.")
    panel_group = app_commands.Group(name="panel", description="Create and manage ticket panels.", parent=group)

    @group.command(name="create", description="Create a support ticket with a category and reason.")
    async def create(interaction: discord.Interaction, category: discord.CategoryChannel, reason: str) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this command in a server.", ephemeral=True); return
        if category.id not in _allowed_ticket_category_ids(interaction.guild.id):
            await interaction.response.send_message("That category is not configured for tickets.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        try:
            channel = await create_ticket(interaction.guild, interaction.user, "DIRECT", category, reason)
            await interaction.followup.send(f"Your ticket is ready: {channel.mention}", ephemeral=True)
        except (RuntimeError, discord.HTTPException) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @group.command(name="setup", description="Configure default ticket settings.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def setup(interaction: discord.Interaction, category: discord.CategoryChannel, log_channel: discord.TextChannel | None = None, support_role: discord.Role | None = None, one_ticket_per_member: bool = True) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        cfg = _settings(interaction.guild.id)
        cfg.update({"default_category_id": category.id, "log_channel_id": log_channel.id if log_channel else None, "support_role_ids": [support_role.id] if support_role else [], "one_ticket_per_member": one_ticket_per_member})
        _save_settings(interaction.guild.id, cfg)
        await interaction.response.send_message("Ticket defaults saved.", ephemeral=True)

    @group.command(name="settings", description="Configure ticket categories, staff roles, logs, and transcripts.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def settings(interaction: discord.Interaction, category: discord.CategoryChannel, log_channel: discord.TextChannel | None = None, support_role: discord.Role | None = None, transcript_limit: app_commands.Range[int, 50, 2000] = 500, auto_delete_minutes: app_commands.Range[int, 0, 10080] = 0, one_ticket_per_member: bool = True) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        cfg = _settings(interaction.guild.id)
        cfg.update({
            "default_category_id": category.id,
            "log_channel_id": log_channel.id if log_channel else None,
            "support_role_ids": [support_role.id] if support_role else [],
            "transcript_limit": int(transcript_limit),
            "auto_delete_minutes": int(auto_delete_minutes),
            "one_ticket_per_member": one_ticket_per_member,
        })
        _save_settings(interaction.guild.id, cfg)
        await interaction.response.send_message("Ticket settings saved.", ephemeral=True)

    @panel_group.command(name="create", description="Create and send a ticket panel.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def panel_create(
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
        category: discord.CategoryChannel,
        title: str = "How can we help?",
        description: str = DEFAULT_PANEL_DESCRIPTION,
        support_role: discord.Role | None = None,
        option_description: str = DEFAULT_OPTION_DESCRIPTION,
        option_icon: str = "🎫",
        placeholder: str = "Select a ticket type…",
        header_image: str = "",
        thumbnail: str = "",
        image: str = "",
        footer_text: str = "",
        footer_icon: str = "",
        show_option_details: bool = True,
        show_workload: bool = True,
        show_guidance: bool = True,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        try:
            panel_id = await create_ticket_panel(
                interaction.guild, channel, category, name, title, description, support_role,
                option_description=option_description, option_emoji=option_icon,
                select_placeholder=placeholder, header_image_url=header_image,
                thumbnail_url=thumbnail, image_url=image, footer_text=footer_text,
                footer_icon_url=footer_icon, show_option_details=show_option_details,
                show_workload=show_workload, show_guidance=show_guidance,
            )
            await interaction.followup.send(
                f"Dropdown ticket panel `{panel_id}` created in {channel.mention}. Add more choices with `/ticket panel add-option`.",
                ephemeral=True,
            )
        except (RuntimeError, discord.HTTPException) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @panel_group.command(name="add-option", description="Add another category to an existing ticket dropdown.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def panel_add_option(
        interaction: discord.Interaction,
        panel_id: str,
        name: str,
        description: str,
        category: discord.CategoryChannel,
        icon: str = "🎫",
        support_role: discord.Role | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        try:
            option_id = await add_ticket_panel_option(
                interaction.guild, panel_id, name=name, description=description,
                emoji=icon, category=category, support_role=support_role,
            )
            await interaction.followup.send(
                f"Added **{name[:100]}** (`{option_id}`) to panel `{panel_id.upper()}`.", ephemeral=True,
            )
        except (RuntimeError, discord.HTTPException) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @panel_group.command(name="remove-option", description="Remove a category from a ticket dropdown.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def panel_remove_option(interaction: discord.Interaction, panel_id: str, option_id: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        try:
            removed = await remove_ticket_panel_option(interaction.guild, panel_id, option_id)
            await interaction.followup.send(
                "Ticket option removed." if removed else "That option was not found.", ephemeral=True,
            )
        except (RuntimeError, discord.HTTPException) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @panel_group.command(name="list", description="List ticket panels in this server.")
    async def panel_list(interaction: discord.Interaction) -> None:
        panels = _panels().get(str(interaction.guild.id), {}) if interaction.guild else {}
        lines: list[str] = []
        if isinstance(panels, dict):
            for panel_id, panel in panels.items():
                if not isinstance(panel, dict):
                    continue
                options = _panel_options(panel)
                lines.append(
                    f"`{panel_id}` • **{panel.get('title') or panel.get('name', 'Panel')}** • "
                    f"{len(options)} option(s) • <#{panel.get('channel_id')}>"
                )
                lines.extend(f"↳ `{option['option_id']}` • {option['name']}" for option in options)
        response = "\n".join(lines) if lines else "No ticket panels are configured."
        if len(response) > 1950:
            response = response[:1947].rsplit("\n", 1)[0] + "\n…"
        await interaction.response.send_message(
            response,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @panel_group.command(name="delete", description="Delete a ticket panel configuration.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def panel_delete(interaction: discord.Interaction, panel_id: str, delete_message: bool = False) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        data = _panels(); guild_panels = data.get(str(interaction.guild.id), {}); panel = guild_panels.pop(panel_id.upper(), None) if isinstance(guild_panels, dict) else None
        data[str(interaction.guild.id)] = guild_panels; _save_panels(data)
        if panel and delete_message:
            channel = interaction.guild.get_channel(int(panel.get("channel_id", 0)))
            if isinstance(channel, discord.TextChannel):
                try: message = await channel.fetch_message(int(panel.get("message_id", 0))); await message.delete()
                except discord.HTTPException: pass
        await interaction.response.send_message("Ticket panel deleted." if panel else "That panel was not found.", ephemeral=True)

    @group.command(name="close", description="Close the current ticket.")
    async def close(interaction: discord.Interaction, reason: str) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside a ticket channel.", ephemeral=True); return
        record = _open().get(str(interaction.guild_id), {}).get(str(interaction.channel.id))
        if not isinstance(record, dict):
            await interaction.response.send_message("This is not a tracked ticket.", ephemeral=True); return
        reason = reason.strip()
        if len(reason) < 3 or len(reason) > 1000:
            await interaction.response.send_message("Give a closing reason between 3 and 1,000 characters.", ephemeral=True); return
        if interaction.user.id != int(record.get("owner_id", 0)) and not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message("Only the ticket owner or staff can close it.", ephemeral=True); return
        await interaction.response.send_message("Closing ticket…", ephemeral=True)
        await close_ticket(interaction.channel, interaction.user, reason)

    @group.command(name="claim", description="Claim the current ticket.")
    async def claim(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside a ticket.", ephemeral=True); return
        data = _open(); record = data.get(str(interaction.guild_id), {}).get(str(interaction.channel.id))
        if not isinstance(record, dict):
            await interaction.response.send_message("This is not a tracked ticket.", ephemeral=True); return
        if not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message(_ticket_staff_denial(), ephemeral=True); return
        if record.get("claimed_by") and str(record["claimed_by"]) != str(interaction.user.id):
            await interaction.response.send_message(f"This ticket is already claimed by <@{record['claimed_by']}>.", ephemeral=True); return
        record.update({"claimed_by": str(interaction.user.id), "status": "Claimed", "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": str(interaction.user.id)})
        data[str(interaction.guild_id)][str(interaction.channel.id)] = record; _save_open(data)
        await _send_claim_announcement(interaction)

    @group.command(name="unclaim", description="Remove your assignment from the current ticket.")
    async def unclaim(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside a ticket.", ephemeral=True); return
        data = _open(); record = data.get(str(interaction.guild_id), {}).get(str(interaction.channel.id))
        if not isinstance(record, dict):
            await interaction.response.send_message("This is not a tracked ticket.", ephemeral=True); return
        if not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message(_ticket_staff_denial(), ephemeral=True); return
        if not record.get("claimed_by"):
            await interaction.response.send_message("This ticket is not claimed.", ephemeral=True); return
        record.update({"claimed_by": None, "status": "Open", "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": str(interaction.user.id)})
        data[str(interaction.guild_id)][str(interaction.channel.id)] = record; _save_open(data)
        await interaction.response.send_message("This ticket is unclaimed and available to staff.")

    @group.command(name="priority", description="Set the current ticket priority.")
    @app_commands.choices(priority=[app_commands.Choice(name=value, value=value) for value in TICKET_PRIORITIES])
    async def priority(interaction: discord.Interaction, priority: app_commands.Choice[str]) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside a ticket.", ephemeral=True); return
        data = _open(); record = data.get(str(interaction.guild_id), {}).get(str(interaction.channel.id))
        if not isinstance(record, dict):
            await interaction.response.send_message("This is not a tracked ticket.", ephemeral=True); return
        if not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message(_ticket_staff_denial(), ephemeral=True); return
        record.update({"priority": priority.value, "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": str(interaction.user.id)})
        data[str(interaction.guild_id)][str(interaction.channel.id)] = record; _save_open(data)
        await interaction.response.send_message(f"Ticket priority set to **{priority.value}**.")

    @group.command(name="status", description="View the current ticket status and assignment.")
    async def status(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside a ticket.", ephemeral=True); return
        record = _ticket_record(interaction.guild_id or 0, interaction.channel.id)
        if not record:
            await interaction.response.send_message("This is not a tracked ticket.", ephemeral=True); return
        owner_id = int(record.get("owner_id", 0))
        if interaction.user.id != owner_id and not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message("Only the ticket owner or staff can view this.", ephemeral=True); return
        claimed = f"<@{record['claimed_by']}>" if record.get("claimed_by") else "Unassigned"
        embed = discord.Embed(
            title=f"Ticket status • {record.get('status', 'Open')}",
            description="A current overview of this support request.",
            color=BRAND,
            timestamp=discord.utils.utcnow(),
        )
        icon_url = _set_guild_author(embed, interaction.guild, "Support desk")
        embed.add_field(name="Case ID", value=f"`{record.get('case_id') or interaction.channel.name}`", inline=True)
        embed.add_field(name="Priority", value=str(record.get("priority", "Medium")), inline=True)
        embed.add_field(name="Assigned staff", value=claimed, inline=True)
        embed.add_field(name="Created", value=f"<t:{int(datetime.fromisoformat(record['created_at']).timestamp())}:R>" if record.get("created_at") else "Unknown", inline=False)
        latest_update = record.get("status_note") or record.get("close_reason")
        if latest_update:
            embed.add_field(name="Latest update", value=str(latest_update)[:1024], inline=False)
        if icon_url:
            embed.set_thumbnail(url=icon_url)
        embed.set_footer(text="Rallybit Tickets • Live case status")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="setstatus", description="Update the current ticket workflow status.")
    @app_commands.choices(status=[app_commands.Choice(name=value, value=value) for value in TICKET_STATUSES])
    async def setstatus(interaction: discord.Interaction, status: app_commands.Choice[str], note: str | None = None) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside a ticket.", ephemeral=True); return
        record = _ticket_record(interaction.guild_id or 0, interaction.channel.id)
        if not record:
            await interaction.response.send_message("This is not a tracked ticket.", ephemeral=True); return
        if not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message(_ticket_staff_denial(), ephemeral=True); return
        if status.value == "Closed":
            if not note or len(note.strip()) < 3:
                await interaction.response.send_message("Give a reason when closing a ticket.", ephemeral=True)
                return
            await interaction.response.send_message("Closing ticket...", ephemeral=True)
            await close_ticket(interaction.channel, interaction.user, note.strip())
            return
        data = _open(); record = data[str(interaction.guild_id)][str(interaction.channel.id)]
        record.update({"status": status.value, "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": str(interaction.user.id)})
        if note:
            record["status_note"] = note[:1000]
        if status.value == "Claimed" and not record.get("claimed_by"):
            record["claimed_by"] = str(interaction.user.id)
        data[str(interaction.guild_id)][str(interaction.channel.id)] = record; _save_open(data)
        await interaction.response.send_message(f"Ticket status set to **{status.value}**.")

    @group.command(name="reopen", description="Reopen a previously closed ticket channel.")
    async def reopen(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside a ticket.", ephemeral=True); return
        data = _open(); record = data.get(str(interaction.guild_id), {}).get(str(interaction.channel.id))
        if not isinstance(record, dict):
            await interaction.response.send_message("This is not a tracked ticket.", ephemeral=True); return
        if not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message(_ticket_staff_denial(), ephemeral=True); return
        if record.get("status") != "Closed":
            await interaction.response.send_message("This ticket is not closed.", ephemeral=True); return
        owner = interaction.guild.get_member(int(record.get("owner_id", 0))) if interaction.guild else None
        if owner:
            await interaction.channel.set_permissions(owner, view_channel=True, send_messages=True, read_message_history=True, attach_files=True, reason=f"Ticket reopened by {interaction.user}")
        new_name = interaction.channel.name.removeprefix("closed-")[:100]
        await interaction.channel.edit(name=new_name or "reopened-ticket", reason=f"Ticket reopened by {interaction.user}")
        _cancel_ticket_deletion(interaction.channel.id)
        record.update({"status": "Open", "closed_by": None, "closed_at": None, "close_reason": None, "delete_at": None, "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": str(interaction.user.id)})
        data[str(interaction.guild_id)][str(interaction.channel.id)] = record; _save_open(data)
        await interaction.response.send_message(f"Ticket reopened by {interaction.user.mention}.")

    @group.command(name="add", description="Add a member to the current ticket.")
    async def add(interaction: discord.Interaction, member: discord.Member) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside a ticket.", ephemeral=True); return
        record = _ticket_record(interaction.guild_id or 0, interaction.channel.id)
        if not record:
            await interaction.response.send_message("This is not a tracked ticket.", ephemeral=True); return
        if not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message(_ticket_staff_denial(), ephemeral=True); return
        await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, attach_files=True, reason=f"Added by {interaction.user}")
        await interaction.response.send_message(f"Added {member.mention} to the ticket.")

    @group.command(name="remove", description="Remove a member from the current ticket.")
    async def remove(interaction: discord.Interaction, member: discord.Member) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside a ticket.", ephemeral=True); return
        record = _open().get(str(interaction.guild_id), {}).get(str(interaction.channel.id), {})
        if not record:
            await interaction.response.send_message("This is not a tracked ticket.", ephemeral=True); return
        if not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message(_ticket_staff_denial(), ephemeral=True); return
        if str(record.get("owner_id")) == str(member.id):
            await interaction.response.send_message("The ticket owner cannot be removed.", ephemeral=True); return
        await interaction.channel.set_permissions(member, overwrite=None, reason=f"Removed by {interaction.user}")
        await interaction.response.send_message(f"Removed {member.mention} from the ticket.")

    @group.command(name="rename", description="Rename the current ticket.")
    async def rename(interaction: discord.Interaction, name: str) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside a ticket.", ephemeral=True); return
        record = _ticket_record(interaction.guild_id or 0, interaction.channel.id)
        if not record:
            await interaction.response.send_message("This is not a tracked ticket.", ephemeral=True); return
        if not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message(_ticket_staff_denial(), ephemeral=True); return
        safe = re.sub(r"[^a-zA-Z0-9-_]", "-", name.lower()).strip("-")[:90]
        await interaction.channel.edit(name=safe or interaction.channel.name, reason=f"Renamed by {interaction.user}")
        await interaction.response.send_message(f"Ticket renamed to `{interaction.channel.name}`.", ephemeral=True)

    @group.command(name="transcript", description="Export the current ticket transcript.")
    async def transcript(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside a ticket.", ephemeral=True); return
        record = _open().get(str(interaction.guild_id), {}).get(str(interaction.channel.id), {})
        if not record:
            await interaction.response.send_message("This is not a tracked ticket.", ephemeral=True); return
        if interaction.user.id != int(record.get("owner_id", 0)) and not _is_ticket_staff(interaction.user, record):
            await interaction.response.send_message("Only the ticket owner or staff can export it.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(file=await _make_transcript(interaction.channel, int(_settings(interaction.guild_id).get("transcript_limit", 500))), ephemeral=True)

    tree.add_command(group)
