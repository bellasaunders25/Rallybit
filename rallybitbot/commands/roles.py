from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from config.config import AUTOROLE_SETTINGS_FILE, REACTION_ROLES_FILE, VERIFICATION_SETTINGS_FILE
from core.logging import log_server_event
from storage.json_store import load_json, save_json

BRAND = 0x5865F2
GREEN = 0x57F287


def _autoroles(guild_id: int) -> list[int]:
    data = load_json(AUTOROLE_SETTINGS_FILE) or {}
    rows = data.get(str(guild_id), [])
    return [int(x) for x in rows if str(x).isdigit()] if isinstance(rows, list) else []


def _save_autoroles(guild_id: int, roles: list[int]) -> None:
    data = load_json(AUTOROLE_SETTINGS_FILE) or {}
    data[str(guild_id)] = list(dict.fromkeys(roles))[:20]
    save_json(AUTOROLE_SETTINGS_FILE, data)


def _reaction_data() -> dict[str, Any]:
    data = load_json(REACTION_ROLES_FILE) or {}
    return data if isinstance(data, dict) else {}


def _emoji_key(emoji: discord.PartialEmoji | str) -> str:
    if isinstance(emoji, str):
        parsed = discord.PartialEmoji.from_str(emoji)
    else:
        parsed = emoji
    return f"id:{parsed.id}" if parsed.id else f"unicode:{parsed.name}"


def _verification(guild_id: int) -> dict[str, Any]:
    data = load_json(VERIFICATION_SETTINGS_FILE) or {}
    defaults = {
        "enabled": False,
        "channel_id": None,
        "message_id": None,
        "role_id": None,
        "remove_role_id": None,
        "title": "Verify your account",
        "description": "Press the button below to gain access to the server.",
        "button_label": "Verify",
    }
    saved = data.get(str(guild_id), {})
    if isinstance(saved, dict):
        defaults.update(saved)
    return defaults


def _save_verification(guild_id: int, settings: dict[str, Any]) -> None:
    data = load_json(VERIFICATION_SETTINGS_FILE) or {}
    data[str(guild_id)] = settings
    save_json(VERIFICATION_SETTINGS_FILE, data)


async def on_member_join(member: discord.Member) -> None:
    me = member.guild.me
    if me is None:
        return
    roles = []
    for role_id in _autoroles(member.guild.id):
        role = member.guild.get_role(role_id)
        if role and not role.managed and role < me.top_role:
            roles.append(role)
    if roles:
        try:
            await member.add_roles(*roles, reason="Rallybit autoroles")
            log_server_event(member.guild.id, f"Assigned {len(roles)} autorole(s) to {member}.")
        except discord.HTTPException:
            pass


async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    if payload.guild_id is None or payload.user_id == getattr(payload, "bot_id", None):
        return
    data = _reaction_data()
    mapping = data.get(str(payload.guild_id), {}).get(str(payload.message_id), {})
    role_id = mapping.get(_emoji_key(payload.emoji)) if isinstance(mapping, dict) else None
    if not role_id:
        return
    from core.bot import client
    guild = client.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id) if guild else None
    role = guild.get_role(int(role_id)) if guild else None
    if member and role and not member.bot and guild.me and role < guild.me.top_role:
        try:
            await member.add_roles(role, reason="Rallybit reaction role")
        except discord.HTTPException:
            pass


async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent) -> None:
    if payload.guild_id is None:
        return
    data = _reaction_data()
    mapping = data.get(str(payload.guild_id), {}).get(str(payload.message_id), {})
    role_id = mapping.get(_emoji_key(payload.emoji)) if isinstance(mapping, dict) else None
    if not role_id:
        return
    from core.bot import client
    guild = client.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id) if guild else None
    role = guild.get_role(int(role_id)) if guild else None
    if member and role and not member.bot and guild.me and role < guild.me.top_role:
        try:
            await member.remove_roles(role, reason="Rallybit reaction role removed")
        except discord.HTTPException:
            pass


class VerificationView(discord.ui.View):
    def __init__(self, guild_id: int, button_label: str = "Verify") -> None:
        super().__init__(timeout=None)
        self.guild_id = guild_id
        button = discord.ui.Button(
            label=button_label[:80] or "Verify",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"rallybit:verify:{guild_id}",
        )
        button.callback = self.verify  # type: ignore[assignment]
        self.add_item(button)

    async def verify(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or interaction.guild.id != self.guild_id or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This verification panel is unavailable.", ephemeral=True)
            return
        cfg = _verification(self.guild_id)
        if not cfg.get("enabled"):
            await interaction.response.send_message("Verification is currently disabled.", ephemeral=True)
            return
        role = interaction.guild.get_role(int(cfg.get("role_id") or 0))
        remove_role = interaction.guild.get_role(int(cfg.get("remove_role_id") or 0)) if cfg.get("remove_role_id") else None
        if not role:
            await interaction.response.send_message("The verification role no longer exists.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message("You are already verified.", ephemeral=True)
            return
        me = interaction.guild.me
        if not me or role >= me.top_role:
            await interaction.response.send_message("Rallybit's role must be above the verification role.", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role, reason="Rallybit verification")
            if remove_role and remove_role in interaction.user.roles and remove_role < me.top_role:
                await interaction.user.remove_roles(remove_role, reason="Rallybit verification completed")
            await interaction.response.send_message(f"You are verified and now have {role.mention}.", ephemeral=True)
            log_server_event(interaction.guild.id, f"{interaction.user} completed verification.")
        except discord.HTTPException:
            await interaction.response.send_message("I could not assign the verification role.", ephemeral=True)


async def restore_verification_views(bot: discord.Client) -> int:
    data = load_json(VERIFICATION_SETTINGS_FILE) or {}
    restored = 0
    for guild_id, cfg in data.items():
        if not str(guild_id).isdigit() or not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        try:
            bot.add_view(VerificationView(int(guild_id), str(cfg.get("button_label", "Verify"))))
            restored += 1
        except Exception as exc:
            print(f"[VERIFY VIEW] {guild_id}: {exc}")
    return restored



async def publish_verification_panel(
    guild: discord.Guild,
    channel: discord.TextChannel,
    verified_role: discord.Role,
    remove_role: discord.Role | None = None,
    title: str = "Verify your account",
    description: str = "Press the button below to gain access to the server.",
    button_label: str = "Verify",
) -> discord.Message:
    me = guild.me
    if verified_role.is_default() or verified_role.managed or not me or verified_role >= me.top_role:
        raise RuntimeError("Rallybit cannot assign that verification role. Move its role higher.")
    cfg = {
        "enabled": True, "channel_id": channel.id, "message_id": None,
        "role_id": verified_role.id, "remove_role_id": remove_role.id if remove_role else None,
        "title": title[:256], "description": description[:4000], "button_label": button_label[:80],
    }
    embed = discord.Embed(title=cfg["title"], description=cfg["description"], color=BRAND)
    embed.set_footer(text="Rallybit verification")
    view = VerificationView(guild.id, cfg["button_label"])
    message = await channel.send(embed=embed, view=view)
    cfg["message_id"] = message.id
    _save_verification(guild.id, cfg)
    from core.bot import client
    client.add_view(view)
    return message

def setup_role_commands(tree: app_commands.CommandTree) -> None:
    autorole = app_commands.Group(name="autorole", description="Assign roles automatically when members join.")
    reaction = app_commands.Group(name="reactionrole", description="Configure reaction role messages.")
    verification = app_commands.Group(name="verification", description="Configure button verification.")

    @autorole.command(name="add", description="Add a role to the join autorole list.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def autorole_add(interaction: discord.Interaction, role: discord.Role) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        if role.is_default() or role.managed or (interaction.guild.me and role >= interaction.guild.me.top_role):
            await interaction.response.send_message("Rallybit cannot assign that role.", ephemeral=True); return
        roles = _autoroles(interaction.guild.id)
        if role.id not in roles: roles.append(role.id)
        _save_autoroles(interaction.guild.id, roles)
        await interaction.response.send_message(f"{role.mention} will be assigned when members join.", ephemeral=True)

    @autorole.command(name="remove", description="Remove a join autorole.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def autorole_remove(interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild:
            _save_autoroles(interaction.guild.id, [x for x in _autoroles(interaction.guild.id) if x != role.id])
        await interaction.response.send_message(f"Removed {role.mention} from autoroles.", ephemeral=True)

    @autorole.command(name="list", description="List roles assigned on join.")
    async def autorole_list(interaction: discord.Interaction) -> None:
        roles = _autoroles(interaction.guild.id) if interaction.guild else []
        await interaction.response.send_message("Autoroles: " + (", ".join(f"<@&{x}>" for x in roles) if roles else "None"), ephemeral=True)

    @autorole.command(name="clear", description="Remove every configured autorole.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def autorole_clear(interaction: discord.Interaction) -> None:
        if interaction.guild: _save_autoroles(interaction.guild.id, [])
        await interaction.response.send_message("All autoroles were cleared.", ephemeral=True)

    @reaction.command(name="add", description="Add an emoji-to-role mapping to an existing message.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def reaction_add(interaction: discord.Interaction, channel: discord.TextChannel, message_id: str, emoji: str, role: discord.Role) -> None:
        if not interaction.guild or not message_id.isdigit():
            await interaction.response.send_message("Use a valid message ID in this server.", ephemeral=True); return
        if role.is_default() or role.managed or (interaction.guild.me and role >= interaction.guild.me.top_role):
            await interaction.response.send_message("Rallybit cannot assign that role.", ephemeral=True); return
        try:
            message = await channel.fetch_message(int(message_id))
            parsed = discord.PartialEmoji.from_str(emoji)
            await message.add_reaction(parsed)
        except (discord.HTTPException, ValueError):
            await interaction.response.send_message("I could not find that message or use that emoji.", ephemeral=True); return
        data = _reaction_data()
        guild_data = data.setdefault(str(interaction.guild.id), {})
        mapping = guild_data.setdefault(str(message.id), {})
        mapping[_emoji_key(parsed)] = role.id
        data[str(interaction.guild.id)] = guild_data
        save_json(REACTION_ROLES_FILE, data)
        await interaction.response.send_message(f"Reacting with {emoji} on [this message]({message.jump_url}) now gives {role.mention}.", ephemeral=True)

    @reaction.command(name="remove", description="Remove a reaction role mapping.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def reaction_remove(interaction: discord.Interaction, message_id: str, emoji: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        data = _reaction_data(); guild_data = data.get(str(interaction.guild.id), {})
        mapping = guild_data.get(str(message_id), {}) if isinstance(guild_data, dict) else {}
        removed = mapping.pop(_emoji_key(emoji), None) if isinstance(mapping, dict) else None
        if not mapping and isinstance(guild_data, dict): guild_data.pop(str(message_id), None)
        data[str(interaction.guild.id)] = guild_data; save_json(REACTION_ROLES_FILE, data)
        await interaction.response.send_message("Reaction role removed." if removed else "That mapping was not found.", ephemeral=True)

    @reaction.command(name="list", description="List configured reaction roles.")
    async def reaction_list(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        guild_data = _reaction_data().get(str(interaction.guild.id), {})
        lines = []
        for message_id, mapping in guild_data.items() if isinstance(guild_data, dict) else []:
            for emoji, role_id in mapping.items(): lines.append(f"Message `{message_id}` • `{emoji}` → <@&{role_id}>")
        await interaction.response.send_message("\n".join(lines[:40]) if lines else "No reaction roles are configured.", ephemeral=True)

    @verification.command(name="setup", description="Create a verification panel.")
    @app_commands.checks.has_permissions(manage_roles=True, manage_channels=True)
    async def verification_setup(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        verified_role: discord.Role,
        remove_role: discord.Role | None = None,
        title: str = "Verify your account",
        description: str = "Press the button below to gain access to the server.",
        button_label: str = "Verify",
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        me = interaction.guild.me
        if verified_role.is_default() or verified_role.managed or not me or verified_role >= me.top_role:
            await interaction.response.send_message("Rallybit cannot assign that verification role.", ephemeral=True); return
        await publish_verification_panel(interaction.guild, channel, verified_role, remove_role, title, description, button_label)
        await interaction.response.send_message(f"Verification panel created in {channel.mention}.", ephemeral=True)

    @verification.command(name="disable", description="Disable button verification.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def verification_disable(interaction: discord.Interaction) -> None:
        if interaction.guild:
            cfg = _verification(interaction.guild.id); cfg["enabled"] = False; _save_verification(interaction.guild.id, cfg)
        await interaction.response.send_message("Verification disabled.", ephemeral=True)

    @verification.command(name="status", description="Show verification settings.")
    async def verification_status(interaction: discord.Interaction) -> None:
        cfg = _verification(interaction.guild.id) if interaction.guild else {}
        await interaction.response.send_message(
            f"Enabled: **{bool(cfg.get('enabled'))}**\nChannel: <#{cfg.get('channel_id')}>\nRole: <@&{cfg.get('role_id')}>\nMessage ID: `{cfg.get('message_id')}`",
            ephemeral=True,
        )

    tree.add_command(autorole)
    tree.add_command(reaction)
    tree.add_command(verification)
