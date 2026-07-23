import discord
from discord import app_commands
from datetime import datetime, timedelta
from core.checks import slash_check_limit
from storage.json_store import load_json
from config.config import LAST_SEEN_FILE, LOG_FILE

class InactivePaginator(discord.ui.View):
    def __init__(self, member_data, days, owner_id):
        super().__init__(timeout=120)
        self.member_data = member_data # List of (member, last_active_dt)
        self.days = days
        self.owner_id = owner_id
        self.page = 0
        self.per_page = 10 # Reduced to prevent 1024 char field limit error
        self.total_pages = (len(member_data) - 1) // self.per_page + 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Only the staff member who opened this report can change pages.", ephemeral=True)
        return False

    def create_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        current_list = self.member_data[start:end]
        
        embed = discord.Embed(
            title="💤 Inactivity Report",
            description=f"Members inactive for **{self.days}+ days**.\nTotal Found: `{len(self.member_data)}`",
            color=0x2B2D31 # Dark sleek color
        )
        
        # Compact single-line formatting
        list_lines = []
        for member, last_active in current_list:
            if last_active:
                ts = int(last_active.timestamp())
                activity_str = f"<t:{ts}:R>"
            else:
                activity_str = "`Never`"
            
            list_lines.append(f"• {member.mention} ({member.name}) - {activity_str}")
        
        embed.add_field(
            name=f"📋 Page {self.page + 1}/{self.total_pages}", 
            value="\n".join(list_lines) or "No members.", 
            inline=False
        )
        
        embed.set_footer(text="Rallybit inactivity tracking")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.gray)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else:
            await interaction.response.send_message("First page.", ephemeral=True)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.gray)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages - 1:
            self.page += 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else:
            await interaction.response.send_message("Last page.", ephemeral=True)

def setup_analytics_commands(tree):
    """Setup Analytics-related slash commands with Pagination."""
    
    @tree.command(name="inactive", description="Find members who haven't used the bot or reacted recently.")
    @app_commands.describe(days="Days of inactivity (default 7)")
    @app_commands.checks.has_permissions(administrator=True)
    async def inactive(interaction: discord.Interaction, days: int = 7):
        """Forensic Inactivity Tracker with Pagination View."""
        if days < 1 or days > 365:
            return await interaction.response.send_message("Choose an inactivity window between 1 and 365 days.", ephemeral=True)
        await interaction.response.defer()
        
        last_seen = {}
        try:
            data = load_json(LAST_SEEN_FILE)
            if data: last_seen = data.get(str(interaction.guild_id), {})
        except: pass

        # Load activity log to check for reactions/wins
        activity_log = {}
        try:
            data = load_json(LOG_FILE)
            if data: activity_log = data.get(str(interaction.guild_id), {})
        except: pass
        
        threshold = datetime.utcnow() - timedelta(days=days)
        inactive_members = []
        
        async for member in interaction.guild.fetch_members(limit=None):
            if member.bot: continue
            uid = str(member.id)
            
            # Check last seen (command usage)
            last_seen_dt = None
            if uid in last_seen:
                try: last_seen_dt = datetime.fromisoformat(last_seen[uid])
                except: pass
            
            # Check activity log (reactions/wins)
            last_react_dt = None
            if uid in activity_log:
                try:
                    timestamps = activity_log[uid]
                    if timestamps:
                        # Get the latest reaction timestamp
                        last_react_dt = datetime.fromisoformat(max(timestamps))
                except: pass
            
            # Determine overall last activity
            latest_activity = None
            if last_seen_dt and last_react_dt:
                latest_activity = max(last_seen_dt, last_react_dt)
            elif last_seen_dt:
                latest_activity = last_seen_dt
            elif last_react_dt:
                latest_activity = last_react_dt
            
            # If they have activity, check if it's within threshold
            if latest_activity:
                if latest_activity < threshold:
                    inactive_members.append((member, latest_activity))
            else:
                # No activity ever recorded for this member in this guild
                inactive_members.append((member, None))
        
        if not inactive_members:
            return await interaction.followup.send(f"✅ Everyone has been active in the last {days} days!")
            
        view = InactivePaginator(inactive_members, days, interaction.user.id)
        await interaction.followup.send(embed=view.create_embed(), view=view)
