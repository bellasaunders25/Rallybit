import discord
from discord.ext import tasks
import asyncio

# Global index to keep track of current presence rotation
_current_presence_idx = 0

@tasks.loop(seconds=60)
async def rotate_presence(client):
    """Modern Multi-Presence logic with dynamic dashboard rotation."""
    global _current_presence_idx
    
    from core.bot_settings import get_bot_settings
    settings = get_bot_settings()
    global_cfg = settings.get("global", {})
    
    presences = global_cfg.get("presences", [])
    if not presences:
        # Fallback if none configured
        presences = [{"text": "Rallybit | {servers} servers", "type": "watching"}]
    
    # 1. Get current presence based on index
    if _current_presence_idx >= len(presences):
        _current_presence_idx = 0
        
    p_cfg = presences[_current_presence_idx]
    presence_text = p_cfg.get("text", "Rallybit")
    presence_type_str = p_cfg.get("type", "watching")
    
    # 2. Calculate Stats
    total_members = sum(g.member_count or 0 for g in client.guilds)
    total_servers = len(client.guilds)
    
    # 3. Parse Text
    final_text = presence_text.replace("{servers}", f"{total_servers:,}").replace("{members}", f"{total_members:,}")
    
    # 4. Map Type
    type_map = {
        "watching": discord.ActivityType.watching,
        "playing": discord.ActivityType.playing,
        "listening": discord.ActivityType.listening,
        "competing": discord.ActivityType.competing
    }
    final_type = type_map.get(presence_type_str.lower(), discord.ActivityType.watching)

    try:
        await client.change_presence(activity=discord.Activity(type=final_type, name=final_text))
    except Exception as e:
        print(f"⚠️ Presence Update Error: {e}")

    # 5. Increment index for next run
    _current_presence_idx += 1
