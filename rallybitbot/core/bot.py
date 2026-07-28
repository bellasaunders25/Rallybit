from __future__ import annotations

import asyncio
import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands

from config.config import DATA_DIR, DISCORD_TOKEN, SHARD_COUNT, SUPPORT_SERVER_URL
from core.bot_settings import get_branding, get_webhook
from core.command_visibility import (
    add_private_options,
    begin_command_visibility,
    force_command_visibility,
    install_response_visibility,
    reset_command_visibility,
)
from core.service_notice import get_service_notice

NOTICE_SENT_KEY = "rallybit_service_notice_sent"


def _service_notice_embed(notice: dict) -> discord.Embed:
    embed = discord.Embed(
        title=notice["title"],
        description=notice["message"],
        color=0xF0B232,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="Rallybit commands are temporarily paused")
    return embed


async def send_service_notice(interaction: discord.Interaction, notice: dict) -> bool:
    """Acknowledge a blocked command before CommandTree stops dispatching it."""
    extras = getattr(interaction, "extras", None)
    if isinstance(extras, dict) and extras.get(NOTICE_SENT_KEY):
        return True

    embed = _service_notice_embed(notice)
    visibility_token = force_command_visibility(True)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except discord.HTTPException as exc:
        print(f"[SERVICE NOTICE] Unable to acknowledge command: {exc!r}")
        return False
    finally:
        reset_command_visibility(visibility_token)

    if isinstance(extras, dict):
        extras[NOTICE_SENT_KEY] = True
    return True


class RallybitCommandTree(app_commands.CommandTree):
    async def _call(self, interaction: discord.Interaction) -> None:
        token = begin_command_visibility(interaction)
        try:
            await super()._call(interaction)
        finally:
            if interaction.guild is not None and interaction.command is not None and not get_service_notice()["active"]:
                try:
                    from core.audit import audit_command_interaction

                    await audit_command_interaction(interaction)
                except Exception as exc:
                    print(f"[AUDIT] Unable to log command use: {exc!r}")
            reset_command_visibility(token)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        notice = get_service_notice()
        if not notice["active"]:
            return True
        await send_service_notice(interaction, notice)
        return False


class BotClient(discord.AutoShardedClient):
    def __init__(self, intents: discord.Intents):
        super().__init__(intents=intents, shard_count=SHARD_COUNT)
        self.tree = RallybitCommandTree(self)
        self.tree.on_error = self.on_app_command_error
        self._commands_synced = False
        self.shard_starts: dict[int, datetime] = {}
        self.restarting_shards: set[int] = set()
        self._extra_listeners: dict[str, list] = defaultdict(list)

    def add_listener(self, coro, name: str | None = None) -> None:
        """Register an additional event listener without replacing BotClient methods."""
        event_name = name or coro.__name__
        if not event_name.startswith("on_"):
            event_name = f"on_{event_name}"
        self._extra_listeners[event_name].append(coro)

    async def _run_extra_listener(self, coro, event_name: str, *args, **kwargs) -> None:
        try:
            await coro(*args, **kwargs)
        except Exception as exc:
            print(f"[EVENT ERROR] {event_name}: {exc!r}")

    def dispatch(self, event: str, /, *args, **kwargs) -> None:
        super().dispatch(event, *args, **kwargs)
        event_name = f"on_{event}"
        for coro in tuple(self._extra_listeners.get(event_name, ())):
            self.loop.create_task(
                self._run_extra_listener(coro, event_name, *args, **kwargs),
                name=f"rallybit:{event_name}",
            )

    async def setup_hook(self):
        from commands.activity import setup_activity_commands
        from commands.admin import setup_admin_commands
        from commands.afk import on_message as afk_on_message
        from commands.afk import setup_afk_commands
        from commands.analytics import setup_analytics_commands
        from commands.audit_logs import (
            on_bulk_message_delete as audit_on_bulk_message_delete,
        )
        from commands.audit_logs import (
            on_guild_channel_create as audit_on_guild_channel_create,
        )
        from commands.audit_logs import (
            on_guild_channel_delete as audit_on_guild_channel_delete,
        )
        from commands.audit_logs import (
            on_guild_channel_update as audit_on_guild_channel_update,
        )
        from commands.audit_logs import (
            on_guild_emojis_update as audit_on_guild_emojis_update,
        )
        from commands.audit_logs import (
            on_guild_role_create as audit_on_guild_role_create,
        )
        from commands.audit_logs import (
            on_guild_role_delete as audit_on_guild_role_delete,
        )
        from commands.audit_logs import (
            on_guild_role_update as audit_on_guild_role_update,
        )
        from commands.audit_logs import (
            on_guild_stickers_update as audit_on_guild_stickers_update,
        )
        from commands.audit_logs import on_guild_update as audit_on_guild_update
        from commands.audit_logs import on_invite_create as audit_on_invite_create
        from commands.audit_logs import on_invite_delete as audit_on_invite_delete
        from commands.audit_logs import on_member_ban as audit_on_member_ban
        from commands.audit_logs import on_member_join as audit_on_member_join
        from commands.audit_logs import on_member_remove as audit_on_member_remove
        from commands.audit_logs import on_member_unban as audit_on_member_unban
        from commands.audit_logs import on_member_update as audit_on_member_update
        from commands.audit_logs import on_message_delete as audit_on_message_delete
        from commands.audit_logs import on_message_edit as audit_on_message_edit
        from commands.audit_logs import (
            on_scheduled_event_create as audit_on_scheduled_event_create,
        )
        from commands.audit_logs import (
            on_scheduled_event_delete as audit_on_scheduled_event_delete,
        )
        from commands.audit_logs import (
            on_scheduled_event_update as audit_on_scheduled_event_update,
        )
        from commands.audit_logs import on_thread_create as audit_on_thread_create
        from commands.audit_logs import on_thread_delete as audit_on_thread_delete
        from commands.audit_logs import on_thread_update as audit_on_thread_update
        from commands.audit_logs import (
            on_voice_state_update as audit_on_voice_state_update,
        )
        from commands.audit_logs import on_webhooks_update as audit_on_webhooks_update
        from commands.audit_logs import setup_audit_log_commands
        from commands.automation import setup_automation_commands
        from commands.channel_archives import setup_channel_archive_commands
        from commands.community import setup_community_commands
        from commands.giveaways import setup_giveaway_commands
        from commands.help import setup_help_commands
        from commands.leaderboard import setup_leaderboard_command
        from commands.levels import on_message as levels_on_message
        from commands.levels import setup_level_commands
        from commands.manual_roles import setup_manual_role_commands
        from commands.moderation import setup_moderation_commands
        from commands.premium import setup_premium_commands
        from commands.prettfy import setup_prettfy_command
        from commands.profile import setup_profile_command
        from commands.quizzes import setup_quiz_commands
        from commands.reports import setup_report_commands
        from commands.reviews import setup_review_command
        from commands.roles import (
            on_member_join as roles_on_member_join,
        )
        from commands.roles import (
            on_raw_reaction_add,
            on_raw_reaction_remove,
            setup_role_commands,
        )
        from commands.security import setup_security_commands
        from commands.snipes import on_message_delete as snipes_on_message_delete
        from commands.snipes import on_message_edit as snipes_on_message_edit
        from commands.snipes import setup_snipe_commands
        from commands.support import setup_support_command
        from commands.tickets import setup_ticket_commands
        from commands.utility import setup_utility_commands
        from commands.welcomes import (
            on_member_join as welcome_on_member_join,
        )
        from commands.welcomes import (
            on_member_remove as welcome_on_member_remove,
        )
        from commands.welcomes import (
            setup_welcome_commands,
        )
        from commands.workforce import setup_workforce_commands

        setup_activity_commands(self.tree)
        setup_audit_log_commands(self.tree)
        setup_afk_commands(self.tree)
        setup_leaderboard_command(self.tree)
        setup_profile_command(self.tree)
        setup_premium_commands(self.tree)
        setup_prettfy_command(self.tree)
        setup_help_commands(self.tree)
        setup_support_command(self.tree)
        setup_ticket_commands(self.tree)
        setup_analytics_commands(self.tree)
        setup_automation_commands(self.tree)
        setup_utility_commands(self.tree)
        setup_admin_commands(self.tree)
        setup_giveaway_commands(self.tree)
        setup_level_commands(self.tree)
        setup_quiz_commands(self.tree)
        setup_moderation_commands(self.tree)
        setup_manual_role_commands(self.tree)
        setup_report_commands(self.tree)
        setup_review_command(self.tree)
        setup_community_commands(self.tree)
        setup_channel_archive_commands(self.tree)
        setup_role_commands(self.tree)
        setup_security_commands(self.tree)
        setup_snipe_commands(self.tree)
        setup_welcome_commands(self.tree)
        setup_workforce_commands(self.tree)
        add_private_options(self.tree)
        install_response_visibility()

        self.add_listener(levels_on_message, "on_message")
        self.add_listener(afk_on_message, "on_message")
        self.add_listener(roles_on_member_join, "on_member_join")
        self.add_listener(on_raw_reaction_add, "on_raw_reaction_add")
        self.add_listener(on_raw_reaction_remove, "on_raw_reaction_remove")
        self.add_listener(snipes_on_message_delete, "on_message_delete")
        self.add_listener(snipes_on_message_edit, "on_message_edit")
        self.add_listener(welcome_on_member_join, "on_member_join")
        self.add_listener(welcome_on_member_remove, "on_member_remove")
        self.add_listener(audit_on_message_delete, "on_message_delete")
        self.add_listener(audit_on_message_edit, "on_message_edit")
        self.add_listener(audit_on_bulk_message_delete, "on_bulk_message_delete")
        self.add_listener(audit_on_member_join, "on_member_join")
        self.add_listener(audit_on_member_remove, "on_member_remove")
        self.add_listener(audit_on_member_update, "on_member_update")
        self.add_listener(audit_on_member_ban, "on_member_ban")
        self.add_listener(audit_on_member_unban, "on_member_unban")
        self.add_listener(audit_on_guild_role_create, "on_guild_role_create")
        self.add_listener(audit_on_guild_role_delete, "on_guild_role_delete")
        self.add_listener(audit_on_guild_role_update, "on_guild_role_update")
        self.add_listener(audit_on_guild_channel_create, "on_guild_channel_create")
        self.add_listener(audit_on_guild_channel_delete, "on_guild_channel_delete")
        self.add_listener(audit_on_guild_channel_update, "on_guild_channel_update")
        self.add_listener(audit_on_voice_state_update, "on_voice_state_update")
        self.add_listener(audit_on_thread_create, "on_thread_create")
        self.add_listener(audit_on_thread_delete, "on_thread_delete")
        self.add_listener(audit_on_thread_update, "on_thread_update")
        self.add_listener(audit_on_guild_emojis_update, "on_guild_emojis_update")
        self.add_listener(audit_on_guild_stickers_update, "on_guild_stickers_update")
        self.add_listener(audit_on_invite_create, "on_invite_create")
        self.add_listener(audit_on_invite_delete, "on_invite_delete")
        self.add_listener(audit_on_webhooks_update, "on_webhooks_update")
        self.add_listener(audit_on_guild_update, "on_guild_update")
        self.add_listener(audit_on_scheduled_event_create, "on_scheduled_event_create")
        self.add_listener(audit_on_scheduled_event_delete, "on_scheduled_event_delete")
        self.add_listener(audit_on_scheduled_event_update, "on_scheduled_event_update")

    def send_webhook(self, url: str | None, payload: dict) -> bool:
        if not url:
            return False
        try:
            request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"))
            request.add_header("Content-Type", "application/json")
            request.add_header("User-Agent", "Rallybit/8.1")
            with urllib.request.urlopen(request, timeout=7):
                pass
            return True
        except Exception as exc:
            print(f"[WEBHOOK] {exc}")
            return False

    def notify_public_status(self, shard_id: int, status: str):
        colours = {"ONLINE": 0x59E3A7, "RESTARTING": 0xFFB84D, "OFFLINE": 0xFF6B7D}
        self.send_webhook(get_webhook("public_status"), {"embeds": [{
            "title": f"Rallybit shard #{shard_id}",
            "description": f"Status: **{status.title()}**",
            "color": colours.get(status, 0x7C6CFF),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]})

    async def restart_cluster(self, shard_id: int) -> bool:
        shard = self.get_shard(shard_id)
        if shard is None:
            return False
        self.restarting_shards.add(shard_id)
        self.notify_public_status(shard_id, "RESTARTING")
        try:
            await shard.reconnect()
            return True
        finally:
            self.restarting_shards.discard(shard_id)

    def get_shard_stats(self) -> dict:
        stats = {}
        for shard_id, shard in self.shards.items():
            guilds = [guild for guild in self.guilds if guild.shard_id == shard_id]
            started = self.shard_starts.get(shard_id)
            uptime = int((datetime.utcnow() - started).total_seconds()) if started else 0
            stats[str(shard_id)] = {
                "status": "RESTARTING" if shard_id in self.restarting_shards else ("ONLINE" if shard.latency is not None else "OFFLINE"),
                "latency_ms": round(shard.latency * 1000) if shard.latency else None,
                "guild_count": len(guilds),
                "member_count": sum(g.member_count or 0 for g in guilds),
                "uptime_seconds": uptime,
            }
        return stats

    def save_guilds_to_file(self):
        payload = {"__SYSTEM__": {
            "shard_count": self.shard_count,
            "total_guilds": len(self.guilds),
            "total_members": sum(g.member_count or 0 for g in self.guilds),
            "shards": self.get_shard_stats(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}
        for guild in self.guilds:
            payload[str(guild.id)] = {
                "name": guild.name,
                "icon": str(guild.icon.url) if guild.icon else None,
                "member_count": guild.member_count or 0,
                "shard_id": guild.shard_id,
            }
        path = Path(DATA_DIR) / "bot_guilds.json"
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp.replace(path)

    def sync_guilds_to_disk(self):
        self.save_guilds_to_file()

    async def _cache_loop(self):
        while not self.is_closed():
            self.save_guilds_to_file()
            await asyncio.sleep(60)

    async def on_ready(self):
        if self._commands_synced:
            return
        self.save_guilds_to_file()
        try:
            synced_commands = await self.tree.sync()
            synced_names = ", ".join(command.name for command in synced_commands)
            print(f"✅ Synced {len(synced_commands)} global command groups: {synced_names}")
        except Exception as exc:
            print(f"[COMMAND SYNC FAILED] {type(exc).__name__}: {exc}")
            raise
        self._commands_synced = True
        self.loop.create_task(self._cache_loop())

        try:
            from core.plan_branding import plan_avatar_sync_loop
            self.loop.create_task(plan_avatar_sync_loop(self), name="rallybit:plan-avatars")
        except Exception as exc:
            print(f"[PLAN AVATARS] Unable to start plan branding: {exc!r}")

        try:
            from commands.manual_roles import temporary_role_sync_loop
            self.loop.create_task(temporary_role_sync_loop(self), name="rallybit:temporary-roles")
        except Exception as exc:
            print(f"[TEMP ROLES] Unable to start temporary-role timers: {exc!r}")

        try:
            from commands.tickets import restore_ticket_views
            restored_ticket_views = await restore_ticket_views(self)
            if restored_ticket_views:
                print(f"✅ Restored {restored_ticket_views} ticket views")
        except Exception as exc:
            print(f"[RESUME TICKETS] Unable to restore ticket views: {exc}")

        try:
            from commands.roles import restore_verification_views
            restored_verification_views = await restore_verification_views(self)
            if restored_verification_views:
                print(f"✅ Restored {restored_verification_views} verification views")
        except Exception as exc:
            print(f"[RESUME VERIFICATION] Unable to restore verification views: {exc}")

        try:
            from commands.giveaways import resume_active_giveaways
            restored_giveaways = await resume_active_giveaways(self)
            if restored_giveaways:
                print(f"✅ Restored {restored_giveaways} giveaways")
        except Exception as exc:
            print(f"[RESUME GIVEAWAYS] Unable to restore giveaways: {exc}")

        try:
            from commands.welcomes import on_ready_refresh
            await on_ready_refresh(self)
        except Exception as exc:
            print(f"[WELCOME CACHE] Unable to refresh invite tracking: {exc}")

        # Restore every durable interactive session before any scheduler is
        # allowed to create new work. This prevents a restart from producing
        # duplicate checks/quizzes while the recovery tasks are still loading.
        recovered = {"activity_checks": 0, "quizzes": 0, "pulses": 0}
        try:
            from commands.activity import resume_active_checks
            recovered["activity_checks"] = await resume_active_checks(self) or 0
        except Exception as exc:
            print(f"[RESUME ACTIVITY] Unable to restore checks: {exc}")
        try:
            from commands.quizzes import resume_active_quizzes
            recovered["quizzes"] = await resume_active_quizzes(self) or 0
        except Exception as exc:
            print(f"[RESUME QUIZ] Unable to restore quizzes: {exc}")
        try:
            from commands.community import resume_active_pulses
            recovered["pulses"] = await resume_active_pulses(self) or 0
        except Exception as exc:
            print(f"[RESUME PULSE] Unable to restore pulses: {exc}")
        if any(recovered.values()):
            print(
                "✅ Session recovery complete | "
                f"checks={recovered['activity_checks']} "
                f"quizzes={recovered['quizzes']} pulses={recovered['pulses']}"
            )

        try:
            from tasks.session_recovery import setup_session_recovery
            setup_session_recovery(self)
        except Exception as exc:
            print(f"[SESSION WATCHDOG] Unable to start: {exc}")

        try:
            from tasks.auto_activity import setup_auto_activity
            setup_auto_activity(self)
        except Exception as exc:
            print(f"[AUTO] Unable to start: {exc}")
        try:
            from commands.automation import setup_automation_task
            setup_automation_task(self)
        except Exception as exc:
            print(f"[AUTOMATION] Unable to start: {exc}")
        try:
            from tasks.status_logger import setup_status_task
            setup_status_task(self)
        except Exception as exc:
            print(f"[STATUS] Unable to start: {exc}")
        try:
            from tasks.auto_quiz import setup_auto_quiz
            setup_auto_quiz(self)
        except Exception as exc:
            print(f"[AUTO QUIZ] Unable to start: {exc}")
        try:
            from core.presence import rotate_presence
            if not rotate_presence.is_running():
                rotate_presence.start(self)
        except Exception as exc:
            print(f"[PRESENCE] Unable to start: {exc}")
        try:
            from core.bot_profile import apply_bot_profile
            await apply_bot_profile(self, include_identity=True)
        except Exception as exc:
            print(f"[BOT PROFILE] Unable to apply the configured profile: {exc!r}")
        brand, version = get_branding()
        print(f"✅ {brand} online | {len(self.guilds):,} servers | {version}")
        self.send_webhook(get_webhook("status"), {"embeds": [{
            "title": f"{brand} is online",
            "description": f"Connected to **{len(self.guilds):,}** servers across **{self.shard_count}** shard(s).",
            "color": 0x59E3A7,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]})

    async def on_shard_ready(self, shard_id: int):
        self.shard_starts[shard_id] = datetime.utcnow()
        self.notify_public_status(shard_id, "ONLINE")
        self.save_guilds_to_file()

    async def on_shard_disconnect(self, shard_id: int):
        if shard_id not in self.restarting_shards:
            self.notify_public_status(shard_id, "OFFLINE")

    async def on_guild_join(self, guild: discord.Guild):
        self.save_guilds_to_file()
        try:
            from core.plan_branding import sync_guild_plan_avatar
            result = await sync_guild_plan_avatar(guild)
            if not result.get("ok"):
                print(f"[PLAN AVATARS] New guild avatar update failed: {result!r}")
        except Exception as exc:
            print(f"[PLAN AVATARS] New guild branding failed: {exc!r}")
        try:
            owner = guild.owner or await self.fetch_user(guild.owner_id)
            await owner.send(f"Thanks for adding **Rallybit** to **{guild.name}**. Get started with `/help`.\nSupport: {SUPPORT_SERVER_URL}")
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def on_guild_remove(self, guild: discord.Guild):
        self.save_guilds_to_file()

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        notice = get_service_notice()
        if notice["active"]:
            await send_service_notice(interaction, notice)
            return
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need the required server permissions to use that command."
        elif isinstance(error, app_commands.CheckFailure):
            message = str(error) or "That command cannot be used here."
        else:
            message = "Rallybit hit an unexpected error while running that command. The incident has been logged."
            print(f"[COMMAND ERROR] {error!r}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


intents = discord.Intents.default()
intents.members = True
intents.reactions = True
intents.message_content = True
intents.moderation = True
intents.webhooks = True
intents.integrations = True
intents.messages = True
client = BotClient(intents=intents)


def run():
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing. Copy .env.example to .env and set the token.")
    client.run(DISCORD_TOKEN)
