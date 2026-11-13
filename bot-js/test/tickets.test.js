import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  ChannelType,
  PermissionFlagsBits as P,
  PermissionsBitField,
  Collection,
} from "discord.js";
import { JsonStore } from "../src/store.js";
import { TicketService, ticketSupportRoles } from "../src/modules/tickets.js";
import {
  panelOptions,
  panelPayload,
  optionEmoji,
  validatePanel,
  ticketControls,
  ROLE_GRANT,
} from "../src/modules/ticket-ui.js";

const G = "123456789012345678",
  C = "123456789012345679",
  OWNER = "123456789012345680",
  STAFF = "123456789012345681",
  A = "123456789012345682",
  B = "123456789012345683";
async function setup(t) {
  const directory = await mkdtemp(join(tmpdir(), "rallybit-ticket-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const store = new JsonStore(directory),
    guild = {
      id: G,
      ownerId: "server-owner",
      name: "Test server",
      iconURL: () => null,
    };
  const members = new Map([
    [
      A,
      {
        id: A,
        guild,
        roles: { cache: new Map([[STAFF, {}]]) },
        permissions: new PermissionsBitField(),
      },
    ],
    [
      B,
      {
        id: B,
        guild,
        roles: { cache: new Map([[STAFF, {}]]) },
        permissions: new PermissionsBitField(),
      },
    ],
  ]);
  const bot = {
    id: "bot",
    permissions: new PermissionsBitField([P.ManageChannels]),
  };
  guild.members = {
    fetch: async (arg) => members.get(typeof arg === "string" ? arg : arg.user),
    fetchMe: async () => bot,
  };
  const sent = [],
    pins = [];
  const channel = {
    id: C,
    guild,
    type: ChannelType.GuildText,
    name: "ticket-test",
    send: async (value) => {
      sent.push(value);
      return {
        id: "message-" + sent.length,
        pin: async () => {
          pins.push(true);
        },
      };
    },
    permissionsFor: () => new PermissionsBitField([P.ManageChannels]),
    messages: { fetch: async () => new Collection() },
    permissionOverwrites: { cache: new Map(), edit: async () => {} },
    delete: async () => {
      channel.deleted = true;
    },
  };
  guild.channels = { fetch: async (id) => (id === C ? channel : null) };
  guild.roles = { cache: new Map([[STAFF, {}]]) };
  const client = { user: bot, guilds: { cache: new Map([[G, guild]]) } };
  guild.client = client;
  channel.client = client;
  await store.write("ticket_settings.json", {
    [G]: { support_role_ids: [STAFF] },
  });
  await store.write("open_tickets.json", {
    [G]: {
      [C]: {
        owner_id: OWNER,
        panel_id: "PANEL",
        option_id: "GENERAL",
        status: "Open",
        claimed_by: null,
        case_id: "T-CASE",
      },
    },
  });
  const service = new TicketService({ store, audit: async () => {} }),
    interaction = (id) => ({
      id: "interaction-" + id,
      guild,
      guildId: G,
      channel,
      channelId: C,
      user: { id },
      client,
    });
  return {
    store,
    guild,
    channel,
    service,
    interaction,
    members,
    sent,
    pins,
    client,
  };
}
test("dropdown support roles do not leak between ticket categories", () => {
  const settings = { support_role_ids: [A] },
    panel = {
      support_role_ids: [B],
      options: [
        { option_id: "ONE", support_role_ids: [STAFF] },
        { option_id: "TWO", support_role_ids: [] },
      ],
    };
  assert.deepEqual(
    ticketSupportRoles(settings, panel, { option_id: "ONE" }).sort(),
    [A, STAFF].sort(),
  );
  assert.deepEqual(ticketSupportRoles(settings, panel, { option_id: "TWO" }), [
    A,
  ]);
});
test("numeric and custom emoji IDs work; ordinary strings are not treated as emoji", () => {
  assert.equal(optionEmoji(STAFF).id, STAFF);
  assert.equal(optionEmoji(`<a:loading:${STAFF}>`).animated, true);
  assert.equal(optionEmoji("✅").name, "✅");
  assert.equal(optionEmoji("not-an-emoji"), null);
});
test("old single-button panels become dropdowns without losing support roles", () => {
  const options = panelOptions({
    name: "Support",
    category_id: C,
    support_role_ids: [STAFF],
  });
  assert.equal(options[0].option_id, "DEFAULT");
  assert.deepEqual(options[0].support_role_ids, [STAFF]);
});
test("all-closed panels show a disabled dropdown and retain media customisations", () => {
  const payload = panelPayload({ id: G }, "PANEL", {
    title: "Support",
    header_image_url: "https://example.com/header.png",
    thumbnail_url: "https://example.com/icon.png",
    image_url: "https://example.com/footer.png",
    footer_text: "Support team",
    options: [{ option_id: "ONE", name: "General", enabled: false }],
  });
  assert.equal(payload.embeds.length, 2);
  assert.equal(payload.components[0].components[0].data.disabled, true);
  assert.ok(payload.embeds[1].data.fields[0].name.includes("Closed"));
  assert.equal(
    payload.embeds[1].data.image.url,
    "https://example.com/footer.png",
  );
});
test("panel validation rejects duplicate IDs and invalid media schemes", () => {
  assert.throws(
    () =>
      validatePanel({ options: [{ option_id: "ONE" }, { option_id: "ONE" }] }),
    /unique/,
  );
  assert.throws(
    () =>
      validatePanel({
        options: [{ name: "General" }],
        image_url: "file:///etc/passwd",
      }),
    /HTTPS/,
  );
});
test("ticket controls retain the IDs of already-posted Python buttons", () => {
  const ids = ticketControls(G, C)[0].components.map((c) => c.data.custom_id);
  assert.deepEqual(
    ids,
    ["claim", "transcript", "close", "delete"].map(
      (action) => `rallybit:ticket:${action}:${G}:${C}`,
    ),
  );
});
test("support-role members can claim; a concurrent staff claim cannot steal it", async (t) => {
  const { service, interaction, store, sent, pins } = await setup(t);
  const results = await Promise.allSettled([
    service.claim(interaction(A)),
    service.claim(interaction(B)),
  ]);
  assert.equal(results.filter((r) => r.status === "fulfilled").length, 1);
  assert.equal(sent.length, 1);
  assert.equal(pins.length, 1);
  assert.ok(
    [A, B].includes((await store.read("open_tickets.json"))[G][C].claimed_by),
  );
});
test("removed support roles stop working on existing ticket controls immediately", async (t) => {
  const { service, interaction, store } = await setup(t);
  await store.write("ticket_settings.json", { [G]: { support_role_ids: [] } });
  await assert.rejects(service.claim(interaction(A)), /support role/);
});
test("role reconciliation revokes only managed grant bits and preserves other overwrites", async (t) => {
  const { service, guild, channel, store } = await setup(t);
  const changes = [];
  await store.write("ticket_settings.json", { [G]: { support_role_ids: [] } });
  await store.update("open_tickets.json", (data) => {
    data[G][C].managed_support_role_ids = [STAFF];
  });
  channel.permissionOverwrites.cache.set(STAFF, {
    id: STAFF,
    type: 0,
    allow: new PermissionsBitField([
      ...Object.keys(ROLE_GRANT).map((key) => P[key]),
      P.AttachFiles,
    ]),
    deny: new PermissionsBitField(),
  });
  channel.permissionOverwrites.edit = async (id, permissions) => {
    changes.push([id, permissions]);
  };
  await service.reconcile(guild);
  assert.equal(changes[0][0], STAFF);
  assert.deepEqual(changes[0][1], {
    ViewChannel: null,
    SendMessages: null,
    ReadMessageHistory: null,
    ManageMessages: null,
  });
  assert.ok(!Object.hasOwn(changes[0][1], "AttachFiles"));
  assert.deepEqual(
    (await store.read("open_tickets.json"))[G][C].managed_support_role_ids,
    [],
  );
});
test("close requires a reason; due deletion cannot delete a reopened ticket", async (t) => {
  const { service, interaction, channel, client, store } = await setup(t);
  await assert.rejects(service.close(interaction(A), ""), /reason/);
  await store.update("open_tickets.json", (data) => {
    Object.assign(data[G][C], { status: "Open", delete_at: null });
  });
  assert.equal(
    await service.remove(channel, client.user, "Timer", { onlyIfDue: true }),
    false,
  );
  assert.equal(channel.deleted, undefined);
});
test("failed transcript delivery prevents permanent channel deletion", async (t) => {
  const { service, channel, client } = await setup(t);
  service.archive = async () => {
    throw new Error("Upload failed");
  };
  await assert.rejects(
    service.remove(channel, client.user, "Manual delete"),
    /Upload failed/,
  );
  assert.equal(channel.deleted, undefined);
});
test("uncertain ticket creation intent blocks a second channel creation", async (t) => {
  const { service, interaction, store } = await setup(t);
  await store.write("open_tickets.json", {});
  await store.write("ticket_jobs.json", {
    request: { kind: "create", status: "pending", guild_id: G, owner_id: A },
  });
  await assert.rejects(
    service.create(interaction(A)),
    /earlier ticket request/,
  );
});
