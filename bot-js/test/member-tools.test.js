import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PermissionsBitField, PermissionFlagsBits as P } from "discord.js";
import { JsonStore } from "../src/store.js";
import {
  MemberTools,
  levelFromXP,
  xpForLevel,
  emojiKey,
} from "../src/modules/member-tools.js";
import {
  WelcomeService,
  renderWelcome,
  inviteSource,
} from "../src/modules/welcome.js";
import { SnipeService } from "../src/modules/snipes.js";
const G = "123456789012345678",
  U = "123456789012345679";
async function setup(t) {
  const directory = await mkdtemp(join(tmpdir(), "rallybit-members-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return new JsonStore(directory);
}
test("level thresholds match legacy XP formula exactly", () => {
  for (const level of [0, 1, 2, 50, 1000])
    assert.equal(levelFromXP(xpForLevel(level)), level);
  assert.equal(levelFromXP(399), 1);
  assert.equal(levelFromXP(400), 2);
  assert.equal(levelFromXP(-1), 0);
});
test("reaction emoji keys remain compatible with stored Python mappings", () => {
  assert.equal(emojiKey(`<a:test:${U}>`), `id:${U}`);
  assert.equal(emojiKey(U), `id:${U}`);
  assert.equal(emojiKey({ id: U, name: "test" }), `id:${U}`);
  assert.equal(emojiKey("✅"), "unicode:✅");
});
test("simultaneous messages award XP only once per persisted cooldown, including restart", async (t) => {
  const store = await setup(t);
  await store.write("level_settings.json", {
    [G]: { enabled: true, xp_min: 15, xp_max: 15, cooldown_seconds: 60 },
  });
  const guild = {
      id: G,
      members: {
        fetchMe: async () => ({ permissions: new PermissionsBitField() }),
      },
    },
    member = { id: U, displayName: "Test", roles: { cache: new Map() } };
  member.guild = guild;
  const message = {
    guild,
    guildId: G,
    channelId: "channel",
    author: { id: U, bot: false },
    member,
  };
  const service = new MemberTools({ store });
  await Promise.all([service.onMessage(message), service.onMessage(message)]);
  await new MemberTools({ store: new JsonStore(store.directory) }).onMessage(
    message,
  );
  const record = (await store.read("level_stats.json"))[G][U];
  assert.equal(record.xp, 15);
  assert.equal(record.messages, 1);
});
test("verification disabled state blocks existing buttons before any role grant", async (t) => {
  const store = await setup(t);
  await store.write("verification_settings.json", { [G]: { enabled: false } });
  await assert.rejects(
    new MemberTools({ store }).verify({
      guildId: G,
      customId: `rallybit:verify:${G}`,
      deferReply: async () => {},
    }),
    /disabled/,
  );
});
test("welcome/goodbye delivery markers survive restarts and do not suppress the other event type", async (t) => {
  const store = await setup(t),
    first = new WelcomeService({ store });
  const results = await Promise.all(
    Array.from({ length: 10 }, () => first.claim("join", G, U)),
  );
  assert.equal(results.filter(Boolean).length, 1);
  assert.equal(await new WelcomeService({ store }).claim("join", G, U), false);
  assert.equal(await first.claim("leave", G, U), true);
});
test("bot joins are identified as OAuth and do not fetch or credit normal invites", async () => {
  const service = new WelcomeService({}),
    member = { user: { bot: true }, guild: {} };
  assert.equal((await service.detect(member)).kind, "oauth");
  assert.ok(inviteSource({ kind: "oauth" }).includes("OAuth"));
});
test("vanity and OAuth placeholders render without claiming a human inviter", () => {
  const member = {
    id: U,
    displayName: "Member",
    guild: { name: "Server", memberCount: 12 },
    user: { username: "member", createdTimestamp: Date.now() },
  };
  const template = "{user} | {inviter} | {invite_type} | {invite_source}";
  assert.ok(
    renderWelcome(template, member, {
      kind: "vanity",
      code: "rallybit",
    }).includes("the server vanity invite"),
  );
  assert.ok(
    renderWelcome(template, member, { kind: "oauth" }).includes("OAuth"),
  );
  assert.ok(
    !renderWelcome(template, member, { kind: "unknown" }).includes(
      "Invite from <@",
    ),
  );
});
test("invite tracking does not attribute historical uses after a cache miss", async () => {
  const service = new WelcomeService({});
  const guild = {
    id: G,
    invites: {
      fetch: async () =>
        new Map([["old", { code: "old", uses: 500, inviter: { id: U } }]]),
    },
    fetchVanityData: async () => ({ code: null }),
  };
  assert.equal(
    (await service.detect({ guild, user: { bot: false } })).kind,
    "unknown",
  );
});
test("snipe caches are bounded, expire and skip bot messages", () => {
  const service = new SnipeService(),
    base = {
      guild: {},
      channelId: "channel",
      author: {
        id: U,
        username: "Member",
        displayAvatarURL: () => "https://example.com/avatar.png",
      },
      content: "Message",
    };
  for (let n = 0; n < 30; n++) service.record({ ...base, content: String(n) });
  assert.equal(service.deleted.get("channel").length, 10);
  service.record({
    ...base,
    channelId: "bot-channel",
    author: { ...base.author, bot: true },
  });
  assert.equal(service.deleted.has("bot-channel"), false);
  for (const row of service.deleted.get("channel")) row.when = 0;
  service.prune();
  assert.equal(service.deleted.has("channel"), false);
});
