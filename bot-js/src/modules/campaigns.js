import {
  ActionRowBuilder,
  ModalBuilder,
  TextInputBuilder,
  TextInputStyle,
  PermissionFlagsBits as P,
  MessageFlags,
} from "discord.js";
import { UserError } from "../access.js";
import {
  embed,
  guildSettings,
  nowISO,
  shortId,
  text,
  updateSettings,
} from "../helpers.js";
import { privateReply } from "../components.js";
import { DurableScheduler } from "../scheduler.js";
import {
  ACTIVITY_DEFAULTS,
  CONTENT,
  epoch,
  EventService,
  sample,
} from "./events.js";
import { randomInt } from "node:crypto";

const KINDS = ["activity", "quiz", "pulse", "icebreaker", "giveaway"];
const categories = new Set(CONTENT.QUESTION_BANK.map((row) => row.category));
export class CampaignService {
  constructor(services) {
    Object.assign(this, services);
    this.events = new EventService(services);
    this.scheduler = null;
  }
  attachScheduler(leases) {
    if (this.scheduler) throw new Error("Scheduler already attached.");
    this.scheduler = new DurableScheduler({ store: this.store, leases });
  }
  async activityAccess(i) {
    const member = await i.guild.members.fetch({
        user: i.user.id,
        force: true,
      }),
      settings = await guildSettings(
        this.store,
        "activity_settings.json",
        i.guildId,
        ACTIVITY_DEFAULTS,
      );
    if (
      !member.permissions.has(P.Administrator) &&
      !member.roles.cache.has(String(settings.permitted_role))
    )
      throw new UserError(
        "You need Administrator or the configured activity-check role.",
      );
    return settings;
  }
  async icebreaker(
    guild,
    channel,
    { category = "mixed", ping_role_id = null } = {},
  ) {
    if (
      !(await this.store.reserve(
        "community_dispatch.json",
        `icebreaker:${guild.id}`,
        30000,
      ))
    )
      throw new UserError(
        "A conversation starter was just posted. Wait 30 seconds before posting another.",
      );
    const pool =
        CONTENT.ICEBREAKERS[category] ??
        Object.values(CONTENT.ICEBREAKERS).flat(),
      prompt = pool[randomInt(pool.length)],
      mention = await this.events.ping(channel, ping_role_id);
    const message = await channel.send({
      embeds: [embed("Conversation starter", prompt)],
      ...mention,
    });
    return message;
  }
  async runSchedule(
    guild,
    id,
    schedule,
    { manualId = null, assertOwner = () => {} } = {},
  ) {
    if (!this.scheduler)
      throw new UserError("The scheduler is not connected to the runtime.");
    if (!KINDS.includes(schedule.kind))
      throw new UserError("Unknown schedule type.");
    const channel = await guild.channels.fetch(String(schedule.channel_id));
    if (!channel?.send)
      throw new UserError("The scheduled text channel no longer exists.");
    const interval =
      Math.max(5, Number(schedule.interval_minutes) || 60) * 60000;
    return this.scheduler.dispatch({
      key: manualId
        ? `manual:${guild.id}:${id}:${manualId}`
        : `automation:${guild.id}:${id}`,
      intervalMs: interval,
      cooldownKey: `${schedule.kind}:${guild.id}`,
      minimumGapMs: schedule.kind === "quiz" ? 300000 : 60000,
      send: async (assertJob) => {
        assertOwner();
        assertJob();
        const options = schedule.options ?? {},
          role = schedule.ping_role_id;
        if (schedule.kind === "icebreaker") {
          await this.icebreaker(guild, channel, {
            category: options.category,
            ping_role_id: role,
          });
          return "Conversation starter posted.";
        }
        const settings =
          schedule.kind === "activity"
            ? {
                ...(await guildSettings(
                  this.store,
                  "activity_settings.json",
                  guild.id,
                )),
                ...(role ? { ping_target: `<@&${role}>` } : {}),
              }
            : undefined;
        const result = await this.events.start(
          schedule.kind,
          guild,
          channel,
          guild.client.user,
          {
            automatic: true,
            ping_role_id: role,
            settings,
            category: options.category,
            duration_seconds: options.duration_seconds,
            duration_minutes: options.duration_minutes,
            prize: options.message,
            prompt: options.message,
            winners: options.winners,
          },
        );
        return `${schedule.kind} started: ${result.check_id ?? result.quiz_id ?? result.pulse_id ?? result.giveaway_id}`;
      },
    });
  }
  async tick(client, assertOwner = () => {}) {
    if (!this.scheduler) throw new Error("Native scheduler not attached");
    if ((await this.store.read("notice.json")).active) return;
    const now = Date.now() / 1000,
      all = await this.store.read("automation_schedules.json");
    for (const [guildId, schedules] of Object.entries(all)) {
      const guild = client.guilds.cache.get(guildId);
      if (!guild) continue;
      for (const [id, row] of Object.entries(schedules)) {
        if (row.enabled === false || Number(row.next_run) > now) continue;
        assertOwner();
        let reserved = false;
        await this.store.update("automation_schedules.json", (data) => {
          const current = data[guildId]?.[id];
          if (
            !current ||
            current.enabled === false ||
            Number(current.next_run) > now
          )
            return;
          current.next_run =
            now + Math.max(5, Number(current.interval_minutes) || 60) * 60;
          reserved = true;
        });
        if (!reserved) continue;
        try {
          const result = await this.runSchedule(guild, id, row, {
            assertOwner,
          });
          await this.store.update("automation_schedules.json", (data) => {
            const current = data[guildId]?.[id];
            if (current)
              Object.assign(current, {
                last_run: nowISO(),
                last_result:
                  result.status === "sent" ? result.result : result.status,
                last_error: null,
              });
          });
        } catch (error) {
          await this.store.update("automation_schedules.json", (data) => {
            if (data[guildId]?.[id])
              data[guildId][id].last_error = text(error.message, 300);
          });
        }
      }
    }
    // Legacy automatic activity/quiz settings share the same durable scheduler
    // and guild-level cooldowns as independent automation schedules.
    for (const [file, kind, enabledKey, channelKey, intervalKey, lastKey] of [
      [
        "activity_settings.json",
        "activity",
        "auto_enabled",
        "auto_channel",
        "auto_hours",
        "last_auto_check",
      ],
      [
        "quiz_settings.json",
        "quiz",
        "enabled",
        "channel_id",
        "interval_hours",
        "last_run",
      ],
    ])
      for (const [guildId, row] of Object.entries(
        await this.store.read(file),
      )) {
        const guild = client.guilds.cache.get(guildId);
        if (!guild || !row?.[enabledKey] || !row[channelKey]) continue;
        const interval =
          Math.max(
            1,
            Math.min(
              168,
              Number(row[intervalKey]) || (kind === "quiz" ? 12 : 1),
            ),
          ) * 3600;
        if (now - (epoch(row[lastKey]) || 0) < interval) continue;
        assertOwner();
        let reserved = false;
        await this.store.update(file, (data) => {
          const current = data[guildId];
          if (
            !current?.[enabledKey] ||
            now - (epoch(current[lastKey]) || 0) < interval
          )
            return;
          current[lastKey] = nowISO();
          reserved = true;
        });
        if (!reserved) continue;
        const schedule = {
          kind,
          channel_id: row[channelKey],
          interval_minutes: interval / 60,
          ping_role_id: row.ping_role_id,
          options: {
            category: row.category,
            duration_seconds: row.duration_seconds,
          },
        };
        try {
          await this.runSchedule(guild, `legacy-${kind}`, schedule, {
            assertOwner,
          });
        } catch (error) {
          console.warn(
            JSON.stringify({
              event: "legacy_schedule_attempt_failed",
              kind,
              code: error.code,
              name: error.name,
            }),
          );
        }
      }
  }
}
export function registerCampaigns(registry, services) {
  const service = new CampaignService(services),
    { events } = service;
  const admin = { guildOnly: true, permissions: ["Administrator"] },
    manager = { guildOnly: true, permissions: ["ManageGuild"] },
    messages = { guildOnly: true, permissions: ["ManageMessages"] },
    publicPolicy = { guildOnly: true };
  events.registerComponents(services.components);
  registry.register(
    "activitycheck",
    async (i) => {
      const settings = await service.activityAccess(i);
      const result = await events.start(
        "activity",
        i.guild,
        i.channel,
        i.user,
        { settings },
      );
      return { content: `Activity check \`${result.check_id}\` started.` };
    },
    publicPolicy,
  );
  registry.register(
    "endactivitycheck",
    async (i) => {
      await service.activityAccess(i);
      if (!(await events.rows("activity"))[i.guildId])
        throw new UserError("No activity check is active.");
      await events.finish("activity", i.guild, i.guildId, "stopped");
      return {
        content: "Activity check ended; recorded participation was retained.",
      };
    },
    publicPolicy,
  );
  const activitySettings = {
    setlogs: (i) => ({
      log_channel_id: i.options.getChannel("channel", true).id,
    }),
    setreactor: (i) => ({ reactor: i.options.getString("emoji", true) }),
    setping: (i) => ({
      ping_target: i.options.getRole("role")
        ? `<@&${i.options.getRole("role").id}>`
        : "@everyone",
    }),
    setwinner: (i) => ({ winner_count: i.options.getInteger("count", true) }),
    setduration: (i) => ({
      check_duration_minutes: i.options.getInteger("minutes", true),
    }),
    setperm: (i) => ({ permitted_role: i.options.getRole("role", true).id }),
    setauto: (i) => ({
      auto_hours: i.options.getInteger("hours", true),
      auto_channel: i.options.getChannel("channel", true).id,
      auto_enabled: true,
      last_auto_check: nowISO(),
    }),
    stopauto: () => ({ auto_enabled: false }),
    setmode: (i) => ({ reactor_type: i.options.getString("mode", true) }),
    setbuttontext: (i) => ({
      button_text: text(i.options.getString("text", true), 80),
    }),
  };
  for (const [command, values] of Object.entries(activitySettings))
    registry.register(
      command,
      async (i) => {
        await updateSettings(
          services.store,
          "activity_settings.json",
          i.guildId,
          values(i),
        );
        return { content: "Activity-check settings saved." };
      },
      admin,
    );
  registry.register(
    "startauto",
    async (i) => {
      const settings = await guildSettings(
        services.store,
        "activity_settings.json",
        i.guildId,
      );
      if (!settings.auto_channel)
        throw new UserError(
          "Run /setauto to choose a channel and interval first.",
        );
      await updateSettings(
        services.store,
        "activity_settings.json",
        i.guildId,
        { auto_enabled: true, last_auto_check: nowISO() },
      );
      return {
        content:
          "Automatic checks enabled. The next check will use the configured interval.",
      };
    },
    admin,
  );
  registry.register(
    "setactivitytext",
    async (i) => {
      const privateReply = i.options.getBoolean("private") ?? false;
      return i.showModal(
        new ModalBuilder()
          .setCustomId(`activitytext:${i.user.id}:${privateReply ? "1" : "0"}`)
          .setTitle("Activity check text")
          .addComponents(
            new ActionRowBuilder().addComponents(
              new TextInputBuilder()
                .setCustomId("text")
                .setLabel("Activity check message")
                .setStyle(TextInputStyle.Paragraph)
                .setMinLength(5)
                .setMaxLength(1500)
                .setRequired(true),
            ),
          ),
      );
    },
    { ...admin, modal: true },
  );
  services.components.register("activitytext", async (i) => {
    const [, owner, visibility] = i.customId.split(":");
    if (owner !== i.user.id)
      throw new UserError("This form belongs to another user.");
    const member = await i.guild.members.fetch({
      user: i.user.id,
      force: true,
    });
    if (!member.permissions.has(P.Administrator))
      throw new UserError("Administrator is required.");
    await i.deferReply(
      visibility === "1" ? { flags: MessageFlags.Ephemeral } : {},
    );
    await updateSettings(services.store, "activity_settings.json", i.guildId, {
      activity_text: text(i.fields.getTextInputValue("text"), 1500),
    });
    return i.editReply({
      content: "Activity check text saved.",
      allowedMentions: { parse: [] },
    });
  });
  registry.register(
    "quiz start",
    async (i) => {
      const category = i.options.getString("category") ?? "mixed";
      if (category !== "mixed" && !categories.has(category))
        throw new UserError(`Choose mixed or: ${[...categories].join(", ")}.`);
      const cfg = await guildSettings(
          services.store,
          "quiz_settings.json",
          i.guildId,
        ),
        result = await events.start("quiz", i.guild, i.channel, i.user, {
          category,
          duration_seconds: i.options.getInteger("duration") ?? 30,
          ping_role_id: i.options.getBoolean("notify_role")
            ? cfg.ping_role_id
            : null,
        });
      return { content: `Quiz \`${result.quiz_id}\` started.` };
    },
    messages,
  );
  registry.register(
    "quiz stop",
    async (i) => {
      if (!(await events.rows("quiz"))[i.guildId])
        throw new UserError("No quiz is active.");
      await events.finish("quiz", i.guild, i.guildId, "stopped");
      return { content: "Quiz ended and results revealed." };
    },
    messages,
  );
  registry.register(
    "quiz setup",
    async (i) => {
      const category = i.options.getString("category") ?? "mixed";
      if (category !== "mixed" && !categories.has(category))
        throw new UserError("Unknown quiz category.");
      await updateSettings(services.store, "quiz_settings.json", i.guildId, {
        channel_id: i.options.getChannel("channel", true).id,
        interval_hours: i.options.getInteger("interval_hours") ?? 12,
        category,
        duration_seconds: i.options.getInteger("duration") ?? 30,
        ping_role_id: i.options.getRole("ping_role")?.id ?? null,
        last_run: nowISO(),
      });
      return {
        content: "Quiz schedule saved. Use /quiz auto to enable or pause it.",
      };
    },
    manager,
  );
  registry.register(
    "quiz auto",
    async (i) => {
      const enabled = i.options.getBoolean("enabled", true),
        cfg = await guildSettings(
          services.store,
          "quiz_settings.json",
          i.guildId,
        );
      if (enabled && !cfg.channel_id)
        throw new UserError("Run /quiz setup first.");
      await updateSettings(services.store, "quiz_settings.json", i.guildId, {
        enabled,
        ...(enabled ? { last_run: nowISO() } : {}),
      });
      return {
        content: `Automatic quizzes ${enabled ? "enabled" : "disabled"}.`,
      };
    },
    manager,
  );
  registry.register(
    "quiz pingrole",
    async (i) => {
      await updateSettings(services.store, "quiz_settings.json", i.guildId, {
        ping_role_id: i.options.getRole("role")?.id ?? null,
      });
      return { content: "Quiz notification role saved." };
    },
    manager,
  );
  registry.register(
    "quiz settings",
    async (i) => {
      const cfg = await guildSettings(
        services.store,
        "quiz_settings.json",
        i.guildId,
      );
      return {
        embeds: [
          embed(
            "Quiz settings",
            `Automatic: ${cfg.enabled ? "Enabled" : "Disabled"}\nChannel: ${cfg.channel_id ? `<#${cfg.channel_id}>` : "Not configured"}\nInterval: ${cfg.interval_hours ?? 12} hours\nCategory: ${cfg.category ?? "mixed"}\nDuration: ${cfg.duration_seconds ?? 30} seconds`,
          ),
        ],
      };
    },
    publicPolicy,
  );
  registry.register(
    "quiz leaderboard",
    async (i) => ({
      embeds: [
        embed(
          "Quiz leaderboard",
          Object.entries(
            (await services.store.read("quiz_stats.json"))[i.guildId] ?? {},
          )
            .sort((a, b) => (b[1].points ?? 0) - (a[1].points ?? 0))
            .slice(0, 15)
            .map(
              ([id, row], index) =>
                `${index + 1}. <@${id}> · ${row.points ?? 0} points · ${row.correct ?? 0} correct`,
            )
            .join("\n") || "No quiz results yet.",
        ),
      ],
    }),
    publicPolicy,
  );
  registry.register(
    "community icebreaker",
    async (i) => {
      const cfg = await guildSettings(
          services.store,
          "community_settings.json",
          i.guildId,
        ),
        message = await service.icebreaker(i.guild, i.channel, {
          category: i.options.getString("category") ?? "mixed",
          ping_role_id: i.options.getBoolean("notify_role")
            ? cfg.ping_role_id
            : null,
        });
      return { content: `Conversation starter posted: ${message.url}` };
    },
    publicPolicy,
  );
  registry.register(
    "community pulse",
    async (i) => {
      const cfg = await guildSettings(
          services.store,
          "community_settings.json",
          i.guildId,
        ),
        row = await events.start("pulse", i.guild, i.channel, i.user, {
          duration_minutes: i.options.getInteger("duration_minutes") ?? 10,
          prompt: i.options.getString("prompt"),
          ping_role_id:
            (i.options.getBoolean("notify_role") ?? true)
              ? cfg.ping_role_id
              : null,
        });
      return { content: `Community pulse \`${row.pulse_id}\` started.` };
    },
    messages,
  );
  registry.register(
    "community pingrole",
    async (i) => {
      await updateSettings(
        services.store,
        "community_settings.json",
        i.guildId,
        { ping_role_id: i.options.getRole("role")?.id ?? null },
      );
      return { content: "Community notification role saved." };
    },
    manager,
  );
  registry.register(
    "community settings",
    async (i) => {
      const cfg = await guildSettings(
        services.store,
        "community_settings.json",
        i.guildId,
      );
      return {
        content: `Community notification role: ${cfg.ping_role_id ? `<@&${cfg.ping_role_id}>` : "None"}\nPulse responses are anonymous to other members.`,
      };
    },
    publicPolicy,
  );
  registry.register(
    "community stoppulse",
    async (i) => {
      if (!(await events.rows("pulse"))[i.guildId])
        throw new UserError("No pulse is active.");
      await events.finish("pulse", i.guild, i.guildId, "stopped");
      return { content: "Community pulse ended." };
    },
    messages,
  );
  registry.register(
    "community pulsehistory",
    async (i) => ({
      embeds: [
        embed(
          "Community pulse history",
          ((await services.store.read("pulse_history.json"))[i.guildId] ?? [])
            .slice(-10)
            .reverse()
            .map(
              (row) =>
                `**${text(row.prompt, 100)}** · ${row.total ?? 0} responses\n${Object.entries(
                  row.counts ?? {},
                )
                  .map(([mood, count]) => `${mood}: ${count}`)
                  .join(" · ")}`,
            )
            .join("\n\n") || "No pulse history.",
        ),
      ],
    }),
    publicPolicy,
  );
  registry.register(
    "giveaway start",
    async (i) => {
      const cfg = await guildSettings(
          services.store,
          "giveaway_settings.json",
          i.guildId,
        ),
        channel =
          i.options.getChannel("channel") ??
          (cfg.default_channel_id
            ? await i.guild.channels.fetch(String(cfg.default_channel_id))
            : i.channel);
      const row = await events.start("giveaway", i.guild, channel, i.user, {
        prize: i.options.getString("prize", true),
        duration_minutes: i.options.getInteger("duration_minutes", true),
        winners: i.options.getInteger("winners") ?? 1,
        required_role_id: i.options.getRole("required_role")?.id,
        ping_role_id:
          i.options.getRole("ping_role")?.id ?? cfg.default_ping_role_id,
      });
      return {
        content: `Giveaway \`${row.giveaway_id}\` started in <#${channel.id}>.`,
      };
    },
    manager,
  );
  registry.register(
    "giveaway end",
    async (i) => {
      const id = i.options.getString("giveaway_id", true).trim().toUpperCase(),
        row = (await events.rows("giveaway"))[id];
      if (!row || String(row.guild_id) !== i.guildId)
        throw new UserError("Giveaway not found in this server.");
      await events.finish("giveaway", i.guild, id, "stopped");
      return { content: "Giveaway ended; winners were saved and revealed." };
    },
    manager,
  );
  registry.register(
    "giveaway reroll",
    async (i) => {
      const id = i.options.getString("giveaway_id", true).trim().toUpperCase(),
        rows =
          (await services.store.read("giveaway_history.json"))[i.guildId] ?? [],
        row = rows.find((row) => String(row.giveaway_id) === id);
      if (!row) throw new UserError("Completed giveaway not found.");
      const eligible = await events.eligible(
          i.guild,
          row.entrant_ids ?? [],
          row.required_role_id,
        ),
        winners = sample(eligible, i.options.getInteger("winners") ?? 1),
        request = i.id || shortId();
      await services.store.update("giveaway_history.json", (data) => {
        const current = data[i.guildId].find(
          (row) => String(row.giveaway_id) === id,
        );
        const rerolls = (current.rerolls ??= []);
        if (rerolls.some((row) => row.request_id === request))
          throw new UserError("This reroll was already performed.");
        rerolls.push({
          request_id: request,
          winner_ids: winners,
          actor_id: i.user.id,
          timestamp: nowISO(),
        });
        current.rerolls = rerolls.slice(-100);
      });
      return {
        content: winners.length
          ? `Rerolled giveaway ${id}: ${winners.map((id) => `<@${id}>`).join(", ")}.`
          : "No eligible entrants remain.",
      };
    },
    manager,
  );
  registry.register(
    "giveaway list",
    async (i) => ({
      embeds: [
        embed(
          "Active giveaways",
          Object.values(await events.rows("giveaway"))
            .filter((row) => String(row.guild_id) === i.guildId)
            .map(
              (row) =>
                `\`${row.giveaway_id}\` · ${text(row.prize, 100)} · <#${row.channel_id}> · <t:${Math.floor(epoch(row.end_time))}:R>`,
            )
            .join("\n") || "No active giveaways.",
        ),
      ],
    }),
    publicPolicy,
  );
  registry.register(
    "giveaway settings",
    async (i) => {
      await updateSettings(
        services.store,
        "giveaway_settings.json",
        i.guildId,
        {
          default_channel_id: i.options.getChannel("channel")?.id ?? null,
          default_ping_role_id: i.options.getRole("ping_role")?.id ?? null,
        },
      );
      return { content: "Giveaway defaults saved." };
    },
    manager,
  );
  for (const action of ["add", "remove", "toggle", "list", "run", "clear"])
    registry.register(
      `automation ${action}`,
      async (i) => {
        const file = "automation_schedules.json",
          id = i.options.getString("schedule_id")?.trim().toUpperCase();
        if (action === "add") {
          const kind = i.options.getString("kind", true),
            duration = i.options.getInteger("duration") ?? 30,
            interval = i.options.getInteger("interval_minutes", true),
            channel = i.options.getChannel("channel", true),
            sid = shortId();
          if (
            !KINDS.includes(kind) ||
            !channel.send ||
            channel.guild.id !== i.guildId
          )
            throw new UserError(
              "Choose a supported automation and server text channel.",
            );
          const row = {
            schedule_id: sid,
            kind,
            channel_id: channel.id,
            interval_minutes: interval,
            ping_role_id: i.options.getRole("ping_role")?.id ?? null,
            enabled: true,
            next_run: Date.now() / 1000 + interval * 60,
            created_by: i.user.id,
            created_at: nowISO(),
            options: {
              message: i.options.getString("message_or_prize"),
              category: i.options.getString("category") ?? "mixed",
              duration_minutes: duration,
              duration_seconds: Math.max(15, Math.min(120, duration)),
              winners: i.options.getInteger("winners") ?? 1,
            },
          };
          await services.store.update(file, (data) => {
            (data[i.guildId] ??= {})[sid] = row;
          });
          return {
            content: `Automation \`${sid}\` saved. First run is in ${interval} minutes.`,
          };
        }
        if (action === "list") {
          const rows = (await services.store.read(file))[i.guildId] ?? {};
          return {
            embeds: [
              embed(
                "Automation schedules",
                Object.entries(rows)
                  .slice(0, 30)
                  .map(
                    ([id, row]) =>
                      `\`${id}\` · ${row.kind} · ${row.enabled === false ? "Paused" : "Enabled"} · <#${row.channel_id}> · every ${row.interval_minutes} minutes`,
                  )
                  .join("\n") || "No schedules.",
              ),
            ],
          };
        }
        if (action === "run") {
          const row = (await services.store.read(file))[i.guildId]?.[id];
          if (!row) throw new UserError("Schedule not found.");
          const result = await service.runSchedule(i.guild, id, row, {
            manualId: i.id || shortId(),
          });
          return {
            content:
              result.status === "sent"
                ? result.result
                : `No announcement sent: ${result.status}. The shared anti-spam cooldown still applies.`,
          };
        }
        await services.store.update(file, (data) => {
          if (action === "clear") {
            data[i.guildId] = {};
            return;
          }
          const row = data[i.guildId]?.[id];
          if (!row) throw new UserError("Schedule not found.");
          if (action === "remove") delete data[i.guildId][id];
          else {
            row.enabled = i.options.getBoolean("enabled", true);
            if (row.enabled)
              row.next_run =
                Date.now() / 1000 +
                Math.max(5, Number(row.interval_minutes)) * 60;
          }
        });
        return { content: `Automation ${action} completed.` };
      },
      manager,
    );
  return service;
}
