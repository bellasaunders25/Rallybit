# Migrating to Rallybit

## Before deployment

1. Revoke and regenerate every credential that existed in the old source archive.
2. Create fresh root and bot `.env` files from the included examples.
3. Ensure `BOT_API_KEY` and `BOT_API_SECRET` contain the same long random value.
4. Back up the old installation somewhere outside the public web root.

## Moving bot data

Stop both bot processes before copying data. Review each JSON file manually and move only datasets that are still needed, such as:

- `activity_settings.json`
- `global_stats.json`
- `activity_log.json`
- `activity_audit_logs.json`
- `badges.json`
- `win_badges.json`
- `last_seen.json`
- `server_analytics.json`

Do not copy old configuration files, credentials, webhooks, access-tier records, entitlement records, logs, backups, `.env` files, virtual environments, or source-code caches.

Rallybit automatically strips known retired feature-gate keys from `bot_settings.json`, but starting from the supplied clean file is safer.

## Discord application settings

- Replace the old callback URL with the Rallybit dashboard callback.
- Confirm the bot has `bot` and `applications.commands` scopes.
- Enable the Server Members privileged intent.
- Reinvite the bot if its permissions or application ID changed.

## Verification checklist

- The public home page and documentation load over HTTPS.
- Discord sign-in returns to `/dashboard/index.php`.
- The dashboard lists servers the signed-in user administers.
- Saving server settings updates `rallybitbot/data/activity_settings.json` through the private API.
- `/activitycheck` starts only one check per server.
- Button and reaction participation both record check-ins.
- Manual stops appear as `Manually Stopped`, not `Completed`, in audit logs.
- Recurring checks start without blocking checks scheduled for other servers.

## Build 7.1 community data

A clean 7.1 installation adds these stores automatically:

- `quiz_settings.json`
- `quiz_stats.json`
- `quiz_history.json`
- `moderation_warnings.json`
- `moderation_history.json`
- `pulse_history.json`

When applying the incremental update, the installer creates these files only when they do not already exist. Existing quiz, moderation, and pulse data is not overwritten.

After updating, reinvite Rallybit or update its server role if you want to use the optional moderation actions. The bot still does not require Administrator.
