import { Events, GatewayIntentBits as I, Partials } from "discord.js";
import { syncGuildCache } from "./api.js";
import { nowISO } from "./helpers.js";

export const GATEWAY_OPTIONS = {
  intents: [
    I.Guilds,
    I.GuildMembers,
    I.GuildModeration,
    I.GuildMessages,
    I.GuildMessageReactions,
    I.MessageContent,
    I.GuildVoiceStates,
    I.GuildInvites,
    I.GuildWebhooks,
    I.GuildEmojisAndStickers,
    I.GuildScheduledEvents,
    I.DirectMessages,
  ],
  partials: [
    Partials.Channel,
    Partials.Message,
    Partials.Reaction,
    Partials.GuildMember,
    Partials.User,
  ],
  allowedMentions: { parse: [] },
  sweepers: { messages: { interval: 300, lifetime: 1800 } },
};
export function bindGateway(
  app,
  client,
  {
    assertOwner = () => {},
    track = (promise) => promise,
    onReady = async () => {},
  } = {},
) {
  if (app.gatewayBound) throw new Error("Gateway listeners already installed");
  app.gatewayBound = true;
  const on = (event, handler) =>
    client.on(event, (...args) =>
      track(
        (async () => {
          assertOwner();
          await handler(...args);
        })().catch((error) =>
          console.warn(
            JSON.stringify({
              event: "gateway_handler_failed",
              handler: event,
              code: error.code,
              name: error.name,
            }),
          ),
        ),
      ),
    );
  const safe = async (label, action) => {
    try {
      assertOwner();
      return await action();
    } catch (error) {
      console.warn(
        JSON.stringify({
          event: "gateway_step_failed",
          handler: label,
          code: error.code,
          name: error.name,
        }),
      );
    }
  };
  app.logging.bind(client, assertOwner);
  app.security.bind(client, assertOwner);
  app.prettfy.assertOwner = assertOwner;
  on(Events.ClientReady, async () => {
    await onReady();
  });
  on(Events.GuildMemberAdd, async (member) => {
    // One ordered pipeline: rejected accounts must not receive welcome messages
    // or autoroles from independent listeners after a security kick.
    if (!(await app.security.onJoin(member))) return;
    await safe("autorole", () => app.members.onJoin(member));
    await safe("welcome", () => app.welcome.onJoin(member));
    await syncGuildCache(client, app.store);
  });
  on(Events.GuildMemberRemove, async (member) => {
    await safe("goodbye", () => app.welcome.onLeave(member));
    await syncGuildCache(client, app.store);
  });
  on(Events.MessageCreate, async (message) => {
    if (!message.guild) {
      app.prettfy.onMessage(message);
      return;
    }
    if (message.author.bot) return;
    if (!(await app.security.onMessage(message))) return;
    await safe("afk", () => app.utility.onMessage(message));
    await safe("levels", () => app.members.onMessage(message));
  });
  on(Events.MessageDelete, async (message) => {
    app.snipes.record(message);
    await app.campaigns.events.deleted(message);
  });
  on(Events.MessageUpdate, (before, after) => app.snipes.record(before, after));
  on(Events.MessageBulkDelete, async (messages) => {
    for (const message of messages.values()) {
      app.snipes.record(message);
      await app.campaigns.events.deleted(message);
    }
  });
  const reaction = (remove) => async (row, user) => {
    if (user.bot) return;
    if (row.partial) await row.fetch();
    if (row.message.partial) await row.message.fetch();
    if (user.partial) await user.fetch();
    if (!row.message.guild) return;
    if (!remove)
      await safe("activity_reaction", () =>
        app.campaigns.events.reaction(row, user),
      );
    await safe("reaction_roles", () => app.members.reaction(row, user, remove));
  };
  on(Events.MessageReactionAdd, reaction(false));
  on(Events.MessageReactionRemove, reaction(true));
  on(Events.InviteCreate, (invite) => app.welcome.refresh(invite.guild));
  on(Events.InviteDelete, (invite) => app.welcome.refresh(invite.guild));
  on(Events.GuildCreate, async (guild) => {
    await app.welcome.refresh(guild);
    await syncGuildCache(client, app.store);
  });
  on(Events.GuildDelete, () => syncGuildCache(client, app.store));
  on(Events.GuildUpdate, () => syncGuildCache(client, app.store));
  on(Events.ShardReady, () => syncGuildCache(client, app.store));
  on(Events.ShardResume, () => syncGuildCache(client, app.store));
  on(Events.ShardDisconnect, () => syncGuildCache(client, app.store));
}
export async function maintenance(
  app,
  client,
  assertOwner = () => {},
  kind = "slow",
) {
  const run = async (name, action) => {
    try {
      assertOwner();
      await action();
    } catch (error) {
      console.warn(
        JSON.stringify({
          event: "maintenance_failed",
          service: name,
          code: error.code,
          name: error.name,
        }),
      );
    }
  };
  if (kind === "fast") {
    await run("events", () => app.campaigns.events.tick(client, assertOwner));
    return;
  }
  await run("roles", () => app.roles.tick(client, assertOwner));
  await run("tickets", () => app.tickets.tick(client, assertOwner));
  await run("security", () => app.security.tick(client, assertOwner));
  await run("campaigns", () => app.campaigns.tick(client, assertOwner));
  await run("previews", () => app.prettfy.cleanup());
  await run("guild_cache", () => syncGuildCache(client, app.store));
}
export async function statusSample(app, client) {
  const now = Math.floor(Date.now() / 1000),
    ping = Math.max(0, Math.round(client.ws.ping));
  await app.store.update(
    "status_history.json",
    (history) => {
      if (!Array.isArray(history)) throw new Error("Invalid status history");
      history.push({
        time: now,
        status: client.isReady() ? "online" : "offline",
        ping,
      });
      const keep = history.filter((row) => row.time >= now - 5 * 86400);
      history.splice(0, history.length, ...keep.slice(-500));
    },
    [],
  );
  await app.store.update("runtime_status.json", (data) => {
    Object.assign(data, {
      runtime: "discord.js",
      updated_at: nowISO(),
      ready: client.isReady(),
    });
  });
}
