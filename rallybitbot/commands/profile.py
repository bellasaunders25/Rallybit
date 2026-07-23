import discord
from urllib.parse import quote
from discord import app_commands
from core.checks import bot_can_run
from core.logging import get_global_stats, save_global_stats, get_user_badges
from config.config import BANNED_USERS_FILE, DASHBOARD_URL
from storage.json_store import load_json

def setup_profile_command(tree):
    """Setup Original Profile command (Master Version)."""
    
    @tree.command(name="profile", description="Show a user's global Rallybit profile.")
    @app_commands.describe(user="Select a user to view their profile.")
    async def profile_command(interaction: discord.Interaction, user: discord.User):
        # 1. Permission Check
        can_run, reason, _ = bot_can_run(interaction)
        if not can_run:
            return await interaction.response.send_message(reason, ephemeral=True)

        user_id = str(user.id)
        
        # 2. Check if the searched user is banned from using the bot
        try:
            banned_users = load_json(BANNED_USERS_FILE)
        except Exception:
            banned_users = {}

        if user_id in banned_users:
            embed = discord.Embed(
                title=f"{user.display_name} ({user.name}) Has Been Banned",
                color=0xFF4B4B
            )
            return await interaction.response.send_message(embed=embed)

        global_stats = get_global_stats()

        # 2. Ensure stats exist
        if user_id not in global_stats:
            global_stats[user_id] = {
                "checkins": 0,
                "respect": 0,
                "wins": 0,
                "recent_wins": 0,
                "streak": 0,
                "original_wins": 0,
                "username": user.name,
                "display_name": user.display_name
            }
            save_global_stats(global_stats)

        stats = global_stats[user_id]
        
        # 3. Update current profile data
        stats['username'] = user.name
        stats['display_name'] = user.display_name
        stats['avatar_url'] = user.display_avatar.url if user.display_avatar else None
        save_global_stats(global_stats)
        
        # 4. Determine Rank Title based on Wins
        wins = stats.get('wins', 0)
        ranks = [
            (500, "🌌 Celestial"),
            (250, "⚡ Godlike"),
            (100, "👑 Activity Legend"),
            (50, "🔥 Activity Master"),
            (25, "💎 Elite Member"),
            (10, "🌟 Hyper Active"),
            (0, "🌱 Newcomer")
        ]
        
        rank = "🌱 Newcomer"
        next_rank, next_rank_wins = None, 10
        
        for threshold, title in ranks:
            if wins >= threshold:
                rank = title
                current_index = ranks.index((threshold, title))
                if current_index > 0:
                    prev = ranks[current_index - 1]
                    next_rank, next_rank_wins = prev[1], prev[0]
                break
        
        # 5. Calculate Progress Bar
        if next_rank:
            prev_threshold = 0
            for t, _ in reversed(ranks):
                if wins >= t: prev_threshold = t
                if t > wins: break
            
            total_range = next_rank_wins - prev_threshold
            prog = wins - prev_threshold
            percent = min(1.0, max(0.0, prog / (total_range or 1)))
            
            bar = "▰" * int(percent * 10) + "▱" * (10 - int(percent * 10))
            progress_text = f"{bar} **{wins}/{next_rank_wins}**"
            next_rank_display = f"Next: {next_rank}"
        else:
            progress_text = "▰" * 10 + " **MAX**"
            next_rank_display = "Max Rank Reached!"

        # 6. Get badges
        user_badges = get_user_badges(user.id, stats)
        badge_str = " ".join(user_badges) if user_badges else "No badges earned yet."

        # 7. Build Original UI Embed
        embed = discord.Embed(
            description=f"# {rank}\n{progress_text}\n*{next_rank_display}*", 
            color=0x00cca3
        )
        embed.set_author(name=f"{user.display_name}'s Profile", icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)

        embed.add_field(name="🏆 Total Wins", value=f"**{stats['wins']:,}**", inline=True)
        embed.add_field(name="✅ Logged Checks", value=f"**{stats['checkins']:,}**", inline=True)
        embed.add_field(name="🎖️ Achievements & Badges", value=f"> {badge_str}", inline=False)
        
        embed.set_footer(text=f"ID: {user.id} • Rallybit Global Profile")

        # 8. Web Profile Button
        view = discord.ui.View()
        clean_name = quote(user.name, safe='')
        view.add_item(discord.ui.Button(label="🌐 View Web Profile", url=f"{DASHBOARD_URL.rsplit('/dashboard', 1)[0]}/id/{clean_name}", style=discord.ButtonStyle.link))

        await interaction.response.send_message(embed=embed, view=view)
