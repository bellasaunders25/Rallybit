from __future__ import annotations

import io
import discord
from discord import app_commands

from core.checks import slash_check_limit
from core.logging import get_guild_settings
from storage.json_store import load_json
from config.config import COMMUNITY_SETTINGS_FILE, DASHBOARD_URL, LOG_FILE, QUIZ_SETTINGS_FILE
from core.bot_settings import get_bot_settings


class SettingsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        support = get_bot_settings().get("global", {}).get("support_server", "https://discord.com")
        self.add_item(discord.ui.Button(label="Need support?", url=support, emoji="💬"))


def setup_utility_commands(tree):
    @tree.command(name="dashboard", description="Get your Rallybit dashboard link.")
    async def dashboard(interaction: discord.Interaction):
        await interaction.response.send_message(f"🌐 **Rallybit dashboard**\n{DASHBOARD_URL}", ephemeral=True)

    @tree.command(name="settings", description="View the current server configuration and permission checks.")
    @slash_check_limit("settings")
    async def settings(interaction: discord.Interaction):
        data = get_guild_settings(interaction.guild.id)
        guild = interaction.guild
        embed = discord.Embed(title="⚙️ Rallybit settings", description=f"Configuration for **{guild.name}**", color=0x7C6CFF)
        embed.add_field(name="Check", value=f"**Winners:** `{data.get('winner_count', 3)}`\n**Duration:** `{data.get('check_duration_minutes', 60)} minutes`\n**Ping:** {data.get('ping_target', '@everyone')}", inline=True)
        mode = data.get("reactor_type", "reaction")
        reactor = data.get("button_text", "I'm Active! ⚡") if mode == "button" else data.get("reactor", "✅")
        embed.add_field(name="Participation", value=f"**Mode:** `{mode}`\n**Reactor:** {reactor}", inline=True)
        log_channel = f"<#{data.get('log_channel_id')}>" if data.get("log_channel_id") else "Not set"
        auto_channel = f"<#{data.get('auto_channel')}>" if data.get("auto_channel") else "Not set"
        embed.add_field(name="Activity automation", value=f"**Enabled:** `{bool(data.get('auto_enabled'))}`\n**Interval:** `{data.get('auto_hours', 1)} hours`\n**Channel:** {auto_channel}\n**Logs:** {log_channel}", inline=False)
        quiz_data = (load_json(QUIZ_SETTINGS_FILE) or {}).get(str(guild.id), {})
        quiz_channel = f"<#{quiz_data.get('channel_id')}>" if quiz_data.get("channel_id") else "Not set"
        quiz_ping = f"<@&{quiz_data.get('ping_role_id')}>" if quiz_data.get("ping_role_id") else "Disabled"
        embed.add_field(name="Community quizzes", value=f"**Automatic:** `{bool(quiz_data.get('enabled'))}`\n**Interval:** `{quiz_data.get('interval_hours', 12)} hours`\n**Category:** `{quiz_data.get('category', 'mixed')}`\n**Channel:** {quiz_channel}\n**Ping role:** {quiz_ping}", inline=False)
        community_data = (load_json(COMMUNITY_SETTINGS_FILE) or {}).get(str(guild.id), {})
        community_ping = f"<@&{community_data.get('ping_role_id')}>" if community_data.get("ping_role_id") else "Disabled"
        embed.add_field(name="Community notifications", value=f"**Pulse / revive role:** {community_ping}\n**Pulse default:** notifies the role\n**Icebreaker default:** silent unless staff enable `notify_role`", inline=False)
        permissions = interaction.channel.permissions_for(guild.me)
        embed.add_field(name="Channel permissions", value=f"{'✅' if permissions.view_channel else '❌'} View  •  {'✅' if permissions.send_messages else '❌'} Send  •  {'✅' if permissions.add_reactions else '❌'} React", inline=False)
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        embed.set_footer(text="Every Rallybit feature is enabled for this server.")
        await interaction.response.send_message(embed=embed, view=SettingsView())

    @tree.command(name="checkinactive", description="List members who have not participated in a recorded activity check.")
    @app_commands.checks.has_permissions(administrator=True)
    async def checkinactive(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        activity_log = load_json(LOG_FILE) or {}
        guild_id = str(interaction.guild.id)
        guild_records = activity_log.get(guild_id, {})
        active_users = set(guild_records.keys()) if isinstance(guild_records, dict) else set()
        inactive = []
        async for member in interaction.guild.fetch_members(limit=None):
            if not member.bot and str(member.id) not in active_users:
                inactive.append(member)
        if not inactive:
            return await interaction.followup.send("🎉 Every current member has a recorded participation event.")
        text = "\n".join(f"{member} ({member.id})" for member in inactive)
        if len(text) <= 1800:
            return await interaction.followup.send(f"📉 **Inactivity report**\n```\n{text}\n```")
        await interaction.followup.send("📉 **Inactivity report**", file=discord.File(io.BytesIO(text.encode()), "inactive-members.txt"))
