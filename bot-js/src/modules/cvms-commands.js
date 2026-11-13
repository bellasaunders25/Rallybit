import {
  ModalBuilder,
  TextInputBuilder,
  TextInputStyle,
  ActionRowBuilder,
  PermissionFlagsBits as P,
  ChannelType,
} from "discord.js";
import { CvmsService, CASES, privacyText } from "./cvms.js";
import { UserError, canModerate } from "../access.js";
import {
  channelPermissions,
  embed,
  shortId,
  text,
  targetMembers,
} from "../helpers.js";
import { privateReply } from "../components.js";
import { normalizeSpeech } from "./cvms-config.js";

export function registerCvms(registry, services, options) {
  const service = new CvmsService(services, options),
    manager = { guildOnly: true, permissions: ["ManageGuild"] },
    staff = { guildOnly: true, moderation: "warn", private: true },
    publicPolicy = { guildOnly: true };
  registry.register(
    "cvms status",
    async (i) => {
      const cfg = await service.config(i.guildId),
        session = service.sessions.get(i.guildId),
        engine = await service.provider.health(),
        pause = (await services.store.read("cvms_pauses.json"))[i.guildId];
      return {
        embeds: [
          embed(
            "CVMS beta status",
            `Beta access: ${(await service.beta(i.guildId)) ? "Granted" : "Not granted"}\nEnabled: ${cfg.enabled ? "Yes" : "No"}\nMode: ${cfg.mode === "always" ? "24/7 (consent required)" : cfg.mode}\n24/7 channel: ${cfg.continuous_channel_id ? `<#${cfg.continuous_channel_id}>` : "Not pinned"}\nLocal speech service: ${engine ? "Ready" : "Unavailable"}\nActive coverage: ${session ? `<#${session.channel.id}>` : "None"}\nState: ${session ? session.continuous ? "Monitoring continuously" : "Timed session" : pause ? `Paused: ${text(pause.reason, 300)} — use /cvms start to resume.` : service.waiting.get(i.guildId) || "Not monitoring"}\nConfigured channels: ${cfg.channel_ids.length}\nPhrase rules: ${cfg.rules.length}\nDropped clips (capacity limit): ${service.speech.dropped}\n\nOne voice channel per server, up to two active sessions across this instance. English speech only in this beta. No automatic punishments. Accuracy and continuous coverage are not guaranteed.`,
          ),
        ],
      };
    },
    publicPolicy,
  );
  registry.register(
    "cvms beta",
    async (i) => {
      const enabled = i.options.getBoolean("enabled", true);
      if (!enabled)
        await service.stop(i.guildId, "Beta access removed.", { pause: true });
      await services.store.update("cvms_beta.json", (data) => {
        data[i.guildId] = {
          enabled,
          granted_by: i.user.id,
          updated_at: new Date().toISOString(),
        };
      });
      return {
        content: `Free CVMS beta access ${enabled ? "granted" : "removed"} for this server. Monitoring is not enabled by granting access.`,
      };
    },
    { guildOnly: true, developer: true, private: true },
  );
  registry.register(
    "cvms setup",
    async (i) => {
      const log = i.options.getChannel("log_channel", true),
        bot = await i.guild.members.fetchMe({ force: true }),
        actor = await i.guild.members.fetch({ user: i.user.id, force: true });
      if (log.type !== ChannelType.GuildText || log.guild.id !== i.guildId)
        throw new UserError("Choose a staff text channel in this server.");
      channelPermissions(log, actor, [P.ViewChannel]);
      channelPermissions(log, bot, [
        P.ViewChannel,
        P.SendMessages,
        P.EmbedLinks,
      ]);
      if (log.permissionsFor(i.guild.roles.everyone)?.has(P.ViewChannel))
        throw new UserError(
          "The staff case channel must not be visible to @everyone. Review other role access too.",
        );
      const enabled = i.options.getBoolean("enabled", true);
      if (enabled && !(await service.beta(i.guildId)))
        throw new UserError(
          "Ask a Rallybit developer to admit this server with /cvms beta first.",
        );
      await service.stop(i.guildId, "CVMS settings changed.", { pause: true });
      await service.update(i.guildId, (cfg) =>
        Object.assign(cfg, {
          enabled,
          log_channel_id: log.id,
          mode: i.options.getString("mode") ?? "manual",
          threshold: i.options.getInteger("threshold") ?? 75,
          retention_hours: i.options.getInteger("retention_hours") ?? 24,
        }),
      );
      return {
        content:
          "CVMS settings saved. Add a voice channel and phrase rules, obtain consent, then use /cvms start to begin. Settings changes pause monitoring. CVMS uses local processing only and never punishes automatically.",
      };
    },
    manager,
  );
  registry.register(
    "cvms channel",
    async (i) => {
      const action = i.options.getString("action", true),
        selected = i.options.getChannel("channel");
      if (action === "list")
        return {
          content:
            (await service.config(i.guildId)).channel_ids
              .map((id) => `<#${id}>`)
              .join("\n") || "No configured CVMS voice channels.",
        };
      const channel = await service.voiceChannel(i.guild, selected);
      const actor = await i.guild.members.fetch({
        user: i.user.id,
        force: true,
      });
      channelPermissions(channel, actor, [P.ViewChannel, P.Connect]);
      await service.stop(i.guildId, "CVMS channel settings changed.", {
        pause: true,
      });
      await service.update(i.guildId, (cfg) => {
        const ids = new Set(cfg.channel_ids);
        if (action === "add") ids.add(channel.id);
        else if (action === "remove") {
          ids.delete(channel.id);
          if (cfg.continuous_channel_id === channel.id) {
            cfg.continuous_channel_id = null;
            cfg.mode = "manual";
          }
        }
        else throw new UserError("Choose add or remove.");
        if (ids.size > 20)
          throw new UserError(
            "Up to 20 configured channels are allowed. Only one can be active per server.",
          );
        cfg.channel_ids = [...ids];
      });
      return {
        content: `CVMS voice channel ${action === "add" ? "added" : "removed"}. Monitoring remains paused until explicitly started.`,
      };
    },
    manager,
  );
  registry.register(
    "cvms rule",
    async (i) => {
      const action = i.options.getString("action", true),
        cfg = await service.config(i.guildId);
      if (action === "list")
        return {
          embeds: [
            embed(
              "CVMS phrase rules",
              cfg.rules
                .map((row) => `\`${row.id}\` — ${text(row.phrase, 120)}`)
                .join("\n") ||
                "No phrase rules. Rules are literal phrases, not context-aware verdicts.",
            ),
          ],
        };
      const phrase = text(i.options.getString("phrase"), 120),
        id = text(i.options.getString("rule_id"), 32).toUpperCase();
      await service.stop(i.guildId, "CVMS phrase rules changed.", {
        pause: true,
      });
      let created;
      await service.update(i.guildId, (config) => {
        if (action === "add") {
          if (normalizeSpeech(phrase).length < 3)
            throw new UserError(
              "Provide a readable phrase of at least three characters.",
            );
          if (config.rules.length >= 50)
            throw new UserError("Up to 50 rules are supported.");
          if (
            config.rules.some(
              (row) => normalizeSpeech(row.phrase) === normalizeSpeech(phrase),
            )
          )
            throw new UserError("That phrase is already configured.");
          created = shortId();
          config.rules.push({ id: created, phrase, enabled: true });
        } else if (action === "remove") {
          if (!config.rules.some((row) => row.id === id))
            throw new UserError("Rule not found.");
          config.rules = config.rules.filter((row) => row.id !== id);
        } else throw new UserError("Choose add or remove.");
      });
      return {
        content: `CVMS rule ${created ? `added: ${created}` : "removed"}. Monitoring is paused. Matches only create staff-review cases.`,
      };
    },
    { ...manager, private: true },
  );
  registry.register(
    "cvms watch",
    async (i) => {
      await service.staff(i.guild, i.user.id);
      const action = i.options.getString("action", true),
        user = i.options.getUser("member");
      if (action === "list") {
        const rows =
          (await services.store.read("cvms_watch.json"))[i.guildId] ?? {};
        return {
          content:
            Object.values(rows)
              .filter((row) => row.expires_at > service.now())
              .map(
                (row) =>
                  `<@${row.user_id}> until <t:${Math.floor(row.expires_at / 1000)}:R>`,
              )
              .join("\n") || "No active watches.",
        };
      }
      if (!user) throw new UserError("Choose a member.");
      if (action === "add") {
        await targetMembers(i, user.id);
        const reason = text(i.options.getString("reason"), 500);
        if (reason.length < 3) throw new UserError("Give a moderation reason.");
        await services.store.update("cvms_watch.json", (data) => {
          const rows = (data[i.guildId] ??= {});
          if (Object.keys(rows).length >= 50 && !rows[user.id])
            throw new UserError("The watch-list limit is 50 members.");
          rows[user.id] = {
            user_id: user.id,
            reason,
            added_by: i.user.id,
            expires_at:
              service.now() + (i.options.getInteger("minutes") ?? 60) * 60000,
          };
        });
      } else if (action === "remove")
        await services.store.update("cvms_watch.json", (data) => {
          delete data[i.guildId]?.[user.id];
        });
      else throw new UserError("Choose add or remove.");
      await service.stop(i.guildId, "The watch list changed.", { pause: true });
      return {
        content: `Watch ${action === "add" ? "saved" : "removed"}. Watches are time-limited and require everyone's consent. Start a session to resume monitoring.`,
      };
    },
    staff,
  );
  registry.register(
    "cvms reports",
    async (i) => {
      await service.stop(i.guildId, "Report integration settings changed.", {
        pause: true,
      });
      await service.update(i.guildId, (cfg) => {
        cfg.report_integration = i.options.getBoolean("enabled", true);
      });
      return {
        content:
          "Voice report integration saved. Reports do not bypass consent or trigger punishments. Use /cvms start to resume after this configuration change.",
      };
    },
    manager,
  );
  registry.register(
    "cvms consent",
    async (i) => {
      if (i.options.getBoolean("enabled", true))
        return service.consentPrompt(i);
      await service.withdraw(i.guildId, i.user.id);
      return {
        content:
          "Your CVMS consent has been withdrawn. Any session involving you was stopped.",
      };
    },
    { ...publicPolicy, private: true },
  );
  registry.register(
    "cvms privacy",
    async (i) => ({
      embeds: [
        embed(
          "CVMS privacy and limitations",
          privacyText(await service.config(i.guildId)),
        ),
      ],
    }),
    publicPolicy,
  );
  registry.register(
    "cvms start",
    async (i) => {
      await service.staff(i.guild, i.user.id);
      const actor = await i.guild.members.fetch({
          user: i.user.id,
          force: true,
        }),
        selected = i.options.getChannel("channel") ?? actor.voice.channel,
        minutes = i.options.getInteger("minutes"),
        cfg = await service.config(i.guildId),
        continuous = i.options.getBoolean("continuous") === true || (cfg.mode === "always" && minutes === null && i.options.getBoolean("continuous") !== false);
      if (!selected)
        throw new UserError("Choose a voice channel or join one first.");
      if (continuous && minutes !== null)
        throw new UserError("Choose either 24/7 monitoring or a timed session. Omit minutes when continuous is true.");
      if (!continuous && cfg.mode === "always")
        throw new UserError("This server is in 24/7 mode. Omit minutes to resume it, or use /cvms setup mode:manual before starting a timed session.");
      const channel = await service.prepareChannel(i.guild, selected, i.user, { continuous });
      let session;
      try {
        session = await service.start(i.guild, channel, i.user, {
          minutes: minutes ?? 30,
          continuous,
          source: "manual",
        });
      } catch (error) {
        session = service.sessions.get(i.guildId);
        if (!session || session.channel.id !== channel.id) {
          if (!continuous || !(error instanceof UserError)) throw error;
          service.waiting.set(i.guildId, error.message);
          return { content: `24/7 monitoring is configured for <#${channel.id}>, but no receiver is active yet. ${error.message} Check /cvms status. It can start automatically when eligibility allows; /cvms stop pauses it.` };
        }
      }
      return {
        content: `CVMS is active in <#${channel.id}>${session.continuous ? " in 24/7 mode while everyone's consent remains current" : ` until <t:${Math.floor(session.endsAt / 1000)}:R>`}. Matches go to private staff review. Use /cvms stop to pause it.`,
      };
    },
    staff,
  );
  const stopFor = async (i) => {
    const actor = await i.guild.members.fetch({ user: i.user.id, force: true }),
      session = service.sessions.get(i.guildId);
    if (
      !(await canModerate(actor, "warn", services.store)) &&
      (!session || actor.voice.channelId !== session.channel.id)
    )
      throw new UserError(
        "Only current monitored-channel participants or authorised staff can stop this session.",
      );
    await service.stop(
      i.guildId,
      "A participant or staff member stopped monitoring.",
      { pause: true },
    );
    return {
      content:
        "CVMS stopped. It will not automatically restart until staff explicitly starts a new session.",
    };
  };
  registry.register("cvms stop", stopFor, publicPolicy);
  registry.register(
    "cvms report",
    async (i) => {
      const user = i.options.getUser("member", true);
      if (user.id === i.user.id || user.bot)
        throw new UserError("Choose another member, not yourself or a bot.");
      const report = {
        target_id: user.id,
        reporter_id: i.user.id,
        report_id: shortId(),
        reason: i.options.getString("reason", true),
      };
      const result = await service.addReport(i, report),
        cfg = await service.config(i.guildId),
        log = await i.guild.channels.fetch(cfg.log_channel_id);
      await log.send({
        embeds: [
          embed("CVMS voice report", text(report.reason, 1000)).addFields(
            { name: "Reported member", value: `<@${user.id}>` },
            { name: "Reporter", value: `<@${i.user.id}>` },
            {
              name: "Important",
              value:
                "This report is not proof of misconduct and cannot bypass consent.",
            },
          ),
        ],
        allowedMentions: { parse: [] },
      });
      return { content: result };
    },
    { ...publicPolicy, private: true },
  );
  registry.register(
    "cvms cases",
    async (i) => {
      const rows = Object.values(
        (await services.store.read(CASES))[i.guildId] ?? {},
      )
        .filter((row) => row.expires_at > service.now())
        .sort((a, b) => b.created_epoch - a.created_epoch)
        .slice(0, 15);
      return {
        embeds: [
          embed(
            "CVMS staff cases",
            rows
              .map(
                (row) =>
                  `\`${row.id}\` · <@${row.user_id}> · ${row.status} · <#${row.channel_id}>`,
              )
              .join("\n") || "No retained cases.",
          ),
        ],
      };
    },
    staff,
  );
  registry.register(
    "cvms review",
    (i) =>
      service.review(
        i,
        text(i.options.getString("case_id", true), 32).toUpperCase(),
        i.options.getString("action", true),
      ),
    staff,
  );
  services.components.register("cvms", async (i) => {
    const [, kind, ...args] = i.customId.split(":");
    if (kind === "consent") {
      await i.deferReply({ flags: 64 });
      return privateReply(i, await service.consent(i, args[0]));
    }
    if (kind === "stop") {
      if (
        args[0] !== i.guildId ||
        service.sessions.get(i.guildId)?.id !== args[1]
      )
        throw new UserError("This monitoring session has already ended.");
      await i.deferReply({ flags: 64 });
      return privateReply(i, await stopFor(i));
    }
    const [gid, id, action, owner] = args;
    if (!i.guild || gid !== i.guildId)
      throw new UserError("This control belongs to another server.");
    await service.staff(i.guild, i.user.id);
    if (kind === "case" && ["confirm", "dismiss"].includes(action)) {
      await i.deferReply({ flags: 64 });
      return privateReply(i, await service.review(i, id, action));
    }
    if (
      kind === "case" &&
      ["warn", "disconnect", "restrict"].includes(action)
    ) {
      const modal = new ModalBuilder()
        .setCustomId(`cvms:action:${gid}:${id}:${action}:${i.user.id}`)
        .setTitle(`CVMS staff ${action}`);
      modal.addComponents(
        new ActionRowBuilder().addComponents(
          new TextInputBuilder()
            .setCustomId("reason")
            .setLabel("Reason after reviewing context")
            .setStyle(TextInputStyle.Paragraph)
            .setMinLength(3)
            .setMaxLength(500)
            .setRequired(true),
        ),
      );
      if (action !== "warn")
        modal.addComponents(
          new ActionRowBuilder().addComponents(
            new TextInputBuilder()
              .setCustomId("confirmation")
              .setLabel(`Type ${action.toUpperCase()} to confirm`)
              .setStyle(TextInputStyle.Short)
              .setMaxLength(20)
              .setRequired(true),
          ),
        );
      return i.showModal(modal);
    }
    if (kind === "action") {
      if (owner !== i.user.id)
        throw new UserError("This confirmation belongs to another moderator.");
      if (
        action !== "warn" &&
        i.fields.getTextInputValue("confirmation") !== action.toUpperCase()
      )
        throw new UserError("Confirmation did not match. No action was taken.");
      await i.deferReply({ flags: 64 });
      return privateReply(
        i,
        await service.action(
          i,
          id,
          action,
          i.fields.getTextInputValue("reason"),
        ),
      );
    }
    throw new UserError("Unknown CVMS control.");
  });
  return service;
}
