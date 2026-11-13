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
