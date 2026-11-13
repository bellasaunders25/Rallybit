import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { LeaseStore } from "../src/lease.js";
import { JsonStore } from "../src/store.js";
import { DurableScheduler } from "../src/scheduler.js";

async function fixture(run) {
  const folder = await mkdtemp(join(tmpdir(), "rallybit-jobs-"));
  let now = Date.parse("2026-11-13T09:00:00Z");
  const clock = () => now;
  const a = new LeaseStore(join(folder, "lease.sqlite"), { now: clock });
  const b = new LeaseStore(join(folder, "lease.sqlite"), { now: clock });
  try {
    await run({
      folder,
      a,
      b,
      clock,
      advance: (duration) => {
        now += duration;
      },
    });
  } finally {
    a.close();
    b.close();
    await rm(folder, { recursive: true });
  }
}

test("expired owner cannot renew or release a replacement owner lease", () =>
  fixture(async ({ a, b, advance }) => {
    const original = a.acquire("runtime", 30000);
    assert.ok(original);
    assert.equal(b.acquire("runtime", 30000), null);
    advance(30001);
    const replacement = b.acquire("runtime", 30000);
    assert.ok(replacement);
    assert.equal(a.renew(original), false);
    a.release(original);
    assert.equal(b.held(replacement), true);
  }));

test("two schedulers with separate job keys share a quiz cooldown", () =>
  fixture(async ({ folder, a, b, clock }) => {
    const store = new JsonStore(folder);
    const first = new DurableScheduler({ store, leases: a, now: clock });
    const second = new DurableScheduler({ store, leases: b, now: clock });
    let sends = 0;
    const job = (key) => ({
      key,
      cooldownKey: "quiz:guild",
      intervalMs: 3600000,
      send: async (assertOwner) => {
        assertOwner();
        sends += 1;
      },
    });
    await Promise.all([
      first.dispatch(job("legacy:guild")),
      second.dispatch(job("automation:guild")),
    ]);
    assert.equal(sends, 1);
  }));

test("uncertain send and runtime restart cannot replay a missed backlog", () =>
  fixture(async ({ folder, a, b, clock, advance }) => {
    const first = new DurableScheduler({
      store: new JsonStore(folder),
      leases: a,
      now: clock,
    });
    const job = {
      key: "quiz:guild",
      intervalMs: 3600000,
      send: async () => {
        throw new Error("connection lost after send");
      },
    };
    await assert.rejects(first.dispatch(job), /connection lost/);
    const replacement = new DurableScheduler({
      store: new JsonStore(folder),
      leases: b,
      now: clock,
    });
    let sends = 0;
    const retry = {
      ...job,
      send: async () => {
        sends += 1;
      },
    };
    assert.equal((await replacement.dispatch(retry)).status, "not-due");
    advance(7 * 86400000);
    assert.equal((await replacement.dispatch(retry)).status, "sent");
    assert.equal((await replacement.dispatch(retry)).status, "not-due");
    assert.equal(sends, 1);
  }));

test("failed storage reservation prevents every external send", () =>
  fixture(async ({ a, clock }) => {
    let sent = false;
    const scheduler = new DurableScheduler({
      leases: a,
      now: clock,
      store: {
        update: async () => {
          const e = new Error("disk full");
          e.code = "ENOSPC";
          throw e;
        },
      },
    });
    await assert.rejects(
      scheduler.dispatch({
        key: "quiz:guild",
        intervalMs: 3600000,
        send: async () => {
          sent = true;
        },
      }),
      /disk full/,
    );
    assert.equal(sent, false);
  }));
