from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

import discord
from discord import app_commands

BRAND = 0x5865F2
MAX_SNIPES = 10
TTL_SECONDS = 2 * 60 * 60


@dataclass
class DeletedSnipe:
    author_id: int
    author_name: str
    avatar_url: str
    content: str
    attachments: list[str]
    created_at: float
    deleted_at: float


@dataclass
class EditedSnipe:
    author_id: int
    author_name: str
    avatar_url: str
    before: str
    after: str
    edited_at: float


deleted: dict[int, deque[DeletedSnipe]] = defaultdict(lambda: deque(maxlen=MAX_SNIPES))
edited: dict[int, deque[EditedSnipe]] = defaultdict(lambda: deque(maxlen=MAX_SNIPES))


def _prune() -> None:
    now = time.time()
    for store in (deleted, edited):
        for channel_id in list(store):
            while store[channel_id] and now - getattr(store[channel_id][-1], "deleted_at", getattr(store[channel_id][-1], "edited_at", now)) > TTL_SECONDS:
                store[channel_id].pop()
            if not store[channel_id]:
                store.pop(channel_id, None)


async def on_message_delete(message: discord.Message) -> None:
    if not message.guild or message.author.bot:
        return
    if not message.content and not message.attachments:
        return
    deleted[message.channel.id].appendleft(DeletedSnipe(
        author_id=message.author.id,
        author_name=str(message.author),
        avatar_url=message.author.display_avatar.url,
        content=message.content or "*(no text)*",
        attachments=[a.url for a in message.attachments[:5]],
        created_at=message.created_at.timestamp(),
        deleted_at=time.time(),
    ))
    _prune()


async def on_message_edit(before: discord.Message, after: discord.Message) -> None:
    if not before.guild or before.author.bot or before.content == after.content:
        return
    edited[before.channel.id].appendleft(EditedSnipe(
        author_id=before.author.id,
        author_name=str(before.author),
        avatar_url=before.author.display_avatar.url,
        before=before.content or "*(empty)*",
        after=after.content or "*(empty)*",
        edited_at=time.time(),
    ))
    _prune()


def setup_snipe_commands(tree: app_commands.CommandTree) -> None:
    @tree.command(name="snipe", description="Show a recently deleted message in this channel.")
    @app_commands.describe(index="1 is the most recent deleted message")
    async def snipe(interaction: discord.Interaction, index: app_commands.Range[int, 1, 10] = 1) -> None:
        _prune()
        rows = deleted.get(interaction.channel_id or 0, deque())
        if len(rows) < index:
            await interaction.response.send_message("There is no deleted message at that position.", ephemeral=True)
            return
        row = rows[index - 1]
        embed = discord.Embed(description=row.content[:4000], color=BRAND, timestamp=discord.utils.utcnow())
        embed.set_author(name=row.author_name, icon_url=row.avatar_url)
        embed.add_field(name="Deleted", value=f"<t:{int(row.deleted_at)}:R>", inline=True)
        embed.add_field(name="Position", value=f"{index}/{len(rows)}", inline=True)
        if row.attachments:
            embed.add_field(name="Attachments", value="\n".join(row.attachments)[:1024], inline=False)
            if row.attachments[0].lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                embed.set_image(url=row.attachments[0])
        embed.set_footer(text="Snipes are held in memory for up to 2 hours and clear on restart.")
        await interaction.response.send_message(embed=embed)

    @tree.command(name="editsnipe", description="Show a recently edited message in this channel.")
    async def editsnipe(interaction: discord.Interaction, index: app_commands.Range[int, 1, 10] = 1) -> None:
        _prune()
        rows = edited.get(interaction.channel_id or 0, deque())
        if len(rows) < index:
            await interaction.response.send_message("There is no edited message at that position.", ephemeral=True)
            return
        row = rows[index - 1]
        embed = discord.Embed(title="Edited message", color=BRAND, timestamp=discord.utils.utcnow())
        embed.set_author(name=row.author_name, icon_url=row.avatar_url)
        embed.add_field(name="Before", value=row.before[:1024], inline=False)
        embed.add_field(name="After", value=row.after[:1024], inline=False)
        embed.add_field(name="Edited", value=f"<t:{int(row.edited_at)}:R>", inline=True)
        embed.set_footer(text="Edit snipes are held in memory for up to 2 hours and clear on restart.")
        await interaction.response.send_message(embed=embed)

    @tree.command(name="clearsnipes", description="Clear cached snipes in this channel.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clearsnipes(interaction: discord.Interaction) -> None:
        deleted.pop(interaction.channel_id or 0, None)
        edited.pop(interaction.channel_id or 0, None)
        await interaction.response.send_message("Cached snipes for this channel were cleared.", ephemeral=True)
