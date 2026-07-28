"""Rallybit runtime configuration.

All secrets are loaded from environment variables. Copy .env.example to .env
for local development and never commit the real .env file.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SERVER_LOGS_DIR = DATA_DIR / "server_logs"
SERVER_LOGS_DIR.mkdir(parents=True, exist_ok=True)

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
PREFIX = os.getenv("BOT_PREFIX", "!")
SHARD_COUNT = max(1, int(os.getenv("SHARD_COUNT", "1") or 1))
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8080") or 8080)
API_SECRET = os.getenv("BOT_API_SECRET", "").strip()
TOPGG_WEBHOOK_TOKEN = os.getenv("TOPGG_WEBHOOK_TOKEN", "").strip()
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost/dashboard").rstrip("/")
SUPPORT_SERVER_URL = os.getenv("SUPPORT_SERVER_URL", "https://discord.com")
BOT_PROFILE_NAME = os.getenv("BOT_PROFILE_NAME", "").strip()
BOT_PROFILE_AVATAR_URL = os.getenv("BOT_PROFILE_AVATAR_URL", "").strip()
BOT_PRESENCE_STATUS = os.getenv("BOT_PRESENCE_STATUS", "online").strip().lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free").strip() or "openrouter/free"

# Existing custom emoji IDs are intentionally retained until the visual rebrand.
WIN_BADGE_1 = "<:5won:1445611065705627732>"
WIN_BADGE_2 = "<:50won:1445610973053452439>"
WIN_BADGE_3 = "<:70won:1445611028296630438>"
OWNER_BADGE = "<:owner:1445610988438290473>"
DEV_BADGE = "<:developer:1445611005945057330>"
MANAGER_BADGE = "<:manager:1445613111942975660>"
CONTRIBUTOR_BADGE = "<:contributor:1445612710707593370>"
EARLY_BADGE = "<:marketingmanager:1449509486766330000>"


def data_file(name: str) -> str:
    return str(DATA_DIR / name)


ACTIVE_CHECKS_FILE = data_file("active_checks.json")
ACTIVE_QUIZZES_FILE = data_file("active_quizzes.json")
ACTIVE_PULSES_FILE = data_file("active_pulses.json")
GLOBAL_STATS_FILE = data_file("global_stats.json")
SETTINGS_FILE = data_file("activity_settings.json")
LOG_FILE = data_file("activity_log.json")
PROMO_LOG_FILE = data_file("promotion_log.json")
LIMITS_FILE = data_file("limits.json")
BANNED_SERVERS_FILE = data_file("banned_servers.json")
BANNED_USERS_FILE = data_file("banned_users.json")
ADMINS_FILE = data_file("admins.json")
BOT_SETTINGS_FILE = data_file("bot_settings.json")
NOTICE_FILE = data_file("notice.json")
PREMIUM_ENTITLEMENTS_FILE = data_file("premium_entitlements.json")
STAFF_SHIFTS_FILE = data_file("staff_shifts.json")
WORKFORCE_SETTINGS_FILE = data_file("workforce_settings.json")
STAFF_REQUESTS_FILE = data_file("staff_requests.json")
AUDIT_SETTINGS_FILE = data_file("audit_settings.json")
AUDIT_EVENTS_FILE = data_file("audit_events.json")
PREMIUM_BACKUPS_FILE = data_file("premium_backups.json")
NETWORK_SETTINGS_FILE = data_file("network_settings.json")
ACTIVITY_AUDIT_FILE = data_file("activity_audit_logs.json")
BADGES_FILE = data_file("badges.json")
ANALYTICS_FILE = data_file("server_analytics.json")
LAST_SEEN_FILE = data_file("last_seen.json")
MEMBER_EVENTS_FILE = data_file("member_events.json")
WIN_BADGES_FILE = data_file("win_badges.json")
QUIZ_SETTINGS_FILE = data_file("quiz_settings.json")
QUIZ_STATS_FILE = data_file("quiz_stats.json")
QUIZ_HISTORY_FILE = data_file("quiz_history.json")
WARNINGS_FILE = data_file("moderation_warnings.json")
MOD_HISTORY_FILE = data_file("moderation_history.json")
MOD_PERMISSIONS_FILE = data_file("moderation_permissions.json")
PULSE_HISTORY_FILE = data_file("pulse_history.json")
COMMUNITY_SETTINGS_FILE = data_file("community_settings.json")

SECURITY_SETTINGS_FILE = data_file("security_settings.json")
SECURITY_HISTORY_FILE = data_file("security_history.json")
SECURITY_QUARANTINE_FILE = data_file("security_quarantine.json")
SECURITY_LOCKDOWN_FILE = data_file("security_lockdown.json")


# Rallybit 8.0 community management data stores.
GIVEAWAY_SETTINGS_FILE = data_file("giveaway_settings.json")
ACTIVE_GIVEAWAYS_FILE = data_file("active_giveaways.json")
GIVEAWAY_HISTORY_FILE = data_file("giveaway_history.json")
WELCOME_SETTINGS_FILE = data_file("welcome_settings.json")
INVITE_TRACKING_FILE = data_file("invite_tracking.json")
LEVEL_SETTINGS_FILE = data_file("level_settings.json")
LEVEL_STATS_FILE = data_file("level_stats.json")
AUTOROLE_SETTINGS_FILE = data_file("autorole_settings.json")
REACTION_ROLES_FILE = data_file("reaction_roles.json")
VERIFICATION_SETTINGS_FILE = data_file("verification_settings.json")
TICKET_SETTINGS_FILE = data_file("ticket_settings.json")
TICKET_PANELS_FILE = data_file("ticket_panels.json")
OPEN_TICKETS_FILE = data_file("open_tickets.json")
TICKET_HISTORY_FILE = data_file("ticket_history.json")
AUTOMATION_SCHEDULES_FILE = data_file("automation_schedules.json")
AFK_STATUS_FILE = data_file("afk_status.json")
REPORT_SETTINGS_FILE = data_file("report_settings.json")
REPORTS_FILE = data_file("reports.json")
REVIEW_SETTINGS_FILE = data_file("review_settings.json")
PRETTFY_HISTORY_FILE = data_file("prettfy_history.json")
PRETTFY_DRAFTS_FILE = data_file("prettfy_drafts.json")
PLAN_AVATAR_STATE_FILE = data_file("plan_avatar_state.json")
TEMPORARY_ROLES_FILE = data_file("temporary_roles.json")
