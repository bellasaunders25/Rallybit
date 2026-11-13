import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, readFile, access } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  ChannelType,
  PermissionsBitField,
  PermissionFlagsBits as P,
} from "discord.js";
import { JsonStore } from "../src/store.js";
import {
  PrettfyService,
  normalizeName,
  validateNamingRows,
  previewHTML,
  requestNamingBatch,
} from "../src/modules/prettfy.js";
import {
  EventService,
  EVENT_FILES,
  eventPayload,
  epoch,
} from "../src/modules/events.js";
import { CampaignService } from "../src/modules/campaigns.js";
import { LeaseStore } from "../src/lease.js";
const G = "123456789012345678",
  U = "123456789012345679",
  C = "123456789012345680";
async function setup(t) {
  const dir = await mkdtemp(join(tmpdir(), "rallybit-events-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  return new JsonStore(dir);
}
function fixtures() {
  let sent = 0;
  const messages = new Map(),
    client = { user: { id: "bot" }, guilds: { cache: new Map() } };
  const bot = {
    permissions: new PermissionsBitField(P.Administrator),
    roles: { highest: { position: 50 } },
  };
  const guild = {
    id: G,
    name: "Server",
    client,
    ownerId: U,
    members: {
      fetchMe: async () => bot,
      fetch: async ({ user }) => ({
        id: user,
        user: { id: user, bot: false, username: "User" },
        displayName: "User",
        permissions: new PermissionsBitField(P.Administrator),
        roles: { cache: new Map() },
      }),
    },
    roles: { fetch: async () => null },
  };
  const channel = {
    id: C,
    guild,
    permissionsFor: () => bot.permissions,
    messages: {
      fetch: async (value) =>
        typeof value === "object" ? messages : messages.get(value),
    },
    send: async (payload) => {
      const message = {
        id: `message${++sent}`,
        author: client.user,
        embeds: payload.embeds.map((e) => e.toJSON()),
        edit: async () => {},
        react: async () => {},
      };
      messages.set(message.id, message);
      return message;
    },
  };
  guild.channels = {
    fetch: async (id) => (id ? channel : new Map([[C, channel]])),
  };
  client.guilds.cache.set(G, guild);
  return {
    guild,
    client,
    channel,
    get sent() {
      return sent;
    },
  };
}
test("channel names use supported brackets, lowercase and hyphens, except voice", () => {
  assert.equal(
    normalizeName("【📢】 News & Updates", { type: ChannelType.GuildForum }),
    "『📢』-news-updates",
  );
  assert.equal(
    normalizeName("General Voice", { type: ChannelType.GuildVoice }),
    "General Voice",
  );
  assert.equal(
    normalizeName("ＣＯＭＭＵＮＩＴＹ", { type: ChannelType.GuildMedia }),
    "community",
  );
  assert.throws(() => normalizeName(" "), /empty/);
});
test("AI output rejects unknown IDs and duplicates without executing anything", () => {
  const inventory = [{ id: C, name: "general" }];
  assert.throws(
    () =>
      validateNamingRows(
        { items: [{ id: U, new_name: "test" }] },
        inventory,
        "channels",
      ),
    /unknown/,
  );
  assert.throws(
    () =>
      validateNamingRows(
        {
          items: [
            { id: C, new_name: "a" },
            { id: C, new_name: "b" },
          ],
        },
        inventory,
        "channels",
      ),
    /duplicate/,
  );
  assert.deepEqual(
    validateNamingRows(
      '```json\n{"items":[{"id":"' + C + '","new_name":"General Chat"}]}\n```',
      inventory,
      "channels",
    ),
    [{ id: C, new_name: "general-chat" }],
  );
});
test("OpenRouter transport uses the configured free model and does not include credentials in prompts", async () => {
  const previous = process.env.OPENROUTER_API_KEY,
    model = process.env.OPENROUTER_MODEL;
  process.env.OPENROUTER_API_KEY = "test-secret";
  process.env.OPENROUTER_MODEL = "openrouter/free";
  try {
    const result = await requestNamingBatch(
      [{ id: C, name: "general" }],
      { brief: "clean" },
      "channels",
      {
        fetch: async (url, options) => {
          assert.equal(
            String(url),
            "https://openrouter.ai/api/v1/chat/completions",
          );
          const data = JSON.parse(options.body);
          assert.equal(data.model, "openrouter/free");
          assert.ok(!JSON.stringify(data.messages).includes("test-secret"));
          return new Response(
            JSON.stringify({
              id: "test",
              created: 1700000000,
              model: "openrouter/free",
              choices: [
                {
                  index: 0,
                  message: { role: "assistant", content: '{"items":[]}' },
                  finish_reason: "stop",
                },
              ],
              usage: {
                prompt_tokens: 10,
                completion_tokens: 5,
                total_tokens: 15,
              },
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        },
      },
    );
    assert.equal(result, '{"items":[]}');
    process.env.OPENROUTER_MODEL = "paid/model";
    await assert.rejects(
      requestNamingBatch([], {}, "channels"),
      /free OpenRouter/,
    );
  } finally {
    if (previous === undefined) delete process.env.OPENROUTER_API_KEY;
    else process.env.OPENROUTER_API_KEY = previous;
    if (model === undefined) delete process.env.OPENROUTER_MODEL;
    else process.env.OPENROUTER_MODEL = model;
  }
});
test("incomplete model responses retry only missing IDs and preserve complete coverage", async (t) => {
  const store = await setup(t),
    calls = [];
  const service = new PrettfyService(
    { store },
    {
      request: async (rows) => {
        calls.push(rows.map((r) => r.id));
        return { items: [{ id: rows[0].id, new_name: "new" }] };
      },
    },
  );
  const result = await service.plan(
    [
      { id: C, name: "one" },
      { id: U, name: "two" },
    ],
    {},
    "channels",
  );
  assert.equal(result.length, 2);
  assert.deepEqual(calls, [[C, U], [U]]);
});
test("incomplete plan never applies partial changes after bounded retries", async (t) => {
  const store = await setup(t);
  let calls = 0;
  const service = new PrettfyService(
    { store },
    {
      request: async () => {
        calls++;
        return { items: [] };
      },
    },
  );
  await assert.rejects(
    service.plan([{ id: C, name: "one" }], {}, "channels"),
    /incomplete/,
  );
  assert.equal(calls, 3);
});
test("HTML review escapes names, includes unchanged rows and is removable", async (t) => {
  const store = await setup(t),
    service = new PrettfyService({ store }),
    inventory = [{ id: C, name: "<script>alert(1)</script>", kind: "forum" }];
  const html = previewHTML('" onload="evil', inventory, [], "channels");
  assert.ok(!html.includes("<script>"));
  assert.ok(html.includes("&lt;script&gt;"));
  assert.ok(html.includes("Unchanged"));
  const preview = await service.preview("Server", inventory, [], "channels");
  assert.match(preview.url, /token=[a-f0-9]{32}$/);
  assert.match(await readFile(preview.path, "utf8"), /forum/);
  await rm(preview.path);
  await assert.rejects(access(preview.path));
});
test("rename intent is durably saved before Discord mutation and includes forums", async (t) => {
  const store = await setup(t),
    fx = fixtures();
  const member = await fx.guild.members.fetch({ user: U });
  member.roles.highest = {};
  fx.guild.members.fetch = async () => member;
  const target = {
    id: C,
    name: "old",
    type: ChannelType.GuildForum,
    setName: async (name) => {
      const snapshot = (await store.read("prettfy_history.json"))[G][0]
        .channels[0];
      assert.equal(snapshot.name, "old");
      assert.equal(snapshot.applied_name, name);
      target.name = name;
    },
  };
  fx.guild.channels.fetch = async (id) =>
    id ? target : new Map([[C, target]]);
  const service = new PrettfyService({ store });
  await store.write("prettfy_history.json", {
    [G]: [{ id: "snapshot", status: "active", channels: [], roles: [] }],
  });
  assert.equal(
    (await service.inventory(fx.guild, "channels", false, U))[0].type,
    ChannelType.GuildForum,
  );
  await service.apply(
    fx.guild,
    U,
    [{ id: C, name: "old" }],
    [{ id: C, new_name: "new" }],
    "channels",
    "snapshot",
  );
  assert.equal(target.name, "new");
});
test("undo skips a later human rename instead of overwriting it", async (t) => {
  const store = await setup(t),
    fx = fixtures();
  let changed = false;
  await store.write("prettfy_history.json", {
    [G]: [
      {
        id: "history",
        status: "active",
        created_at: "date",
        channels: [{ id: C, name: "old", applied_name: "bot-name" }],
        roles: [],
      },
    ],
  });
  fx.guild.channels.fetch = async () => ({
    id: C,
    name: "human-name",
    setName: async () => {
      changed = true;
    },
  });
  const service = new PrettfyService({ store });
  service.ask = async () => "UNDO";
  await service.undo(fx.guild, { id: U, send: async () => {} });
  assert.equal(changed, false);
});
test("legacy Python undo snapshots get an explicit HTML restoration review", async (t) => {
  const store = await setup(t),
    fx = fixtures();
  let renamed, question;
  await store.write("prettfy_history.json", {
    [G]: [
      {
        id: "history",
        status: "active",
        created_at: "date",
        channels: [{ id: C, name: "old" }],
        roles: [],
      },
    ],
  });
  fx.guild.channels.fetch = async () => ({
    id: C,
    name: "current",
    setName: async (name) => {
      renamed = name;
    },
  });
  const service = new PrettfyService({ store });
  service.ask = async (user, prompt) => {
    question = prompt;
    return "UNDO";
  };
  await service.undo(fx.guild, { id: U, send: async () => {} });
  assert.match(question, /Legacy snapshot review:/);
  assert.equal(renamed, "old");
});
test("uncertain automatic quiz send is never replayed, including after restart", async (t) => {
  const store = await setup(t),
    fx = fixtures();
  let attempts = 0;
  fx.channel.send = async () => {
    attempts++;
    throw new Error("network lost after send");
  };
  await assert.rejects(
    new EventService({ store }).start(
      "quiz",
      fx.guild,
      fx.channel,
      { id: U },
      { automatic: true },
    ),
    /not be confirmed/,
  );
  await assert.rejects(
    new EventService({ store: new JsonStore(store.directory) }).start(
      "quiz",
      fx.guild,
      fx.channel,
      { id: U },
      { automatic: true },
    ),
    /already active/,
  );
  const recovered = new EventService({ store });
  await recovered.tick(fx.client);
  await recovered.tick(fx.client);
  await assert.rejects(
    recovered.start(
      "quiz",
      fx.guild,
      fx.channel,
      { id: U },
      { automatic: true },
    ),
    /recently/,
  );
  assert.equal(attempts, 1);
});
test("simultaneous quiz starts publish exactly one announcement", async (t) => {
  const store = await setup(t),
    fx = fixtures(),
    service = new EventService({ store });
  const results = await Promise.allSettled(
    Array.from({ length: 12 }, () =>
      service.start(
        "quiz",
        fx.guild,
        fx.channel,
        { id: U },
        { automatic: true },
      ),
    ),
  );
  assert.equal(results.filter((r) => r.status === "fulfilled").length, 1);
  assert.equal(fx.sent, 1);
});
test("quizzes do not ping everyone or any role unless explicitly configured", async (t) => {
  const store = await setup(t),
    fx = fixtures();
  let sent;
  fx.channel.send = async (payload) => {
    sent = payload;
    return { id: C };
  };
  await new EventService({ store }).start(
    "quiz",
    fx.guild,
    fx.channel,
    { id: U },
    {},
  );
  assert.deepEqual(sent.allowedMentions, { parse: [] });
  assert.equal(sent.content, null);
});
test("quiz finalization awards once across crashes and retains legacy numeric counters", async (t) => {
  const store = await setup(t),
    fx = fixtures(),
    service = new EventService({ store });
  const row = await service.start("quiz", fx.guild, fx.channel, { id: U }, {});
  row.answers[U] = { name: "Member", answer: row.question.answer, elapsed: 5 };
  row.phase = "finalizing";
  await store.write("quiz_stats.json", {
    [G]: { [U]: { answered: "2", points: "100" } },
  });
  await store.write(EVENT_FILES.quiz, { [G]: row });
  await service.finish("quiz", fx.guild, G);
  const first = (await store.read("quiz_stats.json"))[G][U];
  assert.equal(first.answered, 3);
  assert.equal(first.correct, 1);
  assert.ok(Number.isFinite(first.points));
  await store.write(EVENT_FILES.quiz, { [G]: row });
  await new EventService({ store }).finish("quiz", fx.guild, G);
  assert.deepEqual((await store.read("quiz_stats.json"))[G][U], first);
});
test("all event payloads retain persistent legacy component IDs and disable on end", () => {
  const base = {
    check_id: "A",
    quiz_id: "Q",
    pulse_id: "P",
    giveaway_id: "G",
    end_time: Date.now() / 1000 + 60,
    settings_snapshot: { reactor_type: "button" },
    question: {
      question: "What?",
      choices: ["A", "B"],
      answer: 0,
      category: "general",
    },
    prize: "Test",
    entries: [],
    winner_count: 1,
  };
  for (const kind of Object.keys(EVENT_FILES)) {
    const payload = eventPayload(kind, base, true);
    assert.ok(
      payload.components.every((row) =>
        row.toJSON().components.every((button) => button.disabled),
      ),
    );
  }
});
test("legacy naive UTC timestamps parse consistently", () => {
  assert.equal(epoch("2026-11-13T12:00:00"), epoch("2026-11-13T12:00:00Z"));
});
test("deleting an activity announcement cancels it without awarding a win", async (t) => {
  const store = await setup(t),
    fx = fixtures(),
    service = new EventService({ store });
  const row = await service.start(
    "activity",
    fx.guild,
    fx.channel,
    { id: U },
    { settings: { reactor_type: "button" } },
  );
  row.winners = [{ user_id: C, name: "Member", time: Date.now() / 1000 }];
  await store.write(EVENT_FILES.activity, { [G]: row });
  await service.deleted({ id: row.message_id, guildId: G, guild: fx.guild });
  assert.equal((await store.read("global_stats.json"))[C], undefined);
  assert.equal(
    (await store.read("activity_audit_logs.json"))[G][row.check_id].status,
    "cancelled",
  );
  assert.equal((await store.read(EVENT_FILES.activity))[G], undefined);
});
test("automatic schedules reserve their next interval before failures and do not flood retries", async (t) => {
  const store = await setup(t),
    fx = fixtures(),
    service = new CampaignService({ store });
  const leases = new LeaseStore(join(store.directory, "jobs.sqlite"));
  service.attachScheduler(leases);
  try {
    await store.write("automation_schedules.json", {
      [G]: {
        schedule: {
          kind: "quiz",
          enabled: true,
          channel_id: C,
          interval_minutes: 5,
          next_run: 0,
          options: {},
        },
      },
    });
    let sends = 0;
    service.events.start = async () => {
      sends++;
      throw new Error("failure");
    };
    await service.tick(fx.client);
    await service.tick(fx.client);
    assert.equal(sends, 1);
    assert.ok(
      (await store.read("automation_schedules.json"))[G].schedule.next_run >
        Date.now() / 1000,
    );
  } finally {
    leases.close();
  }
});
