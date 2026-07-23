from __future__ import annotations

import io
import json
import re
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands

from commands.moderation import can_use_moderation_action, moderation_denial
from core.logging import log_server_event


BRAND = 0x5865F2
ARCHIVE_PERMISSION_ACTION = "warn"
MAX_ARCHIVE_PART_BYTES = 7_500_000
MESSAGE_ID_PATTERN = re.compile(r"(?:^|/)(\d{15,22})/?$")


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._").lower()
    return cleaned[:60] or "channel"


def _indented(value: str) -> str:
    return "\n".join(f"    {line}" for line in value.splitlines())


def _message_block(message: discord.Message) -> str:
    created = message.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    author_name = getattr(message.author, "display_name", str(message.author))
    username = str(message.author)
    lines = [
        f"[{created}] {author_name} ({username}, ID: {message.author.id})",
        f"Message ID: {message.id}",
        f"Message link: {message.jump_url}",
    ]
    if message.edited_at:
        edited = message.edited_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines.append(f"Edited: {edited}")
    if message.reference and message.reference.message_id:
        lines.append(f"Reply to message ID: {message.reference.message_id}")

    content = message.content or message.system_content
    lines.append("Content:")
    lines.append(_indented(content) if content else "    (no text content)")

    if message.attachments:
        lines.append("Attachments:")
        for attachment in message.attachments:
            lines.append(
                f"    {attachment.filename} ({attachment.size:,} bytes, {attachment.content_type or 'unknown type'}): {attachment.url}"
            )
    if message.stickers:
        lines.append("Stickers:")
        for sticker in message.stickers:
            lines.append(f"    {sticker.name} (ID: {sticker.id}): {sticker.url}")
    if message.embeds:
        lines.append("Embeds:")
        for index, embed in enumerate(message.embeds, start=1):
            serialized = json.dumps(embed.to_dict(), ensure_ascii=False, separators=(",", ":"))
            lines.append(f"    Embed {index}: {serialized}")
    if message.reactions:
        reactions = ", ".join(f"{reaction.emoji} x{reaction.count}" for reaction in message.reactions)
        lines.append(f"Reactions: {reactions}")
    if getattr(message, "poll", None):
        lines.append(f"Poll: {message.poll}")

    lines.append("-" * 88)
    return "\n".join(lines) + "\n"


def _archive_header(
    channel: discord.TextChannel,
    archived_at: datetime,
    archived_by: discord.Member,
    message_count: int,
    part_number: int,
    part_count: int,
) -> bytes:
    header = [
        "Rallybit channel archive",
        f"Server: {channel.guild.name} (ID: {channel.guild.id})",
        f"Channel: #{channel.name} (ID: {channel.id})",
        f"Archived at: {archived_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Archived by: {archived_by} (ID: {archived_by.id})",
        f"Messages archived: {message_count:,}",
        f"Archive part: {part_number}/{part_count}",
        "=" * 88,
        "",
    ]
    return "\n".join(header).encode("utf-8")


async def _build_archive(
    channel: discord.TextChannel,
    archived_at: datetime,
    archived_by: discord.Member,
    max_part_bytes: int,
) -> tuple[int, list[bytes]]:
    body_limit = max(250_000, max_part_bytes - 4_000)
    bodies: list[bytearray] = [bytearray()]
    message_count = 0

    async for message in channel.history(limit=None, oldest_first=True):
        block = _message_block(message).encode("utf-8")
        if bodies[-1] and len(bodies[-1]) + len(block) > body_limit:
            bodies.append(bytearray())
        bodies[-1].extend(block)
        message_count += 1

    part_count = len(bodies)
    parts = [
        _archive_header(channel, archived_at, archived_by, message_count, index, part_count) + bytes(body)
        for index, body in enumerate(bodies, start=1)
    ]
    return message_count, parts


def _archive_file(payload: bytes, channel_name: str, archived_at: datetime, part: int, total: int) -> discord.File:
    timestamp = archived_at.strftime("%Y%m%d-%H%M%S")
    filename = f"channel-archive-{_safe_filename(channel_name)}-{timestamp}-part-{part:02d}-of-{total:02d}.txt"
    return discord.File(io.BytesIO(payload), filename=filename)


def _archive_embed(
    source: discord.TextChannel,
    archived_at: datetime,
    archived_by: discord.Member,
    message_count: int,
    part_count: int,
) -> discord.Embed:
    embed = discord.Embed(
        title="Channel archived",
        description="The complete available message history has been exported and attached.",
        color=BRAND,
        timestamp=archived_at,
    )
    embed.add_field(name="Channel", value=f"{source.mention}\n`#{source.name}` · `{source.id}`", inline=True)
    embed.add_field(name="Archived by", value=f"{archived_by.mention}\n`{archived_by.id}`", inline=True)
    embed.add_field(name="Archived", value=discord.utils.format_dt(archived_at, "F"), inline=False)
    embed.add_field(name="Messages", value=f"{message_count:,}", inline=True)
    embed.add_field(name="Transcript files", value=str(part_count), inline=True)
    embed.set_footer(text=f"{source.guild.name} • Rallybit channel archive")
    return embed


async def _publish_archive(
    destination: discord.TextChannel | discord.ForumChannel,
    source: discord.TextChannel,
    archived_at: datetime,
    archived_by: discord.Member,
    message_count: int,
    parts: list[bytes],
) -> str:
    embed = _archive_embed(source, archived_at, archived_by, message_count, len(parts))
    allowed_mentions = discord.AllowedMentions.none()
    first_file = _archive_file(parts[0], source.name, archived_at, 1, len(parts))

    if isinstance(destination, discord.ForumChannel):
        result = await destination.create_thread(
            name=f"Archive — {source.name} — {archived_at:%Y-%m-%d}",
            embed=embed,
            file=first_file,
            allowed_mentions=allowed_mentions,
            reason=f"Channel archived by {archived_by}",
        )
        target = result.thread
        archive_url = result.message.jump_url
    else:
        result = await destination.send(embed=embed, file=first_file, allowed_mentions=allowed_mentions)
        target = destination
        archive_url = result.jump_url

    for index, payload in enumerate(parts[1:], start=2):
        await target.send(
            content=f"Archive part {index}/{len(parts)} for `#{source.name}`.",
            file=_archive_file(payload, source.name, archived_at, index, len(parts)),
            allowed_mentions=allowed_mentions,
        )
    return archive_url


def _message_id(value: str) -> int | None:
    match = MESSAGE_ID_PATTERN.search(value.strip())
    return int(match.group(1)) if match else None


async def _range_messages(
    channel: discord.TextChannel,
    start_message_id: int,
    finish_message_id: int,
) -> list[discord.Message]:
    start_message = await channel.fetch_message(start_message_id)
    finish_message = start_message if finish_message_id == start_message_id else await channel.fetch_message(finish_message_id)
    older, newer = sorted((start_message, finish_message), key=lambda message: message.id)
    if older.id == newer.id:
        return [older]

    messages = [older]
    async for message in channel.history(
        limit=None,
        after=discord.Object(id=older.id),
        before=discord.Object(id=newer.id),
        oldest_first=True,
    ):
        messages.append(message)
    messages.append(newer)
    return messages


async def _member_messages(
    channel: discord.TextChannel,
    member: discord.Member,
    amount: int | None,
) -> list[discord.Message]:
    messages: list[discord.Message] = []
    async for message in channel.history(limit=None):
        if message.author.id != member.id:
            continue
        messages.append(message)
        if amount is not None and len(messages) >= amount:
            break
    return messages


async def _latest_messages(channel: discord.TextChannel, amount: int) -> list[discord.Message]:
    return [message async for message in channel.history(limit=amount)]


async def _delete_message_set(
    channel: discord.TextChannel,
    messages: list[discord.Message],
    reason: str,
) -> tuple[int, int]:
    unique_messages = list({message.id: message for message in messages}.values())
    bulk_cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    recent = [message for message in unique_messages if message.created_at > bulk_cutoff]
    older = [message for message in unique_messages if message.created_at <= bulk_cutoff]
    deleted = 0
    failed = 0

    for offset in range(0, len(recent), 100):
        batch = recent[offset:offset + 100]
        if len(batch) == 1:
            try:
                await batch[0].delete()
                deleted += 1
            except discord.NotFound:
                failed += 1
            except discord.Forbidden:
                raise
            except discord.HTTPException:
                failed += 1
            continue
        try:
            await channel.delete_messages(batch, reason=reason)
            deleted += len(batch)
        except discord.Forbidden:
            raise
        except discord.HTTPException:
            for message in batch:
                try:
                    await message.delete()
                    deleted += 1
                except discord.NotFound:
                    failed += 1
                except discord.Forbidden:
                    raise
                except discord.HTTPException:
                    failed += 1

    for message in older:
        try:
            await message.delete()
            deleted += 1
        except discord.NotFound:
            failed += 1
        except discord.Forbidden:
            raise
        except discord.HTTPException:
            failed += 1
    return deleted, failed


def setup_channel_archive_commands(tree: app_commands.CommandTree) -> None:
    group = app_commands.Group(
        name="channel",
        description="Archive and manage server channels.",
        guild_only=True,
        default_permissions=discord.Permissions(manage_messages=True),
    )

    @group.command(name="transcript", description="Archive every available message from a channel.")
    @app_commands.describe(
        source="Text channel whose complete history should be archived",
        destination="Text or forum channel that should receive the archive",
    )
    async def channel_transcript(
        interaction: discord.Interaction,
        source: discord.TextChannel,
        destination: discord.TextChannel | discord.ForumChannel,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this command in a server.", ephemeral=True)
            return
        if source.guild.id != interaction.guild.id or destination.guild.id != interaction.guild.id:
            await interaction.response.send_message("Both channels must belong to this server.", ephemeral=True)
            return
        if not can_use_moderation_action(interaction.user, ARCHIVE_PERMISSION_ACTION):
            await interaction.response.send_message(moderation_denial(ARCHIVE_PERMISSION_ACTION), ephemeral=True)
            return

        actor_source_permissions = source.permissions_for(interaction.user)
        actor_destination_permissions = destination.permissions_for(interaction.user)
        if not actor_source_permissions.view_channel or not actor_source_permissions.read_message_history:
            await interaction.response.send_message("You must be able to view and read the source channel's history.", ephemeral=True)
            return
        if not actor_destination_permissions.view_channel:
            await interaction.response.send_message("You must be able to view the archive destination.", ephemeral=True)
            return

        bot_member = interaction.guild.me
        if bot_member is None:
            await interaction.response.send_message("I could not resolve my server permissions.", ephemeral=True)
            return
        source_permissions = source.permissions_for(bot_member)
        destination_permissions = destination.permissions_for(bot_member)
        if not source_permissions.view_channel or not source_permissions.read_message_history:
            await interaction.response.send_message(
                "I need View Channel and Read Message History in the source channel.", ephemeral=True
            )
            return
        if isinstance(destination, discord.ForumChannel):
            can_publish = (
                destination_permissions.view_channel
                and destination_permissions.create_public_threads
                and destination_permissions.send_messages_in_threads
                and destination_permissions.attach_files
            )
            permission_help = "View Channel, Create Public Threads, Send Messages in Threads, and Attach Files"
        else:
            can_publish = (
                destination_permissions.view_channel
                and destination_permissions.send_messages
                and destination_permissions.attach_files
            )
            permission_help = "View Channel, Send Messages, and Attach Files"
        if not can_publish:
            await interaction.response.send_message(
                f"I need {permission_help} in the archive destination.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        archived_at = datetime.now(timezone.utc)
        max_part_bytes = min(MAX_ARCHIVE_PART_BYTES, max(500_000, interaction.guild.filesize_limit - 250_000))
        try:
            message_count, parts = await _build_archive(source, archived_at, interaction.user, max_part_bytes)
            archive_url = await _publish_archive(
                destination, source, archived_at, interaction.user, message_count, parts
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "I lost access while creating the archive. Check my permissions in both selected channels.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Discord rejected the archive upload (`{exc.code}`). Try again or check the destination's file permissions.",
                ephemeral=True,
            )
            return

        log_server_event(
            interaction.guild.id,
            f"Channel archive: source={source.id} destination={destination.id} messages={message_count} archived_by={interaction.user.id}",
        )
        await interaction.followup.send(
            f"Archived **{message_count:,}** messages from {source.mention} in [the selected destination]({archive_url}).",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @group.command(name="purge", description="Delete messages by range, member, or amount.")
    @app_commands.describe(
        channel="Channel to purge; defaults to the current channel",
        start_message_id="First message ID or message link in an inclusive range",
        finish_message_id="Last message ID or message link in an inclusive range",
        member="Delete messages sent by this member",
        amount="Latest messages to delete, or maximum messages when a member is selected",
        reason="Reason recorded in the server audit log",
    )
    async def channel_purge(
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        start_message_id: str | None = None,
        finish_message_id: str | None = None,
        member: discord.Member | None = None,
        amount: app_commands.Range[int, 1, 1000] | None = None,
        reason: app_commands.Range[str, 1, 400] | None = None,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this command in a server.", ephemeral=True)
            return
        if not can_use_moderation_action(interaction.user, ARCHIVE_PERMISSION_ACTION):
            await interaction.response.send_message(moderation_denial(ARCHIVE_PERMISSION_ACTION), ephemeral=True)
            return

        target_channel = channel or (interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None)
        if target_channel is None or target_channel.guild.id != interaction.guild.id:
            await interaction.response.send_message("Run this in a text channel or select the channel to purge.", ephemeral=True)
            return

        range_requested = bool(start_message_id or finish_message_id)
        if range_requested:
            if not start_message_id or not finish_message_id:
                await interaction.response.send_message(
                    "Provide both `start_message_id` and `finish_message_id` for a range purge.", ephemeral=True
                )
                return
            if member is not None or amount is not None:
                await interaction.response.send_message(
                    "A message-ID range cannot be combined with `member` or `amount`.", ephemeral=True
                )
                return
        elif member is None and amount is None:
            await interaction.response.send_message(
                "Choose a start/finish message range, a member, or an amount to purge.", ephemeral=True
            )
            return

        actor_permissions = target_channel.permissions_for(interaction.user)
        bot_member = interaction.guild.me
        bot_permissions = target_channel.permissions_for(bot_member) if bot_member else None
        if not actor_permissions.view_channel or not actor_permissions.read_message_history:
            await interaction.response.send_message(
                "You must be able to view and read the selected channel's history.", ephemeral=True
            )
            return
        if not bot_permissions or not (
            bot_permissions.view_channel and bot_permissions.read_message_history and bot_permissions.manage_messages
        ):
            await interaction.response.send_message(
                "I need View Channel, Read Message History, and Manage Messages in the selected channel.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if range_requested:
                start_id = _message_id(start_message_id or "")
                finish_id = _message_id(finish_message_id or "")
                if start_id is None or finish_id is None:
                    await interaction.followup.send(
                        "One of those message IDs or links is invalid. Copy each message ID again and retry.", ephemeral=True
                    )
                    return
                messages = await _range_messages(target_channel, start_id, finish_id)
                mode = f"inclusive range {start_id}–{finish_id}"
            elif member is not None:
                messages = await _member_messages(target_channel, member, int(amount) if amount is not None else None)
                mode = f"member {member.id}" + (f", latest {int(amount)} matching" if amount is not None else ", all matching")
            else:
                messages = await _latest_messages(target_channel, int(amount or 0))
                mode = f"latest {int(amount or 0)}"
        except discord.NotFound:
            await interaction.followup.send(
                "I could not find both range messages in the selected channel. Make sure the IDs came from that channel.",
                ephemeral=True,
            )
            return
        except discord.Forbidden:
            await interaction.followup.send(
                "I could not read the selected channel's history. Check my channel permissions.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Discord rejected the message lookup (`{exc.code}`). Please try again.", ephemeral=True
            )
            return

        if not messages:
            await interaction.followup.send("No matching messages were found, so nothing was deleted.", ephemeral=True)
            return

        await interaction.edit_original_response(content=f"Deleting **{len(messages):,}** matching messages from {target_channel.mention}…")
        audit_reason = f"{(reason or 'Channel purge').strip()} | Requested by {interaction.user} ({interaction.user.id})"[:512]
        try:
            deleted, failed = await _delete_message_set(target_channel, messages, audit_reason)
        except discord.Forbidden:
            await interaction.edit_original_response(
                content="The purge stopped because I no longer have permission to delete messages in that channel."
            )
            return

        log_server_event(
            interaction.guild.id,
            f"Channel purge: channel={target_channel.id} mode={mode} matched={len(messages)} deleted={deleted} failed={failed} moderator={interaction.user.id} reason={(reason or 'No reason provided')[:160]}",
        )
        result = f"Deleted **{deleted:,}** messages from {target_channel.mention}."
        if failed:
            result += f" **{failed:,}** messages could not be deleted."
        await interaction.edit_original_response(content=result)

    tree.add_command(group)
