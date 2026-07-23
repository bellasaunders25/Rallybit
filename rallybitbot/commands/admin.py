from __future__ import annotations

import discord
from discord import app_commands

from config.config import OWNER_ID, ADMINS_FILE, BANNED_SERVERS_FILE, BANNED_USERS_FILE
from storage.json_store import load_json, save_json


def _admin_ids() -> list[int]:
    raw = load_json(ADMINS_FILE) or []
    if isinstance(raw, dict):
        raw = raw.get("admin_ids", raw.get("admins", []))
    ids = []
    for value in raw:
        try: ids.append(int(value))
        except (TypeError, ValueError): pass
    if OWNER_ID and OWNER_ID not in ids: ids.append(OWNER_ID)
    return ids


def setup_admin_commands(tree: app_commands.CommandTree):
    @tree.command(name="addadmin", description="Add a Rallybit bot administrator.")
    @app_commands.describe(user="The user to grant bot administration access.")
    async def add_admin(interaction: discord.Interaction, user: discord.User):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        admins = _admin_ids()
        if user.id not in admins: admins.append(user.id)
        save_json(ADMINS_FILE, admins)
        await interaction.response.send_message(f"✅ {user.mention} is now a Rallybit administrator.", ephemeral=True)

    @tree.command(name="banuser", description="Block a user from using Rallybit.")
    async def ban_user(interaction: discord.Interaction, user: discord.User, reason: str = "Global bot ban"):
        if interaction.user.id not in _admin_ids():
            return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        data = load_json(BANNED_USERS_FILE) or {}
        data[str(user.id)] = {"reason": reason, "moderator_id": str(interaction.user.id)}
        save_json(BANNED_USERS_FILE, data)
        await interaction.response.send_message(f"✅ Blocked {user.mention}.", ephemeral=True)

    @tree.command(name="banserver", description="Block a server from using Rallybit.")
    async def ban_server(interaction: discord.Interaction, server_id: str, reason: str = "Global bot ban"):
        if interaction.user.id not in _admin_ids():
            return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        server_id = server_id.strip()
        if not server_id.isdigit():
            return await interaction.response.send_message("Enter a valid numeric Discord server ID.", ephemeral=True)
        data = load_json(BANNED_SERVERS_FILE) or {}
        data[server_id] = {"reason": reason[:300], "moderator_id": str(interaction.user.id)}
        save_json(BANNED_SERVERS_FILE, data)
        await interaction.response.send_message(f"✅ Blocked server `{server_id}`.", ephemeral=True)

    @tree.command(name="syncguilds", description="Refresh the dashboard server cache.")
    async def sync_guilds(interaction: discord.Interaction):
        if interaction.user.id not in _admin_ids():
            return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        from core.bot import client
        client.save_guilds_to_file()
        await interaction.response.send_message("✅ Dashboard server cache refreshed.", ephemeral=True)
