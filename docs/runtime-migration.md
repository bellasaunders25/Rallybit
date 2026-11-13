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

`bot-js/` is the new discord.js runtime foundation. It is **not yet a full
replacement** for the Python bot. Its command coverage report is executable:

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

Currently implemented: **88 of 202 commands**. This includes dashboard, help,
support, the three account-age-gate configuration commands, all 28 staff/workforce
commands, role give/remove/temp, moderation panels and warning tools, reports,
reviews, channel archives/purges, logging configuration, activity/profile utilities,
developer commands, insights/case exports, configuration backups and owner-network
operations. The native HTTP API and shared bot-profile services are implemented
and tested independently; full dashboard action compatibility is still pending.
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

The current JavaScript suite has 45 passing tests. New checks cover moderation
role revocation on existing panels, role hierarchy, atomic report claims, failed
report delivery, inclusive large-ID purges, old-message deletion, authenticated
dashboard JSON operations, storage-full failures, profile rollback, spreadsheet
formula escaping and journaled backup recovery. CVMS work is paused until the
existing discord.js migration and safe production cutover are complete.

Remaining conversion work, in order:

1. Complete authenticated dashboard API compatibility and health reporting;
   expand guard and registration tests alongside each migrated module.
2. Moderation, reports, reviews and channel archives.
3. Tickets: existing persistent component IDs, forms, transcripts, claim/close/
   reopen/delete workflows, permission reconciliation, and timed deletion.
4. Quizzes, community pulses, activity checks, giveaways and automation recovery.
   One durable scheduler must own all dispatches; do not replay a missed backlog.
5. Security listeners, audit events, welcome/goodbye, invites, levels, autoroles,
   reaction roles and verification.
6. Premium analytics, backups, Network, Prettfy and developer tools, with the
   existing state formats preserved or explicitly migrated.
7. Compare all commands, arguments, choices, checks and persistent components to
   the old runtime, test recovery and failure cases, back up data, stop the old
   gateway/schedulers, and perform a single-instance cutover with rollback.

## Dashboard redesign brief

The supplied reference calls for a light interface with deep green Rallybit
branding, a compact public-site navigation, clean feature cards, and a fixed
dashboard sidebar. The dashboard overview should include a server selector,
search, member/moderation/ticket/message totals, an activity chart, module
switches, moderation events and recent configuration changes. Use consistent
line icons, clear borders and spacing, and responsive layouts across every page.
The screenshot domains are visual examples, not an instruction to change the
current production domain or OAuth redirect.
