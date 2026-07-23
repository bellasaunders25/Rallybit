import discord
from core.bot_settings import get_bot_settings


def setup_support_command(tree):
    @tree.command(name="support", description="Get help with Rallybit.")
    async def support(interaction: discord.Interaction):
        link = get_bot_settings().get("global", {}).get("support_server", "https://discord.com")
        embed = discord.Embed(title="Rallybit support", description=f"Join the support community for setup help, bug reports, and suggestions.\n\n**Join here:** {link}", color=0x7C6CFF)
        await interaction.response.send_message(embed=embed, ephemeral=True)
