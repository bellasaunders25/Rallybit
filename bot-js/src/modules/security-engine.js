import {
  AuditLogEvent,
  ChannelType,
  Events,
  PermissionFlagsBits as P,
} from "discord.js";
import { UserError, canModerate } from "../access.js";
import {
  embed,
  KeyedQueue,
  nowISO,
  shortId,
  text,
  targetMembers,
} from "../helpers.js";
import { assignableRole } from "./member-tools.js";

export const MODULE_DEFAULTS = {
  anti_bot: { enabled: false, punishment: "kick", ban_added_bot: true },
  anti_webhook: {
    enabled: false,
    punishment: "strip",
    delete_created_webhook: true,
  },
  anti_guild_update: { enabled: false, punishment: "strip", revert: true },
  anti_integration: { enabled: false, punishment: "strip" },
  anti_dangerous_permissions: {
    enabled: false,
    punishment: "strip",
    revert: true,
  },
  mass_ban: { enabled: false, punishment: "strip", count: 5, seconds: 60 },
  mass_kick: { enabled: false, punishment: "strip", count: 5, seconds: 60 },
  mass_channel_delete: {
    enabled: false,
    punishment: "strip",
    count: 3,
    seconds: 60,
  },
  mass_role_delete: {
    enabled: false,
    punishment: "strip",
    count: 3,
    seconds: 60,
  },
  anti_emoji_delete: {
    enabled: false,
    punishment: "strip",
    count: 5,
    seconds: 60,
  },
  anti_sticker_delete: {
    enabled: false,
    punishment: "strip",
    count: 5,
    seconds: 60,
  },
  anti_spam: {
    enabled: false,
    message_limit: 6,
    window_seconds: 5,
    timeout_minutes: 5,
  },
  automod: { enabled: false, block_invites: false, bad_words: [] },
  age_gate: {
    enabled: false,
    min_days: 7,
    dm_member: true,
    appeal_url: "",
    dm_message: "",
  },
  scam_trap: {
    enabled: false,
    channel_id: null,
    channel_name: "do-not-text-here",
    action: "ban",
    appeal_url: "",
    warning_message: "",
    bypass_trusted_roles: false,
    delete_message_seconds: 604800,
  },
};
export const DANGEROUS = [
  "Administrator",
  "ManageGuild",
  "ManageRoles",
  "ManageChannels",
  "BanMembers",
  "KickMembers",
  "ManageWebhooks",
].reduce((bits, key) => bits | P[key], 0n);
const LOCK_BITS = [
  "SendMessages",
  "AddReactions",
  "Connect",
  "CreatePublicThreads",
  "CreatePrivateThreads",
  "SendMessagesInThreads",
].reduce((bits, key) => bits | P[key], 0n);
const THRESHOLDS = [
  "mass_ban",
  "mass_kick",
  "mass_channel_delete",
  "mass_role_delete",
  "anti_emoji_delete",
  "anti_sticker_delete",
];
const AUDIT_MODULES = new Map([
  [AuditLogEvent.MemberBanAdd, "mass_ban"],
  [AuditLogEvent.MemberKick, "mass_kick"],
  [AuditLogEvent.ChannelDelete, "mass_channel_delete"],
  [AuditLogEvent.RoleDelete, "mass_role_delete"],
  [AuditLogEvent.EmojiDelete, "anti_emoji_delete"],
  [AuditLogEvent.StickerDelete, "anti_sticker_delete"],
  [AuditLogEvent.WebhookCreate, "anti_webhook"],
  [AuditLogEvent.WebhookUpdate, "anti_webhook"],
  [AuditLogEvent.WebhookDelete, "anti_webhook"],
  [AuditLogEvent.IntegrationCreate, "anti_integration"],
]);
export function securitySettings(saved = {}) {
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) saved = {};
  const settings = {
    enabled: true,
    log_channel_id: null,
    trusted_role_ids: [],
    bypass_administrators: false,
    dm_server_owner: false,
    event_logging: {},
    ...saved,
    modules: {},
  };
  for (const [key, defaults] of Object.entries(MODULE_DEFAULTS))
    settings.modules[key] = {
      ...structuredClone(defaults),
      ...saved.modules?.[key],
    };
  if (!saved.modules?.age_gate && saved.agegate)
    Object.assign(settings.modules.age_gate, {
      enabled: Boolean(saved.agegate.enabled),
      min_days: saved.agegate.minimum_days ?? 7,
      dm_member: saved.agegate.dm_member !== false,
    });
  if (!saved.modules?.scam_trap && saved.trap)
    Object.assign(settings.modules.scam_trap, saved.trap);
  return settings;
}
/** Reconcile the legacy PHP fields only when that representation was edited. */
export function reconcileSecurityWrite(incoming, previous = {}) {
  const next = structuredClone(incoming);
  for (const [id, row] of Object.entries(next)) {
    if (!row || typeof row !== "object" || Array.isArray(row))
      throw new UserError("Invalid server security settings.");
    const before = previous[id] ?? {};
    row.modules ??= {};
    if (
      row.agegate &&
      JSON.stringify(row.agegate) !== JSON.stringify(before.agegate)
    ) {
      row.modules.age_gate = {
        ...row.modules.age_gate,
        enabled: Boolean(row.agegate.enabled),
        min_days: Math.max(
          0,
          Math.min(3650, Number(row.agegate.minimum_days) || 0),
        ),
        dm_member: row.agegate.dm_member !== false,
      };
    } else if (row.modules.age_gate) {
      row.agegate = {
        ...row.agegate,
        enabled: Boolean(row.modules.age_gate.enabled),
        minimum_days: row.modules.age_gate.min_days ?? 7,
        dm_member: row.modules.age_gate.dm_member !== false,
      };
    }
    if (row.trap && JSON.stringify(row.trap) !== JSON.stringify(before.trap))
      row.modules.scam_trap = { ...row.modules.scam_trap, ...row.trap };
    else if (row.modules.scam_trap)
      row.trap = { ...row.trap, ...row.modules.scam_trap };
  }
  return next;
}
export class SecurityService {
  constructor(services) {
    Object.assign(this, services);
    this.queue = new KeyedQueue();
    this.spam = new Map();
    this.counters = new Map();
    this.bound = false;
  }
  async settings(id) {
    return securitySettings(
      (await this.store.read("security_settings.json"))[id],
    );
  }
  async update(id, change) {
    await this.store.update("security_settings.json", (data) => {
      const settings = securitySettings(data[id]);
      change(settings);
      settings.agegate = {
        ...settings.agegate,
        enabled: settings.modules.age_gate.enabled,
        minimum_days: settings.modules.age_gate.min_days,
        dm_member: settings.modules.age_gate.dm_member,
      };
      settings.trap = { ...settings.modules.scam_trap };
      data[id] = settings;
    });
  }
  async trusted(guild, user, settings, trap = false) {
    if (!user) return false;
    if ([guild.ownerId, guild.client.user.id].includes(user.id)) return true;
    if (trap && !settings.modules.scam_trap.bypass_trusted_roles) return false;
    const member = await guild.members
      .fetch({ user: user.id, force: true })
      .catch((error) => {
        if (error.code === 10007) return null;
        throw error;
      });
    if (!member) return false;
    return (
      (!trap &&
        settings.bypass_administrators &&
        member.permissions.has(P.Administrator)) ||
      (settings.trusted_role_ids ?? []).some((id) =>
        member.roles.cache.has(String(id)),
      )
    );
  }
  async record(guild, module, action, target, actor, reason) {
    const row = {
      timestamp: nowISO(),
      module,
      action,
      target_id: target?.id ?? null,
      target_name: target?.tag ?? target?.username ?? "",
      actor_id: actor?.id ?? null,
      actor_name: actor?.tag ?? actor?.username ?? "",
      reason: text(reason, 1000),
    };
    await this.store.update("security_history.json", (data) => {
      data[guild.id] = [row, ...(data[guild.id] ?? [])].slice(0, 250);
    });
    const settings = await this.settings(guild.id);
    await this.audit(
      { guild, guildId: guild.id, user: actor },
      "security",
      `${module}: ${action}`,
      row.reason,
      target?.id ?? "",
      settings.log_channel_id,
    );
    if (settings.dm_server_owner) {
      const owner = await guild.fetchOwner();
      await owner
        .send({
          embeds: [
            embed("Security alert", `${module}: ${action}\n${row.reason}`),
          ],
          allowedMentions: { parse: [] },
        })
        .catch(() => {});
    }
  }
  async botTarget(guild, id, permission) {
    const [member, bot] = await Promise.all([
      guild.members.fetch({ user: id, force: true }),
      guild.members.fetchMe({ force: true }),
    ]);
    if (
      [guild.ownerId, bot.id].includes(id) ||
      member.roles.highest.comparePositionTo(bot.roles.highest) >= 0
    )
      throw new UserError(
        "That account is protected by Discord role hierarchy.",
      );
    if (!bot.permissions.has(permission))
      throw new UserError("Rallybit lacks the required moderation permission.");
    return { member, bot };
  }
  async quarantineRole(guild) {
    const roles = await guild.roles.fetch();
    let role = [...roles.values()].find(
      (r) => r.name === "Rallybit Quarantined" && !r.managed,
    );
    if (!role)
      role = await guild.roles.create({
        name: "Rallybit Quarantined",
        permissions: 0n,
        reason: "Rallybit security quarantine",
      });
    for (const channel of (await guild.channels.fetch()).values())
      if (channel) {
        const denied = [
          ChannelType.GuildVoice,
          ChannelType.GuildStageVoice,
        ].includes(channel.type)
          ? { Connect: false, Speak: false }
          : {
              SendMessages: false,
              AddReactions: false,
              CreatePublicThreads: false,
              CreatePrivateThreads: false,
              SendMessagesInThreads: false,
            };
        await channel.permissionOverwrites.edit(role.id, denied, {
          reason: "Rallybit quarantine permissions",
        });
      }
    return role;
  }
  async quarantine(guild, id, actor, reason) {
    return this.queue.run(`quarantine:${guild.id}:${id}`, async () => {
      const { member, bot } = await this.botTarget(guild, id, P.ManageRoles),
        role = await this.quarantineRole(guild);
      const removable = [...member.roles.cache.values()].filter(
        (r) =>
          r.id !== guild.id &&
          r.id !== role.id &&
          !r.managed &&
          r.comparePositionTo(bot.roles.highest) < 0,
      );
      await this.store.update("security_quarantine.json", (data) => {
        const previous = (data[guild.id] ??= {})[id];
        data[guild.id][id] = {
          roles: [
            ...new Set([
              ...(previous?.roles ?? []).map(String),
              ...removable.map((r) => r.id),
            ]),
          ],
          actor_id: actor?.id ?? null,
          reason: text(reason),
          timestamp: previous?.timestamp ?? nowISO(),
          status: "pending",
        };
      });
      // Saved original roles precede removals, so an interrupted quarantine is
      // always recoverable rather than overwriting the saved list with empties.
      if (removable.length) await member.roles.remove(removable, reason);
      await member.roles.add(role, reason);
      await this.store.update("security_quarantine.json", (data) => {
        data[guild.id][id].status = "quarantined";
      });
      return `Quarantined <@${id}>; ${removable.length} manageable roles saved.`;
    });
  }
  async release(guild, id, actor, reason) {
    return this.queue.run(`quarantine:${guild.id}:${id}`, async () => {
      const { member, bot } = await this.botTarget(guild, id, P.ManageRoles),
        row = (await this.store.read("security_quarantine.json"))[guild.id]?.[
          id
        ];
      if (!row) throw new UserError("No saved quarantine record was found.");
      const fresh = await guild.members.fetch({ user: actor.id, force: true }),
        restore = [];
      for (const roleId of row.roles ?? []) {
        const role = await guild.roles.fetch(String(roleId));
        if (!role || role.managed || role.id === guild.id) continue;
        assignableRole(fresh, role, bot);
        restore.push(role);
      }
      if (restore.length) await member.roles.add(restore, reason);
      const quarantine = [...guild.roles.cache.values()].find(
        (r) => r.name === "Rallybit Quarantined" && !r.managed,
      );
      if (quarantine && member.roles.cache.has(quarantine.id))
        await member.roles.remove(quarantine, reason);
      await this.store.update("security_quarantine.json", (data) => {
        delete data[guild.id]?.[id];
      });
      return `Released <@${id}> and restored ${restore.length} roles.`;
    });
  }
  async punish(guild, target, punishment, reason, module, actor = null) {
    const settings = await this.settings(guild.id);
    if (!settings.enabled || !settings.modules[module]?.enabled) return false;
    if (await this.trusted(guild, target, settings, module === "scam_trap"))
      return false;
    if (
      !(await this.store.reserve(
        "security_dispatch.json",
        `${guild.id}:${target.id}:${module}`,
        30000,
      ))
    )
      return false;
    if (punishment === "strip")
      await this.quarantine(guild, target.id, actor, reason);
    else {
      const { member } = await this.botTarget(
        guild,
        target.id,
        punishment === "ban" ? P.BanMembers : P.KickMembers,
      );
      if (punishment === "ban")
        await member.ban({ reason, deleteMessageSeconds: 0 });
      else if (punishment === "kick") await member.kick(reason);
      else throw new UserError("Unsupported security punishment.");
    }
    await this.record(guild, module, punishment, target, actor, reason);
    return true;
  }
  async lockdown(guild, enabled, actor) {
    return this.queue.run(`lockdown:${guild.id}`, async () => {
      const role = await guild.roles.fetch(guild.id),
        saved = (await this.store.read("security_lockdown.json"))[guild.id];
      if (enabled) {
        if (!saved)
          await this.store.update("security_lockdown.json", (data) => {
            data[guild.id] = {
              permissions: String(role.permissions.bitfield),
              timestamp: nowISO(),
              actor_id: actor.id,
            };
          });
        await role.setPermissions(
          role.permissions.bitfield & ~LOCK_BITS,
          `Rallybit lockdown by ${actor.id}`,
        );
      } else {
        if (!saved) throw new UserError("No saved lockdown state exists.");
        const original = BigInt(saved.permissions);
        await role.setPermissions(
          (role.permissions.bitfield & ~LOCK_BITS) | (original & LOCK_BITS),
          `Rallybit lockdown restored by ${actor.id}`,
        );
        await this.store.update("security_lockdown.json", (data) => {
          delete data[guild.id];
        });
      }
      await this.record(
        guild,
        "lockdown",
        enabled ? "Server locked" : "Server unlocked",
        null,
        actor,
        "Manual administrator command.",
      );
    });
  }
  async trap(guild, input = {}) {
    const settings = await this.settings(guild.id),
      config = { ...settings.modules.scam_trap, ...input };
    if (!["ban", "kick"].includes(config.action))
      throw new UserError("Choose ban or kick.");
    let channel = config.channel_id
      ? await guild.channels.fetch(String(config.channel_id)).catch((error) => {
          if (error.code === 10003) return null;
          throw error;
        })
      : null;
    const bot = await guild.members.fetchMe({ force: true });
    if (
      !bot.permissions.has(P.ManageChannels) ||
      !bot.permissions.has(
        config.action === "ban" ? P.BanMembers : P.KickMembers,
      )
    )
      throw new UserError(
        "Rallybit needs Manage Channels and the selected punishment permission.",
      );
    const name =
      text(
        config.channel_name
          .toLowerCase()
          .replace(/[^a-z0-9-]/g, "-")
          .replace(/-+/g, "-")
          .replace(/^-|-$/g, ""),
        100,
      ) || "do-not-text-here";
    if (!channel)
      channel = await guild.channels.create({
        name,
        type: ChannelType.GuildText,
        topic: "Rallybit security honeypot: do not send messages here.",
        permissionOverwrites: [
          {
            id: guild.id,
            allow: [P.ViewChannel, P.SendMessages, P.ReadMessageHistory],
            deny: [
              P.AddReactions,
              P.CreatePublicThreads,
              P.CreatePrivateThreads,
              P.SendMessagesInThreads,
              P.UseApplicationCommands,
            ],
          },
          {
            id: bot.id,
            allow: [
              P.ViewChannel,
              P.SendMessages,
              P.ReadMessageHistory,
              P.ManageMessages,
              P.ManageChannels,
            ],
          },
        ],
        reason: "Rallybit security trap setup",
      });
    else {
      await channel.setName(name, "Rallybit security trap repair");
      await channel.permissionOverwrites.edit(
        guild.id,
        {
          ViewChannel: true,
          SendMessages: true,
          ReadMessageHistory: true,
          AddReactions: false,
          CreatePublicThreads: false,
          CreatePrivateThreads: false,
          SendMessagesInThreads: false,
          UseApplicationCommands: false,
        },
        { reason: "Rallybit security trap repair" },
      );
    }
    const warning = `# SECURITY TRAP — DO NOT SEND MESSAGES HERE\nThis is a security honeypot, not a chat channel. Posting here triggers an automatic ${config.action}. If your account is compromised, this helps staff contain it.${config.warning_message ? `\n\n${text(config.warning_message, 1000)}` : ""}${config.appeal_url ? `\nAppeal: ${text(config.appeal_url, 500)}` : ""}`;
    const message = await channel.send({
      content: warning,
      allowedMentions: { parse: [] },
    });
    await message.pin("Rallybit security trap warning").catch(() => {});
    await this.update(guild.id, (cfg) => {
      cfg.modules.scam_trap = {
        ...config,
        enabled: true,
        channel_name: name,
        channel_id: channel.id,
        warning_message_id: message.id,
      };
    });
    return channel;
  }
  async onJoin(member) {
    const settings = await this.settings(member.guild.id);
    if (!settings.enabled) return true;
    if (member.user.bot) {
      const config = settings.modules.anti_bot;
      if (!config.enabled) return true;
      const actor = await this.recentActor(
        member.guild,
        AuditLogEvent.BotAdd,
        member.id,
      );
      if (!actor || (await this.trusted(member.guild, actor, settings)))
        return true;
      if (config.ban_added_bot)
        await this.punish(
          member.guild,
          member.user,
          "ban",
          "Unauthorised bot addition.",
          "anti_bot",
          actor,
        );
      await this.punish(
        member.guild,
        actor,
        config.punishment,
        `Unauthorised bot added: ${member.id}`,
        "anti_bot",
        actor,
      );
      return false;
    }
    const gate = settings.modules.age_gate,
      days = Math.max(
        0,
        Math.floor((Date.now() - member.user.createdTimestamp) / 86400000),
      );
    if (gate.enabled && days < Math.max(0, Number(gate.min_days) || 0)) {
      const reason = `Account age ${days} days; minimum ${gate.min_days} days.`;
      if (gate.dm_member)
        await member
          .send({
            content: text(
              gate.dm_message ||
                `Your account is too new to join **${member.guild.name}**. ${reason}${gate.appeal_url ? `\nAppeal: ${gate.appeal_url}` : ""}`,
              2000,
            ),
            allowedMentions: { parse: [] },
          })
          .catch(() => {});
      await this.punish(member.guild, member.user, "kick", reason, "age_gate");
      return false;
    }
    return true;
  }
  async onMessage(message) {
    if (!message.guild || message.author.bot || !message.member) return true;
    const settings = await this.settings(message.guildId);
    if (!settings.enabled) return true;
    const modules = settings.modules;
    const trap = modules.scam_trap;
    if (trap.enabled && String(trap.channel_id) === message.channelId) {
      if (await this.trusted(message.guild, message.author, settings, true)) {
        await message.delete();
        return false;
      }
      await message.delete().catch(() => {});
      await this.punish(
        message.guild,
        message.author,
        trap.action,
        "Posted in the clearly labelled security trap channel.",
        "scam_trap",
      );
      return false;
    }
    if (await this.trusted(message.guild, message.author, settings))
      return true;
    const spam = modules.anti_spam,
      key = `${message.guildId}:${message.author.id}`,
      now = Date.now();
    if (spam.enabled) {
      const window =
          Math.max(2, Math.min(60, Number(spam.window_seconds) || 5)) * 1000,
        rows = (this.spam.get(key) ?? []).filter(
          (stamp) => stamp > now - window,
        );
      rows.push(now);
      this.spam.set(key, rows);
      if (rows.length >= Math.max(3, Number(spam.message_limit) || 6)) {
        this.spam.delete(key);
        if (
          await this.store.reserve(
            "security_dispatch.json",
            `spam:${key}`,
            30000,
          )
        ) {
          const { member } = await this.botTarget(
            message.guild,
            message.author.id,
            P.ModerateMembers,
          );
          await member.timeout(
            Math.min(40320, Math.max(1, Number(spam.timeout_minutes) || 5)) *
              60000,
            "Rallybit anti-spam",
          );
          await message.delete().catch(() => {});
          await this.record(
            message.guild,
            "anti_spam",
            "Timed out",
            message.author,
            null,
            `Message rate exceeded in ${window / 1000} seconds.`,
          );
        }
        return false;
      }
    }
    const automod = modules.automod,
      content = String(message.content).normalize("NFKC").toLowerCase();
    let reason = "";
    if (automod.enabled) {
      if (
        automod.block_invites &&
        /(?:discord(?:app)?\.com\/invite\/|discord\.gg\/)[\w-]+/i.test(content)
      )
        reason = "Discord invite link";
      if (!reason)
        for (const term of automod.bad_words ?? []) {
          const safe = String(term)
            .normalize("NFKC")
            .toLowerCase()
            .replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          if (
            safe &&
            new RegExp(
              `(?<![\\p{L}\\p{N}_])${safe}(?![\\p{L}\\p{N}_])`,
              "u",
            ).test(content)
          ) {
            reason = "Configured blocked term";
            break;
          }
        }
      if (reason) {
        await message.delete();
        await this.record(
          message.guild,
          "automod",
          "Message removed",
          message.author,
          null,
          reason,
        );
        return false;
      }
    }
    return true;
  }
  async recentActor(guild, type, id) {
    try {
      const logs = await guild.fetchAuditLogs({ type, limit: 5 });
      return (
        [...logs.entries.values()].find(
          (row) =>
            String(row.targetId) === String(id) &&
            Math.abs(Date.now() - row.createdTimestamp) < 15000,
        )?.executor ?? null
      );
    } catch {
      return null;
    }
  }
  async onAudit(entry, guild) {
    const module = AUDIT_MODULES.get(entry.action);
    if (
      !module ||
      !entry.executorId ||
      Math.abs(Date.now() - entry.createdTimestamp) > 15000
    )
      return;
    const settings = await this.settings(guild.id),
      config = settings.modules[module];
    if (!settings.enabled || !config.enabled) return;
    const actor = entry.executor ?? { id: entry.executorId };
    if (await this.trusted(guild, actor, settings)) return;
    if (
      !(await this.store.reserve(
        "security_seen_events.json",
        String(entry.id),
        86400000,
      ))
    )
      return;
    if (THRESHOLDS.includes(module)) {
      const key = `${guild.id}:${module}:${actor.id}`,
        now = Date.now(),
        rows = (this.counters.get(key) ?? []).filter(
          (stamp) =>
            stamp > now - Math.max(2, Number(config.seconds) || 60) * 1000,
        );
      rows.push(now);
      this.counters.set(key, rows);
      if (rows.length < Math.max(1, Number(config.count) || 3)) return;
      this.counters.delete(key);
    }
    if (
      module === "anti_webhook" &&
      entry.action === AuditLogEvent.WebhookCreate &&
      config.delete_created_webhook &&
      entry.targetId
    ) {
      const hooks = await guild.fetchWebhooks();
      const hook = hooks.get(entry.targetId);
      if (hook) await hook.delete("Rallybit unauthorised webhook");
    }
    await this.punish(
      guild,
      actor,
      config.punishment,
      `Unauthorised audit action ${entry.action}.`,
      module,
      actor,
    );
  }
  async onGuildUpdate(before, after) {
    const settings = await this.settings(after.id),
      config = settings.modules.anti_guild_update;
    if (!settings.enabled || !config.enabled) return;
    const keys = [
        "name",
        "description",
        "verificationLevel",
        "explicitContentFilter",
        "defaultMessageNotifications",
      ],
      changed = keys.filter((key) => before[key] !== after[key]);
    if (!changed.length) return;
    const actor = await this.recentActor(
      after,
      AuditLogEvent.GuildUpdate,
      after.id,
    );
    if (!actor || (await this.trusted(after, actor, settings))) return;
    if (config.revert) {
      const latest = await after.fetch(),
        patch = {};
      for (const key of changed)
        if (latest[key] === after[key]) patch[key] = before[key];
      if (Object.keys(patch).length)
        await after.edit({
          ...patch,
          reason: "Rallybit unauthorised server update reverted",
        });
    }
    await this.punish(
      after,
      actor,
      config.punishment,
      `Changed server settings: ${changed.join(", ")}`,
      "anti_guild_update",
      actor,
    );
  }
  async onRoleUpdate(before, after) {
    const settings = await this.settings(after.guild.id),
      config = settings.modules.anti_dangerous_permissions;
    if (!settings.enabled || !config.enabled) return;
    const added =
      after.permissions.bitfield & ~before.permissions.bitfield & DANGEROUS;
    if (!added) return;
    const actor = await this.recentActor(
      after.guild,
      AuditLogEvent.RoleUpdate,
      after.id,
    );
    if (!actor || (await this.trusted(after.guild, actor, settings))) return;
    if (config.revert) {
      const latest = await after.guild.roles.fetch(after.id);
      await latest.setPermissions(
        latest.permissions.bitfield & ~added,
        "Rallybit unauthorised dangerous permission grant reverted",
      );
    }
    await this.punish(
      after.guild,
      actor,
      config.punishment,
      `Dangerous permissions added to role ${after.id}.`,
      "anti_dangerous_permissions",
      actor,
    );
  }
  async onMemberUpdate(before, after) {
    const settings = await this.settings(after.guild.id),
      config = settings.modules.anti_dangerous_permissions;
    if (!settings.enabled || !config.enabled) return;
    const added = [...after.roles.cache.values()].filter(
      (role) =>
        !before.roles.cache.has(role.id) &&
        (role.permissions.bitfield & DANGEROUS) !== 0n,
    );
    if (!added.length) return;
    const actor = await this.recentActor(
      after.guild,
      AuditLogEvent.MemberRoleUpdate,
      after.id,
    );
    if (!actor || (await this.trusted(after.guild, actor, settings))) return;
    if (config.revert) {
      const bot = await after.guild.members.fetchMe({ force: true });
      const removable = added.filter(
        (role) =>
          !role.managed && role.comparePositionTo(bot.roles.highest) < 0,
      );
      if (removable.length)
        await after.roles.remove(
          removable,
          "Rallybit dangerous role grant reverted",
        );
    }
    await this.punish(
      after.guild,
      actor,
      config.punishment,
      `Dangerous roles granted to ${after.id}.`,
      "anti_dangerous_permissions",
      actor,
    );
  }
  async tick(client, assertOwner = () => {}) {
    const jobs = await this.store.read("security_purge_jobs.json");
    for (const [id, row] of Object.entries(jobs)) {
      if (row.status !== "pending") continue;
      const guild = client.guilds.cache.get(String(row.guild_id));
      if (!guild) continue;
      assertOwner();
      try {
        const ban = await guild.bans
          .fetch(String(row.target_id))
          .catch((error) => {
            if (error.code === 10026) return null;
            throw error;
          });
        if (ban && ban.reason !== row.reason) {
          await this.store.update("security_purge_jobs.json", (data) => {
            data[id].status = "needs_review";
          });
          continue;
        }
        if (ban) {
          assertOwner();
          await guild.bans.remove(
            String(row.target_id),
            `Recover completed Rallybit purge ${id}`,
          );
        }
        await this.store.update("security_purge_jobs.json", (data) => {
          data[id].status = "completed";
        });
      } catch (error) {
        console.warn(
          JSON.stringify({
            event: "security_purge_recovery_failed",
            code: error.code,
          }),
        );
      }
    }
    for (const map of [this.spam, this.counters])
      for (const [key, rows] of map) {
        const current = rows.filter((time) => time > Date.now() - 3600000);
        if (current.length) map.set(key, current);
        else map.delete(key);
      }
    for (const name of [
      "security_dispatch.json",
      "security_seen_events.json",
    ]) {
      const values = await this.store.read(name);
      if (Object.values(values).some((expiry) => Number(expiry) < Date.now()))
        await this.store.update(name, (data) => {
          for (const [key, expiry] of Object.entries(data))
            if (Number(expiry) < Date.now()) delete data[key];
        });
    }
  }
  bind(client, assertOwner = () => {}) {
    if (this.bound) throw new Error("Security listeners already installed");
    this.bound = true;
    const on = (event, handler) =>
      client.on(event, async (...args) => {
        try {
          assertOwner();
          await handler(...args);
        } catch (error) {
          console.warn(
            JSON.stringify({
              event: "security_event_failed",
              name: error.name,
              code: error.code,
            }),
          );
        }
      });
    on(Events.GuildAuditLogEntryCreate, (entry, guild) =>
      this.onAudit(entry, guild),
    );
    on(Events.GuildUpdate, (before, after) =>
      this.onGuildUpdate(before, after),
    );
    on(Events.GuildRoleUpdate, (before, after) =>
      this.onRoleUpdate(before, after),
    );
    on(Events.GuildMemberUpdate, (before, after) =>
      this.onMemberUpdate(before, after),
    );
    on(Events.ChannelCreate, async (channel) => {
      if (!channel.guild || !(await this.settings(channel.guild.id)).enabled)
        return;
      const role = [...channel.guild.roles.cache.values()].find(
        (role) => role.name === "Rallybit Quarantined",
      );
      if (role)
        await channel.permissionOverwrites.edit(
          role.id,
          [ChannelType.GuildVoice, ChannelType.GuildStageVoice].includes(
            channel.type,
          )
            ? { Connect: false, Speak: false }
            : {
                SendMessages: false,
                AddReactions: false,
                CreatePublicThreads: false,
                CreatePrivateThreads: false,
                SendMessagesInThreads: false,
              },
          { reason: "Rallybit quarantine coverage" },
        );
    });
  }
}

export function registerSecurityEngine(registry, services) {
  const service = new SecurityService(services),
    manager = { guildOnly: true, permissions: ["ManageGuild"] },
    admin = { guildOnly: true, permissions: ["Administrator"] };
  registry.register(
    "security system",
    async (i) => {
      const enabled = i.options.getBoolean("enabled", true);
      await service.update(i.guildId, (s) => {
        s.enabled = enabled;
      });
      return {
        content: `Security system ${enabled ? "enabled" : "disabled"}.`,
      };
    },
    admin,
  );
  registry.register(
    "security overview",
    async (i) => {
      const settings = await service.settings(i.guildId);
      return {
        embeds: [
          embed(
            "Security overview",
            `System: ${settings.enabled ? "Enabled" : "Disabled"}\n` +
              Object.entries(settings.modules)
                .map(
                  ([name, cfg]) =>
                    `${cfg.enabled ? "Enabled" : "Disabled"} · ${name}${cfg.punishment ? ` · ${cfg.punishment}` : ""}`,
                )
                .join("\n"),
          ),
        ],
      };
    },
    manager,
  );
  registry.register(
    "security audit",
    async (i) => {
      const bot = await i.guild.members.fetchMe({ force: true }),
        settings = await service.settings(i.guildId);
      const issues = [
        "ViewAuditLog",
        "ManageChannels",
        "ManageMessages",
        "KickMembers",
        "BanMembers",
        "ModerateMembers",
        "ManageRoles",
        "ManageWebhooks",
      ]
        .filter((key) => !bot.permissions.has(P[key]))
        .map((key) => `Rallybit lacks ${key}.`);
      if (i.guild.roles.everyone.permissions.bitfield & DANGEROUS)
        issues.push("The everyone role has dangerous permissions.");
      if (!settings.log_channel_id)
        issues.push("No security log channel is configured.");
      return {
        embeds: [
          embed(
            "Security audit",
            issues.join("\n") || "No major configuration issues detected.",
          ),
        ],
      };
    },
    manager,
  );
  registry.register(
    "security history",
    async (i) => ({
      embeds: [
        embed(
          "Security history",
          (
            (await services.store.read("security_history.json"))[i.guildId] ??
            []
          )
            .slice(0, i.options.getInteger("limit") ?? 10)
            .map(
              (row) =>
                `**${row.module}: ${row.action}**\n${text(row.reason, 180)}`,
            )
            .join("\n\n") || "No security history.",
        ),
      ],
    }),
    manager,
  );
  registry.register(
    "security lockdown",
    async (i) => {
      const enabled = i.options.getBoolean("enabled", true);
      await service.lockdown(i.guild, enabled, i.user);
      return {
        content: enabled
          ? "Server lockdown applied."
          : "Original lockdown-controlled permissions restored; unrelated changes were preserved.",
      };
    },
    admin,
  );
  const clearInvites = async (i) => {
    let deleted = 0,
      failed = 0;
    for (const invite of (await i.guild.invites.fetch()).values()) {
      try {
        await invite.delete(`Rallybit invite cleanup by ${i.user.id}`);
        deleted++;
      } catch {
        failed++;
      }
    }
    return { deleted, failed };
  };
  registry.register(
    "security clearinvites",
    async (i) => {
      if (!i.options.getBoolean("confirmation", true))
        throw new UserError("Set confirmation to true.");
      const result = await clearInvites(i);
      return {
        content: `Deleted ${result.deleted} invites; ${result.failed} failed.`,
      };
    },
    admin,
  );
  registry.register(
    "security panic",
    async (i) => {
      if (
        i.options.getString("confirmation", true).trim().toUpperCase() !==
        "PANIC"
      )
        throw new UserError("Type PANIC to confirm.");
      await service.lockdown(i.guild, true, i.user);
      const result = await clearInvites(i);
      return {
        content: `Lockdown applied. Deleted ${result.deleted} invites; ${result.failed} failed.`,
      };
    },
    admin,
  );
  for (const action of ["quarantine", "release"])
    registry.register(
      `security ${action}`,
      async (i) => {
        const id = i.options.getUser("member", true).id;
        await targetMembers(i, id, P.ManageRoles);
        return {
          content: await service[action](
            i.guild,
            id,
            i.user,
            text(i.options.getString("reason") || "Security review", 500),
          ),
        };
      },
      { guildOnly: true, permissions: ["ManageRoles"] },
    );
  registry.register(
    "security purgeuser",
    async (i) => {
      if (i.options.getString("confirmation")?.trim().toUpperCase() !== "PURGE")
        throw new UserError(
          "Type PURGE to confirm the temporary ban/unban and message deletion.",
        );
      const id = i.options.getUser("member", true).id;
      await targetMembers(i, id, P.BanMembers);
      const op = i.id || shortId(),
        reason = `Rallybit purge ${op}: ${text(i.options.getString("reason") || "Security message purge", 350)}`,
        days = i.options.getInteger("delete_days") ?? 7;
      await services.store.update("security_purge_jobs.json", (data) => {
        data[op] = {
          guild_id: i.guildId,
          target_id: id,
          reason,
          status: "pending",
          created_at: nowISO(),
        };
      });
      await i.guild.bans.create(id, {
        deleteMessageSeconds: Math.max(0, Math.min(7, days)) * 86400,
        reason,
      });
      try {
        await i.guild.bans.remove(id, `Rallybit purge ${op} complete`);
      } catch {
        throw new UserError(
          "Messages were purged, but the user is still banned. Use /mod unban immediately; the recovery job was retained.",
        );
      }
      await services.store.update("security_purge_jobs.json", (data) => {
        data[op].status = "completed";
      });
      return {
        content: `Deleted up to ${days} days of <@${id}>’s messages using a temporary ban, then unbanned them. They will need to rejoin the server.`,
      };
    },
    { guildOnly: true, moderation: "ban" },
  );
  for (const action of ["setup", "repair"])
    registry.register(
      `security-trap ${action}`,
      async (i) => {
        const input =
          action === "setup"
            ? {
                action: i.options.getString("action") || "ban",
                channel_name:
                  i.options.getString("channel_name") || "do-not-text-here",
                appeal_url: i.options.getString("appeal_url") || "",
                bypass_trusted_roles:
                  i.options.getBoolean("bypass_trusted_roles") ?? false,
                warning_message: i.options.getString("warning_message") || "",
              }
            : {};
        const channel = await service.trap(i.guild, input);
        return {
          content: `Security trap ready in <#${channel.id}>. Its warning explains the automatic action.`,
        };
      },
      admin,
    );
  registry.register(
    "security-trap disable",
    async (i) => {
      const cfg = (await service.settings(i.guildId)).modules.scam_trap;
      await service.update(i.guildId, (s) => {
        s.modules.scam_trap.enabled = false;
      });
      if (i.options.getBoolean("delete_channel") && cfg.channel_id) {
        const channel = await i.guild.channels.fetch(String(cfg.channel_id));
        if (channel)
          await channel.delete(`Security trap removed by ${i.user.id}`);
      }
      return { content: "Security trap disabled." };
    },
    admin,
  );
  registry.register(
    "security-trap status",
    async (i) => {
      const cfg = (await service.settings(i.guildId)).modules.scam_trap;
      return {
        content: `Scam trap: ${cfg.enabled ? "Enabled" : "Disabled"}\nAction: ${cfg.action}\nChannel: ${cfg.channel_id ? `<#${cfg.channel_id}>` : "Not configured"}`,
      };
    },
    manager,
  );
  registry.register(
    "security-modules set",
    async (i) => {
      const module = i.options.getString("module", true),
        punishment = i.options.getString("punishment");
      if (!MODULE_DEFAULTS[module])
        throw new UserError("Unknown security module.");
      if (punishment && !["ban", "kick", "strip"].includes(punishment))
        throw new UserError("Choose ban, kick or strip.");
      await service.update(i.guildId, (s) => {
        s.modules[module].enabled = i.options.getBoolean("enabled", true);
        if (punishment) s.modules[module].punishment = punishment;
      });
      return { content: `${module} settings saved.` };
    },
    manager,
  );
  registry.register(
    "security-modules threshold",
    async (i) => {
      const module = i.options.getString("module", true);
      if (!THRESHOLDS.includes(module))
        throw new UserError("Choose a threshold-based module.");
      await service.update(i.guildId, (s) => {
        s.modules[module].count = i.options.getInteger("count", true);
        s.modules[module].seconds = i.options.getInteger("seconds", true);
      });
      return { content: "Threshold saved." };
    },
    manager,
  );
  registry.register(
    "security-antispam configure",
    async (i) => {
      await service.update(i.guildId, (s) => {
        Object.assign(s.modules.anti_spam, {
          enabled: i.options.getBoolean("enabled", true),
          message_limit: i.options.getInteger("message_limit") ?? 6,
          window_seconds: i.options.getInteger("window_seconds") ?? 5,
          timeout_minutes: i.options.getInteger("timeout_minutes") ?? 5,
        });
      });
      return { content: "Anti-spam settings saved." };
    },
    manager,
  );
  registry.register(
    "security-automod configure",
    async (i) => {
      await service.update(i.guildId, (s) => {
        Object.assign(s.modules.automod, {
          enabled: i.options.getBoolean("enabled", true),
          block_invites: i.options.getBoolean("block_invites") ?? false,
        });
      });
      return { content: "Automod settings saved." };
    },
    manager,
  );
  for (const action of ["addterm", "removeterm"])
    registry.register(
      `security-automod ${action}`,
      async (i) => {
        const term = text(
          i.options.getString("term", true).normalize("NFKC").toLowerCase(),
          80,
        );
        if (term.length < 2)
          throw new UserError("Terms must contain at least two characters.");
        await service.update(i.guildId, (s) => {
          const terms = new Set(s.modules.automod.bad_words ?? []);
          if (action === "addterm") terms.add(term);
          else terms.delete(term);
          if (terms.size > 100)
            throw new UserError("Up to 100 blocked terms are supported.");
          s.modules.automod.bad_words = [...terms];
        });
        return { content: "Blocked-term list updated." };
      },
      manager,
    );
  registry.register(
    "security-automod terms",
    async (i) => ({
      content:
        (await service.settings(i.guildId)).modules.automod.bad_words.join(
          ", ",
        ) || "No blocked terms.",
    }),
    manager,
  );
  for (const action of ["add", "remove"])
    registry.register(
      `security-trust ${action}`,
      async (i) => {
        const role = i.options.getRole("role", true);
        if (role.id === i.guildId)
          throw new UserError("The everyone role cannot be trusted.");
        await service.update(i.guildId, (s) => {
          const ids = new Set(s.trusted_role_ids.map(String));
          if (action === "add") ids.add(role.id);
          else ids.delete(role.id);
          s.trusted_role_ids = [...ids];
        });
        return {
          content: `Security trust ${action === "add" ? "added" : "removed"} for <@&${role.id}>.`,
        };
      },
      manager,
    );
  registry.register(
    "security-trust list",
    async (i) => ({
      content:
        (await service.settings(i.guildId)).trusted_role_ids
          .map((id) => `<@&${id}>`)
          .join(", ") || "No trusted roles.",
    }),
    manager,
  );
  registry.register(
    "security-trust adminbypass",
    async (i) => {
      await service.update(i.guildId, (s) => {
        s.bypass_administrators = i.options.getBoolean("enabled", true);
      });
      return { content: "Administrator security-bypass setting saved." };
    },
    admin,
  );
  registry.register(
    "security-logs channel",
    async (i) => {
      await service.update(i.guildId, (s) => {
        s.log_channel_id = i.options.getChannel("channel")?.id ?? null;
      });
      return { content: "Security log destination saved." };
    },
    manager,
  );
  registry.register(
    "security-logs ownerdm",
    async (i) => {
      await service.update(i.guildId, (s) => {
        s.dm_server_owner = i.options.getBoolean("enabled", true);
      });
      return { content: "Owner security-notification setting saved." };
    },
    admin,
  );
  registry.register(
    "security-logs events",
    async (i) => {
      await service.update(i.guildId, (s) => {
        for (const key of [
          "member_events",
          "message_events",
          "voice_events",
          "structure_events",
        ])
          s.event_logging[key] = i.options.getBoolean(key) ?? false;
      });
      return { content: "Security event-log settings saved." };
    },
    manager,
  );
  registry.register(
    "security-antibot configure",
    async (i) => {
      const punishment = i.options.getString("punishment", true);
      if (!["ban", "kick", "strip"].includes(punishment))
        throw new UserError("Choose ban, kick or strip.");
      await service.update(i.guildId, (s) => {
        Object.assign(s.modules.anti_bot, {
          enabled: i.options.getBoolean("enabled", true),
          punishment,
          ban_added_bot: i.options.getBoolean("ban_added_bot") ?? true,
        });
      });
      return { content: "Anti-bot settings saved." };
    },
    manager,
  );
  return service;
}
