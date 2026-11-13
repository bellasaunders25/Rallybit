import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PermissionFlagsBits as P, PermissionsBitField } from "discord.js";
import { JsonStore } from "../src/store.js";
import { ModerationService, parseDuration } from "../src/modules/moderation.js";
import { ReportService } from "../src/modules/reports.js";
import {
  deleteMessages,
  rangeMessages,
  messageBlock,
} from "../src/modules/channels.js";

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), "rallybit-moderation-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const store = new JsonStore(directory);
  const guild = { id: "guild", ownerId: "owner", name: "Test server" };
  const role = (position) => ({
    position,
    comparePositionTo: (other) => position - other.position,
  });
  const member = (id, position, permissions = []) => ({
    id,
    guild,
    displayName: id,
    displayAvatarURL: () => "https://cdn.discordapp.com/embed/avatars/0.png",
    roles: { highest: role(position), cache: new Map() },
    permissions: new PermissionsBitField(permissions),
    send: async () => {},
  });
  const actor = member("actor", 5, [P.ManageMessages]),
    target = member("target", 2),
    bot = member("bot", 9);
  guild.members = {
    fetch: async ({ user }) => ({ actor, target, bot })[user],
    fetchMe: async () => bot,
  };
  const i = {
    id: "interaction",
    guildId: guild.id,
    guild,
    user: { id: "actor", username: "Moderator" },
  };
  return {
    store,
    guild,
    actor,
    target,
    bot,
    i,
    services: { store, audit: async () => {} },
  };
}
test("timeout duration parsing rejects invalid values and anything above 28 days", () => {
  assert.equal(parseDuration("2h"), 7200000);
  assert.equal(parseDuration("28d"), 28 * 86400000);
  for (const value of ["0s", "29d", "100w", "1e9m", "-5m", "abc"])
    assert.throws(() => parseDuration(value));
});
test("warnings retain legacy shape, deduplicate retries and remove atomically", async (t) => {
  const { i, store, services } = await fixture(t);
  const mod = new ModerationService(services);
  await mod.execute(i, "warn", "target", { reason: "Test" });
  await mod.execute(i, "warn", "target", { reason: "Retry" });
  const warnings = (await store.read("moderation_warnings.json")).guild.target;
  assert.equal(warnings.length, 1);
  assert.equal(warnings[0].moderator_id, "actor");
  await mod.execute({ ...i, id: "remove" }, "unwarn", "target", {
    warning_id: "LATEST",
  });
  assert.equal((await mod.warnings("guild", "target"))[0].active, false);
});
test("removed moderation role blocks an already-open panel", async (t) => {
  const { i, store, services } = await fixture(t);
  await store.update("moderation_permissions.json", (data) => {
    data.guild = { warn_role_ids: ["staff-role"] };
  });
  let shown = false;
  await assert.rejects(
    new ModerationService(services).component({
      ...i,
      customId: "mod:actor:target:warn",
      showModal: async () => {
        shown = true;
      },
    }),
    /access/,
  );
  assert.equal(shown, false);
});
test("moderation panel hides actions the opener cannot use", async (t) => {
  const { i, services } = await fixture(t);
  const panel = await new ModerationService(services).panel(i, "target");
  const labels = panel.components.flatMap((row) =>
    row.components.map((button) => button.data.label),
  );
  assert.ok(labels.includes("Warn"));
  assert.ok(
    !labels.includes("Kick") &&
      !labels.includes("Ban") &&
      !labels.includes("Timeout"),
  );
});
test("moderation hierarchy rejects equal roles before writing a warning", async (t) => {
  const { i, target, actor, services } = await fixture(t);
  target.roles.highest = actor.roles.highest;
  await assert.rejects(
    new ModerationService(services).execute(i, "warn", "target"),
    /equal or higher/,
  );
  assert.deepEqual(await services.store.read("moderation_warnings.json"), {});
});
test("two concurrent report claims cannot overwrite one another", async (t) => {
  const { i, services } = await fixture(t);
  await services.store.update("reports.json", (data) => {
    data.guild = { ABC: { status: "Pending", target_id: "target" } };
  });
  const make = (id) => ({
    ...i,
    user: { id },
    options: { getString: (key) => (key === "report_id" ? "ABC" : null) },
  });
  const reports = new ReportService(services);
  const results = await Promise.allSettled([
    reports.change(make("one"), "claim"),
    reports.change(make("two"), "claim"),
  ]);
  assert.equal(results.filter((row) => row.status === "fulfilled").length, 1);
  assert.ok(
    ["one", "two"].includes((await reports.records("guild")).ABC.claimed_by),
  );
});
test("failed report delivery preserves the report for staff", async (t) => {
  const { i, guild, services } = await fixture(t);
  await services.store.update("report_settings.json", (data) => {
    data.guild = { channel_id: "staff" };
  });
  guild.channels = {
    fetch: async () => ({
      isTextBased: () => true,
      send: async () => {
        throw new Error("No permission");
      },
    }),
  };
  i.options = {
    getUser: () => ({ id: "target", bot: false }),
    getString: (key) => (key === "reason" ? "A real report" : null),
  };
  const reports = new ReportService(services);
  await assert.rejects(reports.submit(i), /was saved, but delivery failed/);
  const rows = Object.values(await reports.records("guild"));
  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, "Delivery failed");
});
test("message ranges preserve large IDs and include both endpoints in either order", async () => {
  const ids = [
    "1515880619656220683",
    "1515880619656220684",
    "1515880619656220685",
  ];
  const rows = ids.map((id) => ({ id }));
  const channel = {
    messages: {
      fetch: async (arg) =>
        typeof arg === "string"
          ? rows.find((row) => row.id === arg)
          : new Map([[ids[1], rows[1]]]),
    },
  };
  const result = await rangeMessages(
    channel,
    ids[2],
    `https://discord.com/channels/123456789012345678/123456789012345679/${ids[0]}`,
  );
  assert.deepEqual(result.map((row) => row.id).sort(), ids);
  assert.equal((await rangeMessages(channel, ids[0], ids[0])).length, 1);
});
test("purges batch recent messages, individually delete old ones and never duplicate", async () => {
  const now = Date.now(),
    deleted = [],
    batches = [];
  const channel = {
    messages: {
      delete: async (id) => {
        deleted.push(id);
      },
    },
    bulkDelete: async (ids) => {
      batches.push(ids);
      return new Map(ids.map((id) => [id, {}]));
    },
  };
  const rows = [
    { id: "new-1", createdTimestamp: now },
    { id: "new-2", createdTimestamp: now },
    { id: "old", createdTimestamp: now - 20 * 86400000 },
  ];
  const result = await deleteMessages(channel, [...rows, rows[0]], "Test", now);
  assert.deepEqual(result, { deleted: 3, failed: 0 });
  assert.deepEqual(batches, [["new-1", "new-2"]]);
  assert.deepEqual(deleted, ["old"]);
});
test("a bulk-delete failure falls back to individual deletes; access loss stops immediately", async () => {
  const channel = {
    bulkDelete: async () => {
      throw Object.assign(new Error(), { code: 50034 });
    },
    messages: { delete: async () => {} },
  };
  const rows = [
    { id: "a", createdTimestamp: Date.now() },
    { id: "b", createdTimestamp: Date.now() },
  ];
  assert.equal((await deleteMessages(channel, rows, "Test")).deleted, 2);
  channel.messages.delete = async () => {
    throw Object.assign(new Error(), { code: 50013 });
  };
  await assert.rejects(deleteMessages(channel, rows, "Test"));
});
test("transcripts include identity, content, links, attachments and embed data", () => {
  const block = messageBlock({
    id: "123",
    createdTimestamp: 0,
    author: { id: "456", username: "Test" },
    content: "hello",
    url: "https://discord.com/test",
    attachments: new Map([
      [
        "x",
        { name: "test.png", size: 12, url: "https://example.com/test.png" },
      ],
    ]),
    embeds: [{ toJSON: () => ({ title: "Embed" }) }],
  });
  for (const item of [
    "456",
    "123",
    "hello",
    "test.png",
    "Embed",
    "https://discord.com/test",
  ])
    assert.ok(block.includes(item));
});
