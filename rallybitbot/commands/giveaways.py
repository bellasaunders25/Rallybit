from __future__ import annotations

import asyncio
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands

from config.config import ACTIVE_GIVEAWAYS_FILE, GIVEAWAY_HISTORY_FILE, GIVEAWAY_SETTINGS_FILE
from core.logging import log_server_event
from storage.json_store import load_json, save_json

BRAND = 0x5865F2
GREEN = 0x57F287
RED = 0xED4245
active_giveaways: dict[str, "GiveawaySession"] = {}


def _settings(guild_id: int) -> dict[str, Any]:
    all_data = load_json(GIVEAWAY_SETTINGS_FILE) or {}
    default = {"default_channel_id": None, "default_ping_role_id": None}
    saved = all_data.get(str(guild_id), {})
    if isinstance(saved, dict):
        default.update(saved)
    return default


def _save_settings(guild_id: int, settings: dict[str, Any]) -> None:
    all_data = load_json(GIVEAWAY_SETTINGS_FILE) or {}
    all_data[str(guild_id)] = settings
    save_json(GIVEAWAY_SETTINGS_FILE, all_data)


def _active_store() -> dict[str, Any]:
    data = load_json(ACTIVE_GIVEAWAYS_FILE) or {}
    return data if isinstance(data, dict) else {}


def _persist(session: "GiveawaySession", phase: str = "active") -> None:
    data = _active_store()
    data[session.giveaway_id] = {
        "schema": 1,
        "phase": phase,
        "giveaway_id": session.giveaway_id,
        "guild_id": session.guild.id,
        "channel_id": session.channel.id,
        "message_id": session.message.id if session.message else None,
        "host_id": session.host.id,
        "prize": session.prize,
        "winner_count": session.winner_count,
        "required_role_id": session.required_role_id,
        "end_time": session.end_time,
        "entries": [str(x) for x in sorted(session.entries)],
    }
    save_json(ACTIVE_GIVEAWAYS_FILE, data)


def _remove(giveaway_id: str) -> None:
    data = _active_store()
    if data.pop(giveaway_id, None) is not None:
        save_json(ACTIVE_GIVEAWAYS_FILE, data)


def _history(guild_id: int, record: dict[str, Any]) -> None:
    data = load_json(GIVEAWAY_HISTORY_FILE) or {}
    rows = data.setdefault(str(guild_id), [])
    if not any(str(r.get("giveaway_id")) == str(record.get("giveaway_id")) for r in rows if isinstance(r, dict)):
        rows.append(record)
    data[str(guild_id)] = rows[-250:]
    save_json(GIVEAWAY_HISTORY_FILE, data)


class GiveawayView(discord.ui.View):
    def __init__(self, session: "GiveawaySession") -> None:
        super().__init__(timeout=None)
        self.session = session
        button = discord.ui.Button(
            label="Enter giveaway",
            emoji="🎉",
            style=discord.ButtonStyle.success,
            custom_id=f"rallybit:giveaway:{session.giveaway_id}:enter",
        )
        button.callback = self.enter  # type: ignore[assignment]
        self.add_item(button)

    async def enter(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.guild.id != self.session.guild.id:
            await interaction.response.send_message("This giveaway is unavailable.", ephemeral=True)
            return
        if self.session.finished or time.time() >= self.session.end_time:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("This giveaway is server-only.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("Bots cannot enter giveaways.", ephemeral=True)
            return
        if self.session.required_role_id and member.get_role(self.session.required_role_id) is None:
            role = interaction.guild.get_role(self.session.required_role_id)
            await interaction.response.send_message(
                f"You need {role.mention if role else 'the required role'} to enter.", ephemeral=True
            )
            return
        if member.id in self.session.entries:
            self.session.entries.remove(member.id)
            _persist(self.session)
            await interaction.response.send_message("You left the giveaway.", ephemeral=True)
        else:
            self.session.entries.add(member.id)
            _persist(self.session)
            await interaction.response.send_message("You entered the giveaway. Good luck!", ephemeral=True)
        await self.session.refresh()

    def lock(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
                item.label = "Giveaway ended"
                item.style = discord.ButtonStyle.secondary


class GiveawaySession:
    def __init__(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        host: discord.abc.User,
        prize: str,
        winner_count: int,
        duration_minutes: int,
        required_role_id: int | None = None,
        *,
        resume_data: dict[str, Any] | None = None,
    ) -> None:
        self.guild = guild
        self.channel = channel
        self.host = host
        self.prize = prize[:250]
        self.winner_count = max(1, min(20, int(winner_count)))
        self.required_role_id = required_role_id
        self.message: discord.Message | None = None
        self.finished = False
        self._finish_lock = asyncio.Lock()
        if resume_data:
            self.giveaway_id = str(resume_data.get("giveaway_id") or uuid.uuid4().hex[:8].upper())
            self.end_time = float(resume_data.get("end_time") or time.time())
            self.entries = {int(v) for v in resume_data.get("entries", []) if str(v).isdigit()}
        else:
            self.giveaway_id = uuid.uuid4().hex[:8].upper()
            self.end_time = time.time() + max(1, min(10080, int(duration_minutes))) * 60
            self.entries: set[int] = set()
        self.view = GiveawayView(self)

    def embed(self, ended: bool = False, winners: list[int] | None = None) -> discord.Embed:
        if ended:
            winner_text = "No valid entries."
            if winners:
                winner_text = ", ".join(f"<@{uid}>" for uid in winners)
            embed = discord.Embed(
                title="🎉 Giveaway ended",
                description=f"## {self.prize}\n\n**Winner{'s' if len(winners or []) != 1 else ''}:** {winner_text}",
                color=GREEN if winners else RED,
                timestamp=datetime.now(timezone.utc),
            )
        else:
            embed = discord.Embed(
                title="🎉 Giveaway",
                description=f"## {self.prize}\n\nPress the button below to enter.",
                color=BRAND,
                timestamp=datetime.fromtimestamp(self.end_time, tz=timezone.utc),
            )
            embed.add_field(name="Ends", value=f"<t:{int(self.end_time)}:R>", inline=True)
            embed.add_field(name="Winners", value=str(self.winner_count), inline=True)
            embed.add_field(name="Entries", value=str(len(self.entries)), inline=True)
            if self.required_role_id:
                embed.add_field(name="Required role", value=f"<@&{self.required_role_id}>", inline=False)
        embed.set_footer(text=f"Hosted by {self.host} • ID {self.giveaway_id}")
        return embed

    async def refresh(self) -> None:
        if self.message and not self.finished:
            try:
                await self.message.edit(embed=self.embed(), view=self.view)
            except discord.HTTPException:
                pass

    async def finish(self, reason: str = "time") -> list[int]:
        async with self._finish_lock:
            if self.finished:
                return []
            self.finished = True
            valid = []
            for uid in self.entries:
                member = self.guild.get_member(uid)
                if member and not member.bot and (not self.required_role_id or member.get_role(self.required_role_id)):
                    valid.append(uid)
            winners = random.sample(valid, min(self.winner_count, len(valid))) if valid else []
            self.view.lock()
            if self.message:
                try:
                    await self.message.edit(embed=self.embed(True, winners), view=self.view)
                    if winners:
                        await self.channel.send(
                            f"Congratulations {', '.join(f'<@{uid}>' for uid in winners)}! You won **{discord.utils.escape_markdown(self.prize)}**.",
                            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                        )
                except discord.HTTPException:
                    pass
            _history(self.guild.id, {
                "giveaway_id": self.giveaway_id,
                "prize": self.prize,
                "channel_id": self.channel.id,
                "host_id": self.host.id,
                "entries": len(valid),
                "entrant_ids": [str(x) for x in valid],
                "winner_ids": [str(x) for x in winners],
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
            })
            _remove(self.giveaway_id)
            active_giveaways.pop(self.giveaway_id, None)
            log_server_event(self.guild.id, f"Giveaway {self.giveaway_id} ended ({reason}); {len(winners)} winner(s).")
            return winners


async def start_giveaway(
    guild: discord.Guild,
    channel: discord.TextChannel,
    host: discord.abc.User,
    prize: str,
    duration_minutes: int,
    winner_count: int = 1,
    required_role: discord.Role | None = None,
    ping_role: discord.Role | None = None,
) -> GiveawaySession:
    session = GiveawaySession(
        guild, channel, host, prize, winner_count, duration_minutes,
        required_role.id if required_role else None,
    )
    active_giveaways[session.giveaway_id] = session
    content = ping_role.mention if ping_role and (ping_role.mentionable or channel.permissions_for(guild.me).mention_everyone) else None
    allowed = discord.AllowedMentions(roles=[ping_role] if content and ping_role else False, users=False, everyone=False)
    try:
        session.message = await channel.send(content=content, embed=session.embed(), view=session.view, allowed_mentions=allowed)
        _persist(session)
    except Exception:
        active_giveaways.pop(session.giveaway_id, None)
        _remove(session.giveaway_id)
        raise

    async def timer() -> None:
        await asyncio.sleep(max(0.0, session.end_time - time.time()))
        await session.finish("time")

    asyncio.create_task(timer(), name=f"rallybit-giveaway-{session.giveaway_id}")
    log_server_event(guild.id, f"Giveaway {session.giveaway_id} started in #{channel.name}: {prize}")
    return session


async def resume_active_giveaways(bot: discord.Client) -> int:
    restored = 0
    for gid, saved in list(_active_store().items()):
        if not isinstance(saved, dict) or gid in active_giveaways:
            continue
        try:
            guild = bot.get_guild(int(saved["guild_id"]))
            channel = guild.get_channel(int(saved["channel_id"])) if guild else None
            if not guild or not isinstance(channel, discord.TextChannel):
                _remove(gid)
                continue
            host = guild.get_member(int(saved.get("host_id", 0))) or bot.user
            if host is None:
                _remove(gid)
                continue
            session = GiveawaySession(
                guild, channel, host, str(saved.get("prize", "Giveaway")),
                int(saved.get("winner_count", 1)), 1,
                int(saved["required_role_id"]) if saved.get("required_role_id") else None,
                resume_data=saved,
            )
            message_id = saved.get("message_id")
            if message_id:
                try:
                    session.message = await channel.fetch_message(int(message_id))
                except discord.HTTPException:
                    session.message = None
            active_giveaways[session.giveaway_id] = session
            if session.message:
                await session.message.edit(embed=session.embed(), view=session.view)
            if session.end_time <= time.time():
                await session.finish("recovered-expired")
            else:
                asyncio.create_task(_resume_timer(session), name=f"rallybit-giveaway-resume-{session.giveaway_id}")
            restored += 1
        except Exception as exc:
            print(f"[GIVEAWAY RECOVERY] {gid}: {exc}")
    return restored


async def _resume_timer(session: GiveawaySession) -> None:
    await asyncio.sleep(max(0.0, session.end_time - time.time()))
    await session.finish("time")


def setup_giveaway_commands(tree: app_commands.CommandTree) -> None:
    group = app_commands.Group(name="giveaway", description="Create and manage server giveaways.")

    @group.command(name="start", description="Start a button giveaway.")
    @app_commands.describe(prize="Prize description", duration_minutes="How long it runs", winners="Number of winners")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def start(
        interaction: discord.Interaction,
        prize: str,
        duration_minutes: app_commands.Range[int, 1, 10080],
        winners: app_commands.Range[int, 1, 20] = 1,
        channel: discord.TextChannel | None = None,
        required_role: discord.Role | None = None,
        ping_role: discord.Role | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Choose a text channel.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        session = await start_giveaway(interaction.guild, target, interaction.user, prize, duration_minutes, winners, required_role, ping_role)
        await interaction.followup.send(f"Giveaway `{session.giveaway_id}` started in {target.mention}.", ephemeral=True)

    @group.command(name="end", description="End an active giveaway early.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def end(interaction: discord.Interaction, giveaway_id: str) -> None:
        session = active_giveaways.get(giveaway_id.upper())
        if not session or not interaction.guild or session.guild.id != interaction.guild.id:
            await interaction.response.send_message("That active giveaway was not found.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        winners = await session.finish("manual")
        await interaction.followup.send(f"Giveaway ended with {len(winners)} winner(s).", ephemeral=True)

    @group.command(name="reroll", description="Choose new winners from a completed giveaway.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reroll(interaction: discord.Interaction, giveaway_id: str, winners: app_commands.Range[int, 1, 20] = 1) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        data = load_json(GIVEAWAY_HISTORY_FILE) or {}
        record = next((r for r in reversed(data.get(str(interaction.guild.id), [])) if str(r.get("giveaway_id", "")).upper() == giveaway_id.upper()), None)
        if not record:
            await interaction.response.send_message("That giveaway was not found in this server's history.", ephemeral=True)
            return
        entries = [int(x) for x in record.get("entrant_ids", []) if str(x).isdigit()]
        if not entries:
            await interaction.response.send_message("No retained entries are available for that giveaway.", ephemeral=True)
            return
        chosen = random.sample(entries, min(winners, len(entries)))
        await interaction.response.send_message(
            f"New winner{'s' if len(chosen) != 1 else ''}: {', '.join(f'<@{x}>' for x in chosen)}",
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )

    @group.command(name="list", description="List active giveaways in this server.")
    async def list_cmd(interaction: discord.Interaction) -> None:
        sessions = [s for s in active_giveaways.values() if interaction.guild and s.guild.id == interaction.guild.id]
        if not sessions:
            await interaction.response.send_message("There are no active giveaways.", ephemeral=True)
            return
        lines = [f"`{s.giveaway_id}` • {s.prize} • {s.channel.mention} • <t:{int(s.end_time)}:R>" for s in sessions]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="settings", description="Set default giveaway channel and ping role.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def settings_cmd(interaction: discord.Interaction, channel: discord.TextChannel | None = None, ping_role: discord.Role | None = None) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        cfg = _settings(interaction.guild.id)
        cfg["default_channel_id"] = channel.id if channel else None
        cfg["default_ping_role_id"] = ping_role.id if ping_role else None
        _save_settings(interaction.guild.id, cfg)
        await interaction.response.send_message("Giveaway defaults updated.", ephemeral=True)

    tree.add_command(group)
