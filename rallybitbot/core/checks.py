from __future__ import annotations

import discord
from discord.ext import commands

from core.limits import check_limit, increment_limit
from core.bot_settings import get_bot_settings


def bot_can_run(ctx_or_interaction):
    """Every Discord server receives the complete Rallybit feature set.

    Discord permissions still protect staff/moderation actions, but there is
    no global per-server or per-user feature ban list.
    """
    if isinstance(ctx_or_interaction, commands.Context):
        return True, "", ctx_or_interaction.send
    if isinstance(ctx_or_interaction, discord.Interaction) and ctx_or_interaction.guild:
        send_func = ctx_or_interaction.response.send_message if not ctx_or_interaction.response.is_done() else ctx_or_interaction.followup.send
        return True, "", send_func
    return True, "", None


async def check_command_limits(ctx_or_interaction, command):
    can_run, reason, send_func = bot_can_run(ctx_or_interaction)
    if not can_run:
        if send_func: await send_func(reason, ephemeral=isinstance(ctx_or_interaction, discord.Interaction))
        return False
    guild = ctx_or_interaction.guild
    allowed, status = check_limit(guild.id, command)
    if not allowed:
        config = get_bot_settings().get(command, {})
        if status == -1 or not config.get("active", True):
            message = f"`/{command}` is temporarily unavailable."
        elif status == -2:
            message = "This server is blocked from using Rallybit."
        else:
            message = f"`/{command}` has reached its temporary anti-spam limit. Try again later."
        if send_func: await send_func(message, ephemeral=isinstance(ctx_or_interaction, discord.Interaction))
        return False
    increment_limit(guild.id, command)
    return True


def slash_check_limit(command):
    async def predicate(interaction: discord.Interaction):
        return await check_command_limits(interaction, command)
    return discord.app_commands.check(predicate)
