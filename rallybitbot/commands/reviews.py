from __future__ import annotations

import uuid
from typing import Any

import discord
from discord import app_commands

from config.config import REVIEW_SETTINGS_FILE
from core.logging import log_server_event
from storage.json_store import load_json

BRAND = 0x7C6CFF
REVIEW_TYPES = {
    "staff": ("Staff", "staff_channel_id"),
    "member": ("Member", "member_channel_id"),
}


def _settings(guild_id: int) -> dict[str, Any]:
    data = load_json(REVIEW_SETTINGS_FILE) or {}
    saved = data.get(str(guild_id), {})
    return saved if isinstance(saved, dict) else {}


def _review_embed(
    guild: discord.Guild,
    review_type: str,
    reviewer: discord.Member,
    subject: discord.Member,
    stars: int,
    reason: str,
) -> discord.Embed:
    type_label = REVIEW_TYPES[review_type][0]
    review_id = f"RV-{uuid.uuid4().hex[:8].upper()}"
    rating = "★" * stars + "☆" * (5 - stars)
    embed = discord.Embed(
        title=f"{subject.display_name} • {type_label} review",
        description=reason,
        color=BRAND,
        timestamp=discord.utils.utcnow(),
    )
    if guild.icon:
        embed.set_author(name=f"{guild.name} • Reviews", icon_url=str(guild.icon.url))
    else:
        embed.set_author(name=f"{guild.name} • Reviews")
    embed.add_field(name="Reviewed user", value=f"{subject.mention}\n`{subject.id}`", inline=True)
    embed.add_field(name="Reviewer", value=f"{reviewer.mention}\n`{reviewer.id}`", inline=True)
    embed.add_field(name="Score", value=f"**{stars}/5**\n{rating}", inline=True)
    embed.add_field(name="Review type", value=type_label, inline=True)
    embed.set_thumbnail(url=str(subject.display_avatar.url))
    hero_image = guild.banner or guild.splash
    if hero_image:
        embed.set_image(url=str(hero_image.url))
    embed.set_footer(text=f"Rallybit Reviews • {review_id}")
    return embed


def setup_review_command(tree: app_commands.CommandTree) -> None:
    @tree.command(name="review", description="Post a staff or member review to the configured review channel.")
    @app_commands.describe(
        type="Choose whether this is a staff or member review.",
        user="The person you are reviewing.",
        stars="Your rating from 1 to 5 stars.",
        reason="Explain the rating clearly and respectfully.",
    )
    @app_commands.choices(type=[
        app_commands.Choice(name="Staff", value="staff"),
        app_commands.Choice(name="Member", value="member"),
    ])
    async def review(
        interaction: discord.Interaction,
        type: app_commands.Choice[str],
        user: discord.Member,
        stars: app_commands.Range[int, 1, 5],
        reason: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this command in a server.", ephemeral=True)
            return
        clean_reason = reason.strip()
        if len(clean_reason) < 3:
            await interaction.response.send_message("Give a short reason for the review.", ephemeral=True)
            return
        if len(clean_reason) > 1000:
            await interaction.response.send_message("Review reasons can be up to 1,000 characters.", ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message("Reviews can only be submitted for server members.", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message("You cannot review yourself.", ephemeral=True)
            return
        type_key = type.value
        type_label, channel_key = REVIEW_TYPES[type_key]
        channel_id = _settings(interaction.guild.id).get(channel_key)
        channel = interaction.guild.get_channel(int(channel_id or 0))
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                f"The {type_label.lower()} review channel has not been configured yet. Ask an administrator to set it in the Rallybit dashboard.",
                ephemeral=True,
            )
            return
        bot_member = interaction.guild.me
        permissions = channel.permissions_for(bot_member) if bot_member else None
        if permissions is None or not permissions.send_messages or not permissions.embed_links:
            await interaction.response.send_message(
                f"Rallybit needs Send Messages and Embed Links in {channel.mention}.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        embed = _review_embed(interaction.guild, type_key, interaction.user, user, int(stars), clean_reason)
        try:
            message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await interaction.followup.send("The review could not be posted. Check Rallybit's channel permissions and try again.", ephemeral=True)
            return
        log_server_event(interaction.guild.id, f"{type_label} review for {user} posted by {interaction.user} in #{channel.name}.")
        await interaction.followup.send(f"Your {type_label.lower()} review was posted: {message.jump_url}", ephemeral=True)
