import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  AuditLogEvent,
  PermissionFlagsBits as P,
  PermissionsBitField,
} from "discord.js";
import { JsonStore } from "../src/store.js";
import {
  SecurityService,
  securitySettings,
  reconcileSecurityWrite,
} from "../src/modules/security-engine.js";
import { createApp } from "../src/app.js";
const G = "123456789012345678",
  U = "123456789012345679";
async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), "rallybit-security-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const store = new JsonStore(directory);
  const guild = { id: G, ownerId: "owner", client: { user: { id: "bot" } } };
  return {
    store,
    guild,
    service: new SecurityService({ store, audit: async () => {} }),
  };
}
test("security defaults are disabled per module and preserve legacy agegate configuration", () => {
  assert.ok(
    Object.values(securitySettings().modules).every(
      (row) => row.enabled === false,
    ),
  );
  assert.equal(
    securitySettings({ agegate: { enabled: true, minimum_days: 30 } }).modules
      .age_gate.min_days,
    30,
  );
  assert.equal(
    securitySettings({
      agegate: { enabled: true },
      modules: { age_gate: { enabled: false } },
    }).modules.age_gate.enabled,
    false,
  );
});
test("dashboard edits reconcile legacy fields into canonical settings without reviving disabled modules", () => {
  const before = {
    [G]: {
      agegate: { enabled: true, minimum_days: 7, dm_member: true },
      modules: { age_gate: { enabled: true, min_days: 7, dm_member: true } },
    },
  };
  const changed = structuredClone(before);
  changed[G].agegate.enabled = false;
  assert.equal(
    reconcileSecurityWrite(changed, before)[G].modules.age_gate.enabled,
    false,
  );
  const canonical = structuredClone(before);
  canonical[G].modules.age_gate.enabled = false;
  assert.equal(
    reconcileSecurityWrite(canonical, before)[G].agegate.enabled,
    false,
  );
});
test("slash agegate disable updates both dashboard and runtime representations", async (t) => {
  const { store } = await fixture(t);
  await store.write("security_settings.json", {
    [G]: {
      agegate: { enabled: true },
      modules: { age_gate: { enabled: true } },
    },
  });
  const app = createApp({ dataDirectory: store.directory }),
    i = {
      guildId: G,
      inGuild: () => true,
      memberPermissions: new PermissionsBitField([P.ManageGuild]),
    };
  await app.registry.handler("security-agegate disable")(i);
  const saved = (await store.read("security_settings.json"))[G];
  assert.equal(saved.modules.age_gate.enabled, false);
  assert.equal(saved.agegate.enabled, false);
});
test("global security disable stops join and message enforcement immediately", async (t) => {
  const { store, guild, service } = await fixture(t);
  await store.write("security_settings.json", {
    [G]: {
      enabled: false,
      modules: {
        age_gate: { enabled: true, min_days: 365 },
        scam_trap: { enabled: true, channel_id: "trap" },
      },
    },
  });
  let acted = false;
  service.punish = async () => {
    acted = true;
  };
  assert.equal(
    await service.onJoin({
      id: U,
      guild,
      user: { id: U, bot: false, createdTimestamp: Date.now() },
    }),
    true,
  );
  assert.equal(
    await service.onMessage({
      guild,
      guildId: G,
      channelId: "trap",
      member: {},
      author: { id: U, bot: false },
    }),
    true,
  );
  assert.equal(acted, false);
});
test("disabled agegate does not kick a young account while other security remains enabled", async (t) => {
  const { store, guild, service } = await fixture(t);
  await store.write("security_settings.json", {
    [G]: {
      enabled: true,
      modules: { age_gate: { enabled: false, min_days: 365 } },
    },
  });
  let acted = false;
  service.punish = async () => {
    acted = true;
  };
  await service.onJoin({
    id: U,
    guild,
    user: { id: U, bot: false, createdTimestamp: Date.now() },
  });
  assert.equal(acted, false);
});
test("audit thresholds deduplicate repeated gateway events and punish only after the configured count", async (t) => {
  const { store, guild, service } = await fixture(t);
  await store.write("security_settings.json", {
    [G]: {
      modules: {
        mass_ban: { enabled: true, count: 3, seconds: 60, punishment: "strip" },
      },
    },
  });
  service.trusted = async () => false;
  let punishments = 0;
  service.punish = async () => {
    punishments++;
  };
  const entry = (id) => ({
    id,
    action: AuditLogEvent.MemberBanAdd,
    executorId: U,
    executor: { id: U },
    createdTimestamp: Date.now(),
  });
  await service.onAudit(entry("one"), guild);
  await service.onAudit(entry("one"), guild);
  await service.onAudit(entry("two"), guild);
  assert.equal(punishments, 0);
  await service.onAudit(entry("three"), guild);
  assert.equal(punishments, 1);
});
test("an old audit-log entry cannot trigger fresh punishment after restart", async (t) => {
  const { store, guild, service } = await fixture(t);
  await store.write("security_settings.json", {
    [G]: { modules: { mass_ban: { enabled: true, count: 1 } } },
  });
  let acted = false;
  service.punish = async () => {
    acted = true;
  };
  await service.onAudit(
    {
      id: "old",
      action: AuditLogEvent.MemberBanAdd,
      executorId: U,
      createdTimestamp: Date.now() - 3600000,
    },
    guild,
  );
  assert.equal(acted, false);
});
test("lockdown restores only controlled bits and preserves unrelated permission changes", async (t) => {
  const { store, guild, service } = await fixture(t);
  const role = {
    permissions: new PermissionsBitField([
      P.SendMessages,
      P.Connect,
      P.ViewChannel,
    ]),
    setPermissions: async (bits) => {
      role.permissions = new PermissionsBitField(bits);
    },
  };
  guild.roles = { fetch: async () => role };
  service.record = async () => {};
  await service.lockdown(guild, true, { id: U });
  assert.equal(role.permissions.has(P.SendMessages), false);
  role.permissions = new PermissionsBitField(
    role.permissions.bitfield | P.UseExternalEmojis,
  );
  await service.lockdown(guild, false, { id: U });
  assert.equal(role.permissions.has(P.SendMessages), true);
  assert.equal(role.permissions.has(P.UseExternalEmojis), true);
  assert.equal((await store.read("security_lockdown.json"))[G], undefined);
});
test("purge recovery unbans only the bot’s own matching temporary purge ban", async (t) => {
  const { store, guild, service } = await fixture(t);
  await store.write("security_purge_jobs.json", {
    own: {
      guild_id: G,
      target_id: U,
      status: "pending",
      reason: "Rallybit purge own",
    },
    other: {
      guild_id: G,
      target_id: "different",
      status: "pending",
      reason: "Rallybit purge other",
    },
  });
  const unbanned = [];
  guild.bans = {
    fetch: async (id) => ({
      reason: id === U ? "Rallybit purge own" : "Independent moderator ban",
    }),
    remove: async (id) => {
      unbanned.push(id);
    },
  };
  await service.tick({ guilds: { cache: new Map([[G, guild]]) } });
  assert.deepEqual(unbanned, [U]);
  assert.equal(
    (await store.read("security_purge_jobs.json")).other.status,
    "needs_review",
  );
});
