from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands

from config.config import ADMINS_FILE, OWNER_ID, PREMIUM_ENTITLEMENTS_FILE
from storage.json_store import load_json, save_json


PLAN_DEFINITIONS: dict[str, dict[str, Any]] = {
    "free": {
        "name": "Free",
        "rank": 0,
        "coming_soon": False,
        "unlimited_servers": False,
    },
    "community": {
        "name": "Community",
        "rank": 10,
        "coming_soon": True,
        "unlimited_servers": False,
    },
    "pro": {
        "name": "Pro",
        "rank": 20,
        "coming_soon": True,
        "unlimited_servers": False,
    },
    "network": {
        "name": "Network",
        "rank": 30,
        "coming_soon": True,
        "unlimited_servers": True,
    },
}

SERVER_PLANS = {"community", "pro"}
USER_PLANS = {"network"}
MAX_HISTORY = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _admin_ids() -> set[int]:
    raw = load_json(ADMINS_FILE) or []
    if isinstance(raw, dict):
        raw = raw.get("admin_ids", raw.get("admins", []))
    result = {OWNER_ID} if OWNER_ID else set()
    if isinstance(raw, list):
        for value in raw:
            try:
                result.add(int(value))
            except (TypeError, ValueError):
                continue
    return result


def parse_expiry(value: Any) -> datetime | None:
    if value in (None, "", "never"):
        return None
    try:
        expiry = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Expiration must be a valid ISO-8601 date and time.") from exc
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry.astimezone(timezone.utc)


def normalise_expiry(value: Any, *, require_future: bool = False) -> str | None:
    expiry = parse_expiry(value)
    if expiry is None:
        return None
    if require_future and expiry <= _now():
        raise ValueError("Expiration must be in the future.")
    return expiry.isoformat()


def entitlement_is_active(record: Any, now: datetime | None = None) -> bool:
    if not isinstance(record, dict) or str(record.get("plan", "free")) not in PLAN_DEFINITIONS:
        return False
    try:
        expiry = parse_expiry(record.get("expires_at"))
    except ValueError:
        return False
    return expiry is None or expiry > (now or _now())


def _empty_data() -> dict[str, Any]:
    return {"version": 1, "users": {}, "servers": {}, "history": []}


def load_entitlements() -> dict[str, Any]:
    raw = load_json(PREMIUM_ENTITLEMENTS_FILE) or {}
    data = _empty_data()
    if not isinstance(raw, dict):
        return data
    for key in ("users", "servers"):
        if isinstance(raw.get(key), dict):
            data[key] = raw[key]
    if isinstance(raw.get("history"), list):
        data["history"] = raw["history"][-MAX_HISTORY:]
    return data


def _validate_subject(subject_type: str, subject_id: Any, plan: str | None = None) -> tuple[str, str]:
    kind = str(subject_type or "").strip().lower()
    identifier = str(subject_id or "").strip()
    if kind not in {"user", "server"}:
        raise ValueError("Subject type must be user or server.")
    if not identifier.isdigit() or not 15 <= len(identifier) <= 22:
        raise ValueError("Enter a valid Discord user or server ID.")
    if plan is not None:
        allowed = USER_PLANS if kind == "user" else SERVER_PLANS
        if plan not in allowed:
            if kind == "user":
                raise ValueError("User-ID grants are reserved for the Network plan.")
            raise ValueError("Server-ID grants support the Community and Pro plans.")
    return kind, identifier


def grant_entitlement(
    *,
    subject_type: str,
    subject_id: Any,
    plan: str,
    expires_at: Any = None,
    granted_by: Any,
) -> dict[str, Any]:
    selected_plan = str(plan or "").strip().lower()
    kind, identifier = _validate_subject(subject_type, subject_id, selected_plan)
    expiry = normalise_expiry(expires_at, require_future=True)
    data = load_entitlements()
    bucket = "users" if kind == "user" else "servers"
    previous = data[bucket].get(identifier)
    record = {
        "plan": selected_plan,
        "expires_at": expiry,
        "granted_at": _now_iso(),
        "granted_by": str(granted_by or "system"),
    }
    data[bucket][identifier] = record
    data["history"].append({
        "action": "extended" if isinstance(previous, dict) else "granted",
        "subject_type": kind,
        "subject_id": identifier,
        "plan": selected_plan,
        "expires_at": expiry,
        "actor_id": str(granted_by or "system"),
        "timestamp": record["granted_at"],
    })
    data["history"] = data["history"][-MAX_HISTORY:]
    if not save_json(PREMIUM_ENTITLEMENTS_FILE, data):
        raise RuntimeError("Could not save the premium entitlement.")
    return record


def revoke_entitlement(*, subject_type: str, subject_id: Any, revoked_by: Any) -> bool:
    kind, identifier = _validate_subject(subject_type, subject_id)
    data = load_entitlements()
    bucket = "users" if kind == "user" else "servers"
    previous = data[bucket].pop(identifier, None)
    if previous is None:
        return False
    data["history"].append({
        "action": "revoked",
        "subject_type": kind,
        "subject_id": identifier,
        "plan": previous.get("plan"),
        "expires_at": previous.get("expires_at"),
        "actor_id": str(revoked_by or "system"),
        "timestamp": _now_iso(),
    })
    data["history"] = data["history"][-MAX_HISTORY:]
    if not save_json(PREMIUM_ENTITLEMENTS_FILE, data):
        raise RuntimeError("Could not revoke the premium entitlement.")
    return True


def _candidate(record: Any, source: str, subject_id: int) -> dict[str, Any] | None:
    if not entitlement_is_active(record):
        return None
    plan = str(record.get("plan", "free"))
    return {
        "plan": plan,
        "name": PLAN_DEFINITIONS[plan]["name"],
        "rank": PLAN_DEFINITIONS[plan]["rank"],
        "source": source,
        "subject_id": str(subject_id),
        "expires_at": record.get("expires_at"),
        "unlimited_servers": bool(PLAN_DEFINITIONS[plan]["unlimited_servers"]),
        "coming_soon": bool(PLAN_DEFINITIONS[plan]["coming_soon"]),
    }


def resolve_entitlement(
    *,
    user_id: int,
    guild_id: int | None = None,
    guild_owner_id: int | None = None,
) -> dict[str, Any]:
    if user_id in _admin_ids():
        return {
            "plan": "network",
            "name": "Developer preview",
            "rank": PLAN_DEFINITIONS["network"]["rank"],
            "source": "developer",
            "subject_id": str(user_id),
            "expires_at": None,
            "unlimited_servers": True,
            "coming_soon": False,
        }

    data = load_entitlements()
    candidates: list[dict[str, Any]] = []

    if guild_id is not None:
        server_candidate = _candidate(data["servers"].get(str(guild_id)), "server", guild_id)
        if server_candidate and server_candidate["plan"] in SERVER_PLANS:
            candidates.append(server_candidate)

    # Network follows the subscriber across every server they actually own.
    # A moderator or administrator cannot carry their personal grant elsewhere.
    if guild_owner_id:
        owner_candidate = _candidate(data["users"].get(str(guild_owner_id)), "owner", guild_owner_id)
        if owner_candidate and owner_candidate["plan"] in USER_PLANS:
            candidates.append(owner_candidate)
    elif guild_id is None:
        own_candidate = _candidate(data["users"].get(str(user_id)), "user", user_id)
        if own_candidate and own_candidate["plan"] in USER_PLANS:
            candidates.append(own_candidate)

    if not candidates:
        return {
            "plan": "free",
            "name": "Free",
            "rank": 0,
            "source": "default",
            "subject_id": str(guild_id or user_id),
            "expires_at": None,
            "unlimited_servers": False,
            "coming_soon": False,
        }
    return max(candidates, key=lambda item: int(item["rank"]))


def has_plan(entitlement: dict[str, Any], required_plan: str) -> bool:
    required = PLAN_DEFINITIONS.get(required_plan, PLAN_DEFINITIONS["network"])
    return int(entitlement.get("rank", 0)) >= int(required["rank"])


def premium_check(required_plan: str):
    async def predicate(interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        entitlement = resolve_entitlement(
            user_id=interaction.user.id,
            guild_id=guild.id if guild else None,
            guild_owner_id=guild.owner_id if guild else None,
        )
        if has_plan(entitlement, required_plan):
            return True
        label = PLAN_DEFINITIONS.get(required_plan, PLAN_DEFINITIONS["network"])["name"]
        raise app_commands.CheckFailure(
            f"This preview command requires the **{label}** plan. Paid plans are coming soon."
        )

    return app_commands.check(predicate)
