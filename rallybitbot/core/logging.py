from datetime import datetime
from config.config import (
    LOG_FILE, GLOBAL_STATS_FILE, SETTINGS_FILE, BADGES_FILE, WIN_BADGES_FILE, SERVER_LOGS_DIR
)
import os
from storage.json_store import load_json, save_json
import discord

# Helper for dynamic reloading
def get_global_stats():
    """Dynamically reload global stats to pick up dashboard changes."""
    return load_json(GLOBAL_STATS_FILE)

def get_activity_log():
    """Dynamically reload activity log."""
    return load_json(LOG_FILE)

def get_user_badges_data():
    """Dynamically reload badges."""
    return load_json(BADGES_FILE)

# Initial load for backward compatibility (if needed) but functions are preferred
activity_log = get_activity_log()
global_stats = get_global_stats()
user_badges = get_user_badges_data()

def log_activity(guild_id, user_id, save=True, user_obj=None):
    """Log user activity for a guild."""
    now = datetime.utcnow().isoformat()
    guild_id = str(guild_id)
    user_id = str(user_id)

    # ===== Update activity_log.json (per server log) =====
    if guild_id not in activity_log:
        activity_log[guild_id] = {}
    if user_id not in activity_log[guild_id]:
        activity_log[guild_id][user_id] = []
    activity_log[guild_id][user_id].append(now)
    
    if save:
        save_json(LOG_FILE, activity_log)

    # ===== Update GLOBAL stats (check-ins) =====
    global global_stats
    global_stats = get_global_stats() # Refresh from disk
    
    if user_id not in global_stats:
        global_stats[user_id] = {
            "checkins": 0,
            "respect": 0,
            "wins": 0,
            "recent_wins": 0,
            "streak": 0,
            "original_wins": 0
        }

    global_stats[user_id]["checkins"] += 1
    
    if user_obj:
        global_stats[user_id]["username"] = user_obj.name
        global_stats[user_id]["display_name"] = user_obj.display_name
        global_stats[user_id]["avatar_url"] = user_obj.display_avatar.url if user_obj.display_avatar else None

    if save:
        save_json(GLOBAL_STATS_FILE, global_stats)

def save_activity_log():
    """Save activity log to file."""
    save_json(LOG_FILE, activity_log)

def save_global_stats(stats_dict=None):
    """Save global stats to file. Accepts an optional dict or uses a fresh disk read.
    
    IMPORTANT: Never uses the stale module-level global_stats variable when called
    with no argument — it always reloads from disk first to avoid overwriting live data.
    """
    global global_stats
    if stats_dict is not None:
        to_save = stats_dict
        global_stats = stats_dict  # keep in-memory cache in sync
    else:
        # Always re-read from disk to avoid stomping wins written since startup
        to_save = get_global_stats()
        global_stats = to_save
    save_json(GLOBAL_STATS_FILE, to_save)

def get_user_badges(user_id: int, stats: dict):
    """Get badges for a user based on their stats."""
    badges = []

    # Win-based badges (Dynamic)
    wins = stats.get("wins", 0)
    
    try:
        win_badges_cfg = load_json(WIN_BADGES_FILE)
        if isinstance(win_badges_cfg, list):
            # Sort by required wins (ascending) to maintain logical order in profile
            win_badges_cfg.sort(key=lambda x: x.get("wins", 0))
            
            for item in win_badges_cfg:
                required_wins = item.get("wins", 0)
                badge_icon = item.get("badge", "")
                
                if wins >= required_wins:
                    badges.append(badge_icon)
    except Exception as e:
        print(f"❌ Error loading dynamic badges: {e}")

    # Custom badges from badges.json
    # Custom badges from badges.json (Reload to support live updates)
    try:
        current_badges = load_json(BADGES_FILE)
        uid = str(user_id)
        if uid in current_badges:
            for b in current_badges[uid]:
                badges.append(f"{b}")
    except Exception:
        pass

    return badges

async def log_action_to_channel(guild, user, command, details="", command_channel=None):
    """Send a configuration action to the server's selected log channel."""
    guild_id = str(guild.id)

    data = get_guild_settings(guild_id)
    log_channel_id = data.get("log_channel_id")
    
    if log_channel_id:
        log_target = guild.get_channel(log_channel_id)
        if log_target and log_target.permissions_for(guild.me).send_messages:
            embed = discord.Embed(
                description=f"{user.mention} (**{user.name}**) used `/{command}`.",
                color=discord.Color.teal()
            )
            embed.add_field(name="Action Details", value=details or "No additional details provided.", inline=False)
            
            # Show where the command was actually used
            if command_channel:
                 embed.add_field(name="Used In", value=command_channel.mention, inline=True)
            else:
                 embed.add_field(name="Used In", value="Unknown", inline=True)

            embed.set_author(name=f"Bot Log | /{command.upper()}", icon_url=user.display_avatar.url)
            embed.set_footer(text=f"User ID: {user.id} | Server ID: {guild.id}")
            embed.timestamp = datetime.utcnow()
            try:
                await log_target.send(embed=embed)
            except Exception as e:
                # Silently fail if log can't be sent (e.g., channel deleted)
                print(f"Failed to send log to channel {log_channel_id} in {guild.name}: {e}")

async def log_activity_result(guild, winners, duration_min, check_id=None, status="completed"):
    """Send a result summary to the configured server log channel."""
    guild_id = str(guild.id)

    data = get_guild_settings(guild_id)
    log_channel_id = data.get("log_channel_id")
    if not log_channel_id: return

    log_target = guild.get_channel(log_channel_id)
    if not (log_target and log_target.permissions_for(guild.me).send_messages): return

    labels = {
        "completed": ("🏁 Rallybit: COMPLETED", "finished after reaching its participant target", discord.Color.dark_green()),
        "manually_stopped": ("🛑 Rallybit: STOPPED", "was ended manually by an authorised staff member", discord.Color.orange()),
        "timed_out": ("⏰ Rallybit: TIME ENDED", "closed when its configured duration elapsed", discord.Color.gold()),
        "cancelled": ("⚠️ Rallybit: CANCELLED", "was cancelled before a result could be completed", discord.Color.red()),
    }
    title, summary, colour = labels.get(status, labels["completed"])
    id_text = f" (**ID:** `{check_id}`)" if check_id else ""
    desc_text = f"The activity check{id_text} {summary}."

    embed = discord.Embed(title=title, description=desc_text, color=colour)
    
    winner_text = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, entry in enumerate(winners):
        u = entry["user"] # entry is {"user": obj, "time": ts}
        ts = entry["time"]
        medal = medals[i] if i < 3 else "🏅"
        winner_text += f"{medal} **{u.name}** (`{u.id}`) • <t:{ts}:R>\n"

    embed.add_field(name="🏆 Verified Winners", value=winner_text or "No participants reacted.", inline=False)
    embed.add_field(name="⏱️ Summary", value=f"**Duration:** {duration_min}m\n**Winners:** {len(winners)}", inline=False)
    
    embed.set_author(name="Activity Log | Completion", icon_url=guild.me.display_avatar.url)
    embed.set_footer(text=f"Server ID: {guild.id}")
    embed.timestamp = datetime.utcnow()
    
    try: await log_target.send(embed=embed)
    except: pass

def get_guild_settings(guild_id):
    """Get settings for a guild. ALWAYS load fresh from disk (no caching)."""
    settings_data = load_json(SETTINGS_FILE)
    return settings_data.get(str(guild_id), {})

def set_guild_settings(guild_id, data):
    """Set settings for a guild. ALWAYS load, modify, save (no caching)."""
    settings_data = load_json(SETTINGS_FILE)
    settings_data[str(guild_id)] = data
    save_json(SETTINGS_FILE, settings_data)

def log_server_event(guild_id, message, user=None, shard_id=None):
    """Append a rich metadata log message to the server's log file."""
    try:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        
        # 1. Identity Tagging
        user_tag = f" | User: {user.name} ({user.id})" if user else ""
        
        # 2. Shard Tracking
        shard_tag = f" | SHARD #{shard_id}" if shard_id is not None else ""
        
        # 3. Final Entry Formatting
        log_entry = f"[{timestamp}]{shard_tag}{user_tag} >> {message}\n"
        
        # 4. Atomic Write
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logs_dir = os.path.join(base_dir, "data", "server_logs")
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir, exist_ok=True)
            
        file_path = os.path.join(logs_dir, f"{guild_id}.txt")
        
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
            
    except Exception as e:
        print(f"❌ Log Error for {guild_id}: {e}")

def update_last_seen(guild_id, user_id):
    """Record the last time a user was seen using a command in a guild."""
    try:
        from config.config import LAST_SEEN_FILE
        data = load_json(LAST_SEEN_FILE)
        if not isinstance(data, dict): data = {}
        
        gid = str(guild_id)
        uid = str(user_id)
        
        if gid not in data: data[gid] = {}
        data[gid][uid] = datetime.utcnow().isoformat()
        
        save_json(LAST_SEEN_FILE, data)
    except: pass

def reset_guild_settings(guild_id):
    """Reset guild settings to default (by removing them from the store)."""
    settings_data = load_json(SETTINGS_FILE)
    gid = str(guild_id)
    if gid in settings_data:
        del settings_data[gid]
        save_json(SETTINGS_FILE, settings_data)
        return True
    return False

def log_system_event(message, shard_id=None):
    """Global system-wide diagnostics (Shard Connections/Syncs)."""
    try:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        shard_tag = f"[SHARD #{shard_id}] " if shard_id is not None else "[ENGINE] "
        log_entry = f"[{timestamp}] {shard_tag}{message}\n"
        
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        file_path = os.path.join(base_dir, "data", "system_audit.txt")
        
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except: pass

def update_activity_audit_log(guild_id, check_id, channel_id, channel_name, starter_id, starter_name, start_time, end_time, duration_minutes, status, participants):
    """Save or update an activity check audit log entry."""
    try:
        from storage.json_store import load_json, save_json
        from config.config import DATA_DIR
        audit_file = os.path.join(DATA_DIR, "activity_audit_logs.json")
        audit_data = load_json(audit_file) or {}
        
        gid = str(guild_id)
        if gid not in audit_data:
            audit_data[gid] = {}
            
        audit_data[gid][check_id] = {
            "check_id": check_id,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "starter_id": starter_id,
            "starter_name": starter_name,
            "start_time": start_time,
            "end_time": end_time,
            "duration_minutes": duration_minutes,
            "status": status,
            "participants": participants,
            "winners": participants if status != "active" else []
        }
        save_json(audit_file, audit_data)
    except Exception as e:
        print(f"❌ Failed to write audit log: {e}")

