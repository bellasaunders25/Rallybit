import {
  ChannelType,
  EmbedBuilder,
  PermissionFlagsBits as P,
} from "discord.js";
import { UserError, enforceAccess, assertAllowed } from "./access.js";
import {
  channelPermissions,
  guildSettings,
  shortId,
  snowflake,
  text,
} from "./helpers.js";
import { mediaURL, panelOptions } from "./modules/ticket-ui.js";
import { assignableRole } from "./modules/member-tools.js";
import { DASHBOARD_ACTIONS } from "./api.js";

const needs = (actor, ...permissions) => {
  if (!actor.permissions.has(permissions))
    throw new UserError(
      `You need ${permissions.map((bit) => Object.keys(P).find((k) => P[k] === bit)).join(" and ")}.`,
    );
};
export const asBoolean = (value, fallback = true) =>
  value == null
    ? fallback
    : !["0", "false", "no", "off", ""].includes(String(value).toLowerCase());
const bounded = (value, min, max, fallback) => {
  const n = value == null ? fallback : Number(value);
  if (!Number.isInteger(n) || n < min || n > max)
    throw new UserError(`Enter a whole number between ${min} and ${max}.`);
  return n;
};
export function dashboardEmbed(params) {
  const data = {};
  if (params.title) data.title = text(params.title, 256);
  if (params.description) data.description = text(params.description, 4096);
  if (params.title_url) data.url = mediaURL(params.title_url);
  const color = String(params.color || "#09623f");
  if (!/^#?[a-f0-9]{6}$/i.test(color))
    throw new UserError("Choose a six-digit hex colour.");
  data.color = parseInt(color.replace("#", ""), 16);
  if (params.author_name)
    data.author = {
      name: text(params.author_name, 256),
      ...(params.author_url ? { url: mediaURL(params.author_url) } : {}),
      ...(params.author_icon_url
        ? { icon_url: mediaURL(params.author_icon_url) }
        : {}),
    };
  if (params.footer_text)
    data.footer = {
      text: text(params.footer_text, 2048),
      ...(params.footer_icon_url
        ? { icon_url: mediaURL(params.footer_icon_url) }
        : {}),
    };
  if (params.thumbnail_url)
    data.thumbnail = { url: mediaURL(params.thumbnail_url) };
  if (params.image_url) data.image = { url: mediaURL(params.image_url) };
  if (asBoolean(params.show_timestamp, false))
    data.timestamp = new Date().toISOString();
  if (params.fields != null) {
    if (!Array.isArray(params.fields) || params.fields.length > 25)
      throw new UserError("An embed supports up to 25 fields.");
    data.fields = params.fields.map((row) => {
      if (!row || !text(row.name) || !text(row.value))
        throw new UserError("Each embed field needs a name and value.");
      return {
        name: text(row.name, 256),
        value: text(row.value, 1024),
        inline: asBoolean(row.inline, false),
      };
    });
  }
  const size =
    (data.title?.length ?? 0) +
    (data.description?.length ?? 0) +
    (data.author?.name.length ?? 0) +
    (data.footer?.text.length ?? 0) +
    (data.fields ?? []).reduce(
      (n, row) => n + row.name.length + row.value.length,
      0,
    );
  if (size > 6000 || (!size && !data.image && !data.thumbnail))
    throw new UserError(
      "Use a non-empty embed with no more than 6,000 text characters.",
    );
  return new EmbedBuilder(data);
}
export function embedPayload(message, index = 1) {
  const e = message.embeds[index - 1];
  if (!e) throw new UserError("That embed index does not exist.");
  const data = e.toJSON ? e.toJSON() : e;
  return {
    channel_id: message.channelId,
    channel_name: message.channel.name,
    message_id: message.id,
    embed_index: index,
    embed_count: message.embeds.length,
    jump_url: message.url,
    content: message.content,
    title: data.title ?? "",
    title_url: data.url ?? "",
    description: data.description ?? "",
    color: `#${(data.color ?? 0x09623f).toString(16).padStart(6, "0")}`,
    author_name: data.author?.name ?? "",
    author_url: data.author?.url ?? "",
    author_icon_url: data.author?.icon_url ?? "",
    thumbnail_url: data.thumbnail?.url ?? "",
    image_url: data.image?.url ?? "",
    footer_text: data.footer?.text ?? "",
    footer_icon_url: data.footer?.icon_url ?? "",
    fields: data.fields ?? [],
    show_timestamp: Boolean(data.timestamp),
  };
}
export function ticketPanelPayload(id, panel) {
  return {
    ...panel,
    panel_id: id,
    options: panelOptions(panel).map((row) => ({
      ...row,
      support_role_id: row.support_role_ids[0] ?? null,
    })),
  };
}

export function createDashboardActions(app) {
  const actions = new Map(),
    { store } = app;
  const context = (guild, actor, channel = null) => ({
    id: `dashboard-${shortId()}`,
    guild,
    guildId: guild.id,
    user: actor.user,
    member: actor,
    channel,
    channelId: channel?.id,
    client: guild.client,
  });
  async function targetChannel(guild, actor, id, { write = true } = {}) {
    const channel = await guild.channels.fetch(snowflake(id, "channel ID"));
    if (!channel?.messages || channel.guild.id !== guild.id)
      throw new UserError("Choose a text channel in this server.");
    channelPermissions(channel, actor, [
      P.ViewChannel,
      ...(write ? [P.SendMessages] : [P.ReadMessageHistory]),
    ]);
    const bot = await guild.members.fetchMe({ force: true });
    channelPermissions(channel, bot, [
      P.ViewChannel,
      ...(write ? [P.SendMessages, P.EmbedLinks] : [P.ReadMessageHistory]),
    ]);
    return channel;
  }
  async function invoke(path, { guild, actor }, values, channel = null) {
    const i = context(guild, actor, channel),
      get = (name, required = false) => {
        const value = values[name] ?? null;
        if (required && value == null) throw new UserError(`Missing ${name}.`);
        return value;
      };
    i.options = {
      getString: get,
      getInteger: get,
      getBoolean: get,
      getUser: get,
      getMember: get,
      getRole: get,
      getChannel: get,
    };
    await enforceAccess(i, app.registry.policy(path), store);
    const result = await app.registry.handler(path)(i);
    return { message: result.content ?? "Action completed." };
  }
  actions.set("logging.dashboard", async ({ guild, actor, params }) => {
    needs(actor, P.ManageGuild);
    await app.audit(
      context(guild, actor),
      "dashboard",
      "Dashboard settings changed",
      text(params.message || "Server settings were updated.", 1500),
    );
    return { message: "Dashboard change logged." };
  });
  for (const action of ["embed.load", "embed.update", "embed.send"])
    actions.set(action, async ({ guild, actor, params }) => {
      needs(actor, P.ManageMessages);
      let channel, message;
      if (action === "embed.send") {
        channel = await targetChannel(guild, actor, params.channel_id);
        const content = text(params.content, 2000),
          card = dashboardEmbed(params);
        message = await channel.send({
          content: content || undefined,
          embeds: [card],
          allowedMentions: { parse: [] },
        });
      } else {
        const id = snowflake(params.message_id, "message ID");
        const candidates = params.channel_id
          ? [
              await targetChannel(guild, actor, params.channel_id, {
                write: false,
              }),
            ]
          : [...(await guild.channels.fetch()).values()].filter(
              (c) =>
                c?.messages &&
                c
                  .permissionsFor(actor)
                  ?.has([P.ViewChannel, P.ReadMessageHistory]),
            );
        for (const candidate of candidates) {
          try {
            const found = await candidate.messages.fetch(id);
            if (found) {
              channel = candidate;
              message = found;
              break;
            }
          } catch (error) {
            if (![10008, 10003, 50001, 50013].includes(error.code)) throw error;
          }
        }
        if (!message)
          throw new UserError(
            "Message not found in accessible channels. Select its channel and check the message ID.",
          );
        if (message.author.id !== guild.client.user.id)
          throw new UserError("Rallybit can only edit embeds it sent.");
        if (action === "embed.update") {
          await targetChannel(guild, actor, channel.id);
          const index = bounded(params.embed_index, 1, 10, 1);
          if (!message.embeds[index - 1])
            throw new UserError("That embed index does not exist.");
          const embeds = message.embeds.map((e) => e.toJSON());
          embeds[index - 1] = dashboardEmbed(params).toJSON();
          const total = embeds.reduce(
            (n, e) =>
              n +
              (e.title?.length ?? 0) +
              (e.description?.length ?? 0) +
              (e.author?.name?.length ?? 0) +
              (e.footer?.text?.length ?? 0) +
              (e.fields ?? []).reduce(
                (m, f) => m + f.name.length + f.value.length,
                0,
              ),
            0,
          );
          if (total > 6000)
            throw new UserError(
              "Combined embeds exceed the Discord 6,000-character limit.",
            );
          message = await message.edit({
            content:
              params.content === undefined
                ? message.content
                : text(params.content, 2000),
            embeds,
            allowedMentions: { parse: [] },
          });
        }
      }
      return {
        message:
          action === "embed.load"
            ? "Embed loaded."
            : action === "embed.send"
              ? "Embed sent."
              : "Embed updated.",
        embed: embedPayload(message, bounded(params.embed_index, 1, 10, 1)),
        message_id: message.id,
      };
    });
  for (const kind of ["activity", "quiz", "pulse", "giveaway"])
    for (const operation of ["start", kind === "giveaway" ? "end" : "stop"])
      actions.set(`${kind}.${operation}`, async (data) => {
        const { guild, actor, params } = data;
        if (kind === "activity")
          await app.campaigns.activityAccess(context(guild, actor));
        else
          needs(actor, kind === "giveaway" ? P.ManageGuild : P.ManageMessages);
        if (operation === "start") {
          const channel = await targetChannel(guild, actor, params.channel_id),
            options = { ...params };
          if (kind === "activity")
            options.settings = await app.campaigns.activityAccess(
              context(guild, actor),
            );
          if (kind === "quiz")
            options.duration_seconds = bounded(
              params.duration_seconds,
              15,
              120,
              30,
            );
          else
            options.duration_minutes = bounded(
              params.duration_minutes,
              1,
              kind === "giveaway" ? 43200 : 1440,
              kind === "giveaway" ? 60 : 10,
            );
          if (kind === "giveaway")
            options.winners = bounded(params.winners, 1, 20, 1);
          const row = await app.campaigns.events.start(
            kind,
            guild,
            channel,
            actor.user,
            options,
          );
          return {
            message: `${kind[0].toUpperCase() + kind.slice(1)} started in #${channel.name}.`,
            id: row.giveaway_id ?? row.quiz_id ?? row.check_id ?? row.pulse_id,
          };
        }
        const key =
            kind === "giveaway"
              ? text(params.giveaway_id, 40).toUpperCase()
              : guild.id,
          row = (await app.campaigns.events.rows(kind))[key];
        if (!row || String(row.guild_id ?? guild.id) !== guild.id)
          throw new UserError(`No matching ${kind} is active.`);
        await app.campaigns.events.finish(kind, guild, key, "dashboard");
        return { message: `${kind} ended.` };
      });
  actions.set("icebreaker.post", async ({ guild, actor, params }) => {
    needs(actor, P.ManageMessages);
    const channel = await targetChannel(guild, actor, params.channel_id);
    await app.campaigns.icebreaker(guild, channel, params);
    return { message: "Conversation starter posted." };
  });
  actions.set("security.trap", async ({ guild, actor, params }) => {
    needs(actor, P.Administrator);
    const channel = await app.security.trap(guild, params);
    return { message: `Security trap ready in #${channel.name}.` };
  });
  actions.set("security.lockdown", async ({ guild, actor, params }) => {
    needs(actor, P.Administrator);
    const enabled = asBoolean(params.enabled);
    await app.security.lockdown(guild, enabled, actor.user);
    return {
      message: enabled
        ? "Server lockdown enabled."
        : "Saved server permissions restored.",
    };
  });
  for (const action of ["warn", "timeout", "kick", "ban"])
    actions.set(`moderation.${action}`, async ({ guild, actor, params }) => {
      const channel = params.channel_id
        ? await targetChannel(guild, actor, params.channel_id)
        : null;
      const result = await app.moderation.execute(
        context(guild, actor, channel),
        action,
        snowflake(params.user_id, "member ID"),
        {
          reason: params.reason || "Dashboard moderation action",
          duration: `${bounded(params.minutes, 1, 40320, 10)}m`,
        },
      );
      return { message: result.content };
    });
  actions.set("verification.publish", async ({ guild, actor, params }) => {
    needs(actor, P.ManageRoles, P.ManageChannels);
    const cfg = await guildSettings(
        store,
        "verification_settings.json",
        guild.id,
      ),
      channel = await targetChannel(
        guild,
        actor,
        params.channel_id || cfg.channel_id,
      );
    const role = await guild.roles.fetch(
        snowflake(params.role_id || cfg.role_id, "verified role ID"),
      ),
      removeId = params.remove_role_id || cfg.remove_role_id,
      remove = removeId
        ? await guild.roles.fetch(snowflake(removeId, "removal role ID"))
        : null,
      bot = await guild.members.fetchMe({ force: true });
    assignableRole(actor, role, bot);
    if (remove) assignableRole(actor, remove, bot);
    if (remove?.id === role.id)
      throw new UserError("Verified and removal roles must differ.");
    const message = await app.members.verificationPanel(
      guild,
      channel,
      role,
      remove,
      { ...cfg, ...params },
    );
    return { message: "Verification panel published.", message_id: message.id };
  });
  for (const action of [
    "ticket.panel",
    "ticket.panel.load",
    "ticket.panel.update",
  ])
    actions.set(action, async ({ guild, actor, params }) => {
      needs(actor, P.ManageMessages);
      const panels = await app.tickets.panels(guild.id);
      let id, panel, channel;
      if (action !== "ticket.panel") {
        const messageId = snowflake(params.message_id, "message ID");
        [id, panel] =
          Object.entries(panels).find(
            ([key, row]) =>
              String(row.message_id) === messageId &&
              (!params.panel_id ||
                key === String(params.panel_id).toUpperCase()),
          ) ?? [];
        if (!panel)
          throw new UserError(
            "That message is not a saved Rallybit ticket panel in this server.",
          );
        channel = await targetChannel(guild, actor, panel.channel_id, {
          write: action !== "ticket.panel.load",
        });
        const message = await channel.messages.fetch(messageId);
        if (message.author.id !== guild.client.user.id)
          throw new UserError("Rallybit can only edit its own ticket panels.");
        if (action === "ticket.panel.load")
          return {
            message: `Ticket panel ${id} loaded.`,
            panel: ticketPanelPayload(id, panel),
          };
      } else channel = await targetChannel(guild, actor, params.channel_id);
      const input = { ...params };
      delete input.channel_id;
      delete input.message_id;
      delete input.panel_id;
      input.options = (
        Array.isArray(params.options)
          ? params.options
          : [
              {
                name: params.name || "Support",
                description: params.option_description,
                emoji: params.option_emoji,
                category_id: params.category_id,
                support_role_id: params.support_role_id,
              },
            ]
      ).map((row) => ({
        ...row,
        enabled: asBoolean(row.enabled),
        support_role_ids: row.support_role_id
          ? [String(row.support_role_id)]
          : (row.support_role_ids ?? []),
      }));
      if (input.options.length < 1 || input.options.length > 25)
        throw new UserError("Choose between 1 and 25 ticket options.");
      for (const row of input.options) {
        const category = await guild.channels.fetch(
          snowflake(row.category_id, "category ID"),
        );
        if (category?.type !== ChannelType.GuildCategory)
          throw new UserError(
            `Choose a valid category for ${text(row.name, 100)}.`,
          );
        for (const roleId of row.support_role_ids)
          if (!(await guild.roles.fetch(snowflake(roleId, "support role ID"))))
            throw new UserError("A selected support role no longer exists.");
      }
      for (const key of [
        "show_author",
        "show_option_details",
        "show_workload",
        "show_guidance",
        "show_timestamp",
      ])
        if (key in input) input[key] = asBoolean(input[key]);
      const saved =
        action === "ticket.panel.update"
          ? await app.tickets.updatePanel(guild, id, input)
          : await app.tickets.createPanel(
              guild,
              channel,
              await guild.channels.fetch(input.options[0].category_id),
              input,
            );
      id ??= saved.panel_id;
      return {
        message:
          saved.warning ||
          `Ticket panel ${action === "ticket.panel.update" ? "updated" : "published"}.`,
        panel_id: id,
        panel: ticketPanelPayload(id, saved),
      };
    });
  actions.set("reactionrole.add", async (data) => {
    const { guild, actor, params } = data,
      channel = await targetChannel(guild, actor, params.channel_id, {
        write: false,
      }),
      role = await guild.roles.fetch(snowflake(params.role_id, "role ID"));
    return invoke(
      "reactionrole add",
      data,
      {
        channel,
        role,
        emoji: String(params.emoji || ""),
        message_id: snowflake(params.message_id, "message ID"),
      },
      channel,
    );
  });
  actions.set("level.setxp", async (data) => {
    const { guild, params } = data,
      member = await guild.members.fetch({
        user: snowflake(params.user_id, "member ID"),
        force: true,
      });
    return invoke("level setxp", data, {
      member: member.user,
      xp: bounded(params.xp, 0, 100000000, 0),
    });
  });
  actions.set("automation.run", async ({ guild, actor, params }) => {
    needs(actor, P.ManageGuild);
    const id = text(params.schedule_id, 40).toUpperCase(),
      row = (await store.read("automation_schedules.json"))[guild.id]?.[id];
    if (!row) throw new UserError("Schedule not found.");
    const channel = await targetChannel(guild, actor, row.channel_id);
    const result = await app.campaigns.runSchedule(guild, id, row, {
      manualId: shortId(),
    });
    return {
      message:
        result.status === "sent"
          ? result.result
          : `Schedule not sent: ${result.status}.`,
      channel_id: channel.id,
    };
  });
  if (
    DASHBOARD_ACTIONS.some((name) => !actions.has(name)) ||
    actions.size !== DASHBOARD_ACTIONS.length
  )
    throw new Error("Incomplete dashboard migration");
  for (const [name, handler] of actions)
    actions.set(name, async (data) => {
      await assertAllowed(store, data.guild.id, data.actor.id);
      app.assertOwner?.();
      return handler(data);
    });
  return actions;
}
