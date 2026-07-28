from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands

from core.audit import (
    AUDIT_EVENT_LABELS,
    AUDIT_EVENT_TYPES,
    emit_audit_event,
    get_audit_settings,
    save_audit_settings,
)

BRAND = 0x7567EE
EVENT_CHOICES = [app_commands.Choice(name=AUDIT_EVENT_LABELS[event], value=event) for event in AUDIT_EVENT_TYPES]
CHANNEL_CHOICES = [app_commands.Choice(name="Default for every event", value="default"), *EVENT_CHOICES]


def _changed(before: Any, after: Any, attribute: str) -> bool:
    return getattr(before, attribute, None) != getattr(after, attribute, None)


def _message_snapshot(message: discord.Message) -> str:
    content = str(message.content or "").strip()
    if not content:
        content = "No text content"
    if message.attachments:
        content += f"\nAttachments: {len(message.attachments)}"
    return content[:1000]


async def _recent_audit_actor(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    target_id: int,
) -> tuple[discord.abc.User | None, str | None]:
    """Resolve who performed a native Discord change when audit-log access exists."""
    await asyncio.sleep(0.65)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=20)
    try:
        async for entry in guild.audit_logs(limit=8, action=action, after=cutoff):
            if int(getattr(entry.target, "id", 0) or 0) == int(target_id):
                return entry.user, entry.reason
    except (discord.Forbidden, discord.HTTPException):
        pass
    return None, None


def _reason_field(reason: str | None) -> tuple[tuple[str, str, bool], ...]:
    return (("Audit reason", reason, False),) if reason else ()


async def on_message_delete(message: discord.Message) -> None:
    if message.guild is None or message.author.bot:
        return
    await emit_audit_event(
        message.guild,
        "messages",
        "Message deleted",
        _message_snapshot(message),
        target=f"Message `{message.id}` by {message.author} (`{message.author.id}`)",
        channel=message.channel,
    )


async def on_message_edit(before: discord.Message, after: discord.Message) -> None:
    if before.guild is None or before.author.bot or before.content == after.content:
        return
    await emit_audit_event(
        before.guild,
        "messages",
        "Message edited",
        f"[Open message]({after.jump_url})",
        actor=before.author,
        target=f"Message `{before.id}`",
        channel=before.channel,
        fields=(("Before", _message_snapshot(before), False), ("After", _message_snapshot(after), False)),
    )


async def on_bulk_message_delete(messages: list[discord.Message]) -> None:
    visible = [message for message in messages if message.guild is not None and not message.author.bot]
    if not visible:
        return
    first = visible[0]
    await emit_audit_event(
        first.guild,
        "messages",
        "Messages bulk deleted",
        f"**{len(visible)}** cached messages were removed in one bulk action.",
        target=f"Messages in #{getattr(first.channel, 'name', 'unknown')}",
        channel=first.channel,
    )


async def on_member_join(member: discord.Member) -> None:
    await emit_audit_event(
        member.guild,
        "members",
        "Member joined",
        f"{member.mention} joined the server.",
        target=f"{member} (`{member.id}`)",
        fields=(("Account created", discord.utils.format_dt(member.created_at, "R"), True),),
    )


async def on_member_remove(member: discord.Member) -> None:
    actor, reason = await _recent_audit_actor(member.guild, discord.AuditLogAction.kick, member.id)
    removed_by_staff = actor is not None
    await emit_audit_event(
        member.guild,
        "moderation" if removed_by_staff else "members",
        "Member kicked" if removed_by_staff else "Member left",
        f"**{member}** was kicked from the server." if removed_by_staff else f"**{member}** left the server.",
        actor=actor,
        target=f"{member} (`{member.id}`)",
        fields=_reason_field(reason),
    )


async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    fields: list[tuple[str, Any, bool]] = []
    if before.display_name != after.display_name:
        fields.append(("Display name", f"`{before.display_name}` → `{after.display_name}`", False))
    before_roles = {role.id: role for role in before.roles}
    after_roles = {role.id: role for role in after.roles}
    added = [role.mention for role_id, role in after_roles.items() if role_id not in before_roles]
    removed = [role.mention for role_id, role in before_roles.items() if role_id not in after_roles]
    if added:
        fields.append(("Roles added", " ".join(added), False))
    if removed:
        fields.append(("Roles removed", " ".join(removed), False))
    if before.timed_out_until != after.timed_out_until:
        fields.append(("Timeout", str(after.timed_out_until or "Removed"), False))
    if not fields:
        return
    action = discord.AuditLogAction.member_role_update if added or removed else discord.AuditLogAction.member_update
    actor, reason = await _recent_audit_actor(after.guild, action, after.id)
    fields.extend(_reason_field(reason))
    await emit_audit_event(
        after.guild,
        "members",
        "Member updated",
        f"Changes were detected for {after.mention}.",
        actor=actor,
        target=f"{after} (`{after.id}`)",
        fields=fields,
    )


async def on_member_ban(guild: discord.Guild, user: discord.User) -> None:
    actor, reason = await _recent_audit_actor(guild, discord.AuditLogAction.ban, user.id)
    await emit_audit_event(guild, "moderation", "Member banned", f"**{user}** was banned.", actor=actor, target=f"{user} (`{user.id}`)", fields=_reason_field(reason))


async def on_member_unban(guild: discord.Guild, user: discord.User) -> None:
    actor, reason = await _recent_audit_actor(guild, discord.AuditLogAction.unban, user.id)
    await emit_audit_event(guild, "moderation", "Member unbanned", f"**{user}** was unbanned.", actor=actor, target=f"{user} (`{user.id}`)", fields=_reason_field(reason))


async def on_guild_role_create(role: discord.Role) -> None:
    actor, reason = await _recent_audit_actor(role.guild, discord.AuditLogAction.role_create, role.id)
    await emit_audit_event(role.guild, "roles", "Role created", f"{role.mention} was created.", actor=actor, target=f"{role.name} (`{role.id}`)", fields=_reason_field(reason))


async def on_guild_role_delete(role: discord.Role) -> None:
    actor, reason = await _recent_audit_actor(role.guild, discord.AuditLogAction.role_delete, role.id)
    await emit_audit_event(role.guild, "roles", "Role deleted", f"**{role.name}** was deleted.", actor=actor, target=f"{role.name} (`{role.id}`)", fields=_reason_field(reason))


async def on_guild_role_update(before: discord.Role, after: discord.Role) -> None:
    fields = []
    for attribute, label in (("name", "Name"), ("colour", "Colour"), ("permissions", "Permissions"), ("mentionable", "Mentionable"), ("hoist", "Displayed separately")):
        if _changed(before, after, attribute):
            fields.append((label, f"`{getattr(before, attribute)}` → `{getattr(after, attribute)}`", False))
    if fields:
        actor, reason = await _recent_audit_actor(after.guild, discord.AuditLogAction.role_update, after.id)
        fields.extend(_reason_field(reason))
        await emit_audit_event(after.guild, "roles", "Role updated", f"Changes were detected for {after.mention}.", actor=actor, target=f"{after.name} (`{after.id}`)", fields=fields)


async def on_guild_channel_create(channel: discord.abc.GuildChannel) -> None:
    actor, reason = await _recent_audit_actor(channel.guild, discord.AuditLogAction.channel_create, channel.id)
    await emit_audit_event(channel.guild, "channels", "Channel created", f"{channel.mention} was created.", actor=actor, target=f"{channel.name} (`{channel.id}`)", channel=channel, fields=_reason_field(reason))


async def on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
    actor, reason = await _recent_audit_actor(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
    await emit_audit_event(channel.guild, "channels", "Channel deleted", f"**{channel.name}** was deleted.", actor=actor, target=f"{channel.name} (`{channel.id}`)", fields=_reason_field(reason))


async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
    fields = []
    for attribute, label in (("name", "Name"), ("category_id", "Category"), ("position", "Position"), ("slowmode_delay", "Slowmode")):
        if _changed(before, after, attribute):
            fields.append((label, f"`{getattr(before, attribute, None)}` → `{getattr(after, attribute, None)}`", False))
    if fields:
        actor, reason = await _recent_audit_actor(after.guild, discord.AuditLogAction.channel_update, after.id)
        fields.extend(_reason_field(reason))
        await emit_audit_event(after.guild, "channels", "Channel updated", f"Changes were detected for {after.mention}.", actor=actor, target=f"{after.name} (`{after.id}`)", channel=after, fields=fields)


async def on_thread_create(thread: discord.Thread) -> None:
    actor, reason = await _recent_audit_actor(thread.guild, discord.AuditLogAction.thread_create, thread.id)
    await emit_audit_event(thread.guild, "channels", "Thread created", f"{thread.mention} was created.", actor=actor, target=f"{thread.name} (`{thread.id}`)", channel=thread, fields=_reason_field(reason))


async def on_thread_delete(thread: discord.Thread) -> None:
    actor, reason = await _recent_audit_actor(thread.guild, discord.AuditLogAction.thread_delete, thread.id)
    await emit_audit_event(thread.guild, "channels", "Thread deleted", f"**{thread.name}** was deleted.", actor=actor, target=f"{thread.name} (`{thread.id}`)", fields=_reason_field(reason))


async def on_thread_update(before: discord.Thread, after: discord.Thread) -> None:
    fields = []
    for attribute, label in (("name", "Name"), ("archived", "Archived"), ("locked", "Locked"), ("slowmode_delay", "Slowmode")):
        if _changed(before, after, attribute):
            fields.append((label, f"`{getattr(before, attribute, None)}` → `{getattr(after, attribute, None)}`", False))
    if not fields:
        return
    actor, reason = await _recent_audit_actor(after.guild, discord.AuditLogAction.thread_update, after.id)
    fields.extend(_reason_field(reason))
    await emit_audit_event(after.guild, "channels", "Thread updated", f"Changes were detected for {after.mention}.", actor=actor, target=f"{after.name} (`{after.id}`)", channel=after, fields=fields)


def _inventory_changes(before: Any, after: Any) -> tuple[str, str]:
    before_by_id = {int(item.id): item for item in before}
    after_by_id = {int(item.id): item for item in after}
    added = [str(item.name) for item_id, item in after_by_id.items() if item_id not in before_by_id]
    removed = [str(item.name) for item_id, item in before_by_id.items() if item_id not in after_by_id]
    renamed = [f"{before_by_id[item_id].name} → {item.name}" for item_id, item in after_by_id.items() if item_id in before_by_id and before_by_id[item_id].name != item.name]
    summary = []
    if added:
        summary.append(f"Added: {', '.join(added[:20])}")
    if removed:
        summary.append(f"Removed: {', '.join(removed[:20])}")
    if renamed:
        summary.append(f"Renamed: {', '.join(renamed[:20])}")
    return "\n".join(summary), "added" if added and not removed and not renamed else "removed" if removed and not added and not renamed else "updated"


async def on_guild_emojis_update(guild: discord.Guild, before: Any, after: Any) -> None:
    summary, action = _inventory_changes(before, after)
    if summary:
        await emit_audit_event(guild, "configuration", "Emoji inventory updated", summary, target=f"Server emojis ({action})")


async def on_guild_stickers_update(guild: discord.Guild, before: Any, after: Any) -> None:
    summary, action = _inventory_changes(before, after)
    if summary:
        await emit_audit_event(guild, "configuration", "Sticker inventory updated", summary, target=f"Server stickers ({action})")


async def on_invite_create(invite: discord.Invite) -> None:
    guild = invite.guild
    if not isinstance(guild, discord.Guild):
        return
    await emit_audit_event(guild, "security", "Invite created", f"Invite `{invite.code}` was created.", actor=invite.inviter, target=f"discord.gg/{invite.code}", channel=invite.channel)


async def on_invite_delete(invite: discord.Invite) -> None:
    guild = invite.guild
    if not isinstance(guild, discord.Guild):
        return
    await emit_audit_event(guild, "security", "Invite deleted", f"Invite `{invite.code}` was deleted or expired.", target=f"discord.gg/{invite.code}", channel=invite.channel)


async def on_webhooks_update(channel: discord.abc.GuildChannel) -> None:
    await emit_audit_event(channel.guild, "configuration", "Webhooks updated", f"A webhook was created, changed or deleted in {channel.mention}.", target=f"#{channel.name}", channel=channel)


async def on_guild_update(before: discord.Guild, after: discord.Guild) -> None:
    fields = []
    for attribute, label in (("name", "Name"), ("description", "Description"), ("verification_level", "Verification level"), ("default_notifications", "Default notifications"), ("explicit_content_filter", "Content filter")):
        if _changed(before, after, attribute):
            fields.append((label, f"`{getattr(before, attribute, None)}` → `{getattr(after, attribute, None)}`", False))
    if not fields:
        return
    actor, reason = await _recent_audit_actor(after, discord.AuditLogAction.guild_update, after.id)
    fields.extend(_reason_field(reason))
    await emit_audit_event(after, "configuration", "Server settings updated", "The Discord server profile or safety settings changed.", actor=actor, target=f"{after.name} (`{after.id}`)", fields=fields)


async def on_scheduled_event_create(event: discord.ScheduledEvent) -> None:
    await emit_audit_event(event.guild, "configuration", "Scheduled event created", f"**{event.name}** was created.", target=f"{event.name} (`{event.id}`)")


async def on_scheduled_event_delete(event: discord.ScheduledEvent) -> None:
    await emit_audit_event(event.guild, "configuration", "Scheduled event deleted", f"**{event.name}** was deleted.", target=f"{event.name} (`{event.id}`)")


async def on_scheduled_event_update(before: discord.ScheduledEvent, after: discord.ScheduledEvent) -> None:
    fields = []
    for attribute, label in (("name", "Name"), ("status", "Status"), ("start_time", "Start"), ("end_time", "End"), ("channel_id", "Channel")):
        if _changed(before, after, attribute):
            fields.append((label, f"`{getattr(before, attribute, None)}` → `{getattr(after, attribute, None)}`", False))
    if fields:
        await emit_audit_event(after.guild, "configuration", "Scheduled event updated", f"Changes were detected for **{after.name}**.", target=f"{after.name} (`{after.id}`)", fields=fields)


async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
    if before.channel == after.channel and before.self_mute == after.self_mute and before.self_deaf == after.self_deaf:
        return
    if before.channel != after.channel:
        if before.channel is None:
            description = f"{member.mention} joined {after.channel.mention}."
        elif after.channel is None:
            description = f"{member.mention} left **{before.channel.name}**."
        else:
            description = f"{member.mention} moved from **{before.channel.name}** to {after.channel.mention}."
    else:
        description = f"{member.mention} changed their voice state in {after.channel.mention}."
    await emit_audit_event(member.guild, "voice", "Voice state changed", description, actor=member, target=f"{member} (`{member.id}`)", channel=after.channel or before.channel)


def setup_audit_log_commands(tree: app_commands.CommandTree) -> None:
    group = app_commands.Group(name="logs", description="Configure complete Dyno-style server action logging.")

    @group.command(name="channel", description="Choose the default or event-specific log channel.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(event=CHANNEL_CHOICES)
    async def log_channel(
        interaction: discord.Interaction,
        event: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        settings = get_audit_settings(interaction.guild.id)
        if event.value == "default":
            settings["default_channel_id"] = str(channel.id) if channel else None
        elif channel:
            settings["channel_ids"][event.value] = str(channel.id)
        else:
            settings["channel_ids"].pop(event.value, None)
        save_audit_settings(interaction.guild.id, settings)
        destination = channel.mention if channel else "the inherited default" if event.value != "default" else "disabled"
        await interaction.response.send_message(f"**{event.name}** logging now uses {destination}.", ephemeral=True)
        await emit_audit_event(interaction.guild, "configuration", "Logging channel updated", f"{event.name} logging now uses {destination}.", actor=interaction.user, channel=interaction.channel)

    @group.command(name="toggle", description="Enable or disable one category of event logging.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(event=EVENT_CHOICES)
    async def log_toggle(interaction: discord.Interaction, event: app_commands.Choice[str], enabled: bool) -> None:
        assert interaction.guild is not None
        settings = get_audit_settings(interaction.guild.id)
        settings["enabled_events"][event.value] = enabled
        save_audit_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"**{event.name}** logging is now **{'enabled' if enabled else 'disabled'}**.", ephemeral=True)

    @group.command(name="overview", description="View every configured logging category and destination.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def log_overview(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        settings = get_audit_settings(interaction.guild.id)
        default_id = settings.get("default_channel_id")
        lines = []
        for event in AUDIT_EVENT_TYPES:
            enabled = settings["enabled_events"].get(event, True)
            channel_id = settings["channel_ids"].get(event) or default_id
            destination = f"<#{channel_id}>" if channel_id else "Not configured"
            lines.append(f"{'✅' if enabled else '❎'} **{AUDIT_EVENT_LABELS[event]}** · {destination}")
        embed = discord.Embed(title="Rallybit logging overview", description="\n".join(lines), colour=BRAND)
        embed.set_footer(text="Event-specific channels override the default log channel.")
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @group.command(name="test", description="Send a test entry through the configured logging system.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(event=EVENT_CHOICES)
    async def log_test(interaction: discord.Interaction, event: app_commands.Choice[str]) -> None:
        assert interaction.guild is not None
        delivered = await emit_audit_event(interaction.guild, event.value, "Logging test", f"{event.name} logging is working.", actor=interaction.user, channel=interaction.channel)
        await interaction.response.send_message("The test log was delivered." if delivered else "The event was recorded, but no usable Discord log channel is configured for it.", ephemeral=True)

    tree.add_command(group)
