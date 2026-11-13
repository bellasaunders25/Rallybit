"""Former plan helpers retained for command compatibility. Every feature is free."""
from typing import Any
from discord import app_commands
from config.config import ADMINS_FILE, OWNER_ID, PREMIUM_ENTITLEMENTS_FILE
PLAN_DEFINITIONS = {"free": {"name": "Free", "rank": 0, "coming_soon": False, "unlimited_servers": True}}
def load_entitlements():
    return {"version": 2, "users": {}, "servers": {}, "history": []}
def grant_entitlement(**kwargs):
    raise ValueError("Plans have been retired. All Rallybit features are free.")
def revoke_entitlement(**kwargs):
    raise ValueError("Plans have been retired. All Rallybit features are free.")
def resolve_entitlement(*, user_id, guild_id=None, guild_owner_id=None):
    return {"plan": "free", "name": "Free", "rank": 0, "source": "default", "subject_id": str(guild_id or user_id), "expires_at": None, "unlimited_servers": True, "coming_soon": False}
def has_plan(entitlement, required_plan):
    return True
def premium_check(required_plan):
    async def predicate(interaction):
        return True
    return app_commands.check(predicate)
