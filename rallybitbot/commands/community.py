from __future__ import annotations

import asyncio
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands

from config.config import ACTIVE_PULSES_FILE, COMMUNITY_SETTINGS_FILE, PULSE_HISTORY_FILE
from core.checks import bot_can_run
from core.logging import log_action_to_channel, log_server_event
from storage.json_store import load_json, save_json

BRAND = 0x5865F2
GREEN = 0x57F287
YELLOW = 0xFEE75C

ICEBREAKERS: dict[str, list[str]] = {
    "casual": [
        "What tiny thing made your day better recently?",
        "What song have you had on repeat lately?",
        "What is your most-used emoji, and does it actually represent you?",
        "Which snack would you defend in a completely unnecessary debate?",
        "What is one thing you are looking forward to this week?",
        "What fictional world would be fun to visit—but terrible to live in?",
    ],
    "gaming": [
        "Which game deserves a remake, and what would you change?",
        "What is your proudest gaming achievement?",
        "Which game has the best soundtrack?",
        "What mechanic instantly makes a game more fun for you?",
        "Which game would you erase from memory just to experience it fresh again?",
    ],
    "creative": [
        "Pitch a terrible movie in one sentence.",
        "Invent a new holiday. What does everyone do on it?",
        "Describe your dream game without naming an existing game.",
        "Turn the last emoji you used into a superhero.",
        "Create a restaurant name using your favourite colour and last snack.",
    ],
    "debate": [
        "Are spoilers ever acceptable?",
        "Is pineapple on pizza actually a problem, or just free publicity?",
        "Would you rather have perfect Wi-Fi or never need to charge a device?",
        "Are remakes better when they stay faithful or reinvent everything?",
        "Should group chats have an official bedtime?",
    ],
}

MOODS = [
    ("great", "Amazing", "✨", 0x57F287),
    ("good", "Good", "🙂", 0x3BA55D),
    ("okay", "Okay", "😐", 0xFEE75C),
    ("rough", "Rough", "🌧️", 0xED4245),
]

active_pulses: dict[int, "PulseSession"] = {}


def _bar(value: int, total: int, width: int = 12) -> str:
    filled = round((value / total) * width) if total else 0
    return "▰" * filled + "▱" * (width - filled)


def _guild_community_settings(guild_id: int) -> dict[str, Any]:
    all_settings = load_json(COMMUNITY_SETTINGS_FILE) or {}
    defaults = {
        "ping_role_id": None,
    }
    saved = all_settings.get(str(guild_id), {})
    if isinstance(saved, dict):
        defaults.update(saved)
    return defaults


def _save_guild_community_settings(guild_id: int, settings: dict[str, Any]) -> None:
    all_settings = load_json(COMMUNITY_SETTINGS_FILE) or {}
    all_settings[str(guild_id)] = settings
    save_json(COMMUNITY_SETTINGS_FILE, all_settings)


def _active_pulse_store() -> dict[str, Any]:
    data = load_json(ACTIVE_PULSES_FILE) or {}
    return data if isinstance(data, dict) else {}


def _save_active_pulse(session: "PulseSession", phase: str = "active", reason: str | None = None) -> None:
    """Persist a live pulse so its anonymous tally and timer survive restarts."""
    data = _active_pulse_store()
    data[str(session.guild.id)] = {
        "schema": 2,
        "phase": phase,
        "reason": reason,
        "guild_id": session.guild.id,
        "channel_id": session.channel.id,
        "message_id": session.message.id if session.message else None,
        "pulse_id": session.pulse_id,
        "starter_id": session.starter.id,
        "duration_minutes": session.duration_minutes,
        "prompt": session.prompt,
        "started_at": session.started_at.isoformat(),
        "end_time": session.end_time,
        "responses": {str(uid): mood for uid, mood in session.responses.items()},
        "notified_role_id": session.notified_role_id,
    }
    save_json(ACTIVE_PULSES_FILE, data)


def _remove_active_pulse(guild_id: int | str) -> None:
    data = _active_pulse_store()
    if data.pop(str(guild_id), None) is not None:
        save_json(ACTIVE_PULSES_FILE, data)


def _configured_ping_role(guild: discord.Guild) -> discord.Role | None:
    settings = _guild_community_settings(guild.id)
    role_id = settings.get("ping_role_id")
    if not role_id:
        return None
    try:
        role = guild.get_role(int(role_id))
    except (TypeError, ValueError):
        role = None
    if role is None or role.is_default() or role.managed:
        return None
    return role


def _role_ping_payload(
    guild: discord.Guild,
    channel: discord.TextChannel,
    role: discord.Role | None,
    message: str,
) -> tuple[str | None, discord.AllowedMentions, bool]:
    """Return safe content/mentions for one role, never @everyone or arbitrary roles."""
    if role is None or role.guild.id != guild.id or role.is_default() or role.managed:
        return None, discord.AllowedMentions.none(), False

    member = guild.me
    permissions = channel.permissions_for(member) if member is not None else None
    can_ping = bool(role.mentionable or (permissions and permissions.mention_everyone))
    if not can_ping:
        log_server_event(
            guild.id,
            f"Community post could not ping @{role.name}; make the role mentionable or grant Rallybit Mention Everyone in #{channel.name}.",
        )
        return None, discord.AllowedMentions.none(), False

    allowed_mentions = discord.AllowedMentions(
        everyone=False,
        users=False,
        roles=[role],
        replied_user=False,
    )
    return f"{role.mention} {message}", allowed_mentions, True


def _record_pulse_history_once(session: "PulseSession", counts: dict[str, int], total: int, reason: str) -> None:
    history = load_json(PULSE_HISTORY_FILE) or {}
    entries = history.setdefault(str(session.guild.id), [])
    if any(str(entry.get("pulse_id")) == session.pulse_id for entry in entries if isinstance(entry, dict)):
        return
    entries.append({
        "pulse_id": session.pulse_id,
        "prompt": session.prompt,
        "counts": counts,
        "responses": total,
        "started_at": session.started_at.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    })
    history[str(session.guild.id)] = entries[-100:]
    save_json(PULSE_HISTORY_FILE, history)


class PulseButton(discord.ui.Button):
    def __init__(self, pulse_id: str, mood: str, label: str, emoji: str, row: int):
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            row=row,
            custom_id=f"rallybit:pulse:{pulse_id}:{mood}",
        )
        self.mood = mood

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, PulseView):
            return
        await view.session.respond(interaction, self.mood)


class PulseView(discord.ui.View):
    def __init__(self, session: "PulseSession"):
        super().__init__(timeout=None)
        self.session = session
        for index, (mood, label, emoji, _) in enumerate(MOODS):
            self.add_item(PulseButton(session.pulse_id, mood, label, emoji, 0 if index < 4 else 1))

    def lock(self) -> None:
        for child in self.children:
            child.disabled = True


class PulseSession:
    def __init__(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        starter: discord.abc.User,
        duration_minutes: int,
        prompt: str | None = None,
        *,
        resume_data: dict[str, Any] | None = None,
    ):
        self.guild = guild
        self.channel = channel
        self.starter = starter
        self.duration_minutes = int(duration_minutes)
        self.prompt = prompt or "How is everyone feeling right now?"
        self.message: discord.Message | None = None
        self.finished = False
        self._lock = asyncio.Lock()
        self._retry_task: asyncio.Task | None = None

        if resume_data:
            self.pulse_id = str(resume_data.get("pulse_id") or uuid.uuid4().hex[:8].upper())
            saved_responses = resume_data.get("responses", {})
            self.responses = {
                int(uid): str(mood)
                for uid, mood in saved_responses.items()
                if str(uid).isdigit() and str(mood) in {item[0] for item in MOODS}
            } if isinstance(saved_responses, dict) else {}
            try:
                self.started_at = datetime.fromisoformat(str(resume_data.get("started_at")))
                if self.started_at.tzinfo is None:
                    self.started_at = self.started_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                self.started_at = datetime.now(timezone.utc)
            try:
                self.end_time = float(resume_data.get("end_time"))
            except (TypeError, ValueError):
                self.end_time = self.started_at.timestamp() + self.duration_minutes * 60
            try:
                role_id = resume_data.get("notified_role_id")
                self.notified_role_id = int(role_id) if role_id else None
            except (TypeError, ValueError):
                self.notified_role_id = None
        else:
            self.pulse_id = uuid.uuid4().hex[:8].upper()
            self.responses: dict[int, str] = {}
            self.started_at = datetime.now(timezone.utc)
            self.end_time = self.started_at.timestamp() + self.duration_minutes * 60
            self.notified_role_id: int | None = None

        self.view = PulseView(self)

    def embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="💬 Community Pulse",
            description=f"## {self.prompt}\nChoose the option closest to how you feel. Responses are shown only as anonymous totals.",
            colour=BRAND,
        )
        closes_at = datetime.fromtimestamp(self.end_time, tz=timezone.utc)
        embed.add_field(name="Privacy", value="Rallybit does not publish who selected each response.", inline=True)
        embed.add_field(name="Closes", value=discord.utils.format_dt(closes_at, style="R"), inline=True)
        embed.set_footer(text=f"Pulse ID {self.pulse_id} • You can change your response before it closes")
        return embed

    async def respond(self, interaction: discord.Interaction, mood: str) -> None:
        if self.finished or time.time() >= self.end_time:
            if not self.finished:
                asyncio.create_task(self.finish("time"), name=f"rallybit-pulse-expire-{self.guild.id}")
            return await interaction.response.send_message("This community pulse has ended.", ephemeral=True)
        if interaction.user.bot:
            return await interaction.response.send_message("Bots cannot respond to community pulses.", ephemeral=True)
        if interaction.guild_id != self.guild.id or not interaction.message or (self.message and interaction.message.id != self.message.id):
            return await interaction.response.send_message("That pulse control is no longer valid.", ephemeral=True)
        previous = self.responses.get(interaction.user.id)
        self.responses[interaction.user.id] = mood
        _save_active_pulse(self)
        label = next(label for key, label, _, _ in MOODS if key == mood)
        text = f"Your response was changed to **{label}**." if previous else f"Your **{label}** response was recorded anonymously."
        await interaction.response.send_message(text, ephemeral=True)

    def _result_embed(self, reason: str) -> tuple[discord.Embed, dict[str, int], int]:
        counts = {mood: 0 for mood, _, _, _ in MOODS}
        for mood in self.responses.values():
            if mood in counts:
                counts[mood] += 1
        total = len(self.responses)
        lines = []
        for mood, label, emoji, _ in MOODS:
            count = counts[mood]
            percent = round(count / total * 100) if total else 0
            lines.append(f"{emoji} **{label}** — `{count}` ({percent}%)\n`{_bar(count, total)}`")
        result = discord.Embed(
            title="📊 Community Pulse Results",
            description=f"**Prompt:** {self.prompt}\n\n" + "\n\n".join(lines),
            colour=GREEN if counts["great"] + counts["good"] >= counts["rough"] else YELLOW,
        )
        ended_labels = {
            "stopped": "Stopped by staff",
            "message_missing": "Recovered after the original message disappeared",
            "restart_recovery": "Recovered after restart",
        }
        result.add_field(name="Responses", value=str(total), inline=True)
        result.add_field(name="Ended", value=ended_labels.get(reason, "Scheduled close"), inline=True)
        result.set_footer(text="Results are aggregate-only; individual responses are deleted after finalisation")
        return result, counts, total

    async def _publish_and_cleanup(self, result: discord.Embed, reason: str) -> bool:
        try:
            if self.message is not None:
                await self.message.edit(embed=result, view=self.view)
            else:
                self.message = await self.channel.send(embed=result, view=self.view)
        except discord.NotFound:
            try:
                self.message = await self.channel.send(embed=result, view=self.view)
            except (discord.Forbidden, discord.HTTPException):
                _remove_active_pulse(self.guild.id)
                active_pulses.pop(self.guild.id, None)
                return True
        except (discord.Forbidden, discord.HTTPException) as exc:
            log_server_event(self.guild.id, f"Community pulse {self.pulse_id} result could not be published yet: {exc}")
            _save_active_pulse(self, "finalizing", reason)
            return False

        _remove_active_pulse(self.guild.id)
        active_pulses.pop(self.guild.id, None)
        log_server_event(self.guild.id, f"Community pulse {self.pulse_id} ended with {len(self.responses)} anonymous responses.")
        return True

    async def _retry_publish(self, reason: str) -> None:
        await asyncio.sleep(15)
        result, _, _ = self._result_embed(reason)
        if not await self._publish_and_cleanup(result, reason):
            self._retry_task = asyncio.create_task(self._retry_publish(reason), name=f"rallybit-pulse-finalize-{self.guild.id}")

    async def finish(self, reason: str = "time") -> None:
        async with self._lock:
            if self.finished:
                return
            self.finished = True
            self.view.lock()
            _save_active_pulse(self, "finalizing", reason)
            result, counts, total = self._result_embed(reason)
            _record_pulse_history_once(self, counts, total, reason)
            if not await self._publish_and_cleanup(result, reason):
                if self._retry_task is None or self._retry_task.done():
                    self._retry_task = asyncio.create_task(self._retry_publish(reason), name=f"rallybit-pulse-finalize-{self.guild.id}")


async def start_pulse(
    guild: discord.Guild,
    channel: discord.TextChannel,
    starter: discord.abc.User,
    duration_minutes: int,
    prompt: str | None = None,
    ping_role: discord.Role | None = None,
) -> PulseSession:
    if guild.id in active_pulses or str(guild.id) in _active_pulse_store():
        raise RuntimeError("A community pulse is already active in this server.")
    duration_minutes = max(1, min(60, int(duration_minutes)))
    session = PulseSession(guild, channel, starter, duration_minutes, prompt)
    active_pulses[guild.id] = session
    content, allowed_mentions, pinged = _role_ping_payload(
        guild,
        channel,
        ping_role,
        "A new anonymous community pulse is open!",
    )
    if pinged and ping_role is not None:
        session.notified_role_id = ping_role.id
    try:
        session.message = await channel.send(
            content=content,
            embed=session.embed(),
            view=session.view,
            allowed_mentions=allowed_mentions,
        )
        _save_active_pulse(session)
    except Exception:
        active_pulses.pop(guild.id, None)
        _remove_active_pulse(guild.id)
        raise

    async def timer() -> None:
        await asyncio.sleep(max(0.0, session.end_time - time.time()))
        await session.finish("time")

    asyncio.create_task(timer(), name=f"rallybit-pulse-{guild.id}-{session.pulse_id}")
    return session


async def resume_active_pulses(bot: discord.Client) -> int:
    """Restore pulse buttons, temporary responses and close timers after restart."""
    data = _active_pulse_store()
    if not data:
        return 0
    pending = {
        gid: saved for gid, saved in data.items()
        if not str(gid).isdigit() or int(gid) not in active_pulses
    }
    if not pending:
        return 0
    restored = 0
    print(f"🔄 [Resumer] Found {len(pending)} ongoing community pulse(s). Re-hydrating...")
    for gid_str, saved in list(pending.items()):
        if not isinstance(saved, dict):
            _remove_active_pulse(gid_str)
            continue
        try:
            guild = bot.get_guild(int(gid_str))
            if guild is None:
                _remove_active_pulse(gid_str)
                continue
            if guild.id in active_pulses:
                continue
            channel = guild.get_channel(int(saved.get("channel_id", 0)))
            if not isinstance(channel, discord.TextChannel):
                _remove_active_pulse(gid_str)
                continue
            starter_id = int(saved.get("starter_id", bot.user.id if bot.user else 0))
            starter = guild.get_member(starter_id)
            if starter is None:
                starter = await bot.fetch_user(starter_id)

            session = PulseSession(
                guild,
                channel,
                starter,
                int(saved.get("duration_minutes", 10)),
                str(saved.get("prompt") or "How is everyone feeling right now?"),
                resume_data=saved,
            )
            active_pulses[guild.id] = session

            message_id = saved.get("message_id")
            if message_id:
                try:
                    session.message = await channel.fetch_message(int(message_id))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    session.message = None

            phase = str(saved.get("phase", "active"))
            remaining = session.end_time - time.time()
            if phase == "active" and remaining > 0 and session.message is None:
                try:
                    session.message = await channel.send(
                        content="♻️ Rallybit restored this community pulse after a restart. Existing responses and the original closing time were preserved.",
                        embed=session.embed(),
                        view=session.view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    _save_active_pulse(session)
                except (discord.Forbidden, discord.HTTPException) as exc:
                    active_pulses.pop(guild.id, None)
                    print(f"[RESUME PULSE] Could not recreate pulse {session.pulse_id}: {exc}")
                    continue
            if phase == "active" and remaining > 0 and session.message is not None:
                bot.add_view(session.view, message_id=session.message.id)
                try:
                    await session.message.edit(view=session.view)
                except discord.HTTPException:
                    pass

                async def timer(target: PulseSession = session) -> None:
                    await asyncio.sleep(max(0.0, target.end_time - time.time()))
                    await target.finish("time")

                asyncio.create_task(timer(), name=f"rallybit-resume-pulse-{guild.id}-{session.pulse_id}")
            else:
                reason = str(saved.get("reason") or ("message_missing" if session.message is None else "restart_recovery"))
                asyncio.create_task(session.finish(reason), name=f"rallybit-finalize-pulse-{guild.id}-{session.pulse_id}")

            restored += 1
            print(f" ✅ Resumed pulse {session.pulse_id}: {guild.name}")
        except Exception as exc:
            active_pulses.pop(int(gid_str), None) if str(gid_str).isdigit() else None
            print(f"[RESUME PULSE] Could not restore {gid_str}: {exc}")

    return restored


def setup_community_commands(tree: app_commands.CommandTree) -> None:
    community = app_commands.Group(name="community", description="Conversation starters and anonymous community pulses.")

    @community.command(name="icebreaker", description="Post a conversation starter for your community.")
    @app_commands.guild_only()
    @app_commands.describe(
        category="casual, gaming, creative, debate or mixed",
        notify_role="Ping the configured Chat Revive role (staff only)",
    )
    async def icebreaker(interaction: discord.Interaction, category: str = "mixed", notify_role: bool = False):
        can_run, reason, _ = bot_can_run(interaction)
        if not can_run:
            return await interaction.response.send_message(reason, ephemeral=True)
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Icebreakers must be posted in a server text channel.", ephemeral=True)
        if notify_role and not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message(
                "You need **Manage Messages** to notify the configured community role. Run the command with `notify_role:false` to post without a ping.",
                ephemeral=True,
            )
        category = category.lower().strip()
        if category == "mixed":
            category = random.choice(list(ICEBREAKERS))
        if category not in ICEBREAKERS:
            return await interaction.response.send_message("Choose `mixed`, `casual`, `gaming`, `creative` or `debate`.", ephemeral=True)
        prompt = random.choice(ICEBREAKERS[category])
        embed = discord.Embed(
            title=f"💡 {category.title()} Icebreaker",
            description=f"## {prompt}\nDrop your answer below and see where the conversation goes.",
            colour=BRAND,
        )
        embed.set_footer(text=f"Started by {interaction.user.display_name} • Rallybit Community")
        role = _configured_ping_role(interaction.guild) if notify_role else None
        content, allowed_mentions, pinged = _role_ping_payload(
            interaction.guild,
            interaction.channel,
            role,
            "A new conversation starter just dropped!",
        )
        await interaction.response.send_message(content=content, embed=embed, allowed_mentions=allowed_mentions)

    @community.command(name="pulse", description="Start an anonymous mood pulse in this channel.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.describe(
        duration_minutes="How long the pulse stays open",
        prompt="Optional custom question",
        notify_role="Ping the configured Chat Revive role",
    )
    async def pulse(
        interaction: discord.Interaction,
        duration_minutes: app_commands.Range[int, 1, 60] = 10,
        prompt: str | None = None,
        notify_role: bool = True,
    ):
        can_run, reason, _ = bot_can_run(interaction)
        if not can_run:
            return await interaction.response.send_message(reason, ephemeral=True)
        if interaction.guild.id in active_pulses or str(interaction.guild.id) in _active_pulse_store():
            return await interaction.response.send_message("A community pulse is already active in this server.", ephemeral=True)
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Community pulses must be started in a server text channel.", ephemeral=True)
        if prompt and len(prompt) > 200:
            return await interaction.response.send_message("Keep the pulse prompt under 200 characters.", ephemeral=True)
        perms = interaction.channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await interaction.response.send_message("Rallybit needs Send Messages and Embed Links in this channel.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        role = _configured_ping_role(interaction.guild) if notify_role else None
        session = await start_pulse(
            interaction.guild,
            interaction.channel,
            interaction.user,
            duration_minutes,
            prompt,
            ping_role=role,
        )
        await log_action_to_channel(
            interaction.guild,
            interaction.user,
            "community pulse",
            f"Started pulse `{session.pulse_id}` in {interaction.channel.mention}.",
            interaction.channel,
        )
        ping_note = f" and notified {role.mention}" if role is not None and session.notified_role_id == role.id else ""
        await interaction.followup.send(
            f"✅ Community pulse `{session.pulse_id}` started{ping_note}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @community.command(name="pingrole", description="Set or clear the role used for pulse and icebreaker notifications.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(role="Chat Revive role, or leave empty to disable community pings")
    async def community_pingrole(interaction: discord.Interaction, role: discord.Role | None = None):
        settings = _guild_community_settings(interaction.guild.id)
        if role is None:
            settings["ping_role_id"] = None
            _save_guild_community_settings(interaction.guild.id, settings)
            return await interaction.response.send_message("🔕 Community pulse and icebreaker role pings disabled.", ephemeral=True)
        if role.is_default():
            return await interaction.response.send_message("Choose a normal server role instead of `@everyone`.", ephemeral=True)
        if role.managed:
            return await interaction.response.send_message("Discord-managed integration roles cannot be used as a community notification role.", ephemeral=True)
        if isinstance(interaction.channel, discord.TextChannel):
            perms = interaction.channel.permissions_for(interaction.guild.me)
            if not role.mentionable and not perms.mention_everyone:
                return await interaction.response.send_message(
                    f"{role.mention} is not mentionable. Make it mentionable or grant Rallybit **Mention @everyone, @here and All Roles** in the channels where community posts will run.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        settings["ping_role_id"] = role.id
        _save_guild_community_settings(interaction.guild.id, settings)
        await interaction.response.send_message(
            f"🔔 Community pulses can now notify {role.mention}. Staff can also use `notify_role:true` with `/community icebreaker`.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @community.command(name="settings", description="View community notification settings for this server.")
    @app_commands.guild_only()
    async def community_settings(interaction: discord.Interaction):
        settings = _guild_community_settings(interaction.guild.id)
        role_id = settings.get("ping_role_id")
        try:
            role = interaction.guild.get_role(int(role_id)) if role_id else None
        except (TypeError, ValueError):
            role = None
        role_text = role.mention if role else ("Deleted role — run `/community pingrole` again" if role_id else "Disabled")
        embed = discord.Embed(
            title="💬 Community settings",
            description="Controls the optional role notification used by community engagement posts.",
            colour=BRAND,
        )
        embed.add_field(name="Notification role", value=role_text, inline=True)
        embed.add_field(name="Community pulse", value="Uses the role by default; set `notify_role:false` for a silent pulse.", inline=False)
        embed.add_field(name="Icebreaker", value="Silent by default; staff can set `notify_role:true` when posting.", inline=False)
        embed.set_footer(text="Rallybit never falls back to @everyone")
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @community.command(name="stoppulse", description="Close the active community pulse now.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def stop_pulse(interaction: discord.Interaction):
        session = active_pulses.get(interaction.guild.id)
        if not session:
            return await interaction.response.send_message("There is no active community pulse.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await session.finish("stopped")
        await interaction.followup.send("🛑 The community pulse was closed.", ephemeral=True)

    @community.command(name="pulsehistory", description="View recent anonymous pulse trends.")
    @app_commands.guild_only()
    async def pulse_history(interaction: discord.Interaction):
        history = load_json(PULSE_HISTORY_FILE) or {}
        records = history.get(str(interaction.guild.id), [])
        if not records:
            return await interaction.response.send_message("No completed community pulses have been recorded yet.", ephemeral=True)
        totals = {mood: 0 for mood, _, _, _ in MOODS}
        response_total = 0
        for record in records[-10:]:
            response_total += int(record.get("responses", 0))
            for mood in totals:
                totals[mood] += int(record.get("counts", {}).get(mood, 0))
        lines = []
        for mood, label, emoji, _ in MOODS:
            count = totals[mood]
            percent = round(count / response_total * 100) if response_total else 0
            lines.append(f"{emoji} **{label}:** {count} ({percent}%)")
        embed = discord.Embed(title="📈 Recent Community Pulse Trend", description="\n".join(lines), colour=BRAND)
        embed.add_field(name="Window", value=f"Last {min(10, len(records))} pulse(s)", inline=True)
        embed.add_field(name="Anonymous responses", value=str(response_total), inline=True)
        embed.set_footer(text="Only aggregate pulse results are stored")
        await interaction.response.send_message(embed=embed)

    tree.add_command(community)
