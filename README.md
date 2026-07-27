# Rallybit

Rallybit is a Discord community toolkit for activity checks, recurring community quizzes, anonymous pulse checks, participation rankings, moderation workflows, audit logs, and a web dashboard. Every existing command remains free; advanced Community, Pro, and Network tools are available as developer previews while public plans are Coming soon.

## Plans and preview access

- **Free** keeps every existing Rallybit command at no charge.
- **Community** adds insights, searchable member case files, recent-case filters, and moderation workload statistics for one manually granted server.
- **Pro** adds the AI-guided `/prettfy` naming wizard with per-phase previews and undo, filtered CSV exports, persistent staff shift tracking, ten retained configuration backups, drift reports, and transactional Rallybit-setting restores for one manually granted server.
- **Network** follows one Discord owner across every server they own, adding an owned-server overview, configured announcement destinations, owner-only cross-server broadcasts, and network exports. It never follows a moderator or administrator into a server owned by someone else.
- The bot's server-specific avatar follows the effective plan colour: Free keeps the shared violet logo, while Community, Pro, and Network use their matching guild avatar. Plan changes and expirations are synchronised automatically.
- Paid checkout is not enabled. Bot developers can grant and revoke previews, with optional UTC expiration, from Dashboard → Developer tools.

Use `/premium plans` and `/premium status` in Discord to view available tiers and effective access.

## Build 7.2.2 highlights

### Restart-safe live sessions

- Active activity checks now restore their original timer, input mode, participant target, ping and recorded participants after a process or Raspberry Pi restart.
- Live quizzes restore their exact question, answer buttons, hidden answers, speed timings and remaining duration.
- Community pulses restore their buttons, temporary anonymous response map and remaining duration, then delete identity-level recovery data after publishing aggregate results.
- Expired sessions are finalised automatically after startup instead of remaining stuck.
- Recovery runs before recurring schedulers, preventing duplicate automatic checks or quizzes during boot.
- Quiz points, pulse history and activity awards use idempotent commit markers so interrupted finalisation does not award results twice.

## Build 7.2.0 highlights

### Security suite

- `/security-trap setup` creates a top-level `do-not-text-here` honeypot and applies a configurable instant ban or kick.
- `/security-agegate configure` DMs and kicks Discord accounts below a server-defined account age.
- Per-server anti-bot, anti-webhook, anti-integration, dangerous-permission, anti-nuke, anti-spam, automod, quarantine, lockdown, panic, invite purge, audit, history, trusted-role, and logging controls.
- Automatic modules default to disabled until a server administrator configures them.


- Added random community quizzes with hidden answers, explanations, speed-based scoring, streaks, and per-server quiz leaderboards.
- Added recurring quiz schedules configurable by channel, category, interval, answer time, and an optional chat-revive role ping.
- Added a separate community notification role for pulses and staff-triggered icebreakers, with safe one-role mention handling and no `@everyone` fallback.
- Added an interactive moderation panel with warnings, warning history, timeouts, timeout removal, kicks, bans, message clearing, nickname changes, role changes, and unbans by user ID.
- Added role-hierarchy and permission validation before every moderation action.
- Added moderation history and warning IDs stored in local JSON data.
- Added anonymous community pulse checks. Individual choices are held only while a pulse is open; completed history stores aggregate totals.
- Added conversation-starting icebreakers for casual, gaming, creative, and debate channels.
- Updated the public site, documentation, command centre, changelog, and invite permissions for the new features.

## Project layout

```text
Rallybit/
├── index.html, style.css, script.js   Public website
├── dashboard/                         Discord OAuth management dashboard
├── docs/                              Documentation
├── changelogs/                        Release notes
├── rallybitbot/                       Python Discord bot and local API bridge
├── .env.example                       Website/dashboard configuration template
└── .htaccess                          Apache routing and security headers
```

## Requirements

### Website

- PHP 8.1 or newer
- PHP cURL extension
- Apache with `mod_rewrite` and `mod_headers`, or equivalent Nginx rules
- HTTPS for production Discord OAuth

### Bot

- Python 3.11 or newer
- Packages from `rallybitbot/requirements.txt`
- Discord application with **Server Members Intent** enabled
- **Message Content Intent** enabled for optional security invite/term filtering and message edit/delete logs
- Presence Intent is not required

## Bot permissions

Core activity and community features use:

- View Channels
- Send Messages
- Embed Links
- Attach Files
- Read Message History
- Add Reactions
- Use External Emojis
- Mention Everyone, Here, and Roles

Moderation and security actions additionally use their matching permissions:

- Manage Messages
- Moderate Members
- Kick Members
- Ban Members
- Manage Nicknames
- Manage Roles
- Manage Channels
- Manage Webhooks
- Manage Server
- View Audit Log

Rallybit does not require Administrator. The full invite permission integer used by the included website is `1100451671286`.

## 1. Configure the website

Copy the root environment template:

```bash
cp .env.example .env
```

Fill in:

- `DISCORD_CLIENT_ID` — Discord application ID
- `DISCORD_CLIENT_SECRET` — Discord OAuth secret
- `DISCORD_REDIRECT_URI` — exact callback URL, such as `https://example.com/dashboard/callback.php`
- `MASTER_ADMIN_ID` — Discord user ID allowed to open developer tools
- `SUPPORT_SERVER_URL` — support-server invite
- `BOT_API_URL` — private bot API address, normally `http://127.0.0.1:8080`
- `BOT_API_KEY` — a long random secret shared with the bot
- `DASHBOARD_SESSION_DAYS` — persistent dashboard login lifetime, from 1 to 90 days (default 30)

In the Discord Developer Portal, add the exact redirect URI and enable the `identify` and `guilds` OAuth scopes.

Dashboard sessions persist across browser restarts. Discord access tokens are refreshed server-side using the OAuth refresh token, which remains inside the protected PHP session and is never written to a browser-readable cookie.

## 2. Configure the bot

```bash
cd rallybitbot
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `rallybitbot/.env`:

- `DISCORD_BOT_TOKEN`
- `OWNER_ID`
- `DASHBOARD_URL`
- `SUPPORT_SERVER_URL`
- `BOT_PROFILE_NAME` — optional global username for this Discord application
- `BOT_PROFILE_AVATAR_URL` — optional public HTTPS avatar URL smaller than 8 MB
- `BOT_PRESENCE_STATUS` — `online`, `idle`, `dnd`, or `offline`
- `BOT_API_SECRET` — must exactly match the website's `BOT_API_KEY`
- `API_HOST=127.0.0.1` unless the bridge is protected by a private network or reverse proxy

Start it with:

```bash
python main.py
```

## New command groups

```text
/quiz start
/quiz stop
/quiz setup
/quiz auto
/quiz pingrole
/quiz settings
/quiz leaderboard

/mod panel
/mod warn
/mod warnings
/mod unwarn
/mod unban
/role give
/role remove
/role temp
/ticket panel create
/ticket panel add-option
/ticket panel remove-option

/channel transcript
/channel purge

/premium status
/premium plans
/insights overview
/insights export
/staff clockin
/staff clockout
/staff status
/staff leaderboard
/prettfy
/case member
/case recent
/case stats
/case export
/backup create
/backup list
/backup inspect
/backup drift
/backup restore
/backup delete
/network channel
/network overview
/network broadcast
/network export

/community icebreaker
/community pulse
/community pingrole
/community settings
/community stoppulse
/community pulsehistory
```

Use `/help` inside Discord for the complete command centre.

### Dropdown ticket panels

Ticket launchers use one persistent select menu instead of a separate message for every request type. A panel supports up to 25 options, and every option can have its own name, 100-character description, Unicode or custom server emoji, ticket category, and support role. Use `/ticket panel add-option` or `/ticket panel remove-option` to update the live message.

The dashboard publisher also supports an optional header banner, thumbnail, main image, custom author, footer text and footer icon, accent colour, timestamp, workload, guidance, and in-embed option details. Media must use public HTTPS URLs. Older Rallybit button panels are read as one-option dropdowns and refreshed without discarding their saved routing.

### Safe role commands

`/role give` and `/role remove` enforce the acting moderator's role hierarchy as well as Rallybit's. `/role temp` supports days, weeks, 30-day months, and 365-day years, persists timers across restarts, displays a compact live nickname countdown, and restores the member's original nickname after their final temporary role ends. A custom base nickname is optional and additionally requires Manage Nicknames.

### Prettfy AI naming wizard

`/prettfy` is a Pro preview command for administrators with Manage Channels and Manage Roles. The bot collects the design brief in DMs, proposes channel names first, and changes only explicitly approved names. It then repeats the preview and approval process for roles. One live DM checklist shows the exact step that is waiting, running, complete, skipped, or failed, including generation retries and rename counts. OpenRouter responses are validated, common JSON wrappers are handled safely, and one malformed response is retried before the run stops without unapproved changes. Prettfy never changes permission overwrites, role permissions, hierarchy, colours, topics, assignments, or channel types. Use `/prettfy undo_last:true` to preview and restore names from the latest active run.

Configure `OPENROUTER_API_KEY` only in the bot's private `.env` file. `OPENROUTER_MODEL` defaults to `openrouter/free` and can be pinned to another structured-output-compatible model. OpenRouter's free router is rate limited; the key must never be committed or entered through Discord.

## Shared and self-hosted bot identity

Discord bot usernames, profile pictures, and presence are global to a bot application. The Developer tools page can update the shared Rallybit application immediately, so those controls must remain restricted to bot developers.

For a separately branded or self-hosted instance, create a separate Discord application and configure its token plus `BOT_PROFILE_NAME`, `BOT_PROFILE_AVATAR_URL`, and `BOT_PRESENCE_STATUS` in that instance's private `.env` file. Never enter customer bot tokens into the Rallybit web dashboard. Automated dedicated-instance provisioning is a Network feature marked Coming soon.

## 3. Deploy the website

Point the web root at the `Rallybit` directory. Keep `rallybitbot/` blocked from public web access; the included `.htaccess` files deny direct access on Apache.

For production, run the bot under a service manager such as systemd, Docker, or a managed process host. Keep the bot API bound to localhost where the website and bot share a server.

## Live recovery data

```text
active_checks.json
active_quizzes.json
active_pulses.json
```

These files are temporary recovery ledgers. Keep them writable and do not clear them while Rallybit is running. They are cleaned automatically after each live session is safely finalised.

## Community-suite data files

```text
quiz_settings.json
quiz_stats.json
quiz_history.json
moderation_warnings.json
moderation_history.json
pulse_history.json
community_settings.json
premium_entitlements.json
staff_shifts.json
premium_backups.json
network_settings.json
```

The files are created with empty JSON objects in a clean installation. Keep the `rallybitbot/data/` directory writable by the bot service account.

## Security notice

The supplied original archive contained credentials embedded in source files. Removing them from this rebuilt package does **not** invalidate them. Rotate every previously exposed Discord token, OAuth secret, webhook URL, database password, mail credential, and other API secret before deploying Rallybit.

- `security_settings.json` — per-server security configuration.
- `security_history.json` — persistent security actions and triggers.
- `security_quarantine.json` — roles saved for quarantined members.
- `security_lockdown.json` — @everyone permission snapshots for lockdown restoration.
