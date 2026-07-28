import discord
from discord import app_commands

from config.config import STAFF_SHIFTS_FILE
from core.checks import bot_can_run
from core.logging import get_activity_log, get_global_stats
from storage.json_store import load_json


def setup_leaderboard_command(tree):
    """Setup Original Leaderboard & User Leaderboard commands (Master Version)."""

    @tree.command(name="leaderboard", description="Show the server activity or staff-hours leaderboard.")
    @app_commands.choices(category=[
        app_commands.Choice(name="Activity check-ins", value="activity"),
        app_commands.Choice(name="Staff hours", value="staff"),
    ])
    async def leaderboard_command(interaction: discord.Interaction, category: app_commands.Choice[str] | None = None):
        # 1. Permission Check
        can_run, reason, _ = bot_can_run(interaction)
        if not can_run: return await interaction.response.send_message(reason, ephemeral=True)

        if category and category.value == "staff":
            from commands.workforce import (
                _active_seconds,
                _format_duration,
                is_staff_member,
            )

            if not isinstance(interaction.user, discord.Member) or not is_staff_member(interaction.user):
                return await interaction.response.send_message("You do not have a configured staff role for that leaderboard.", ephemeral=True)
            guild_rows = (load_json(STAFF_SHIFTS_FILE) or {}).get(str(interaction.guild_id), {})
            scores = []
            if isinstance(guild_rows, dict):
                for user_id, record in guild_rows.items():
                    if not isinstance(record, dict):
                        continue
                    seconds = sum(max(0, int(row.get("seconds", 0) or 0)) for row in record.get("history", []) if isinstance(row, dict))
                    if isinstance(record.get("active"), dict):
                        seconds += _active_seconds(record["active"])
                    if seconds:
                        scores.append((str(user_id), seconds))
            scores.sort(key=lambda item: item[1], reverse=True)
            if not scores:
                return await interaction.response.send_message("No staff shifts have been recorded yet.")
            lines = [f"**#{position}** <@{user_id}> · **{_format_duration(seconds)}**" for position, (user_id, seconds) in enumerate(scores[:10], 1)]
            embed = discord.Embed(title=f"Staff hours · {interaction.guild.name}", description="\n".join(lines), colour=0x7567EE)
            embed.set_footer(text="Includes completed shifts and current on-duty time")
            return await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            
        await interaction.response.defer(ephemeral=False)
        
        # 2. Extract Local Check-ins
        activity_log = get_activity_log()
        guild_id = str(interaction.guild_id)
        
        if guild_id not in activity_log:
             return await interaction.followup.send("No activity recorded for this server yet.")
             
        local_scores = []
        for uid, timestamps in activity_log[guild_id].items():
            count = len(timestamps)
            if count > 0:
                local_scores.append((uid, count))
                
        local_scores.sort(key=lambda x: x[1], reverse=True)
        top_10 = local_scores[:10]
        
        if not top_10:
             return await interaction.followup.send("No activity data found yet.")

        embed = discord.Embed(
            title=f"📊 Local Leaderboard: {interaction.guild.name}", 
            description="Most active users in this server (by check-ins).", 
            color=0x3498DB
        )
        embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
        
        desc = ""
        for i, (uid, score) in enumerate(top_10, 1):
             if i == 1: prefix = "🥇 **#1**"
             elif i == 2: prefix = "🥈 **#2**"
             elif i == 3: prefix = "🥉 **#3**"
             else: prefix = f"**#{i}**"
             desc += f"{prefix} <@{uid}> • **{score:,} Checks**\n"
             
        embed.description = desc
        
        # Caller Rank Check
        caller_id = str(interaction.user.id)
        c_rank, c_score = "Unranked", 0
        for idx, (uid, score) in enumerate(local_scores, 1):
            if str(uid) == caller_id:
                c_rank, c_score = f"#{idx}", score
                break
        
        embed.set_footer(text=f"Your Server Rank: {c_rank} • {c_score:,} Checks", icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed)

    @tree.command(name="userleaderboard", description="Show the global top 10 for activity wins.")
    async def userleaderboard_command(interaction: discord.Interaction):
        # 1. Permission Check
        can_run, reason, _ = bot_can_run(interaction)
        if not can_run: return await interaction.response.send_message(reason, ephemeral=True)
            
        await interaction.response.defer(ephemeral=False)
        
        # 2. Extract Global Wins
        global_stats = get_global_stats()
        
        sorted_users = []
        for uid, s in global_stats.items():
            wins = s.get('wins', 0)
            if wins > 0:
                sorted_users.append((uid, wins))
        
        sorted_users.sort(key=lambda x: x[1], reverse=True)
        top_10 = sorted_users[:10]
        
        if not top_10:
            return await interaction.followup.send("No global activity recorded yet!")

        embed = discord.Embed(
            title="🏆 Global Top 10 Leaders",
            description="The most active and legendary users across all servers.",
            color=0xFFD700 # Gold
        )
        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/3150/3150115.png")
        
        lb_text = ""
        for i, (uid, wins) in enumerate(top_10, 1):
            if i == 1: prefix = "🥇 **#1**"
            elif i == 2: prefix = "🥈 **#2**"
            elif i == 3: prefix = "🥉 **#3**"
            else: prefix = f"**#{i}**"
            lb_text += f"{prefix} <@{uid}> • **{wins:,} Wins**\n"
            
        embed.description = lb_text
        
        # Caller Rank Check
        caller_id = str(interaction.user.id)
        c_rank, c_wins = "Unranked", 0
        for idx, (uid, wins) in enumerate(sorted_users, 1):
            if str(uid) == caller_id:
                c_rank, c_wins = f"#{idx}", wins
                break
                
        embed.set_footer(text=f"Your Rank: {c_rank} • {c_wins:,} Wins", icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed)
