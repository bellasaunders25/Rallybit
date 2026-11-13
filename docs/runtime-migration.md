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

Currently implemented: dashboard, help, support, and the three account-age-gate
configuration commands. Shared services provide a serialised atomic JSON store,
durable schedule reservations, command dispatch, service-notice embeds, reply
visibility, and errors. The live deployment stays on the corrected Python runtime
while feature parity is implemented and verified.

Remaining conversion work, in order:

1. Command registration schemas, full permission/entitlement guards, authenticated
   dashboard API compatibility, single-instance ownership and health reporting.
2. Moderation, manual and temporary roles, reports, reviews and channel archives.
3. Tickets: existing persistent component IDs, forms, transcripts, claim/close/
   reopen/delete workflows, permission reconciliation, and timed deletion.
4. Quizzes, community pulses, activity checks, giveaways and automation recovery.
   One durable scheduler must own all dispatches; do not replay a missed backlog.
5. Security listeners, audit events, welcome/goodbye, invites, levels, autoroles,
   reaction roles and verification.
6. Workforce, LOA/ROA, premium analytics, backups, Network, Prettfy and developer
   tools, with the existing state formats preserved or explicitly migrated.
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
