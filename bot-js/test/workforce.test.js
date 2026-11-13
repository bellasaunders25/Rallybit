import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CommandRegistry } from "../src/registry.js";
import { JsonStore } from "../src/store.js";
import { registerWorkforce, activeSeconds } from "../src/modules/workforce.js";

async function fixture(run) {
  const directory = await mkdtemp(join(tmpdir(), "rallybit-staff-"));
  try {
    const store = new JsonStore(directory),
      registry = new CommandRegistry();
    let time = Date.parse("2026-11-13T09:00:00Z");
    registerWorkforce(registry, {
      store,
      now: () => time,
      audit: async () => {},
    });
    const interaction = (values = {}, userId = "123") => ({
      guildId: "guild",
      user: { id: userId, username: "Staff" },
      member: { displayName: "Staff" },
      guild: { channels: { fetch: async () => null } },
      options: Object.fromEntries(
        [
          "getString",
          "getInteger",
          "getBoolean",
          "getUser",
          "getChannel",
          "getRole",
        ].map((method) => [method, (name) => values[name] ?? null]),
      ),
    });
    await run({
      store,
      registry,
      interaction,
      advance: (ms) => {
        time += ms;
      },
    });
  } finally {
    await rm(directory, { recursive: true });
  }
}

test("free and Pro shift commands share one ledger; breaks are excluded exactly once", async () =>
  fixture(async ({ store, registry, interaction, advance }) => {
    await registry.handler("clockin")(interaction({ note: "Support shift" }));
    advance(3600000);
    await registry.handler("break start")(interaction());
    advance(900000);
    await registry.handler("break end")(interaction());
    advance(1800000);
    await registry.handler("staff clockout")(interaction());
    const row = (await store.read("staff_shifts.json")).guild["123"];
    assert.equal(row.active, null);
    assert.equal(row.history.length, 1);
    assert.equal(row.history[0].seconds, 5400);
    assert.equal(row.history[0].break_seconds, 900);
    assert.equal(row.history[0].note, "Support shift");
    assert.match(row.history[0].shift_id, /^SH-[0-9A-F]{8}$/);
  }));

test("two simultaneous clock-ins create only one active shift", async () =>
  fixture(async ({ store, registry, interaction }) => {
    const results = await Promise.allSettled([
      registry.handler("clockin")(interaction()),
      registry.handler("clockin")(interaction()),
    ]);
    assert.equal(results.filter((row) => row.status === "fulfilled").length, 1);
    assert.equal(
      (await store.read("staff_shifts.json")).guild["123"].history.length,
      0,
    );
  }));

test("clockout while on break and restart do not inflate worked time", async () =>
  fixture(async ({ store, registry, interaction, advance }) => {
    await registry.handler("clockin")(interaction());
    advance(600000);
    await registry.handler("break start")(interaction());
    advance(3600000);
    const disk = await new JsonStore(store.directory).read("staff_shifts.json");
    assert.equal(
      activeSeconds(
        disk.guild["123"].active,
        Date.parse("2026-11-13T10:10:00Z"),
      ),
      600,
    );
    await registry.handler("clockout")(interaction());
    assert.equal(
      (await store.read("staff_shifts.json")).guild["123"].history[0].seconds,
      600,
    );
  }));

test("shift clear requires confirmation and preserves an active shift by default", async () =>
  fixture(async ({ store, registry, interaction }) => {
    await store.update("staff_shifts.json", (data) => {
      data.guild = {
        123: {
          active: { started_at: "2026-11-13T09:00:00Z" },
          history: [{ shift_id: "SH-TEST", seconds: 60 }],
        },
      };
    });
    const values = {
      member: { id: "123" },
      reason: "Correct duplicate record",
    };
    await registry.handler("shifts clear")(interaction(values));
    assert.equal(
      (await store.read("staff_shifts.json")).guild["123"].history.length,
      1,
    );
    await registry.handler("shifts clear")(
      interaction({ ...values, confirm: true }),
    );
    let record = (await store.read("staff_shifts.json")).guild["123"];
    assert.equal(record.history.length, 0);
    assert.ok(record.active);
    await registry.handler("shifts clear")(
      interaction({ ...values, confirm: true, include_active: true }),
    );
    assert.equal(
      (await store.read("staff_shifts.json")).guild["123"],
      undefined,
    );
  }));

test("LOA rejects impossible calendar dates and cancellation of another member request", async () =>
  fixture(async ({ store, registry, interaction }) => {
    await assert.rejects(
      registry.handler("loa request")(
        interaction({
          duration_days: 2,
          reason: "Holiday",
          start_date: "2026-02-30",
        }),
      ),
      /valid start date/,
    );
    await registry.handler("loa request")(
      interaction({
        duration_days: 2,
        reason: "Holiday",
        start_date: "2026-11-20",
      }),
    );
    const row = Object.values(
      (await store.read("staff_requests.json")).guild,
    )[0];
    assert.equal(row.end_at, "2026-11-22T00:00:00.000Z");
    await assert.rejects(
      registry.handler("loa cancel")(
        interaction({ request_id: row.request_id }, "456"),
      ),
      /own request/,
    );
    assert.equal(
      Object.values((await store.read("staff_requests.json")).guild)[0].status,
      "Pending",
    );
    await registry.handler("loa setstatus")(
      interaction({
        request_id: row.request_id,
        status: "Approved",
        note: "Approved by HR",
      }),
    );
    assert.equal(
      Object.values((await store.read("staff_requests.json")).guild)[0].status,
      "Approved",
    );
    await registry.handler("loa cancel")(
      interaction({ request_id: row.request_id }),
    );
    assert.equal(
      Object.values((await store.read("staff_requests.json")).guild)[0].status,
      "Cancelled",
    );
  }));
