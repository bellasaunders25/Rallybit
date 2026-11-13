import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  ChannelType,
  PermissionsBitField,
  PermissionFlagsBits as P,
} from "discord.js";
import OpusScript from "opusscript";
import { JsonStore } from "../src/store.js";
import { CvmsService, CASES, casePayload } from "../src/modules/cvms.js";
import {
  cvmsConfig,
  consentScope,
  hasConsent,
  normalizeSpeech,
  matchRules,
  parseTranscript,
  pcmWav,
  audible,
} from "../src/modules/cvms-config.js";
import {
  LocalSpeech,
  SpeechQueue,
  localEndpoint,
} from "../src/modules/cvms-audio.js";
import { createDispatcher } from "../src/runtime.js";

const G = "123456789012345678",
  U = "123456789012345679",
  A = "123456789012345680",
  C = "123456789012345681",
  L = "123456789012345682",
  B = "123456789012345683";
async function setup(t) {
  const dir = await mkdtemp(join(tmpdir(), "rallybit-cvms-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const store = new JsonStore(dir),
    events = [];
  let now = Date.now(),
    captured = 0,
    stopped = 0;
  const role = (position) => ({
      position,
      comparePositionTo(other) {
        return position - other.position;
      },
    }),
    members = new Map();
  const client = { user: { id: B }, guilds: { cache: new Map() } },
    guild = {
      id: G,
      name: "Guild",
      client,
      ownerId: "owner",
      roles: { everyone: { id: G } },
    };
  const makeMember = (id, position, admin = false) => ({
    id,
    guild,
    user: { id, username: "User", bot: id === B },
    displayName: "User",
    permissions: new PermissionsBitField(admin ? P.Administrator : []),
    roles: { highest: role(position), cache: new Map() },
    voice: { channelId: C, disconnect: async () => events.push("disconnect") },
  });
  members.set(U, makeMember(U, 10));
  members.set(A, makeMember(A, 40, true));
  members.set(B, makeMember(B, 50, true));
  members.get(B).voice.channelId = null;
  const messages = new Map(),
    message = (payload) => {
      const m = {
        id: "msg" + messages.size,
        author: client.user,
        edit: async (change) => {
          m.payload = change;
          events.push("edit");
        },
        delete: async () => {
          events.push("delete");
          messages.delete(m.id);
        },
        payload,
      };
      messages.set(m.id, m);
      return m;
    };
  const voice = {
    id: C,
    guild,
    name: "Voice",
    type: ChannelType.GuildVoice,
    members: new Map([
      [U, members.get(U)],
      [A, members.get(A)],
    ]),
    permissionsFor: (member) => member.permissions,
    send: async (payload) => {
      events.push("notice");
      return message(payload);
    },
    permissionOverwrites: {
      cache: new Map(),
      edit: async (id, values) => events.push(["overwrite", id, values]),
    },
  };
  const log = {
    id: L,
    guild,
    type: ChannelType.GuildText,
    permissionsFor: (member) =>
      member.id === G ? new PermissionsBitField() : member.permissions,
    send: async (payload) => {
      events.push("case");
      return message(payload);
    },
    messages: {
      fetch: async (id) => {
        const found = messages.get(id);
        if (!found) throw Object.assign(new Error(), { code: 10008 });
        return found;
      },
    },
  };
  voice.messages = log.messages;
  for (const member of members.values())
    member.voice.channel = member.id === B ? null : voice;
  guild.channels = {
    fetch: async (id) => (id === C ? voice : id === L ? log : null),
  };
  guild.members = {
    fetch: async (value) => {
      const m = members.get(typeof value === "string" ? value : value.user);
      if (!m) throw Object.assign(new Error(), { code: 10007 });
      return m;
    },
    fetchMe: async () => members.get(B),
  };
  client.guilds.cache.set(G, guild);
  const provider = { health: async () => true, transcribe: async () => [] },
    moderation = {
      execute: async () => {
        events.push("warn");
        return { content: "Warned" };
      },
    };
  const service = new CvmsService(
    { store, moderation, audit: async () => {} },
    {
      provider,
      now: () => now,
      capture: () => ({
        start: async () => {
          events.push("capture");
          captured++;
        },
        stop: () => {
          stopped++;
          events.push("stop");
        },
      }),
    },
  );
  const cfg = cvmsConfig({
    enabled: true,
    log_channel_id: L,
    channel_ids: [C],
    rules: [{ id: "RULE", phrase: "restricted phrase" }],
  });
  await store.write("cvms_settings.json", { [G]: cfg });
  await store.write("cvms_beta.json", { [G]: { enabled: true } });
  await store.write("cvms_consents.json", {
    [G]: Object.fromEntries(
      [U, A].map((id) => [
        id,
        { scope: consentScope(cfg), expires_at: now + 86400000 },
      ]),
    ),
  });
  const i = {
    guildId: G,
    guild,
    client,
    user: members.get(A).user,
    channel: log,
    channelId: L,
  };
  t.after(() => service.close());
  return {
    store,
    service,
    guild,
    client,
    voice,
    log,
    members,
    events,
    cfg,
    i,
    advance: (ms) => {
      now += ms;
    },
    get captured() {
      return captured;
    },
    get stopped() {
      return stopped;
    },
  };
}
test("CVMS defaults are off, local-only, consent-scoped and bounded", () => {
  const cfg = cvmsConfig();
  assert.equal(cfg.enabled, false);
  assert.equal(cfg.mode, "manual");
  assert.equal(cvmsConfig({ retention_hours: 100 }).retention_hours, 24);
  const record = { scope: consentScope(cfg), expires_at: 2000 };
  assert.equal(hasConsent(record, cfg, 1000), true);
  assert.equal(hasConsent(record, cfg, 2000), false);
  assert.equal(hasConsent(record, { ...cfg, retention_hours: 1 }, 1000), false);
});
test("literal Unicode phrase matching respects word boundaries and does not run regex", () => {
  const rules = [
    { id: "a", phrase: "bad word" },
    { id: "b", phrase: "ass" },
    { id: "c", phrase: ".*" },
  ];
  assert.equal(normalizeSpeech("ＢＡＤ, Word!"), "bad word");
  assert.deepEqual(
    matchRules("A class talks about BAD, word.", rules).map((r) => r.id),
    ["a"],
  );
  assert.equal(matchRules("all text", rules).length, 0);
});
test("unknown, non-finite and no-speech confidence never become a trusted score", () => {
  assert.deepEqual(
    parseTranscript({
      segments: [
        { text: "bad", avg_logprob: null, no_speech_prob: null },
        { text: "bad", avg_logprob: -0.1, no_speech_prob: 0.9 },
        { text: "bad" },
      ],
    }),
    [],
  );
  const [result] = parseTranscript({
    segments: [{ text: "Words", avg_logprob: -0.1, no_speech_prob: 0.1 }],
  });
  assert.ok(result.score > 0.9 && result.score < 1);
});
test("audio WAVs are bounded mono 16 kHz PCM; silence and tiny clips are discarded", () => {
  const pcm = Buffer.alloc(32000);
  for (let n = 0; n < pcm.length; n += 2) pcm.writeInt16LE(1000, n);
  const wav = pcmWav(pcm);
  assert.equal(wav.toString("utf8", 0, 4), "RIFF");
  assert.equal(wav.readUInt32LE(24), 16000);
  assert.equal(wav.readUInt16LE(22), 1);
  assert.equal(audible(pcm), true);
  assert.equal(audible(Buffer.alloc(32000)), false);
  assert.throws(() => pcmWav(Buffer.alloc(256002)), /Invalid/);
  const decoder = new OpusScript(16000, 1, OpusScript.Application.VOIP);
  decoder.delete();
});
test("ASR cannot send audio to paid or external providers and does not follow redirects", async () => {
  for (const url of [
    "https://api.openai.com/inference",
    "http://example.com",
    "http://cvms-asr:8081/load",
    "http://user:secret@localhost:8081",
  ])
    assert.throws(() => localEndpoint(url), /local|External/);
  let sent;
  const provider = new LocalSpeech({
    url: "http://cvms-asr:8081",
    fetch: async (url, options) => {
      sent = { url: String(url), options };
      return new Response(
        JSON.stringify({
          segments: [{ text: "Words", avg_logprob: -0.1, no_speech_prob: 0.1 }],
        }),
      );
    },
  });
  await provider.transcribe(
    pcmWav(Buffer.alloc(32000)),
    new AbortController().signal,
  );
  assert.equal(sent.options.redirect, "error");
  assert.equal(sent.options.body.get("file").name, "clip.wav");
  assert.equal(sent.options.body.get("language"), "en");
});
test("beta admission alone does not enable monitoring", async (t) => {
  const f = await setup(t);
  await f.store.write("cvms_settings.json", {
    [G]: { ...f.cfg, enabled: false },
  });
  await assert.rejects(f.service.start(f.guild, f.voice, f.i.user), /disabled/);
  assert.equal(f.captured, 0);
  assert.deepEqual(f.events, []);
});
test("missing consent or a public evidence channel blocks voice capture before any notice", async (t) => {
  const f = await setup(t);
  await f.store.update("cvms_consents.json", (data) => {
    delete data[G][U];
  });
  await assert.rejects(
    f.service.start(f.guild, f.voice, f.i.user),
    /every member/,
  );
  assert.equal(f.captured, 0);
  assert.equal(f.events.length, 0);
  await f.store.write("cvms_consents.json", {
    [G]: Object.fromEntries(
      [U, A].map((id) => [
        id,
        { scope: consentScope(f.cfg), expires_at: Date.now() + 50000 },
      ]),
    ),
  });
  f.log.permissionsFor = () => new PermissionsBitField(P.Administrator);
  await assert.rejects(
    f.service.start(f.guild, f.voice, f.i.user),
    /not visible/,
  );
  assert.equal(f.captured, 0);
});
test("a public disclosure is posted before a voice receiver is opened; raw audio is not stored", async (t) => {
  const f = await setup(t),
    session = await f.service.start(f.guild, f.voice, f.i.user);
  assert.deepEqual(f.events.slice(0, 2), ["notice", "capture"]);
  assert.equal(session.valid(U), true);
  assert.equal(f.captured, 1);
  const files = await readdir(f.store.directory);
  assert.ok(files.every((name) => name.endsWith(".json")));
  const record = (await f.store.read("cvms_sessions.json"))[G];
  assert.equal(record.channel_id, C);
  assert.equal(Object.hasOwn(record, "pcm"), false);
});
test("consent is personal, expires and requires a current disclosure confirmation", async (t) => {
  const f = await setup(t),
    prompt = await f.service.consentPrompt(f.i),
    token = prompt.components[0].toJSON().components[0].custom_id.split(":")[2];
  await assert.rejects(
    f.service.consent({ ...f.i, user: { id: U } }, token),
    /expired/,
  );
  await f.service.update(G, (cfg) => {
    cfg.retention_hours = 1;
  });
  await assert.rejects(f.service.consent(f.i, token), /privacy settings/);
  const fresh = await f.service.consentPrompt(f.i),
    next = fresh.components[0].toJSON().components[0].custom_id.split(":")[2];
  await f.service.consent(f.i, next);
  await f.service.withdraw(G, A);
  assert.equal((await f.store.read("cvms_consents.json"))[G][A], undefined);
  await assert.rejects(f.service.consent(f.i, next), /expired/);
});
test("withdrawal stops capture immediately and prevents an inference result being posted", async (t) => {
  const f = await setup(t),
    session = await f.service.start(f.guild, f.voice, f.i.user);
  await f.service.withdraw(G, U);
  assert.equal(f.stopped, 1);
  assert.equal(session.valid(U), false);
  await assert.rejects(
    f.service.result(f.guild, session, U, [
      { text: "restricted phrase", score: 0.99 },
    ]),
    /every member/,
  );
  assert.ok(!f.events.includes("case"));
});
test("new human joins pause monitoring, but Rallybit’s own join does not stop itself", async (t) => {
  const f = await setup(t);
  await f.service.start(f.guild, f.voice, f.i.user);
  await f.service.voiceChanged(
    { channelId: null },
    { id: B, guild: f.guild, channelId: C, serverDeaf: false },
  );
  assert.equal(f.stopped, 0);
  f.voice.members.set("new", { id: "new", user: { bot: false } });
  assert.equal(f.service.sessions.get(G).valid(U), false);
  await f.service.voiceChanged(
    { channelId: null },
    { id: "new", guild: f.guild, channelId: C },
  );
  assert.equal(f.stopped, 1);
});
test("matching speech creates one review case without punishments or broad pings", async (t) => {
  const f = await setup(t),
    session = await f.service.start(f.guild, f.voice, f.i.user);
  await f.service.result(f.guild, session, U, [
    { text: "A restricted phrase was spoken.", score: 0.99 },
  ]);
  await f.service.result(f.guild, session, U, [
    { text: "restricted phrase again", score: 0.99 },
  ]);
  const rows = Object.values((await f.store.read(CASES))[G]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, "pending");
  assert.ok(!f.events.includes("warn"));
  assert.ok(!f.events.includes("disconnect"));
  assert.deepEqual(casePayload(G, rows[0]).allowedMentions, { parse: [] });
  await assert.rejects(
    f.service.action(f.i, rows[0].id, "disconnect", "Reviewed"),
    /must mark/,
  );
});
test("removed moderation roles block existing case controls", async (t) => {
  const f = await setup(t);
  f.members.get(A).permissions = new PermissionsBitField();
  await assert.rejects(
    f.service.review(f.i, "case", "confirm"),
    /configured warning role/,
  );
  assert.ok(!f.events.includes("disconnect"));
});
test("confirmed manual voice restriction restores only Connect and preserves other overwrites", async (t) => {
  const f = await setup(t),
    id = "CASE";
  await f.store.write(CASES, {
    [G]: {
      [id]: {
        id,
        user_id: U,
        channel_id: C,
        expires_at: Date.now() + 60000,
        status: "confirmed",
      },
    },
  });
  f.voice.permissionOverwrites.cache.set(U, {
    allow: new PermissionsBitField(P.Speak),
    deny: new PermissionsBitField(),
  });
  await f.service.action(f.i, id, "restrict", "Staff reviewed the context.");
  assert.ok(f.events.includes("disconnect"));
  f.voice.permissionOverwrites.cache.set(U, {
    allow: new PermissionsBitField(P.Speak),
    deny: new PermissionsBitField(P.Connect),
  });
  f.advance(610000);
  await f.service.cleanup(f.client);
  const changes = f.events.filter(Array.isArray);
  assert.deepEqual(changes[0][2], { Connect: false });
  assert.deepEqual(changes.at(-1)[2], { Connect: null });
  assert.equal(Object.hasOwn(changes.at(-1)[2], "Speak"), false);
});
test("expired case excerpts are redacted even when Discord log deletion is unavailable", async (t) => {
  const f = await setup(t);
  f.log.messages.fetch = async () => {
    throw Object.assign(new Error(), { code: 50001 });
  };
  await f.store.write(CASES, {
    [G]: {
      CASE: {
        id: "CASE",
        user_id: U,
        excerpt: "private speech",
        rule_ids: ["a"],
        status: "pending",
        created_epoch: 0,
        expires_at: Date.now() - 1000,
        log_channel_id: L,
        message_id: "missing",
      },
    },
  });
  await f.service.cleanup(f.client);
  assert.equal((await f.store.read(CASES))[G].CASE.excerpt, "");
  assert.equal((await f.store.read(CASES))[G].CASE.status, "expired");
});
test("reports require the reporting member to be in the same configured voice channel", async (t) => {
  const f = await setup(t);
  await f.service.update(G, (cfg) => {
    cfg.report_integration = true;
  });
  f.members.get(A).voice.channelId = "elsewhere";
  await assert.rejects(
    f.service.addReport(f.i, { target_id: U }),
    /current configured/,
  );
  assert.equal((await f.store.read("cvms_reports.json"))[G], undefined);
});
test("stopped sessions cannot replay queued speech and memory is zeroed", async () => {
  let complete,
    calls = 0,
    results = 0;
  const provider = {
      transcribe: () => {
        calls++;
        return new Promise((resolve) => {
          complete = resolve;
        });
      },
    },
    queue = new SpeechQueue(provider, { limit: 1 });
  const pcm = Buffer.alloc(16000, 1);
  queue.submit({
    sessionId: "s",
    pcm,
    valid: () => true,
    result: async () => {
      results++;
    },
  });
  const pending = Buffer.alloc(16000, 2);
  queue.submit({
    sessionId: "s",
    pcm: pending,
    valid: () => true,
    result: async () => {
      results++;
    },
  });
  const dropped = Buffer.alloc(16000, 3);
  assert.equal(
    queue.submit({
      sessionId: "s",
      pcm: dropped,
      valid: () => true,
      result: async () => {},
    }),
    false,
  );
  assert.ok(dropped.every((v) => v === 0));
  queue.cancel("s");
  complete([]);
  for (let n = 0; n < 20 && queue.running; n++)
    await new Promise((r) => setTimeout(r, 1));
  assert.equal(results, 0);
  assert.equal(calls, 1);
  assert.ok(pcm.every((v) => v === 0) && pending.every((v) => v === 0));
  queue.stop();
});
test("privacy withdrawal still works during maintenance without enabling commands", async () => {
  let invoked = false;
  const dispatcher = createDispatcher(
    {
      handler: (path) => {
        assert.equal(path, "cvms consent");
        return async () => {
          invoked = true;
          return { content: "Withdrawn" };
        };
      },
    },
    { read: async () => ({ active: true }) },
  );
  const i = {
    commandName: "cvms",
    isChatInputCommand: () => true,
    options: {
      getSubcommandGroup: () => null,
      getSubcommand: () => "consent",
      getBoolean: (name) => (name === "enabled" ? false : null),
    },
    guild: { members: { fetch: async () => ({}) } },
    user: { id: U },
    deferReply: async () => {},
    editReply: async () => {},
  };
  await dispatcher(i);
  assert.equal(invoked, true);
});
test("withdrawing while the public notice is being posted cannot open a receiver", async (t) => {
  const f = await setup(t),
    send = f.voice.send;
  f.voice.send = async (payload) => {
    await f.service.withdraw(G, U);
    return send(payload);
  };
  await assert.rejects(
    f.service.start(f.guild, f.voice, f.i.user),
    /pending session|consent|stopped/,
  );
  assert.equal(f.captured, 0);
  assert.equal(f.service.sessions.size, 0);
});
test("voice report rate limits prevent repeated monitoring requests", async (t) => {
  const f = await setup(t);
  await f.service.update(G, (cfg) => {
    cfg.report_integration = true;
  });
  await f.service.addReport(f.i, { target_id: U, report_id: "one" });
  await assert.rejects(
    f.service.addReport(f.i, { target_id: U, report_id: "two" }),
    /Wait one minute/,
  );
  assert.equal(
    (await f.store.read("cvms_reports.json"))[G][U].report_id,
    "one",
  );
});
test("automatic watch sessions never exceed the watch expiry", async (t) => {
  const f = await setup(t),
    until = Date.now() + 2000,
    session = await f.service.start(f.guild, f.voice, f.i.user, {
      minutes: 5,
      source: "watch",
      targets: [U],
      until,
    });
  assert.equal(session.endsAt, until);
  f.advance(3000);
  assert.equal(session.valid(U), false);
  assert.equal(session.valid(A), false);
});
