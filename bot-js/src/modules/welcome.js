import { mkdir, open, stat, unlink, readdir } from "node:fs/promises";
import { join } from "node:path";
import { UserError } from "../access.js";
import {
  embed,
  guildSettings,
  KeyedQueue,
  nowISO,
  text,
  updateSettings,
} from "../helpers.js";

export const WELCOME_DEFAULTS = {
  welcome_enabled: false,
  welcome_channel_id: null,
  welcome_message:
    "Welcome {user} to **{server}**! You are member **#{member_count}**.",
  welcome_embed: true,
  welcome_dm: false,
  goodbye_enabled: false,
  goodbye_channel_id: null,
  goodbye_message:
    "**{username}** has left **{server}**. We now have {member_count} members.",
  goodbye_embed: true,
  invite_tracking: true,
};
export function inviteSource(attribution = { kind: "unknown" }) {
  if (attribution.kind === "oauth") return "Bot joined via OAuth authorization";
  if (attribution.kind === "vanity")
    return `Server vanity invite${attribution.code ? ` (discord.gg/${attribution.code})` : ""}`;
  if (attribution.kind === "invite")
    return `Invite from ${attribution.inviter ? `<@${attribution.inviter.id}>` : "an unknown inviter"}${attribution.code ? ` (${attribution.code})` : ""}`;
  return "Invite source unavailable";
}
export function renderWelcome(
  template,
  member,
  attribution = { kind: "unknown" },
  count = 0,
) {
  const inviter = attribution.inviter,
    values = {
      user: `<@${member.id}>`,
      username: member.displayName || member.user.username,
      tag: member.user.tag || member.user.username,
      server: member.guild.name,
      member_count: String(member.guild.memberCount),
      inviter: inviter
        ? `<@${inviter.id}>`
        : attribution.kind === "vanity"
          ? "the server vanity invite"
          : attribution.kind === "oauth"
            ? "OAuth"
            : "an unknown invite",
      inviter_name:
        inviter?.globalName ||
        inviter?.username ||
        (attribution.kind === "vanity"
          ? "Vanity invite"
          : attribution.kind === "oauth"
            ? "OAuth"
            : "Unknown"),
      invite_count: String(count),
      invite_code: attribution.code || "Unknown",
      invite_type: attribution.kind,
      invite_source: inviteSource(attribution),
      account_age_days: String(
        Math.max(
          0,
          Math.floor((Date.now() - member.user.createdTimestamp) / 86400000),
        ),
      ),
    };
  let rendered = String(template || "");
  for (const [key, value] of Object.entries(values))
    rendered = rendered.replaceAll(`{${key}}`, value);
  return text(rendered, 4000);
}
export class WelcomeService {
  constructor(services) {
    Object.assign(this, services);
    this.queue = new KeyedQueue();
    this.snapshots = new Map();
    this.vanities = new Map();
    this.pending = new Map();
    this.lastCleanup = 0;
  }
  async claim(kind, guildId, userId) {
    const dir = join(this.store.directory, ".welcome-delivery");
    await mkdir(dir, { recursive: true });
    const path = join(dir, `${kind}-${guildId}-${userId}.lock`),
      now = Date.now();
    let handle;
    try {
      handle = await open(path, "wx", 0o600);
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      try {
        if (now - (await stat(path)).mtimeMs <= 30000) return false;
        await unlink(path);
        handle = await open(path, "wx", 0o600);
      } catch (cause) {
        if (cause.code === "EEXIST" || cause.code === "ENOENT") return false;
        throw cause;
      }
    }
    try {
      await handle.writeFile(String(now));
      await handle.sync();
    } finally {
      await handle.close();
    }
    if (now - this.lastCleanup > 300000) {
      this.lastCleanup = now;
      for (const name of await readdir(dir)) {
        if (!/^(?:join|leave)-\d+-\d+\.lock$/.test(name)) continue;
        const old = join(dir, name);
        try {
          if (now - (await stat(old)).mtimeMs > 300000) await unlink(old);
        } catch (error) {
          if (error.code !== "ENOENT") throw error;
        }
      }
    }
    return true;
  }
  async refresh(guild) {
    try {
      const invites = await guild.invites.fetch();
      this.snapshots.set(
        guild.id,
        new Map(
          [...invites.values()].map((row) => [row.code, Number(row.uses) || 0]),
        ),
      );
    } catch {
      this.snapshots.delete(guild.id);
    }
    try {
      const vanity = await guild.fetchVanityData();
      if (vanity.code) this.vanities.set(guild.id, vanity);
      else this.vanities.delete(guild.id);
    } catch {
      this.vanities.delete(guild.id);
    }
  }
  async detect(member) {
    if (member.user.bot) return { kind: "oauth" };
    const guild = member.guild,
      queued = (this.pending.get(guild.id) ?? []).filter(
        (row) => row.expires > Date.now(),
      );
    if (queued.length) {
      const next = queued.shift();
      this.pending.set(guild.id, queued);
      return next;
    }
    const before = this.snapshots.get(guild.id);
    let invites;
    try {
      invites = await guild.invites.fetch();
      this.snapshots.set(
        guild.id,
        new Map(
          [...invites.values()].map((row) => [row.code, Number(row.uses) || 0]),
        ),
      );
    } catch {
      return { kind: "unknown" };
    }
    const changed = before
      ? [...invites.values()].filter(
          (row) =>
            before.has(row.code) &&
            (Number(row.uses) || 0) > (before.get(row.code) || 0),
        )
      : [];
    const old = this.vanities.get(guild.id);
    let changedVanity;
    try {
      const vanity = await guild.fetchVanityData();
      if (vanity.code) {
        this.vanities.set(guild.id, vanity);
        if (old && vanity.code === old.code && vanity.uses > old.uses)
          changedVanity = vanity;
      }
    } catch {}
    if (changed.length === 1 && !changedVanity) {
      const invite = changed[0],
        delta = Math.min(100, Number(invite.uses) - before.get(invite.code)),
        result = {
          kind: "invite",
          code: invite.code,
          inviter: invite.inviter,
          expires: Date.now() + 15000,
        };
      if (delta > 1)
        this.pending.set(
          guild.id,
          Array.from({ length: delta - 1 }, () => result),
        );
      return result;
    }
    if (changedVanity && changed.length === 0) {
      const delta = Math.min(100, changedVanity.uses - old.uses),
        result = {
          kind: "vanity",
          code: changedVanity.code,
          expires: Date.now() + 15000,
        };
      if (delta > 1)
        this.pending.set(
          guild.id,
          Array.from({ length: delta - 1 }, () => result),
        );
      return result;
    }
    // Multiple invite deltas cannot reliably identify an individual join.
    return { kind: "unknown" };
  }
  async payload(member, kind, attribution, count) {
    const settings = await guildSettings(
        this.store,
        "welcome_settings.json",
        member.guild.id,
        WELCOME_DEFAULTS,
      ),
      template =
        kind === "join" ? settings.welcome_message : settings.goodbye_message;
    const content = renderWelcome(template, member, attribution, count),
      asEmbed =
        kind === "join" ? settings.welcome_embed : settings.goodbye_embed;
    return {
      ...(asEmbed
        ? {
            embeds: [
              embed(
                kind === "join" ? "Welcome to the server" : "Member left",
                content,
              ).setThumbnail(member.user.displayAvatarURL()),
            ],
          }
        : { content: text(content, 2000) }),
      allowedMentions: { parse: [], users: kind === "join" ? [member.id] : [] },
    };
  }
  async onJoin(member) {
    return this.queue.run(member.guild.id, async () => {
      if (!(await this.claim("join", member.guild.id, member.id))) return;
      const settings = await guildSettings(
        this.store,
        "welcome_settings.json",
        member.guild.id,
        WELCOME_DEFAULTS,
      );
      let attribution = { kind: member.user.bot ? "oauth" : "unknown" },
        count = 0;
      if (settings.invite_tracking) {
        attribution = await this.detect(member);
        await this.store.update("invite_tracking.json", (data) => {
          const guild = (data[member.guild.id] ??= {
            inviter_counts: {},
            members: {},
          });
          guild.inviter_counts ??= {};
          guild.members ??= {};
          if (attribution.inviter)
            count = guild.inviter_counts[attribution.inviter.id] =
              (Number(guild.inviter_counts[attribution.inviter.id]) || 0) + 1;
          guild.members[member.id] = {
            inviter_id: attribution.inviter?.id ?? null,
            invite_code: attribution.code ?? null,
            invite_type: attribution.kind,
            invite_source: inviteSource(attribution),
            joined_at: nowISO(),
          };
        });
      }
      // Security rejection precedes this pipeline. Verify the member still exists
      // before sending any welcome or DM after a kick/ban.
      const current = await member.guild.members
        .fetch({ user: member.id, force: true })
        .catch((error) => {
          if (error.code === 10007) return null;
          throw error;
        });
      if (!current) return;
      if (!settings.welcome_enabled) return;
      const payload = await this.payload(current, "join", attribution, count);
      if (settings.welcome_channel_id) {
        const channel = await member.guild.channels.fetch(
          String(settings.welcome_channel_id),
        );
        if (channel?.send) await channel.send(payload);
      }
      if (settings.welcome_dm) await current.send(payload).catch(() => {});
    });
  }
  async onLeave(member) {
    return this.queue.run(member.guild.id, async () => {
      if (!(await this.claim("leave", member.guild.id, member.id))) return;
      const settings = await guildSettings(
        this.store,
        "welcome_settings.json",
        member.guild.id,
        WELCOME_DEFAULTS,
      );
      if (!settings.goodbye_enabled) return;
      const tracking =
          (await this.store.read("invite_tracking.json"))[member.guild.id] ??
          {},
        row = tracking.members?.[member.id] ?? {};
      let inviter;
      try {
        if (row.inviter_id)
          inviter = await member.client.users.fetch(String(row.inviter_id));
      } catch {}
      const attribution = {
          kind: row.invite_type || "unknown",
          code: row.invite_code,
          inviter,
        },
        count = tracking.inviter_counts?.[row.inviter_id] ?? 0;
      const channel =
        settings.goodbye_channel_id &&
        (await member.guild.channels.fetch(
          String(settings.goodbye_channel_id),
        ));
      if (channel?.send)
        await channel.send(
          await this.payload(member, "leave", attribution, count),
        );
    });
  }
}
export function registerWelcome(registry, services) {
  const service = new WelcomeService(services),
    manager = { guildOnly: true, permissions: ["ManageGuild"] },
    publicPolicy = { guildOnly: true };
  for (const kind of ["welcome", "goodbye"]) {
    registry.register(
      `${kind} setup`,
      async (i) => {
        const channel = i.options.getChannel("channel", true);
        if (!channel.send || channel.guild.id !== i.guildId)
          throw new UserError("Choose a text channel in this server.");
        const patch = {
          [`${kind}_enabled`]: true,
          [`${kind}_channel_id`]: channel.id,
          [`${kind}_message`]:
            i.options.getString("message") ||
            WELCOME_DEFAULTS[`${kind}_message`],
          [`${kind}_embed`]: i.options.getBoolean("embed") ?? true,
        };
        if (kind === "welcome") {
          patch.welcome_dm = i.options.getBoolean("dm_member") ?? false;
          patch.invite_tracking = i.options.getBoolean("track_invites") ?? true;
        }
        await updateSettings(
          services.store,
          "welcome_settings.json",
          i.guildId,
          patch,
        );
        await service.refresh(i.guild);
        return { content: `${kind} messages enabled in <#${channel.id}>.` };
      },
      manager,
    );
    registry.register(
      `${kind} disable`,
      async (i) => {
        await updateSettings(
          services.store,
          "welcome_settings.json",
          i.guildId,
          { [`${kind}_enabled`]: false },
        );
        return { content: `${kind} messages disabled.` };
      },
      manager,
    );
  }
  registry.register(
    "welcome preview",
    async (i) => {
      const member = await i.guild.members.fetch({
        user: i.user.id,
        force: true,
      });
      return service.payload(member, "join", { kind: "unknown" }, 0);
    },
    manager,
  );
  registry.register(
    "invites user",
    async (i) => {
      const user = i.options.getUser("member") ?? i.user,
        tracking =
          (await services.store.read("invite_tracking.json"))[i.guildId] ?? {};
      return {
        embeds: [
          embed(
            "Invite tracking",
            `<@${user.id}> has ${tracking.inviter_counts?.[user.id] ?? 0} tracked joins.\nInvite source: ${tracking.members?.[user.id]?.invite_source || "Unavailable"}`,
          ),
        ],
      };
    },
    publicPolicy,
  );
  registry.register(
    "invites leaderboard",
    async (i) => ({
      embeds: [
        embed(
          "Invite leaderboard",
          Object.entries(
            (await services.store.read("invite_tracking.json"))[i.guildId]
              ?.inviter_counts ?? {},
          )
            .sort((a, b) => Number(b[1]) - Number(a[1]))
            .slice(0, 15)
            .map(
              ([id, count], index) =>
                `${index + 1}. <@${id}> · ${count} tracked joins`,
            )
            .join("\n") || "No attributed invites recorded.",
        ),
      ],
    }),
    publicPolicy,
  );
  return service;
}
