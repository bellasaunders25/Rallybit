# Rallybit runtime migration and incident recovery

## 13 November 2026 incident

The Raspberry Pi root filesystem was full (29 GB used, zero bytes available).
The retired Socento/Stoat Docker stack had a 6.3 GB MongoDB crash log and a
1.1 GB RabbitMQ log with no rotation. Rallybit logged ENOSPC when saving data.
Only one Rallybit process was observed. Vyloza runs separately from
`/mnt/vyloza`; its services and application data were not changed.

The Python storage helper used to return False after write failure, but callers
ignored the return value. Consequently security commands could report success
without saving their changes. The quiz task sent messages before checking that
its last-run timestamp had been saved. Failed timestamp writes allowed another
quiz at the next task tick. PHP session writes also require free disk space;
losing the saved OAuth state causes the reported invalid-request error.

Recovery changes:

- Remove the user-retired Socento containers and deployment after downloading a
  private recovery archive to the operator's computer.
- Storage writes use unique temporary files, flush before replacement, and raise
  errors instead of silently claiming success. Dashboard writes return HTTP 507.
- Both automation schedulers reserve their next interval before sending.
- Automatic quizzes share a durable five-minute minimum dispatch interval across
  both schedulers. Uncertain sends are not immediately retried.
- Rallybit uses its own PHP session-cookie name. Up to eight login states can
  coexist for ten minutes, and each is consumed once.
- Ticket dropdown roles are specific to their selected option. Removed roles
  are reconciled on existing ticket channels immediately after panel updates and
  once per minute for dashboard setting changes. Other overwrite flags remain.
- Rallybit log rotation is specified in compose (three files of 10 MB each).

## JavaScript conversion status

`bot-js/` contains the native discord.js implementation. Production cutover is
still pending platform verification; the live deployment remains Python until
that checklist passes. Its command coverage report is executable:

```
cd bot-js
npm ci
npm test
npm run coverage
```

The reference inventory contains 202 leaf commands in 77 top-level groups.
Every native handler must register against that inventory; duplicate and unknown
paths are rejected. Production startup refuses to connect to Discord until all
202 paths have handlers. No migration placeholders are exposed as working commands.

Currently registered: **202 of 202 native commands**, with no Python command
proxy or placeholder handlers. All **26 dashboard actions** have native handlers.
Modules cover staff/workforce, role timers, moderation, reports/reviews, streaming
archives/purges, audit/security listeners, tickets, member tools, welcome/invites,
activity checks, quizzes, pulses, giveaways, durable schedules, insights, backups,
owner-network tools, developer controls and Prettfy.
All commands are free; no subscription checks are applied. The staff ledger preserves active shifts, breaks, completed record IDs, HR
corrections and LOA/ROA requests. Temporary-role timers survive restarts, enforce
member/role hierarchy, and retain failed nickname restorations for retry.

Shared services now provide a serialised atomic JSON store, safe legacy Discord
ID parsing, a SQLite runtime/job lease, durable schedule reservations, command
dispatch, current member/role checks, free access without subscription gates, service-notice
embeds, reply visibility, and bounded audit records. A central access policy is
required for every registered handler. Permission failures are private.

The complete Discord registration contract has been exported to
`bot-js/contracts/commands.json`. The tests compare all 202 paths, option names,
types, choices, required flags and boundaries against the reference inventory.
The export tool creates no additional gateway connection and performs no Discord
registration writes. The comparison also corrected 22 string-length metadata
fields in the public command reference.

The JavaScript tests cover concurrent clock-ins, break timing across restarts,
record deletion confirmation, request ownership, free access and retained staff permissions,
role hierarchy, timed-role expiry, permanent conversion, nickname recovery,
disk-full failures, duplicate schedulers, uncertain sends and lease replacement.

The live deployment stays on the corrected Python runtime while feature parity
is implemented and verified. Handler coverage alone is not proof of full event,
dashboard API, recovery or persistent-component compatibility.

The current JavaScript suite has over 100 tests. Checks include moderation role
revocation, role hierarchy, atomic report claims, failed report delivery, large-ID
purges, old-message deletion, dashboard authentication/actions, disk-full failures,
profile rollback, formula-safe CSV, journaled backups, duplicate quiz prevention,
safe event recovery, ticket role revocation and timed deletion, Prettfy coverage,
temporary review cleanup, legacy undo snapshots, startup guards and gateway wiring.
CVMS is paused until the existing migration and safe production cutover complete.

## Deployment checklist

1. Run `npm ci`, `npm test`, `npm run coverage`, secret scanning and diff validation.
   All 202 registration paths, options and access policies must match the contract.
2. Build `bot-js/Dockerfile --target test` on the target architecture and run its
   tests without a token, gateway connection or live data volume.
3. Build the `runtime` target. Inspect legacy JSON shapes and validate the data
   snapshot with the new parser. Do not silently replace corrupt files.
4. Preserve the current service notice. Stop Python and its schedulers, disable
   its container auto-restart, and take a private data/environment recovery archive
   outside the web root. Keep the stopped Python container/image for rollback.
   After the snapshot, assign ownership of only Rallybit's data directory to
   UID/GID 1000 (the image's `node` account), without following symbolic links.
5. Start exactly one JS container using `bot-js/compose.yaml`. It owns the existing
   data directory and localhost API port. Confirm health, Discord registration,
   all connected guilds, dashboard resources and persistent-control compatibility.
6. Observe recovery/scheduler logs and restart once. Check that no announcements
   are duplicated and that settings/active shifts/role timers remain intact.
7. Restore the original notice, confirm public health/login routing, and record
   the deployed commit. If verification fails, stop JS before restoring the private
   snapshot and restarting the saved Python container. Never run both gateways.

The Docker image does not contain tokens or customer data. The container has a
read-only root filesystem, a bounded temporary archive directory, rotated logs,
resource limits and no added Linux capabilities. The bot runs as the unprivileged
`node` account. Legacy data ownership is normalised to UID/GID 1000 after taking
the recovery snapshot; the snapshot preserves original ownership for rollback.
No Vyloza files, services or volumes are part of this cutover.

The current deployment has one shard. Its developer restart control restarts that
single supervised process. Targeted multi-shard restarts require a separate sharding
supervisor and are rejected rather than falsely reported as successful.

## Dashboard redesign brief

The supplied reference calls for a light interface with deep green Rallybit
branding, a compact public-site navigation, clean feature cards, and a fixed
dashboard sidebar. The dashboard overview should include a server selector,
search, member/moderation/ticket/message totals, an activity chart, module
switches, moderation events and recent configuration changes. Use consistent
line icons, clear borders and spacing, and responsive layouts across every page.
The screenshot domains are visual examples, not an instruction to change the
current production domain or OAuth redirect.
