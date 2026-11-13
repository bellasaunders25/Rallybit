import { AuditLogEvent, Events, PermissionFlagsBits as P } from "discord.js";
import { UserError } from "../access.js";
import { embed, guildSettings, text, nowISO } from "../helpers.js";

export const AUDIT_TYPES = [
  "commands",
  "configuration",
  "moderation",
  "members",
  "messages",
  "roles",
  "channels",
  "voice",
  "tickets",
  "reports",
  "staff",
  "security",
];
export class LoggingService {
  #bound = false;
  constructor(services) {
    Object.assign(this, services);
  }
  async emit(
    guild,
    type,
    title,
    description,
    { actor, target = "", channel } = {},
  ) {
    if (!guild) return;
    const security =
        (await this.store.read("security_settings.json"))[guild.id] ?? {},
      flag = {
        members: "member_events",
        messages: "message_events",
        voice: "voice_events",
        roles: "structure_events",
        channels: "structure_events",
        configuration: "structure_events",
      }[type];
    const logSecurity =
      security.enabled !== false && flag && security.event_logging?.[flag];
    if (logSecurity)
      await this.store.update("security_history.json", (data) => {
        data[guild.id] = [
          {
            timestamp: nowISO(),
            module: "event_log",
            action: title,
            target_id: /^\d+$/.test(String(target)) ? String(target) : null,
            actor_id: actor?.id ?? null,
            reason: text(description, 1000),
          },
          ...(data[guild.id] ?? []),
        ].slice(0, 250);
      });
    await this.audit(
      {
        guild,
        guildId: guild.id,
        user: actor,
        channel,
        channelId: channel?.id,
      },
      type,
      title,
      description,
      target,
      logSecurity ? security.log_channel_id : null,
    );
  }
  async actor(guild, type, target) {
    if (!guild.members.me?.permissions.has(P.ViewAuditLog)) return null;
    try {
      const logs = await guild.fetchAuditLogs({ type, limit: 5 });
      return (
        [...logs.entries.values()].find(
          (row) =>
            row.targetId === target &&
            Math.abs(Date.now() - row.createdTimestamp) < 15000,
        )?.executor ?? null
      );
    } catch {
      return null;
    }
  }
  bind(client, assertOwner = () => {}) {
    if (this.#bound) throw new Error("Audit listeners already installed");
    this.#bound = true;
    const on = (event, handler) => {
      if (!event) throw new Error("Unknown Discord event");
      client.on(event, async (...args) => {
        try {
          assertOwner();
          await handler(...args);
        } catch (error) {
          console.warn(
            JSON.stringify({
              event: "audit_event_failed",
              name: error.name,
              code: error.code,
            }),
          );
        }
      });
    };
    on(Events.MessageDelete, (m) =>
      this.emit(
        m.guild,
        "messages",
        "Message deleted",
        `Channel: <#${m.channelId}>\nMessage: ${m.id}\n${text(m.content || "Content was not cached.", 1800)}`,
        { target: m.author?.id ?? m.id, channel: m.channel },
      ),
    );
    on(Events.MessageBulkDelete, (messages, channel) =>
      this.emit(
        channel.guild,
        "messages",
        "Messages bulk deleted",
        `${messages.size} messages were deleted in <#${channel.id}>.`,
        { channel },
      ),
    );
    on(Events.MessageUpdate, (before, after) =>
      before.content === after.content
        ? null
        : this.emit(
            after.guild,
            "messages",
            "Message edited",
            `Before: ${text(before.content || "Not cached", 1700)}\nAfter: ${text(after.content || "No text", 1700)}\n${after.url}`,
            { actor: after.author, target: after.id, channel: after.channel },
          ),
    );
    on(Events.GuildMemberAdd, (m) =>
      this.emit(
        m.guild,
        "members",
        "Member joined",
        `<@${m.id}> joined the server.`,
        { actor: m.user, target: m.id },
      ),
    );
    on(Events.GuildMemberRemove, (m) =>
      this.emit(
        m.guild,
        "members",
        "Member left",
        `<@${m.id}> left or was removed from the server.`,
        { target: m.id },
      ),
    );
    on(Events.GuildMemberUpdate, async (before, after) => {
      const changes = [];
      if (before.nickname !== after.nickname)
        changes.push(
          `Nickname: ${text(before.nickname || "None", 100)} → ${text(after.nickname || "None", 100)}`,
        );
      if (
        before.roles.cache
          .map((r) => r.id)
          .sort()
          .join(",") !==
        after.roles.cache
          .map((r) => r.id)
          .sort()
          .join(",")
      )
        changes.push(
          `Roles: ${before.roles.cache.map((r) => r.id).join(", ")} → ${after.roles.cache.map((r) => r.id).join(", ")}`,
        );
      if (
        before.communicationDisabledUntilTimestamp !==
        after.communicationDisabledUntilTimestamp
      )
        changes.push(
          `Timeout: ${after.communicationDisabledUntil?.toISOString() ?? "None"}`,
        );
      if (changes.length)
        await this.emit(
          after.guild,
          "members",
          "Member updated",
          text(changes.join("\n"), 3500),
          { target: after.id },
        );
    });
    for (const [event, type, title] of [
      [Events.GuildBanAdd, AuditLogEvent.MemberBanAdd, "Member banned"],
      [Events.GuildBanRemove, AuditLogEvent.MemberBanRemove, "Member unbanned"],
    ])
      on(event, async (ban) =>
        this.emit(
          ban.guild,
          "moderation",
          title,
          `${ban.user.tag ?? ban.user.username} (${ban.user.id})`,
          {
            actor: await this.actor(ban.guild, type, ban.user.id),
            target: ban.user.id,
          },
        ),
      );
    for (const [event, type, title] of [
      [Events.GuildRoleCreate, AuditLogEvent.RoleCreate, "Role created"],
      [Events.GuildRoleDelete, AuditLogEvent.RoleDelete, "Role deleted"],
    ])
      on(event, async (role) =>
        this.emit(role.guild, "roles", title, `${role.name} (${role.id})`, {
          actor: await this.actor(role.guild, type, role.id),
          target: role.name,
        }),
      );
    on(Events.GuildRoleUpdate, (before, after) =>
      this.emit(
        after.guild,
        "roles",
        "Role updated",
        `${before.name} → ${after.name}\nPermissions: ${before.permissions.bitfield} → ${after.permissions.bitfield}\nPosition: ${before.position} → ${after.position}`,
        { target: after.name },
      ),
    );
    for (const [event, title] of [
      [Events.ChannelCreate, "Channel created"],
      [Events.ChannelDelete, "Channel deleted"],
      [Events.ThreadCreate, "Thread created"],
      [Events.ThreadDelete, "Thread deleted"],
    ])
      on(event, (channel) =>
        this.emit(
          channel.guild,
          "channels",
          title,
          `${channel.name} (${channel.id})`,
          { target: channel.name, channel },
        ),
      );
    for (const event of [Events.ChannelUpdate, Events.ThreadUpdate])
      on(event, (before, after) =>
        this.emit(
          after.guild,
          "channels",
          "Channel updated",
          `${before.name} → ${after.name}\nChannel ID: ${after.id}\nParent: ${before.parentId ?? "None"} → ${after.parentId ?? "None"}`,
          { target: after.name, channel: after },
        ),
      );
    on(Events.VoiceStateUpdate, (before, after) => {
      if (
        before.channelId === after.channelId &&
        before.selfMute === after.selfMute &&
        before.selfDeaf === after.selfDeaf &&
        before.serverMute === after.serverMute &&
        before.serverDeaf === after.serverDeaf
      )
        return;
      return this.emit(
        after.guild,
        "voice",
        "Voice state changed",
        `<@${after.id}>: ${before.channelId ? `<#${before.channelId}>` : "Not connected"} → ${after.channelId ? `<#${after.channelId}>` : "Disconnected"}\nMuted: ${after.mute} · Deafened: ${after.deaf}`,
        {
          actor: after.member?.user,
          target: after.id,
          channel: after.channel ?? before.channel,
        },
      );
    });
    on(Events.WebhooksUpdate, (channel) =>
      this.emit(
        channel.guild,
        "configuration",
        "Webhooks updated",
        `Webhook configuration changed in <#${channel.id}>.`,
        { channel },
      ),
    );
    for (const [event, title] of [
      [Events.InviteCreate, "Invite created"],
      [Events.InviteDelete, "Invite deleted"],
    ])
      on(event, (invite) =>
        this.emit(
          invite.guild,
          "security",
          title,
          `Code: ${text(invite.code, 100)}`,
          {
            actor: invite.inviter,
            target: `discord.gg/${invite.code}`,
            channel: invite.channel,
          },
        ),
      );
    for (const [event, title] of [
      [Events.GuildEmojiCreate, "Emoji created"],
      [Events.GuildEmojiDelete, "Emoji deleted"],
    ])
      on(event, (emoji) =>
        this.emit(
          emoji.guild,
          "configuration",
          title,
          `${emoji.name} (${emoji.id})`,
        ),
      );
    on(Events.GuildEmojiUpdate, (before, after) =>
      this.emit(
        after.guild,
        "configuration",
        "Emoji updated",
        `${before.name} → ${after.name} (${after.id})`,
      ),
    );
    for (const [event, title] of [
      [Events.GuildStickerCreate, "Sticker created"],
      [Events.GuildStickerDelete, "Sticker deleted"],
    ])
      on(event, (row) =>
        this.emit(row.guild, "configuration", title, `${row.name} (${row.id})`),
      );
    on(Events.GuildStickerUpdate, (before, after) =>
      this.emit(
        after.guild,
        "configuration",
        "Sticker updated",
        `${before.name} → ${after.name} (${after.id})`,
      ),
    );
    on(Events.GuildUpdate, (before, after) => {
      const changes = [
        "name",
        "description",
        "verificationLevel",
        "defaultMessageNotifications",
        "explicitContentFilter",
      ]
        .filter((key) => before[key] !== after[key])
        .map(
          (key) =>
            `${key}: ${text(before[key], 200)} → ${text(after[key], 200)}`,
        );
      if (changes.length)
        return this.emit(
          after,
          "configuration",
          "Server settings updated",
          changes.join("\n"),
        );
    });
    for (const [event, title] of [
      [Events.GuildScheduledEventCreate, "Scheduled event created"],
      [Events.GuildScheduledEventDelete, "Scheduled event deleted"],
    ])
      on(event, (row) =>
        this.emit(row.guild, "configuration", title, `${row.name} (${row.id})`),
      );
    on(Events.GuildScheduledEventUpdate, (before, after) =>
      this.emit(
        after.guild,
        "configuration",
        "Scheduled event updated",
        `${before.name} → ${after.name}\nStatus: ${before.status} → ${after.status}`,
      ),
    );
  }
}
export function registerLogging(registry, services) {
  const service = new LoggingService(services),
    policy = { guildOnly: true, permissions: ["ManageGuild"] };
  const validEvent = (i, allowDefault = false) => {
    const event = i.options.getString("event", true);
    if (!(AUDIT_TYPES.includes(event) || (allowDefault && event === "default")))
      throw new UserError("Unknown logging category.");
    return event;
  };
  registry.register(
    "logs channel",
    async (i) => {
      const event = validEvent(i, true),
        channel = i.options.getChannel("channel");
      if (channel && (!channel.send || channel.guild.id !== i.guildId))
        throw new UserError("Choose a text channel in this server.");
      await services.store.update("audit_settings.json", (data) => {
        const settings = (data[i.guildId] ??= {});
        if (event === "default")
          settings.default_channel_id = channel?.id ?? null;
        else {
          settings.channel_ids ??= {};
          if (channel) settings.channel_ids[event] = channel.id;
          else delete settings.channel_ids[event];
        }
      });
      return {
        content: `${event} logging destination: ${channel ? `<#${channel.id}>` : "Removed; event categories inherit the default."}`,
      };
    },
    policy,
  );
  registry.register(
    "logs toggle",
    async (i) => {
      const event = validEvent(i),
        enabled = i.options.getBoolean("enabled", true);
      await services.store.update("audit_settings.json", (data) => {
        const settings = (data[i.guildId] ??= {});
        (settings.enabled_events ??= {})[event] = enabled;
      });
      return {
        content: `${event} logging ${enabled ? "enabled" : "disabled"}.`,
      };
    },
    policy,
  );
  registry.register(
    "logs overview",
    async (i) => {
      const settings = await guildSettings(
        services.store,
        "audit_settings.json",
        i.guildId,
      );
      return {
        embeds: [
          embed(
            "Logging overview",
            AUDIT_TYPES.map(
              (type) =>
                `${settings.enabled !== false && settings.enabled_events?.[type] !== false ? "Enabled" : "Disabled"} · **${type}** · ${settings.channel_ids?.[type] || settings.default_channel_id ? `<#${settings.channel_ids?.[type] || settings.default_channel_id}>` : "Not configured"}`,
            ).join("\n"),
          ),
        ],
      };
    },
    policy,
  );
  registry.register(
    "logs test",
    async (i) => {
      const event = validEvent(i);
      await services.audit(
        i,
        event,
        "Rallybit logging test",
        "This is a test event requested by server staff.",
      );
      return {
        content:
          "Test recorded. Delivery follows the selected category’s enabled state and channel configuration.",
      };
    },
    policy,
  );
  return service;
}
