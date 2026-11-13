import {
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  PermissionFlagsBits as P,
  MessageFlags,
} from "discord.js";
import { UserError } from "../access.js";
import {
  embed,
  guildSettings,
  nowISO,
  snowflake,
  text,
  updateSettings,
} from "../helpers.js";
import { privateReply } from "../components.js";
import { randomInt } from "node:crypto";

export function assignableRole(actor, role, bot) {
  if (!role || role.id === actor.guild.id || role.managed)
    throw new UserError(
      "Choose a normal, non-managed role other than everyone.",
    );
  if (
    actor.id !== actor.guild.ownerId &&
    role.comparePositionTo(actor.roles.highest) >= 0
  )
    throw new UserError("The role must be below your highest role.");
  if (
    !bot.permissions.has(P.ManageRoles) ||
    role.comparePositionTo(bot.roles.highest) >= 0
  )
    throw new UserError(
      "Rallybit needs Manage Roles and a role above the selected role.",
    );
}
export function emojiKey(value) {
  const id =
    typeof value === "string"
      ? value.match(/^(?:<a?:\w+:)?(\d{15,22})>?$/)?.[1]
      : value.id;
  return id
    ? `id:${id}`
    : `unicode:${typeof value === "string" ? value : value.name}`;
}
export const levelFromXP = (value) =>
  Math.floor(Math.sqrt(Math.max(0, Number(value) || 0) / 100));
export const xpForLevel = (value) => Math.max(0, Math.trunc(value)) ** 2 * 100;
export class MemberTools {
  constructor(services) {
    Object.assign(this, services);
  }
  async roleSetup(i, role) {
    const actor = await i.guild.members.fetch({ user: i.user.id, force: true }),
      bot = await i.guild.members.fetchMe({ force: true });
    assignableRole(actor, role, bot);
  }
  async onJoin(member) {
    const ids =
      (await this.store.read("autorole_settings.json"))[member.guild.id] ?? [];
    const bot = await member.guild.members.fetchMe({ force: true });
    if (!bot.permissions.has(P.ManageRoles)) return;
    for (const id of ids) {
      const role = await member.guild.roles.fetch(String(id));
      if (
        role &&
        !role.managed &&
        role.id !== member.guild.id &&
        role.comparePositionTo(bot.roles.highest) < 0 &&
        !member.roles.cache.has(role.id)
      )
        await member.roles.add(role, "Rallybit autorole");
    }
  }
  async reaction(reaction, user, remove = false) {
    if (user.bot) return;
    if (reaction.partial) reaction = await reaction.fetch();
    const message = reaction.message;
    if (!message.guild) return;
    const id = (await this.store.read("reaction_roles.json"))[
      message.guildId
    ]?.[message.id]?.[emojiKey(reaction.emoji)];
    if (!id) return;
    const [member, bot, role] = await Promise.all([
      message.guild.members.fetch({ user: user.id, force: true }),
      message.guild.members.fetchMe({ force: true }),
      message.guild.roles.fetch(String(id)),
    ]);
    if (
      !role ||
      role.managed ||
      role.id === message.guildId ||
      role.comparePositionTo(bot.roles.highest) >= 0 ||
      !bot.permissions.has(P.ManageRoles)
    )
      return;
    await member.roles[remove ? "remove" : "add"](
      role,
      "Rallybit reaction role",
    );
  }
  async verificationPanel(guild, channel, role, remove, options = {}) {
    if (!channel.send || channel.guild.id !== guild.id)
      throw new UserError("Choose a text channel in this server.");
    const bot = await guild.members.fetchMe({ force: true });
    for (const candidate of [role, remove].filter(Boolean))
      if (
        candidate.id === guild.id ||
        candidate.managed ||
        candidate.comparePositionTo(bot.roles.highest) >= 0
      )
        throw new UserError(
          "Rallybit cannot manage one of the verification roles.",
        );
    const settings = {
      enabled: true,
      channel_id: channel.id,
      message_id: null,
      role_id: role.id,
      remove_role_id: remove?.id ?? null,
      title: text(options.title || "Verify your account", 256),
      description: text(
        options.description ||
          "Press the button below to gain access to the server.",
        4000,
      ),
      button_label: text(options.button_label || "Verify", 80),
    };
    await updateSettings(
      this.store,
      "verification_settings.json",
      guild.id,
      settings,
    );
    const message = await channel.send({
      embeds: [embed(settings.title, settings.description)],
      components: [
        new ActionRowBuilder().addComponents(
          new ButtonBuilder()
            .setCustomId(`rallybit:verify:${guild.id}`)
            .setLabel(settings.button_label)
            .setStyle(ButtonStyle.Success),
        ),
      ],
      allowedMentions: { parse: [] },
    });
    await updateSettings(this.store, "verification_settings.json", guild.id, {
      message_id: message.id,
    });
    return message;
  }
  async verify(i) {
    if (i.customId !== `rallybit:verify:${i.guildId}`)
      throw new UserError("This verification panel belongs to another server.");
    await i.deferReply({ flags: MessageFlags.Ephemeral });
    const settings = await guildSettings(
      this.store,
      "verification_settings.json",
      i.guildId,
    );
    if (!settings.enabled) throw new UserError("Verification is disabled.");
    const [member, bot, role, remove] = await Promise.all([
      i.guild.members.fetch({ user: i.user.id, force: true }),
      i.guild.members.fetchMe({ force: true }),
      i.guild.roles.fetch(String(settings.role_id || "")),
      settings.remove_role_id
        ? i.guild.roles.fetch(String(settings.remove_role_id))
        : null,
    ]);
    for (const candidate of [role, remove].filter(Boolean))
      if (
        candidate.id === i.guildId ||
        candidate.managed ||
        candidate.comparePositionTo(bot.roles.highest) >= 0
      )
        throw new UserError(
          "Rallybit cannot manage the configured verification roles.",
        );
    if (!role || !bot.permissions.has(P.ManageRoles))
      throw new UserError(
        "Check the verification role and Rallybit’s Manage Roles permission.",
      );
    if (remove?.id === role.id)
      throw new UserError("Verified and removal roles cannot be the same.");
    const existed = member.roles.cache.has(role.id);
    if (
      !(await this.store.reserve(
        "verification_dispatch.json",
        `${i.guildId}:${i.user.id}`,
        3000,
      ))
    )
      throw new UserError(
        "Verification was just attempted. Wait a moment and try again.",
      );
    try {
      if (!existed) await member.roles.add(role, "Rallybit verification");
      if (remove && member.roles.cache.has(remove.id))
        await member.roles.remove(remove, "Rallybit verification completed");
    } catch (error) {
      if (!existed)
        await member.roles
          .remove(role, "Rollback incomplete verification")
          .catch(() => {});
      throw new UserError(
        "Verification could not finish. Ask staff to check both role positions and permissions.",
      );
    }
    return privateReply(i, {
      content: `You are verified and have <@&${role.id}>.`,
    });
  }
  async rewards(member, settings) {
    const data = (await this.store.read("level_stats.json"))[member.guild.id]?.[
      member.id
    ];
    if (!data) return [];
    const level = levelFromXP(data.xp),
      bot = await member.guild.members.fetchMe({ force: true }),
      added = [];
    if (!bot.permissions.has(P.ManageRoles)) return added;
    for (const [required, id] of Object.entries(settings.reward_roles ?? {})) {
      if (
        Number(required) > level ||
        !/^\d{15,22}$/.test(String(id)) ||
        member.roles.cache.has(String(id))
      )
        continue;
      const role = await member.guild.roles.fetch(String(id));
      if (
        !role ||
        role.id === member.guild.id ||
        role.managed ||
        role.comparePositionTo(bot.roles.highest) >= 0
      )
        continue;
      await member.roles.add(role, `Rallybit level ${required} reward`);
      added.push(role);
    }
    return added;
  }
  async onMessage(message) {
    if (!message.guild || message.author.bot || !message.member) return;
    const settings = await guildSettings(
      this.store,
      "level_settings.json",
      message.guildId,
    );
    if (!settings.enabled) return;
    if (
      (settings.ignored_channel_ids ?? [])
        .map(String)
        .includes(message.channelId) ||
      (settings.ignored_role_ids ?? []).some((id) =>
        message.member.roles.cache.has(String(id)),
      )
    )
      return;
    let gained = false,
      old = 0,
      level = 0,
      announce = false;
    await this.store.update("level_stats.json", (data) => {
      const record = ((data[message.guildId] ??= {})[message.author.id] ??= {
        xp: 0,
        level: 0,
        messages: 0,
        display_name: message.member.displayName,
        last_message_at: null,
      });
      if (
        Date.parse(record.last_message_at) >
        Date.now() -
          Math.max(10, Number(settings.cooldown_seconds) || 60) * 1000
      )
        return;
      old = levelFromXP(record.xp);
      const low = Math.max(1, Math.min(100, Number(settings.xp_min) || 15)),
        high = Math.max(low, Math.min(100, Number(settings.xp_max) || 25));
      record.xp =
        Math.max(0, Number(record.xp) || 0) + randomInt(low, high + 1);
      record.messages = (Number(record.messages) || 0) + 1;
      record.last_message_at = nowISO();
      record.display_name = message.member.displayName;
      record.level = level = levelFromXP(record.xp);
      gained = true;
      if (level > old && (record.last_announced_level ?? -1) < level) {
        record.last_announced_level = level;
        announce = true;
      }
    });
    if (!gained) return;
    const rewards = await this.rewards(message.member, settings);
    if (announce) {
      const channel = settings.announce_channel_id
        ? await message.guild.channels.fetch(
            String(settings.announce_channel_id),
          )
        : message.channel;
      const content =
        text(
          String(
            settings.announce_message || "{user} reached **level {level}**!",
          )
            .replaceAll("{user}", `<@${message.author.id}>`)
            .replaceAll("{username}", message.member.displayName)
            .replaceAll("{level}", String(level)),
          1800,
        ) +
        (rewards.length
          ? `\nRewards: ${rewards.map((r) => `<@&${r.id}>`).join(", ")}`
          : "");
      if (channel?.send)
        await channel.send({
          content,
          allowedMentions: { parse: [], users: [message.author.id] },
        });
    }
  }
}
export function registerMemberTools(registry, services) {
  const service = new MemberTools(services),
    rolesPolicy = { guildOnly: true, permissions: ["ManageRoles"] },
    manager = { guildOnly: true, permissions: ["ManageGuild"] },
    publicPolicy = { guildOnly: true };
  for (const action of ["add", "remove"])
    registry.register(
      `autorole ${action}`,
      async (i) => {
        const role = i.options.getRole("role", true);
        if (action === "add") await service.roleSetup(i, role);
        await services.store.update("autorole_settings.json", (data) => {
          const ids = (data[i.guildId] ?? [])
            .map(String)
            .filter((id) => id !== role.id);
          if (action === "add") ids.push(role.id);
          if (ids.length > 20)
            throw new UserError("Up to 20 autoroles are supported.");
          data[i.guildId] = ids;
        });
        return {
          content: `Autorole ${action === "add" ? "added" : "removed"}: <@&${role.id}>.`,
        };
      },
      rolesPolicy,
    );
  registry.register(
    "autorole list",
    async (i) => ({
      content:
        ((await services.store.read("autorole_settings.json"))[i.guildId] ?? [])
          .map((id) => `<@&${id}>`)
          .join(", ") || "No autoroles.",
    }),
    rolesPolicy,
  );
  registry.register(
    "autorole clear",
    async (i) => {
      await services.store.update("autorole_settings.json", (data) => {
        data[i.guildId] = [];
      });
      return { content: "Autoroles cleared." };
    },
    rolesPolicy,
  );
  registry.register(
    "reactionrole add",
    async (i) => {
      const channel = i.options.getChannel("channel", true),
        id = snowflake(i.options.getString("message_id", true), "message ID"),
        role = i.options.getRole("role", true),
        emoji = i.options.getString("emoji", true);
      if (channel.guild.id !== i.guildId || !channel.messages)
        throw new UserError("Choose a text channel in this server.");
      await service.roleSetup(i, role);
      const message = await channel.messages.fetch(id);
      await message.react(
        emoji.replace(/^\d{15,22}$/, (value) => `ticket_option:${value}`),
      );
      await services.store.update("reaction_roles.json", (data) => {
        ((data[i.guildId] ??= {})[id] ??= {})[emojiKey(emoji)] = role.id;
      });
      return { content: `Reaction role configured for ${message.url}.` };
    },
    rolesPolicy,
  );
  registry.register(
    "reactionrole remove",
    async (i) => {
      const id = snowflake(
          i.options.getString("message_id", true),
          "message ID",
        ),
        emoji = i.options.getString("emoji", true);
      await services.store.update("reaction_roles.json", (data) => {
        delete data[i.guildId]?.[id]?.[emojiKey(emoji)];
      });
      return { content: "Reaction-role mapping removed." };
    },
    rolesPolicy,
  );
  registry.register(
    "reactionrole list",
    async (i) => ({
      embeds: [
        embed(
          "Reaction roles",
          Object.entries(
            (await services.store.read("reaction_roles.json"))[i.guildId] ?? {},
          )
            .flatMap(([id, rows]) =>
              Object.entries(rows).map(
                ([emoji, role]) => `Message \`${id}\`: ${emoji} → <@&${role}>`,
              ),
            )
            .join("\n") || "No reaction roles.",
        ),
      ],
    }),
    rolesPolicy,
  );
  registry.register(
    "verification setup",
    async (i) => {
      const role = i.options.getRole("verified_role", true),
        remove = i.options.getRole("remove_role");
      await service.roleSetup(i, role);
      if (remove) await service.roleSetup(i, remove);
      if (remove?.id === role.id)
        throw new UserError("Verified and removal roles must be different.");
      const message = await service.verificationPanel(
        i.guild,
        i.options.getChannel("channel", true),
        role,
        remove,
        {
          title: i.options.getString("title"),
          description: i.options.getString("description"),
          button_label: i.options.getString("button_label"),
        },
      );
      return { content: `Verification panel sent: ${message.url}` };
    },
    { guildOnly: true, permissions: ["ManageRoles", "ManageChannels"] },
  );
  registry.register(
    "verification disable",
    async (i) => {
      await updateSettings(
        services.store,
        "verification_settings.json",
        i.guildId,
        { enabled: false },
      );
      return {
        content:
          "Verification disabled. Existing buttons now refuse role assignment.",
      };
    },
    rolesPolicy,
  );
  registry.register(
    "verification status",
    async (i) => {
      const settings = await guildSettings(
        services.store,
        "verification_settings.json",
        i.guildId,
      );
      return {
        content: `Verification: ${settings.enabled ? "Enabled" : "Disabled"}\nRole: ${settings.role_id ? `<@&${settings.role_id}>` : "Not configured"}`,
      };
    },
    publicPolicy,
  );
  services.components.register("rallybit:verify", (i) => service.verify(i));
  registry.register(
    "level rank",
    async (i) => {
      const user = i.options.getUser("member") ?? i.user,
        record = (await services.store.read("level_stats.json"))[i.guildId]?.[
          user.id
        ] ?? { xp: 0, messages: 0 },
        level = levelFromXP(record.xp);
      return {
        embeds: [
          embed(
            "Member rank",
            `<@${user.id}>\nLevel: **${level}**\nXP: **${record.xp}** / ${xpForLevel(level + 1)}\nRecorded messages: ${record.messages ?? 0}`,
          ).setThumbnail(user.displayAvatarURL()),
        ],
      };
    },
    publicPolicy,
  );
  registry.register(
    "level leaderboard",
    async (i) => ({
      embeds: [
        embed(
          "Level leaderboard",
          Object.entries(
            (await services.store.read("level_stats.json"))[i.guildId] ?? {},
          )
            .sort((a, b) => (b[1].xp ?? 0) - (a[1].xp ?? 0))
            .slice(0, 15)
            .map(
              ([id, row], index) =>
                `${index + 1}. <@${id}> · Level ${levelFromXP(row.xp)} · ${row.xp} XP`,
            )
            .join("\n") || "No XP recorded.",
        ),
      ],
    }),
    publicPolicy,
  );
  registry.register(
    "level setup",
    async (i) => {
      const low = i.options.getInteger("xp_min") ?? 15,
        high = i.options.getInteger("xp_max") ?? 25;
      if (low > high)
        throw new UserError("Minimum XP cannot exceed maximum XP.");
      await updateSettings(services.store, "level_settings.json", i.guildId, {
        enabled: i.options.getBoolean("enabled", true),
        announce_channel_id:
          i.options.getChannel("announce_channel")?.id ?? null,
        cooldown_seconds: i.options.getInteger("cooldown_seconds") ?? 60,
        xp_min: low,
        xp_max: high,
      });
      return { content: "Level settings saved." };
    },
    manager,
  );
  registry.register(
    "level reward add",
    async (i) => {
      const level = i.options.getInteger("level", true),
        role = i.options.getRole("role", true);
      await service.roleSetup(i, role);
      await services.store.update("level_settings.json", (data) => {
        const settings = (data[i.guildId] ??= {});
        if (!settings.reward_roles || Array.isArray(settings.reward_roles))
          settings.reward_roles = {};
        settings.reward_roles[String(level)] = role.id;
      });
      return { content: `Level ${level} now grants <@&${role.id}>.` };
    },
    { guildOnly: true, permissions: ["ManageGuild", "ManageRoles"] },
  );
  registry.register(
    "level reward remove",
    async (i) => {
      const level = i.options.getInteger("level", true);
      await services.store.update("level_settings.json", (data) => {
        delete data[i.guildId]?.reward_roles?.[String(level)];
      });
      return {
        content: `Level ${level} reward removed. Already-earned roles are not removed.`,
      };
    },
    manager,
  );
  registry.register(
    "level reward list",
    async (i) => ({
      content:
        Object.entries(
          (
            await guildSettings(
              services.store,
              "level_settings.json",
              i.guildId,
            )
          ).reward_roles ?? {},
        )
          .sort((a, b) => Number(a[0]) - Number(b[0]))
          .map(([level, id]) => `Level ${level} → <@&${id}>`)
          .join("\n") || "No level rewards.",
    }),
    publicPolicy,
  );
  registry.register(
    "level setxp",
    async (i) => {
      const user = i.options.getUser("member", true),
        xp = i.options.getInteger("xp", true),
        member = await i.guild.members.fetch({ user: user.id, force: true });
      await services.store.update("level_stats.json", (data) => {
        const row = ((data[i.guildId] ??= {})[user.id] ??= { messages: 0 });
        Object.assign(row, {
          xp,
          level: levelFromXP(xp),
          display_name: member.displayName,
        });
      });
      await service.rewards(
        member,
        await guildSettings(services.store, "level_settings.json", i.guildId),
      );
      return {
        content: `Set <@${user.id}> to ${xp} XP (level ${levelFromXP(xp)}).`,
      };
    },
    manager,
  );
  return service;
}
