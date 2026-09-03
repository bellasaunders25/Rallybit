from __future__ import annotations

from datetime import datetime

import discord

from config.config import DASHBOARD_URL, SUPPORT_SERVER_URL
from core.bot_settings import get_bot_settings, get_branding
from core.checks import bot_can_run


def setup_help_commands(tree):
    @tree.command(name="help", description="View Rallybit commands and setup links.")
    async def help_command(interaction: discord.Interaction):
        settings = get_bot_settings().get("global", {})
        support = settings.get("support_server") or SUPPORT_SERVER_URL
        dashboard = settings.get("dashboard_url") or DASHBOARD_URL
        embed = discord.Embed(
            title="Rallybit command centre",
            description="Every existing Rallybit tool stays free. Advanced operations are available through Coming soon plan previews.",
            color=0x5865F2,
        )
        embed.add_field(
            name="⚡ Activity",
            value="`/activitycheck` Start a check\n`/endactivitycheck` End a check\n`/leaderboard` Server standings\n`/profile` Member profile\n`/inactive` Inactivity report",
            inline=True,
        )
        embed.add_field(
            name="🧠 Community quizzes",
            value="`/quiz start` Start a quiz\n`/quiz setup` Set a schedule\n`/quiz auto` Enable automation\n`/quiz pingrole` Set the revive ping\n`/quiz leaderboard` Quiz league\n`/quiz settings` View setup",
            inline=True,
        )
        embed.add_field(
            name="🛡️ Moderation",
            value="`/mod panel` Interactive panel\n`/mod warn` Add a warning\n`/role give` Manage roles\n`/role temp` Timed role countdown\n`/channel transcript` Archive a channel\n`/channel purge` Purge messages",
            inline=True,
        )
        embed.add_field(
            name="🔐 Security",
            value="`/security overview` Configuration\n`/security-trap setup` Scam honeypot\n`/security-agegate configure` Account-age gate\n`/security audit` Permission scan\n`/security-modules set` Anti-nuke modules\n`/security lockdown` Emergency lock",
            inline=True,
        )
        embed.add_field(
            name="💬 Community tools",
            value="`/community icebreaker` Conversation starter\n`/community pulse` Anonymous mood pulse\n`/review` Staff or member review\n`/afk` Set your away status\n`/community settings` View notifications\n`/community stoppulse` Close a pulse",
            inline=True,
        )
        embed.add_field(
            name="⚙️ Configure activity checks",
            value="`/setactivitytext` Message\n`/setmode` Reaction or button\n`/setreactor` Emoji\n`/setbuttontext` Button label\n`/setwinner` Target\n`/setduration` Duration\n`/setperm` Staff role",
            inline=True,
        )
        embed.add_field(
            name="🔁 Automation & logs",
            value="`/setauto` Check schedule\n`/startauto` Resume checks\n`/stopauto` Pause checks\n`/logs overview` Action logging\n`/logs channel` Log destinations\n`/dashboard` Dashboard link",
            inline=True,
        )
        embed.add_field(
            name="Staff operations",
            value="`/loa request` Leave request\n`/roa request` Activity release\n`/clockin` Start shift\n`/timesheet` Weekly hours\n`/shifts list` HR shift records\n`/shifts remove` Correct a shift",
            inline=True,
        )
        embed.add_field(
            name="Tickets & reports",
            value="`/ticket create` Open ticket\n`/ticket status` Ticket progress\n`/report user` Report member\n`/report status` Report progress\n`/review` Submit review\n`/settings` Server setup",
            inline=True,
        )
        embed.add_field(
            name="Premium operations",
            value="`/prettfy` Preview server styling\n`/case member` Search case files\n`/case stats` Staff workload\n`/backup create` Save configuration\n`/network overview` Owned servers\n`/premium plans` Compare previews",
            inline=True,
        )
        embed.set_footer(text="Rallybit 8.1 • Core commands remain free")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Dashboard", url=dashboard, emoji="🌐"))
        view.add_item(discord.ui.Button(label="Support", url=support, emoji="💬"))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @tree.command(name="about", description="View Rallybit information and live statistics.")
    async def about_command(interaction: discord.Interaction):
        can_run, reason, _ = bot_can_run(interaction)
        if not can_run:
            return await interaction.response.send_message(reason, ephemeral=True)
        client = interaction.client
        settings = get_bot_settings().get("global", {})
        brand, version = get_branding()
        embed = discord.Embed(title=brand, description="A community activity, quiz and moderation toolkit for Discord servers.", color=0x5865F2)
        embed.set_thumbnail(url=client.user.display_avatar.url)
        embed.add_field(name="Live reach", value=f"**Servers:** {len(client.guilds):,}\n**Members:** {sum(g.member_count or 0 for g in client.guilds):,}\n**Latency:** {round(client.latency * 1000)}ms", inline=True)
        embed.add_field(name="Included", value="Activity checks, scheduled quizzes, community pulses, moderation panels, configurable security, logs, analytics and leaderboards.", inline=True)
        embed.set_footer(text=f"{version} • {datetime.utcnow().year} Rallybit")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Dashboard", url=settings.get("dashboard_url") or DASHBOARD_URL, emoji="🌐"))
        view.add_item(discord.ui.Button(label="Support", url=settings.get("support_server") or SUPPORT_SERVER_URL, emoji="💬"))
        await interaction.response.send_message(embed=embed, view=view)
