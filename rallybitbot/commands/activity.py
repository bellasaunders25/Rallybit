import discord
from discord import app_commands
import asyncio
import re
import time
from datetime import datetime
from core.checks import slash_check_limit
from core.logging import (
    get_guild_settings, set_guild_settings, log_activity, 
    log_action_to_channel, save_global_stats, get_global_stats,
    log_server_event, save_activity_log, log_activity_result,
    update_activity_audit_log
)
from config.config import ACTIVE_CHECKS_FILE
from storage.json_store import load_json, save_json

# Registry for active checks - KEYED BY GUILD_ID to prevent overlap
active_guild_checks = {}

class ReactorButtonView(discord.ui.View):
    """Modern Button-based participation for Activity Checks."""
    def __init__(self, guild_id, starter_id, user_ids, users, winner_limit, end_event, label_template, check_id, channel_id, end_time):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.starter_id = starter_id
        self.user_ids = user_ids
        self.users = users
        self.winner_limit = winner_limit
        self.end_event = end_event
        self.label_template = label_template
        self.check_id = check_id
        self.channel_id = channel_id
        self.end_time = end_time
        
        # Initial label setup
        self.update_button_label()

    def update_button_label(self):
        count = len(self.users)
        new_label = self.label_template.replace("{count}", str(count))
        # Ensure label doesn't exceed Discord's 80 character limit
        self.join_check.label = new_label[:80]

    @discord.ui.button(label="I'm Active! ⚡", style=discord.ButtonStyle.success, custom_id="ac_join_btn")
    async def join_check(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Acknowledge immediately to prevent 'Unknown Interaction' (10062)
        await interaction.response.defer(ephemeral=True)

        if time.time() >= self.end_time:
            self.end_event.set()
            return await interaction.followup.send("⏰ This activity check has already ended.", ephemeral=True)

        if interaction.user.bot or interaction.user.id == self.starter_id:
            return await interaction.followup.send("🚫 You cannot participate in your own check!", ephemeral=True)
            
        if interaction.user.id in self.user_ids:
            return await interaction.followup.send("✅ You have already participated!", ephemeral=True)

        if len(self.users) >= self.winner_limit:
            return await interaction.followup.send("⏰ This check is already full!", ephemeral=True)

        now_ts = int(time.time())
        self.users.append({"user": interaction.user, "time": now_ts})
        self.user_ids.add(interaction.user.id)
        
        from core.logging import log_activity
        log_activity(self.guild_id, interaction.user.id, user_obj=interaction.user)
        
        # Save intermediate state to disk immediately
        try:
            winners_to_save = [{"user_id": u["user"].id, "name": u["user"].name, "time": u["time"]} for u in self.users]
            update_active_check_state(self.guild_id, self.channel_id, interaction.message.id, self.end_time, winners_to_save, self.starter_id, self.check_id)
        except Exception as e:
            print(f"⚠️ Failed to update button check state: {e}")
            
        # Update button label for everyone
        self.update_button_label()
        
        # We must edit the original message to show the updated count on the button
        try: await interaction.message.edit(view=self)
        except: pass

        await interaction.followup.send("✅ **Activity Logged!** You have been recorded.", ephemeral=True)
        if len(self.users) >= self.winner_limit: self.end_event.set()

def update_active_check_state(
    guild_id,
    channel_id,
    message_id,
    end_time,
    winners,
    starter_id,
    check_id,
    *,
    settings_snapshot=None,
    phase="active",
    final_status=None,
    start_time=None,
):
    """Save the current state of an activity check to disk."""
    data = load_json(ACTIVE_CHECKS_FILE)
    if not isinstance(data, dict): data = {}
    existing = data.get(str(guild_id), {}) if isinstance(data.get(str(guild_id)), dict) else {}
    payload = {
        "channel_id": channel_id,
        "message_id": message_id,
        "end_time": end_time,
        "winners": winners, # List of {"user_id": id, "name": name, "time": ts}
        "starter_id": starter_id,
        "check_id": check_id,
        "phase": phase,
        "final_status": final_status,
        "start_time": start_time if start_time is not None else existing.get("start_time"),
    }
    payload["settings_snapshot"] = settings_snapshot if settings_snapshot is not None else existing.get("settings_snapshot")
    data[str(guild_id)] = payload
    save_json(ACTIVE_CHECKS_FILE, data)

def remove_active_check_state(guild_id):
    """Clean up the state when a check finishes."""
    data = load_json(ACTIVE_CHECKS_FILE)
    if str(guild_id) in data:
        del data[str(guild_id)]
        save_json(ACTIVE_CHECKS_FILE, data)


def has_persisted_activity_check(guild_id: int | str) -> bool:
    data = load_json(ACTIVE_CHECKS_FILE)
    return isinstance(data, dict) and str(guild_id) in data

async def run_activitycheck(guild, interaction_or_channel, starter_user, resumed_data=None, settings_override=None):
    """Core activity check logic - PERSISTENCE ENABLED."""
    from core.bot import client as bot
    
    guild_id = str(guild.id)
    log_server_event(guild_id, f"🚀 Activity Check Started by {starter_user.name} ({starter_user.id})")
    live_settings = get_guild_settings(guild.id)
    saved_snapshot = resumed_data.get("settings_snapshot") if resumed_data and isinstance(resumed_data, dict) else None
    data = dict(settings_override) if isinstance(settings_override, dict) else (dict(saved_snapshot) if isinstance(saved_snapshot, dict) else live_settings)

    # All Rallybit features are available to every server.

    # Generate unique check ID
    if resumed_data:
        check_id = resumed_data.get("check_id")
        if not check_id:
            import random, string
            check_id = "AC-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    else:
        import random, string
        check_id = "AC-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

    # 1. UI Configuration. A snapshot is stored with the live session so a
    # restart cannot silently change its duration, target, ping or input mode.
    custom_text = data.get(
        "activity_text",
        "**RALLYBIT ACTIVITY CHECK**\n\nReact below if you're active!"
    )
    
    reactor = data.get("reactor", "✅")
    reactor_type = data.get("reactor_type", "reaction") # Default to reaction
    button_text = data.get("button_text", "I'm Active! ⚡")
    ping_target = data.get("ping_target", "@everyone")
    try:
        winner_limit = max(1, min(100, int(data.get("winner_count", 3))))
    except (TypeError, ValueError):
        winner_limit = 3
    try:
        duration_minutes = max(1, min(1440, int(data.get("check_duration_minutes", 60))))
    except (TypeError, ValueError):
        duration_minutes = 60
    button_text = str(button_text or "I'm Active! ⚡")[:80]
    custom_text = str(custom_text or "**RALLYBIT ACTIVITY CHECK**")[:1800]
    timeout_seconds = duration_minutes * 60
    settings_snapshot = {
        "activity_text": custom_text,
        "reactor": reactor,
        "reactor_type": reactor_type,
        "button_text": button_text,
        "ping_target": ping_target,
        "winner_count": winner_limit,
        "check_duration_minutes": duration_minutes,
    }
    
    duration_display = f"{duration_minutes/60:.1f}h" if duration_minutes >= 60 else f"{duration_minutes}m"
    is_inter = isinstance(interaction_or_channel, discord.Interaction)

    if ping_target == "@everyone":
        allowed = discord.AllowedMentions(everyone=True, roles=False, users=False)
    else:
        role_match = re.fullmatch(r"<@&(\d+)>", str(ping_target))
        role = guild.get_role(int(role_match.group(1))) if role_match else None
        allowed = discord.AllowedMentions(everyone=False, roles=[role] if role else False, users=False)

    # The command/automation launcher reserves the guild before yielding. Reuse
    # that event here so two nearly simultaneous launches cannot overlap.
    active_guild_checks.setdefault(guild.id, asyncio.Event())
    resume_phase = str(resumed_data.get("phase") or "active") if resumed_data else "active"
    final_status = str(resumed_data.get("final_status") or "timed_out") if resumed_data else "timed_out"
    cleanup_state = False
    
    # Initialize Winners
    users, user_ids = [], set()

    # Define high-fidelity audit saver
    def save_current_audit_state(status):
        try:
            participants = [{"user_id": str(p["user"].id), "username": p["user"].name, "timestamp": p["time"]} for p in users]
            st_iso = datetime.utcfromtimestamp(start_time_unix).isoformat()
            et_iso = datetime.utcnow().isoformat() if status != "active" else None
            update_activity_audit_log(
                guild_id=guild.id,
                check_id=check_id,
                channel_id=channel.id,
                channel_name=channel.name,
                starter_id=starter_user.id,
                starter_name=starter_user.name,
                start_time=st_iso,
                end_time=et_iso,
                duration_minutes=duration_minutes,
                status=status,
                participants=participants
            )
        except Exception as e:
            print(f"⚠️ Failed to update audit log live: {e}")

    def award_participants():
        """Give every recorded participant one win exactly once for this result."""
        if not users:
            return
        stats = get_global_stats()
        for entry in users:
            user = entry["user"]
            uid = str(user.id)
            record = stats.setdefault(uid, {"checkins": 0, "wins": 0, "streak": 0, "recent_wins": 0})
            awarded_checks = record.setdefault("awarded_activity_checks", [])
            if not isinstance(awarded_checks, list):
                awarded_checks = []
                record["awarded_activity_checks"] = awarded_checks
            if check_id in awarded_checks:
                continue
            record["wins"] = int(record.get("wins", 0)) + 1
            record["recent_wins"] = int(record.get("recent_wins", 0)) + 1
            record["streak"] = int(record.get("streak", 0)) + 1
            awarded_checks.append(check_id)
            record["awarded_activity_checks"] = awarded_checks[-5000:]
            record["username"] = user.name
            record["display_name"] = getattr(user, "display_name", user.name)
            avatar = getattr(user, "display_avatar", None)
            record["avatar_url"] = getattr(avatar, "url", None) if avatar else None
        save_global_stats(stats)

    # 2. Channel & Time Resolution
    if resumed_data:
        try:
            channel = guild.get_channel(int(resumed_data["channel_id"]))
            end_time_unix = float(resumed_data["end_time"])
            start_time_unix = float(resumed_data.get("start_time") or (end_time_unix - timeout_seconds))
        except Exception:
            active_guild_checks.pop(guild.id, None)
            remove_active_check_state(guild.id)
            return
    else:
        if is_inter:
            channel = interaction_or_channel.channel
        else:
            channel = interaction_or_channel
        start_time_unix = time.time()
        end_time_unix = start_time_unix + timeout_seconds

    if channel is None:
        active_guild_checks.pop(guild.id, None)
        remove_active_check_state(guild.id)
        return

    # 3. View Preparation (after channel and end_time_unix resolution)
    view = None
    if reactor_type == "button":
        view = ReactorButtonView(guild.id, starter_user.id, user_ids, users, winner_limit, active_guild_checks[guild.id], button_text, check_id, channel.id, end_time_unix)

    deploy_kwargs = {
        "content": f"{ping_target}\n\n{custom_text}",
        "allowed_mentions": allowed
    }
    if view is not None:
        deploy_kwargs["view"] = view

    # 4. Message Deployment
    message_recreated = False
    if resumed_data:
        try:
            msg = await channel.fetch_message(int(resumed_data["message_id"]))
        except discord.NotFound:
            # If the original prompt disappeared while Rallybit was offline,
            # recreate it and continue with the saved participants and timer.
            recovery_kwargs = dict(deploy_kwargs)
            recovery_kwargs["content"] = f"♻️ **Rallybit restored this activity check after a restart.**\n\n{custom_text}"
            recovery_kwargs["allowed_mentions"] = discord.AllowedMentions.none()
            msg = await channel.send(**recovery_kwargs)
            message_recreated = True
        except (discord.Forbidden, discord.HTTPException):
            # Keep the recovery ledger intact; a later restart/retry can
            # restore the check once Discord or the permissions recover.
            raise
    else:
        if is_inter:
            msg = await interaction_or_channel.followup.send(**deploy_kwargs)
        else:
            msg = await channel.send(**deploy_kwargs)

    # Register Event & Initial State (Already handled above)
    if resumed_data:
        for w in resumed_data.get("winners", []):
            try:
                # We need discord objects, so we fetch if possible or create mock
                u = guild.get_member(int(w["user_id"])) or await bot.fetch_user(int(w["user_id"]))
                users.append({"user": u, "time": w["time"]})
                user_ids.add(u.id)
            except Exception:
                continue
        if view is not None:
            view.update_button_label()
            try:
                bot.add_view(view, message_id=msg.id)
                await msg.edit(view=view)
            except (discord.HTTPException, ValueError):
                pass
        winners_to_save = [{"user_id": u["user"].id, "name": u["user"].name, "time": u["time"]} for u in users]
        update_active_check_state(
            guild.id,
            channel.id,
            msg.id,
            end_time_unix,
            winners_to_save,
            starter_user.id,
            check_id,
            settings_snapshot=settings_snapshot,
            phase=str(resumed_data.get("phase") or "active"),
            final_status=resumed_data.get("final_status"),
            start_time=start_time_unix,
        )
    
    # Save Initial State (if new) and log check startup
    if not resumed_data:
        update_active_check_state(
            guild.id,
            channel.id,
            msg.id,
            end_time_unix,
            [],
            starter_user.id,
            check_id,
            settings_snapshot=settings_snapshot,
            start_time=start_time_unix,
        )
        save_current_audit_state("active")
        
        # Log to Discord logging channel if configured
        if starter_user != bot.user:
            detail_msg = f"Activity check started in {channel.mention}.\n**ID:** `{check_id}`\n**Winners Needed:** {winner_limit}\n**Duration:** {duration_display}\n**Mode:** {reactor_type.upper()}"
            await log_action_to_channel(guild, starter_user, "activitycheck", detail_msg, channel)

    # 5. Emoji Resolution
    emoji_obj = None
    validation_str = reactor
    custom_match = re.match(r'<(a?):([a-zA-Z0-9_]+):([0-9]+)>', reactor)
    if custom_match:
        emoji_id = int(custom_match.group(3))
        emoji_obj = bot.get_emoji(emoji_id)
        if emoji_obj: validation_str = str(emoji_obj)
        else: validation_str = "✅" 

    target_reaction = emoji_obj if emoji_obj else (validation_str if validation_str == "✅" else reactor)
    if (not resumed_data or message_recreated) and reactor_type == "reaction":
        try: await msg.add_reaction(target_reaction)
        except: 
            target_reaction = "✅"; validation_str = "✅"
            await msg.add_reaction("✅")

    def check(reaction, user):
        return (str(reaction.emoji) == validation_str and reaction.message.id == msg.id and not user.bot and user.id != starter_user.id)

    async def sync_reactions():
        if reactor_type != "reaction": return
        try:
            refreshed_msg = await channel.fetch_message(msg.id)
            for reaction in refreshed_msg.reactions:
                if str(reaction.emoji) == validation_str:
                    async for u in reaction.users():
                        if u.bot or u.id == starter_user.id: continue
                        if u.id not in user_ids and len(users) < winner_limit:
                            now_ts = int(time.time())
                            users.append({"user": u, "time": now_ts})
                            user_ids.add(u.id)
                            log_activity(guild_id, u.id, user_obj=u)
                            # SAVE STATE ON SYNC
                            winners_to_save = [{"user_id": u["user"].id, "name": u["user"].name, "time": u["time"]} for u in users]
                            update_active_check_state(guild_id, channel.id, msg.id, end_time_unix, winners_to_save, starter_user.id, check_id)
                            save_current_audit_state("active")
        except: pass

    async def publish_final_message(content: str) -> None:
        """Finalise the original prompt in-place so recovery is idempotent."""
        try:
            await msg.edit(content=content, view=None, allowed_mentions=discord.AllowedMentions.none())
            if reactor_type == "reaction":
                try:
                    await msg.clear_reactions()
                except (discord.Forbidden, discord.HTTPException):
                    pass
        except discord.NotFound:
            await channel.send(content, allowed_mentions=discord.AllowedMentions.none())

    # 6. Main Execution Loop
    try:
        if resume_phase == "finalizing" and final_status != "completed":
            raise asyncio.TimeoutError()
        while len(users) < winner_limit:
            remaining = end_time_unix - time.time()
            if remaining <= 0: raise asyncio.TimeoutError()
            heartbeat = min(remaining, 60.0)

            # --- Task Creation ---
            stop_task = asyncio.create_task(active_guild_checks[guild.id].wait())
            def check_delete(m): return m.id == msg.id
            delete_task = asyncio.create_task(bot.wait_for("message_delete", check=check_delete))

            tasks = [stop_task, delete_task]
            reaction_task = None
            if reactor_type == "reaction":
                reaction_task = asyncio.create_task(bot.wait_for("reaction_add", check=check))
                tasks.append(reaction_task)

            done, pending = await asyncio.wait(
                tasks, 
                timeout=heartbeat,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            for t in pending: 
                t.cancel()
                try: await t 
                except: pass

            if delete_task in done:
                try:
                    delete_task.result()
                    log_server_event(guild_id, "⚠️ Aborted: Message deleted.")
                    save_current_audit_state("cancelled")
                    cleanup_state = True
                    return await channel.send("⚠️ **Activity Check Cancelled**: The original message was deleted.")
                except: pass

            if stop_task in done:
                final_status = "manually_stopped"
                winners_to_save = [{"user_id": u["user"].id, "name": u["user"].name, "time": u["time"]} for u in users]
                update_active_check_state(
                    guild.id, channel.id, msg.id, end_time_unix, winners_to_save,
                    starter_user.id, check_id, settings_snapshot=settings_snapshot,
                    phase="finalizing", final_status=final_status, start_time=start_time_unix,
                )
                log_server_event(guild_id, "🛑 Manual Stop: Received.")
                stopper = None
                try:
                    event_obj = active_guild_checks.get(guild.id)
                    if event_obj:
                        stopper = getattr(event_obj, "_stopper_user", None)
                except: pass
                if not stopper:
                    stopper = starter_user
                
                # Update audit log status as manually stopped with final participants
                save_current_audit_state("manually_stopped")
                
                # Log manual stop to logging channel
                try:
                    await log_action_to_channel(guild, stopper, "endactivitycheck", f"Manually stopped the active check.\n**ID:** `{check_id}`", channel)
                except: pass
                
                raise asyncio.TimeoutError()

            try:
                if reaction_task in done:
                    now_ts = int(time.time())
                    _, user = reaction_task.result()
                    if not user.bot and user.id not in user_ids and user.id != starter_user.id:
                        users.append({"user": user, "time": now_ts})
                        user_ids.add(user.id)
                        log_activity(guild_id, user.id, user_obj=user)
                        # SAVE STATE ON REACTION
                        winners_to_save = [{"user_id": u["user"].id, "name": u["user"].name, "time": u["time"]} for u in users]
                        update_active_check_state(guild_id, channel.id, msg.id, end_time_unix, winners_to_save, starter_user.id, check_id)
                        save_current_audit_state("active")
            except: pass
            
            await sync_reactions()
            if len(users) >= winner_limit: break

        # 7. Completion
        final_status = "completed"
        winners_to_save = [{"user_id": u["user"].id, "name": u["user"].name, "time": u["time"]} for u in users]
        update_active_check_state(
            guild.id, channel.id, msg.id, end_time_unix, winners_to_save,
            starter_user.id, check_id, settings_snapshot=settings_snapshot,
            phase="finalizing", final_status=final_status, start_time=start_time_unix,
        )
        save_current_audit_state(final_status)
        result = "**✅ RALLYBIT CHECK COMPLETE**\n\n"
        medals = ["🥇", "🥈", "🥉"]
        log_winners = []
        for i, entry in enumerate(users):
            u = entry["user"]
            ts = entry["time"]
            medal = medals[i] if i < 3 else "🏅"
            result += f"{medal} **{i+1}.** {u.mention}\n"
            log_winners.append(f"{u.name} (at <t:{ts}:F>)")

        await publish_final_message(result)

        award_participants()
        
        winners_str = ", ".join(log_winners)
        log_server_event(guild_id, f"🏁 Check Complete. Winners: {winners_str}")
        await log_activity_result(guild, users, duration_minutes, check_id=check_id)
        cleanup_state = True

    except asyncio.TimeoutError:
        winners_to_save = [{"user_id": u["user"].id, "name": u["user"].name, "time": u["time"]} for u in users]
        update_active_check_state(
            guild.id, channel.id, msg.id, end_time_unix, winners_to_save,
            starter_user.id, check_id, settings_snapshot=settings_snapshot,
            phase="finalizing", final_status=final_status, start_time=start_time_unix,
        )
        if users:
            save_current_audit_state(final_status)
            result = "**✅ RALLYBIT CHECK ENDED**\n\n"
            medals = ["🥇", "🥈", "🥉"]
            log_winners = []
            for i, entry in enumerate(winners := users):
                u = entry["user"]
                ts = entry["time"]
                medal = medals[i] if i < 3 else "🏅"
                result += f"{medal} **{i+1}.** {u.mention}\n"
                log_winners.append(f"{u.name} (at <t:{ts}:F>)")
            
            await publish_final_message(result)
            
            award_participants()
            winners_str = ", ".join(log_winners)
            event_label = "Manually stopped" if final_status == "manually_stopped" else "Time ended"
            log_server_event(guild_id, f"⏰ {event_label}. Recorded participants: {winners_str}")
            await log_activity_result(guild, users, duration_minutes, check_id=check_id, status=final_status)
            cleanup_state = True
        else:
            save_current_audit_state(final_status)
            empty_message = "🛑 This activity check was stopped before anyone participated." if final_status == "manually_stopped" else "⏰ Time's up! Not enough participants reacted."
            await publish_final_message(empty_message)
            log_server_event(guild_id, f"Check ended with no participants ({final_status}).")
            await log_activity_result(guild, [], duration_minutes, check_id=check_id, status=final_status)
            cleanup_state = True

    except Exception as exc:
        winners_to_save = [{"user_id": u["user"].id, "name": u["user"].name, "time": u["time"]} for u in users]
        update_active_check_state(
            guild.id, channel.id, msg.id, end_time_unix, winners_to_save,
            starter_user.id, check_id, settings_snapshot=settings_snapshot,
            phase="active", final_status=final_status, start_time=start_time_unix,
        )
        log_server_event(guild_id, f"Activity check {check_id} paused for recovery after an internal error: {exc}")
        raise

    finally:
        active_guild_checks.pop(guild.id, None)
        if cleanup_state:
            remove_active_check_state(guild.id)
        save_activity_log(); save_global_stats()

async def resume_active_checks(bot):
    """Scan the ledger and relaunch all active checks after a restart."""
    data = load_json(ACTIVE_CHECKS_FILE)
    if not isinstance(data, dict) or not data:
        return 0
    pending = {
        gid: saved for gid, saved in data.items()
        if not str(gid).isdigit() or int(gid) not in active_guild_checks
    }
    if not pending:
        return 0
    print(f"🔄 [Resumer] Found {len(pending)} ongoing activity checks. Re-hydrating...")
    restored = 0
    for gid_str, check_data in pending.items():
        try:
            guild = bot.get_guild(int(gid_str))
            if not guild:
                remove_active_check_state(gid_str)
                continue
            if guild.id in active_guild_checks:
                continue

            try:
                starter = guild.get_member(int(check_data["starter_id"])) or await bot.fetch_user(int(check_data["starter_id"]))
            except Exception:
                starter = guild.me or bot.user
                if starter is None:
                    print(f"[RESUME] Could not resolve a starter for {gid_str}; keeping recovery data.")
                    continue

            async def launch_resume(target_guild=guild, target_starter=starter, target_data=check_data):
                try:
                    await run_activitycheck(target_guild, None, target_starter, resumed_data=target_data)
                except Exception as exc:
                    active_guild_checks.pop(target_guild.id, None)
                    print(f"[RESUME] {target_guild.id}: {exc}")

            # Reserve synchronously before the task starts so automatic checks
            # cannot race the recovery process during startup.
            active_guild_checks[guild.id] = asyncio.Event()
            asyncio.create_task(launch_resume(), name=f"rallybit-resume-{guild.id}")
            print(f" ✅ Resumed: {guild.name}")
            restored += 1
        except Exception as exc:
            print(f"[RESUME] Could not restore {gid_str}: {exc}")
    return restored

class SetActivityTextModal(discord.ui.Modal, title="Custom Activity Message"):
    """High-fidelity multi-line input for activity check text."""
    text_input = discord.ui.TextInput(
        label="Activity Check Text",
        placeholder="Enter your custom message here...\nTip: You can use @everyone, {ping}, or any emoji!",
        style=discord.TextStyle.paragraph,
        required=True,
        min_length=5,
        max_length=1500
    )

    async def on_submit(self, interaction: discord.Interaction):
        from core.logging import get_guild_settings, set_guild_settings, log_action_to_channel
        data = get_guild_settings(interaction.guild.id)
        content = self.text_input.value
        data["activity_text"] = content
        set_guild_settings(interaction.guild.id, data)
        
        # Restore high-fidelity logging from previous engine
        await log_action_to_channel(
            interaction.guild, 
            interaction.user, 
            "setactivitytext", 
            details=f"Activity text updated: '{content[:50]}...'", 
            command_channel=interaction.channel
        )
        
        await interaction.response.send_message("✅ **Custom Text Saved**: Your formatting (spaces/newlines) has been preserved exactly.", ephemeral=True)


async def launch_activity_check(guild, channel, starter_user, settings_override=None):
    """Reserve and launch an activity check without blocking the caller.

    Used by slash commands, the dashboard and multi-channel automation.
    """
    if guild.id in active_guild_checks or has_persisted_activity_check(guild.id):
        raise RuntimeError("An activity check is already active in this server.")
    active_guild_checks[guild.id] = asyncio.Event()

    async def launch():
        try:
            await run_activitycheck(guild, channel, starter_user, settings_override=settings_override)
        except Exception as exc:
            active_guild_checks.pop(guild.id, None)
            print(f"[ACTIVITY CHECK] {guild.id}: {exc}")
            try:
                await channel.send("⚠️ Rallybit could not start the activity check. Please check its channel permissions.")
            except discord.HTTPException:
                pass

    asyncio.create_task(launch(), name=f"rallybit-check-{guild.id}")
    return True

def setup_activity_commands(tree):
    """Unified Registry for All Activity & Configuration Commands."""
    
    @tree.command(name="activitycheck", description="Start an activity check in the server.")
    @slash_check_limit("activitycheck")
    async def activitycheck(interaction: discord.Interaction):
        if interaction.guild.id in active_guild_checks or has_persisted_activity_check(interaction.guild.id):
            return await interaction.response.send_message("🚫 **Ongoing Check**: There is already an active activity check running in this server!", ephemeral=True)

        data = get_guild_settings(interaction.guild.id)
        perm_role = data.get("permitted_role")
        try:
            permitted_role_id = int(perm_role) if perm_role else None
        except (TypeError, ValueError):
            permitted_role_id = None
        has_perm = any(r.id == permitted_role_id for r in interaction.user.roles) if permitted_role_id else False
        if not (has_perm or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("🚫 **No Permission**.", ephemeral=True)
        
        await launch_activity_check(interaction.guild, interaction.channel, interaction.user)
        await interaction.response.send_message("✅ Rallybit is starting the activity check.", ephemeral=True)

    @tree.command(name="setlogs", description="Set the channel used for activity reports.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setlogs(interaction: discord.Interaction, channel: discord.TextChannel):
        data = get_guild_settings(interaction.guild.id); data["log_channel_id"] = channel.id; set_guild_settings(interaction.guild.id, data)
        await interaction.response.send_message(f"✅ **Logs Configured**: Activity reports will be sent to {channel.mention}")
        await log_action_to_channel(interaction.guild, interaction.user, "setlogs", f"Log channel configured to {channel.mention}.", interaction.channel)

    @tree.command(name="setactivitytext", description="Set high-fidelity custom activity check text (Modal).")
    @app_commands.checks.has_permissions(administrator=True)
    async def setactivitytext(interaction: discord.Interaction):
        await interaction.response.send_modal(SetActivityTextModal())

    @tree.command(name="setreactor", description="Set reaction emoji.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setreactor(interaction: discord.Interaction, emoji: str):
        data = get_guild_settings(interaction.guild.id); data["reactor"] = emoji; set_guild_settings(interaction.guild.id, data)
        await interaction.response.send_message(f"✅ Reactor: {emoji}")
        await log_action_to_channel(interaction.guild, interaction.user, "setreactor", f"Reactor emoji updated to: {emoji}", interaction.channel)

    @tree.command(name="setping", description="Set who to ping (@everyone or Role).")
    @app_commands.checks.has_permissions(administrator=True)
    async def setping(interaction: discord.Interaction, role: discord.Role = None):
        data = get_guild_settings(interaction.guild.id)
        target = role.mention if role else "@everyone"
        data["ping_target"] = target; set_guild_settings(interaction.guild.id, data)
        await interaction.response.send_message(f"✅ Ping: {target}", allowed_mentions=discord.AllowedMentions.none())
        await log_action_to_channel(interaction.guild, interaction.user, "setping", f"Ping target updated to: {target}", interaction.channel)

    @tree.command(name="setwinner", description="Set the number of participants required to finish a check.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setwinner(interaction: discord.Interaction, count: int):
        if count < 1 or count > 100:
            return await interaction.response.send_message("❌ **Invalid Count**: Please choose a number between 1 and 100.", ephemeral=True)

        data = get_guild_settings(interaction.guild.id); data["winner_count"] = count; set_guild_settings(interaction.guild.id, data)
        await interaction.response.send_message(f"✅ **Winners Updated**: Checks will now end after **{count}** active members react.")
        await log_action_to_channel(interaction.guild, interaction.user, "setwinner", f"Winner count updated to: {count}", interaction.channel)

    @tree.command(name="setduration", description="Set how long an activity check remains open.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setduration(interaction: discord.Interaction, minutes: int):
        if minutes < 1 or minutes > 1440:
            return await interaction.response.send_message("❌ **Invalid Duration**: Please choose between 1 and 1440 minutes (24h).", ephemeral=True)

        data = get_guild_settings(interaction.guild.id); data["check_duration_minutes"] = minutes; set_guild_settings(interaction.guild.id, data)
        await interaction.response.send_message(f"✅ **Duration Updated**: Activity checks will now last for **{minutes} minutes**.")
        await log_action_to_channel(interaction.guild, interaction.user, "setduration", f"Check duration updated to: {minutes} minutes.", interaction.channel)

    @tree.command(name="setperm", description="Set role permission for non-admins.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setperm(interaction: discord.Interaction, role: discord.Role):
        data = get_guild_settings(interaction.guild.id); data["permitted_role"] = role.id; set_guild_settings(interaction.guild.id, data)
        await interaction.response.send_message(f"✅ Permitted: {role.mention}")
        await log_action_to_channel(interaction.guild, interaction.user, "setperm", f"Permitted role updated to: {role.name} ({role.id})", interaction.channel)

    @tree.command(name="setauto", description="Schedule recurring activity checks.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setauto(interaction: discord.Interaction, hours: int, channel: discord.TextChannel):
        if hours < 1 or hours > 168: return await interaction.response.send_message("❌ **Invalid Interval**: Choose between 1 and 168 hours.", ephemeral=True)
            
        data = get_guild_settings(interaction.guild.id); data["auto_hours"] = hours; data["auto_channel"] = channel.id; data["auto_enabled"] = True
        set_guild_settings(interaction.guild.id, data); await interaction.response.send_message(f"✅ **Auto-Check Enabled**: Running every **{hours}h** in {channel.mention}")
        await log_action_to_channel(interaction.guild, interaction.user, "setauto", f"Recurring auto-checks enabled: running every {hours}h in {channel.mention}.", interaction.channel)

    @tree.command(name="startauto", description="Resume scheduled activity checks.")
    @app_commands.checks.has_permissions(administrator=True)
    async def startauto(interaction: discord.Interaction):
        data = get_guild_settings(interaction.guild.id)
        if not data.get("auto_hours") or not data.get("auto_channel"):
            return await interaction.response.send_message("❌ **Not Configured**: Use `/setauto` first to set your schedule.", ephemeral=True)
            
        data["auto_enabled"] = True; set_guild_settings(interaction.guild.id, data)
        await interaction.response.send_message("▶️ **Auto-Checks Resumed**.")
        await log_action_to_channel(interaction.guild, interaction.user, "startauto", "Resumed automated activity checks.", interaction.channel)

    @tree.command(name="stopauto", description="Pause scheduled activity checks.")
    @app_commands.checks.has_permissions(administrator=True)
    async def stopauto(interaction: discord.Interaction):
        data = get_guild_settings(interaction.guild.id); data["auto_enabled"] = False; set_guild_settings(interaction.guild.id, data)
        await interaction.response.send_message("⏸️ **Auto-Checks Paused**.")
        await log_action_to_channel(interaction.guild, interaction.user, "stopauto", "Paused automated activity checks.", interaction.channel)

    @tree.command(name="endactivitycheck", description="Manually stop the current check.")
    async def endactivitycheck(interaction: discord.Interaction):
        data = get_guild_settings(interaction.guild.id)
        try:
            permitted_role_id = int(data.get("permitted_role")) if data.get("permitted_role") else None
        except (TypeError, ValueError):
            permitted_role_id = None
        has_perm = interaction.user.guild_permissions.administrator or (permitted_role_id and any(role.id == permitted_role_id for role in interaction.user.roles))
        if not has_perm:
            return await interaction.response.send_message("🚫 You do not have permission to stop this check.", ephemeral=True)
        if interaction.guild.id in active_guild_checks:
            # Track who stopped it manually
            stop_event = active_guild_checks[interaction.guild.id]
            stop_event._stopper_user = interaction.user
            stop_event.set()
            await interaction.response.send_message("✅ Signal sent to end server activity check.", ephemeral=True)
        else: await interaction.response.send_message("❌ No check active in this server.", ephemeral=True)

    @tree.command(name="setmode", description="Choose between emoji reactions and buttons.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(mode=[
        app_commands.Choice(name="Emoji Reactions (Classic)", value="reaction"),
        app_commands.Choice(name="Buttons (Modern)", value="button")
    ])
    async def setmode(interaction: discord.Interaction, mode: app_commands.Choice[str]):
        data = get_guild_settings(interaction.guild.id)
        data["reactor_type"] = mode.value
        set_guild_settings(interaction.guild.id, data)
        
        await interaction.response.send_message(f"✅ **Check Mode Updated**: Now using **{mode.name}** reactor system.")
        await log_action_to_channel(interaction.guild, interaction.user, "setmode", f"Reaction mode updated to: {mode.name}", interaction.channel)

    @tree.command(name="setbuttontext", description="Customize the activity check button text.")
    @app_commands.describe(text="The label for the button. Use {count} for live participant numbers.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setbuttontext(interaction: discord.Interaction, text: str):
        if len(text) > 80:
            return await interaction.response.send_message("❌ **Limit Exceeded**: Button text must be 80 characters or fewer.", ephemeral=True)
            
        data = get_guild_settings(interaction.guild.id)
        data["button_text"] = text
        set_guild_settings(interaction.guild.id, data)
        
        preview = text.replace("{count}", "0")
        await interaction.response.send_message(f"✅ **Button Text Updated!**\nPreview: `{preview}`")
        await log_action_to_channel(interaction.guild, interaction.user, "setbuttontext", f"Button text updated to: {text}", interaction.channel)
