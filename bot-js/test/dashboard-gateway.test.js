import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  ChannelType,
  Events,
  PermissionsBitField,
  PermissionFlagsBits as P,
} from "discord.js";
import { createApp } from "../src/app.js";
import { DASHBOARD_ACTIONS, syncGuildCache } from "../src/api.js";
import {
  createDashboardActions,
  dashboardEmbed,
  embedPayload,
  ticketPanelPayload,
  asBoolean,
} from "../src/dashboard-actions.js";
import { startRuntime, validateRuntimeConfig } from "../src/startup.js";
import { bindGateway, GATEWAY_OPTIONS, maintenance } from "../src/gateway.js";
import { applyConfiguredPresence } from "../src/profile.js";
import { LoggingService } from "../src/modules/logging.js";
async function temporary(t) {
  const directory = await mkdtemp(join(tmpdir(), "rallybit-gateway-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return createApp({ dataDirectory: directory });
}
const G = "123456789012345678",
  U = "123456789012345679",
  C = "123456789012345680",
  M = "123456789012345681";
test("custom presence templates rotate while retaining the selected status", async (t) => {
  const app = await temporary(t),
    fx = fixture();
  let presence;
  fx.client.user.setPresence = (value) => {
    presence = value;
  };
  await app.store.write("bot_settings.json", {
    global: {
      presence_status: "dnd",
      presences: [
        { text: "{servers} servers", type: "watching" },
        { text: "{members} members", type: "listening" },
      ],
    },
  });
  await applyConfiguredPresence(app.store, fx.client, { advance: true });
  assert.equal(presence.status, "dnd");
  assert.equal(presence.activities[0].name, "1 servers");
  await applyConfiguredPresence(app.store, fx.client, { advance: true });
  assert.equal(presence.activities[0].name, "12 members");
});
test("security event logging keeps its own destination without double sending", async (t) => {
  const app = await temporary(t),
    fx = fixture();
  let extra,
    calls = 0;
  const logging = new LoggingService({
    store: app.store,
    audit: async (i, type, title, description, target, destination) => {
      calls++;
      extra = destination;
    },
  });
  await app.store.write("security_settings.json", {
    [G]: {
      enabled: true,
      log_channel_id: C,
      event_logging: { message_events: true },
    },
  });
  await logging.emit(fx.guild, "messages", "Message deleted", "Content", {
    target: U,
  });
  assert.equal(calls, 1);
  assert.equal(extra, C);
  assert.equal(
    (await app.store.read("security_history.json"))[G][0].module,
    "event_log",
  );
  await app.store.update("security_settings.json", (data) => {
    data[G].event_logging.message_events = false;
  });
  await logging.emit(fx.guild, "messages", "Message deleted", "Content", {
    target: U,
  });
  assert.equal(extra, null);
  assert.equal((await app.store.read("security_history.json"))[G].length, 1);
});
function fixture() {
  let edits = 0;
  const actor = {
    id: U,
    user: { id: U, username: "Moderator" },
    permissions: new PermissionsBitField(P.Administrator),
    roles: { cache: new Map(), highest: { position: 50 } },
  };
  const client = {
    user: { id: "bot" },
    guilds: { cache: new Map() },
    options: { shardCount: 1 },
    ws: { shards: new Map([[0, { status: 0, ping: 25 }]]), ping: 25 },
    uptime: 5000,
  };
  const guild = {
    id: G,
    client,
    ownerId: U,
    name: "Guild",
    memberCount: 12,
    shardId: 0,
    iconURL: () => null,
    members: { fetch: async () => actor, fetchMe: async () => actor },
  };
  actor.guild = guild;
  const channel = {
    id: C,
    name: "support",
    type: ChannelType.GuildText,
    guild,
    permissionsFor: (member) => member.permissions,
  };
  const message = {
    id: M,
    channelId: C,
    channel,
    author: client.user,
    content: "Hello",
    url: "https://discord.com/channels/" + G + "/" + C + "/" + M,
    embeds: [dashboardEmbed({ title: "Old" })],
    edit: async (payload) => {
      edits++;
      Object.assign(message, payload);
      return message;
    },
  };
  channel.messages = { fetch: async () => message };
  channel.send = async (payload) => {
    Object.assign(message, payload);
    return message;
  };
  guild.channels = {
    fetch: async (id) => (id ? channel : new Map([[C, channel]])),
  };
  guild.roles = { fetch: async () => null };
  client.guilds.cache.set(G, guild);
  return {
    actor,
    client,
    guild,
    channel,
    message,
    get edits() {
      return edits;
    },
  };
}
test("all 26 dashboard operations are real handlers and reject a roleless actor", async (t) => {
  const app = await temporary(t),
    actions = createDashboardActions(app),
    fx = fixture();
  fx.actor.permissions = new PermissionsBitField();
  assert.equal(actions.size, 26);
  assert.deepEqual([...actions.keys()].sort(), [...DASHBOARD_ACTIONS].sort());
  for (const name of DASHBOARD_ACTIONS) {
    if (["reactionrole.add", "level.setxp"].includes(name)) continue;
    await assert.rejects(
      actions.get(name)({
        guild: fx.guild,
        actor: fx.actor,
        params: { channel_id: C },
      }),
    );
  }
  assert.equal(fx.edits, 0);
});
test("embed editor loads existing bot messages and preserves other embeds on update", async (t) => {
  const app = await temporary(t),
    actions = createDashboardActions(app),
    fx = fixture();
  fx.message.embeds.push(dashboardEmbed({ title: "Keep me" }));
  const result = await actions.get("embed.load")({
    guild: fx.guild,
    actor: fx.actor,
    params: { channel_id: C, message_id: M },
  });
  assert.equal(result.embed.title, "Old");
  const updated = await actions.get("embed.update")({
    guild: fx.guild,
    actor: fx.actor,
    params: {
      channel_id: C,
      message_id: M,
      embed_index: 1,
      title: "New",
      show_timestamp: true,
    },
  });
  assert.equal(updated.embed.title, "New");
  assert.equal(
    fx.message.embeds[1].title ?? fx.message.embeds[1].data.title,
    "Keep me",
  );
  assert.equal(updated.embed.show_timestamp, true);
  assert.equal(fx.edits, 1);
});
test("embed editing rejects another bot’s messages and revoked channel access", async (t) => {
  const app = await temporary(t),
    actions = createDashboardActions(app),
    fx = fixture();
  fx.message.author = { id: "someone-else" };
  await assert.rejects(
    actions.get("embed.update")({
      guild: fx.guild,
      actor: fx.actor,
      params: { channel_id: C, message_id: M, title: "New" },
    }),
    /only edit/,
  );
  fx.channel.permissionsFor = () => new PermissionsBitField();
  await assert.rejects(
    actions.get("embed.load")({
      guild: fx.guild,
      actor: fx.actor,
      params: { channel_id: C, message_id: M },
    }),
    /permissions/,
  );
  assert.equal(fx.edits, 0);
});
test("embed payload validates aggregate limits, media URLs, fields and booleans", () => {
  assert.throws(
    () =>
      dashboardEmbed({ title: "Test", image_url: "http://example.com/a.png" }),
    /HTTPS/,
  );
  assert.throws(
    () =>
      dashboardEmbed({
        title: "Test",
        fields: Array.from({ length: 26 }, () => ({ name: "A", value: "B" })),
      }),
    /25/,
  );
  assert.throws(
    () =>
      dashboardEmbed({
        description: "x".repeat(4096),
        fields: [
          { name: "N", value: "a".repeat(1024) },
          { name: "N", value: "b".repeat(1024) },
        ],
      }),
    /6,000/,
  );
  assert.equal(asBoolean("false"), false);
  assert.equal(asBoolean("0"), false);
  assert.equal(asBoolean(true), true);
});
test("ticket load maps legacy roles into the dashboard selector without losing disabled options", () => {
  const result = ticketPanelPayload("PANEL", {
    options: [
      {
        option_id: "ONE",
        name: "Staff",
        category_id: C,
        support_role_ids: [U],
        enabled: false,
      },
    ],
  });
  assert.equal(result.options[0].support_role_id, U);
  assert.equal(result.options[0].enabled, false);
});
test("ticket dashboard refuses a mismatched message before saving or changing permissions", async (t) => {
  const app = await temporary(t),
    fx = fixture(),
    actions = createDashboardActions(app);
  await app.store.write("ticket_panels.json", {
    [G]: {
      PANEL: {
        message_id: M,
        channel_id: C,
        options: [
          {
            option_id: "ONE",
            name: "Staff",
            category_id: C,
            support_role_ids: [U],
          },
        ],
      },
    },
  });
  await assert.rejects(
    actions.get("ticket.panel.update")({
      guild: fx.guild,
      actor: fx.actor,
      params: { panel_id: "PANEL", message_id: U, options: [] },
    }),
    /not a saved/,
  );
  assert.equal(fx.edits, 0);
});
test("guild cache preserves the PHP shard dictionary and ONLINE status schema", async (t) => {
  const app = await temporary(t),
    fx = fixture();
  await syncGuildCache(fx.client, app.store);
  const data = await app.store.read("bot_guilds.json");
  assert.equal(data.__SYSTEM__.shards["0"].status, "ONLINE");
  assert.equal(data.__SYSTEM__.shards["0"].guild_count, 1);
  assert.equal(data[G].member_count, 12);
});
test("gateway uses one ordered join pipeline and does not welcome security-rejected members", async () => {
  const client = new EventEmitter(),
    calls = [],
    tasks = [];
  const app = {
    store: { write: async () => {} },
    logging: { bind: () => {} },
    security: {
      bind: () => {},
      onJoin: async () => {
        calls.push("security");
        return false;
      },
    },
    prettfy: {},
    members: { onJoin: async () => calls.push("roles") },
    welcome: { onJoin: async () => calls.push("welcome") },
  };
  bindGateway(app, client, {
    track: (p) => {
      tasks.push(p);
      return p;
    },
  });
  client.emit(Events.GuildMemberAdd, {});
  await Promise.all(tasks);
  assert.deepEqual(calls, ["security"]);
  assert.equal(client.listenerCount(Events.GuildMemberAdd), 1);
  assert.throws(() => bindGateway(app, client), /already/);
});
test("maintenance failures are isolated so one denied ticket does not stop role expiry or schedules", async () => {
  const calls = [],
    app = {
      roles: { tick: async () => calls.push("roles") },
      tickets: {
        tick: async () => {
          calls.push("tickets");
          throw new Error();
        },
      },
      security: { tick: async () => calls.push("security") },
      campaigns: { tick: async () => calls.push("campaigns") },
      prettfy: { cleanup: async () => {} },
      store: { write: async () => {} },
    };
  await maintenance(app, { guilds: { cache: new Map() }, ws: {}, options: {} });
  assert.deepEqual(calls, ["roles", "tickets", "security", "campaigns"]);
});
test("startup refuses unapproved cutover, weak secrets and invalid shard configuration", () => {
  assert.throws(() => validateRuntimeConfig({}), /cutover/);
  assert.throws(
    () =>
      validateRuntimeConfig({
        RALLYBIT_JS_CUTOVER_APPROVED: "verified",
        DISCORD_BOT_TOKEN: "test",
        BOT_API_SECRET: "short",
      }),
    /16/,
  );
  assert.throws(
    () =>
      validateRuntimeConfig({
        RALLYBIT_JS_CUTOVER_APPROVED: "verified",
        DISCORD_BOT_TOKEN: "test",
        BOT_API_SECRET: "test-key-long-enough",
        SHARD_COUNT: "bad",
      }),
    /SHARD_COUNT/,
  );
  assert.ok(GATEWAY_OPTIONS.partials.length >= 3);
});
test("full startup connects dashboard, registration and recovery without a real Discord login", async (t) => {
  const app = await temporary(t),
    client = new EventEmitter();
  let connected = false,
    registrations = 0;
  Object.assign(client, {
    options: { shardCount: 1 },
    guilds: { cache: new Map() },
    ws: { ping: 20, shards: new Map() },
    uptime: 1000,
    isReady: () => connected,
    user: {
      id: "bot",
      username: "Rallybit",
      avatar: null,
      setPresence: () => {},
    },
    application: {
      commands: {
        set: async (commands) => {
          registrations++;
          assert.equal(commands.length, 77);
        },
      },
    },
    destroy: async () => {
      connected = false;
    },
    login: async () => {
      connected = true;
      client.emit(Events.ClientReady);
    },
  });
  const env = {
    RALLYBIT_JS_CUTOVER_APPROVED: "verified",
    DISCORD_BOT_TOKEN: "not-a-real-token",
    BOT_API_SECRET: "test-key-long-enough",
    API_PORT: "0",
    API_HOST: "127.0.0.1",
    RALLYBIT_DATA_DIR: app.store.directory,
  };
  const runtime = await startRuntime({ env, client });
  try {
    for (let n = 0; n < 100 && !runtime.isReady(); n++)
      await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(runtime.isReady(), true);
    assert.equal(registrations, 1);
    const health = await fetch(
      `http://127.0.0.1:${runtime.api.address().port}/api/health`,
    );
    assert.equal(health.status, 200);
    assert.equal((await health.json()).engine, "Rallybit discord.js");
    await assert.rejects(
      startRuntime({ env, client: new EventEmitter() }),
      /Another Rallybit/,
    );
  } finally {
    await runtime.stop();
  }
});
