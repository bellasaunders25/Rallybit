# CVMS beta verification — 13 November 2026

Release: `36b7494a11ad10632e713e484243501d2fbb97b4`.

## What was verified

- All 130 JavaScript tests passed locally and on the Raspberry Pi in a read-only, unprivileged, network-disabled test container. These include the existing 202 command paths and the 14 new CVMS paths.
- Consent withdrawal, participant changes, stale disclosure buttons, expired consent/cases, moderation-role removal, bounded audio queues, disk-full failure, case review and temporary Connect-overwrite restoration have regression coverage.
- The final local speech container processed an eight-second public JFK sample in approximately 2.8 seconds under a one-CPU limit. A synthetic, isolated channel generated exactly one pending phrase-match case and no punishment. This is a smoke test, not an accuracy or real-time coverage guarantee.
- The smaller English `tiny.en-q5_1` model was selected after the larger model took approximately 22 seconds for that sample. Model downloads are checksum-verified. Inference has no paid API, external provider account or API key.
- Production reconnected to all seven existing guilds. Discord REST registration matched the exact 216-path / 78-root command contract. The authenticated channel/role resource bridge returned successfully for each connected guild.
- One controlled restart passed with the service notice active. The quiz-dispatch ledger did not change during that paused restart. The original notice state was restored.
- Both bot and local speech containers were healthy. The bot could reach the speech service over the private internal network; the speech service has no published host port or persisted inference logs.
- Runtime dependency checks found the Opus decoder, native AES-256-GCM and DAVE library. FFmpeg is not required for the PCM-to-WAV path used here.
- The public homepage, status page, CVMS guide, command catalogue, terms and privacy policy returned HTTP 200. Login produced a secure session cookie, a fresh 48-character OAuth state and the configured Pages callback. A real Discord-account login was not performed by the agent.
- Vyloza's web, import and push services remained active. No Vyloza files, containers or volumes were changed.

## Deliberately not enabled

No guild has been granted CVMS beta access, enabled monitoring or supplied member consent as part of deployment. No real Discord voice channel was joined or recorded for testing.

An end-to-end Discord voice test with informed, consenting participants is still required before describing voice reception as live-validated. Speech recognition is English-only and literal phrase matching is not a semantic hate-speech verdict. Staff review is required before any punishment.

## Operations and recovery

The production Compose project is now `rallybit-cvms`. It preserves the prior stopped JavaScript container outside the new project's lifecycle. Use the existing production project name for subsequent updates:

```sh
docker compose -p rallybit-cvms -f /opt/rallybit/bot-js/compose.yaml --profile cvms up -d --no-build
```

Build and tag a verified committed release before using `--no-build`; do not start another project with the same bot token or data directory. The self-hosting guide's example project name is for separate installations, not a second production instance.

Private pre-release recovery archive:
`/home/bellasaunders/.rallybit-recovery/20261113T083843Z/rallybit-before-cvms.tar.gz`.

SHA-256: `4099b246364c3361fe4aa5860cbd314cf2ec3a7f0001039aa9aa5e0641feccd7`.

The archive includes private configuration and must never be published. A verified local recovery copy is kept outside the repository. The previous JavaScript container is `rallybit-js-rollback-36b7494`, stopped with automatic restart disabled. The earlier Python recovery container is also stopped; neither may run alongside production.

Recovery must stop the new gateway first, preserve any post-release data, restore the intended snapshot if necessary, and only then start the previous gateway. Never blindly overwrite new moderation records during a later rollback. Future snapshots must exclude CVMS case excerpts and consent grants unless their retention and security have been explicitly reviewed.

## Voice-channel / 24/7 follow-up — 09:08 UTC

Patch `48fd3fa0d9dd50cebc2e6b797f568cf51a8e25fa` separates unsupported Stage channels from ordinary voice channels missing from the allowed list. The reported server had no configured voice channels; the shared eligibility message incorrectly suggested a Stage-channel issue. Channel resolution now uses the actual guild channel. Administrators can add their selected channel through Start without bypassing consent or widening access for other staff.

`/cvms start channel:General continuous:true` persists a selected channel in 24/7 mode. Sessions have no hourly cutoff; empty channels and missing/expired consent wait without receiving audio. Individual consent still expires after 24 hours. Explicit stops remain durable across restarts. Status includes the pinned channel, pause reason or waiting state. Timed sessions remain available in manual mode.

All 140 tests passed both locally and in the restricted Pi test container, including the new channel-resolution, configuration-access, continuous-session, restart, consent-renewal and stale-pause-race checks. Production registered the new Boolean command option, retained all 216 command paths and reconnected to seven guilds. Existing CVMS settings were compared before and after deployment and were unchanged. The local speech service and public documentation/status routes were healthy. No live voice join was initiated by the deployment checks.

The patch snapshot explicitly excludes CVMS case excerpts and individual consent files. Its private path is `/home/bellasaunders/.rallybit-recovery/20261113T090826Z/rallybit-before-cvms-channel-fix.tar.gz`; SHA-256 `54d062ee9df23187d41fbcc4da6eace11d304c20364e87b1ec8f871d338e589c`. Rollback image: `rallybit-discordjs:rollback-48fd3fa`. A rollback should restore code/runtime only unless a separate, reviewed data recovery is needed; do not overwrite current server data. The Compose project remains `rallybit-cvms`.
