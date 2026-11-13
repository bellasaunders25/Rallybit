import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { open } from "node:fs/promises";
import { join } from "node:path";
import { freemem, totalmem, platform, release } from "node:os";
import { ChannelType, PermissionFlagsBits as P } from "discord.js";
import { UserError } from "./access.js";
import { snowflake } from "./helpers.js";
import { reconcileSecurityWrite } from "./modules/security-engine.js";

export const API_FILES = new Set(
  `activity_settings bot_settings activity_log notice limits bot_guilds admins badges global_stats status_history win_badges activity_audit_logs votes server_analytics last_seen member_events quiz_settings quiz_stats quiz_history community_settings pulse_history moderation_history moderation_warnings moderation_permissions security_settings security_history security_quarantine security_lockdown giveaway_settings giveaway_history welcome_settings invite_tracking level_settings level_stats autorole_settings reaction_roles verification_settings ticket_settings ticket_panels open_tickets ticket_history automation_schedules afk_status report_settings reports review_settings premium_entitlements staff_shifts workforce_settings staff_requests audit_settings audit_events banned_servers banned_users temporary_roles premium_backups network_settings`
    .split(" ")
    .map((name) => `${name}.json`),
);
export const DASHBOARD_ACTIONS = [
  "logging.dashboard",
  "embed.load",
  "embed.update",
  "embed.send",
  "activity.start",
  "activity.stop",
  "quiz.start",
  "quiz.stop",
  "pulse.start",
  "pulse.stop",
  "icebreaker.post",
  "giveaway.start",
  "giveaway.end",
  "security.trap",
  "security.lockdown",
  "moderation.warn",
  "moderation.timeout",
  "moderation.kick",
  "moderation.ban",
  "verification.publish",
  "ticket.panel",
  "ticket.panel.load",
  "ticket.panel.update",
  "reactionrole.add",
  "level.setxp",
  "automation.run",
];
const channelTypes = {
  [ChannelType.GuildText]: "text",
  [ChannelType.GuildAnnouncement]: "news",
  [ChannelType.GuildCategory]: "category",
  [ChannelType.GuildVoice]: "voice",
  [ChannelType.GuildStageVoice]: "stage_voice",
  [ChannelType.GuildForum]: "forum",
  [ChannelType.GuildMedia]: "media",
};
export function authorised(provided, expected) {
  const a = Buffer.from(String(provided ?? "").trim()),
    b = Buffer.from(expected ?? "");
  return b.length > 0 && a.length === b.length && timingSafeEqual(a, b);
}
export async function syncGuildCache(client, store) {
  const data = {
    __SYSTEM__: {
      total_guilds: client.guilds.cache.size,
      total_members: [...client.guilds.cache.values()].reduce(
        (n, g) => n + (g.memberCount || 0),
        0,
      ),
      shard_count: client.options.shardCount ?? 1,
      updated_at: new Date().toISOString(),
      shards: {},
    },
  };
  for (const guild of client.guilds.cache.values())
    data[guild.id] = {
      name: guild.name,
      icon: guild.iconURL(),
      member_count: guild.memberCount,
      shard_id: guild.shardId,
    };
  for (const [id, shard] of client.ws.shards ?? []) {
    const guilds = [...client.guilds.cache.values()].filter(
      (g) => g.shardId === id,
    );
    data.__SYSTEM__.shards[String(id)] = {
      status: shard.status === 0 ? "ONLINE" : "OFFLINE",
      latency_ms: shard.ping >= 0 ? shard.ping : null,
      guild_count: guilds.length,
      member_count: guilds.reduce((n, g) => n + (g.memberCount || 0), 0),
      uptime_seconds: Math.floor(client.uptime / 1000) || 0,
    };
  }
  await store.write("bot_guilds.json", data);
}
export function createApi({
  store,
  client,
  secret,
  topggSecret = "",
  actions = new Map(),
  profile,
  restartShard,
  isReady = () => client.isReady(),
}) {
  const started = Date.now();
  const respond = (res, status, data) => {
    res.writeHead(status, {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    res.end(JSON.stringify(data));
  };
  return createServer(async (req, res) => {
    try {
      const route = new URL(req.url, "http://localhost").pathname;
      if (req.method === "GET" && ["/", "/api/health"].includes(route)) {
        const ready = isReady();
        return respond(res, ready ? 200 : 503, {
          ok: ready,
          status: ready ? "healthy" : "degraded",
          engine: "Rallybit discord.js",
          uptime_seconds: Math.floor((Date.now() - started) / 1000),
          metrics: {
            memory_percent: (1 - freemem() / totalmem()) * 100,
            node: process.versions.node,
            platform: `${platform()} ${release()}`,
          },
          bot: {
            guilds: client.guilds.cache.size,
            users: [...client.guilds.cache.values()].reduce(
              (n, g) => n + g.memberCount,
              0,
            ),
            latency_ms: client.ws.ping,
          },
        });
      }
      const vote = route === "/api/topgg/webhook";
      if (
        !authorised(
          vote ? req.headers.authorization : req.headers["x-api-key"],
          vote ? topggSecret : secret,
        )
      )
        return respond(res, 401, { error: "unauthorized" });
      if (req.method !== "POST")
        return respond(res, 405, { error: "POST required" });
      let size = 0;
      const chunks = [];
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 2 * 1024 * 1024) {
          respond(res, 413, { error: "Request too large" });
          req.destroy();
          return;
        }
        chunks.push(chunk);
      }
      const data = JSON.parse(
        Buffer.concat(chunks).toString() || "{}",
        (_key, value, context) =>
          typeof value === "number" &&
          Number.isInteger(value) &&
          !Number.isSafeInteger(value)
            ? context.source
            : value,
      );
      if (!data || typeof data !== "object" || Array.isArray(data))
        throw new UserError("A JSON object is required.");
      if (route === "/api/json/read" || route === "/api/json/write") {
        if (!API_FILES.has(data.file))
          return respond(res, 400, { error: "invalid file" });
        if (route.endsWith("/read"))
          return respond(res, 200, { data: await store.read(data.file) });
        if (!data.data || typeof data.data !== "object")
          throw new UserError("Data must be a JSON object or array.");
        const value =
          data.file === "security_settings.json"
            ? reconcileSecurityWrite(data.data, await store.read(data.file))
            : data.data;
        await store.write(data.file, value);
        return respond(res, 200, { ok: true });
      }
      if (route === "/api/premium/entitlements")
        return respond(res, 200, {
          ok: true,
          data: { users: {}, servers: {}, history: [] },
        });
      if (["/api/premium/grant", "/api/premium/revoke"].includes(route))
        return respond(res, 410, {
          error: "Plans have been retired. All Rallybit features are free.",
        });
      if (vote) {
        const user = snowflake(data.user, "user ID");
        await store.update(
          "votes.json",
          (rows) => {
            if (!Array.isArray(rows))
              throw new UserError("Invalid vote storage.");
            rows.push({
              user_id: user,
              timestamp: Math.floor(Date.now() / 1000),
              claimed: false,
            });
          },
          [],
        );
        return respond(res, 200, { ok: true });
      }
      if (route === "/api/logs/read") {
        const id = snowflake(data.guild_id, "server ID");
        let log = "No activity logs yet.";
        let handle;
        try {
          handle = await open(
            join(store.directory, "server_logs", `${id}.txt`),
            "r",
          );
          const size = (await handle.stat()).size,
            amount = Math.min(size, 2_000_000),
            buffer = Buffer.alloc(amount);
          await handle.read(buffer, 0, amount, Math.max(0, size - amount));
          log = buffer.toString("utf8");
        } catch (error) {
          if (error.code !== "ENOENT") throw error;
        } finally {
          await handle?.close();
        }
        return respond(res, 200, { data: log });
      }
      if (route === "/api/bot/profile") {
        if (!profile) throw new UserError("Profile service unavailable.");
        const result = await profile.update(data);
        return respond(res, 200, {
          ok: true,
          status: data.status ?? "online",
          result,
        });
      }
      if (route === "/api/bot/restart_shard") {
        if (!restartShard || !Number.isInteger(Number(data.shard_id)))
          throw new UserError("Invalid shard.");
        await restartShard(Number(data.shard_id));
        return respond(res, 200, { ok: true });
      }
      if (
        route === "/api/guild/resources" ||
        route === "/api/dashboard/action"
      ) {
        if (!isReady())
          throw new UserError("Rallybit is reconnecting. Try again shortly.");
        const guild = client.guilds.cache.get(
          snowflake(data.guild_id, "server ID"),
        );
        if (!guild) return respond(res, 404, { error: "guild unavailable" });
        if (route === "/api/guild/resources") {
          const [channels, roles, me] = await Promise.all([
            guild.channels.fetch(),
            guild.roles.fetch(),
            guild.members.fetchMe({ force: true }),
          ]);
          return respond(res, 200, {
            ok: true,
            guild: {
              id: guild.id,
              name: guild.name,
              member_count: guild.memberCount,
            },
            channels: [...channels.values()]
              .filter(Boolean)
              .map((channel) => ({
                id: channel.id,
                name: channel.name,
                type: channelTypes[channel.type] ?? String(channel.type),
                position: channel.rawPosition ?? 0,
                category_id: channel.parentId,
              })),
            roles: [...roles.values()]
              .filter((role) => role.id !== guild.id)
              .sort((a, b) => b.position - a.position)
              .map((role) => ({
                id: role.id,
                name: role.name,
                position: role.position,
                managed: role.managed,
                mentionable: role.mentionable,
                color: role.color,
              })),
            bot_top_role_position: me.roles.highest.position,
            bot_permissions: Object.fromEntries(
              Object.entries(P).map(([key, bit]) => [
                key.replace(
                  /[A-Z]/g,
                  (letter, index) => (index ? "_" : "") + letter.toLowerCase(),
                ),
                me.permissions.has(bit),
              ]),
            ),
          });
        }
        const actor = await guild.members.fetch({
          user: snowflake(data.actor_id, "actor ID"),
          force: true,
        });
        const handler = actions.get(data.action);
        if (!handler)
          throw new UserError(
            "This dashboard operation has not passed JavaScript migration checks.",
          );
        const result = await handler({
          guild,
          actor,
          params:
            data.params &&
            typeof data.params === "object" &&
            !Array.isArray(data.params)
              ? data.params
              : {},
        });
        return respond(res, 200, { ok: true, ...result });
      }
      return respond(res, 404, { error: "Unknown endpoint" });
    } catch (error) {
      const disk = ["ENOSPC", "EDQUOT", "EACCES", "EROFS"].includes(error.code);
      console.warn(
        JSON.stringify({
          event: "api_failed",
          name: error.name,
          code: error.code,
        }),
      );
      if (!res.headersSent)
        respond(
          res,
          disk
            ? 507
            : error instanceof UserError || error instanceof SyntaxError
              ? 400
              : 500,
          {
            error: disk
              ? "Storage failed; the change could not be saved."
              : error instanceof UserError
                ? error.message
                : "The request could not be completed.",
          },
        );
    }
  });
}
