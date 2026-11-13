import {
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  ChannelType,
  PermissionFlagsBits as P,
} from "discord.js";
import { randomUUID } from "node:crypto";
import { UserError, canModerate, assertAllowed } from "../access.js";
import {
  embed,
  KeyedQueue,
  nowISO,
  shortId,
  text,
  channelPermissions,
  targetMembers,
} from "../helpers.js";
import {
  CVMS_DEFAULTS,
  cvmsConfig,
  consentScope,
  hasConsent,
  matchRules,
  recognitionRuns,
  CONSENT_HOURS,
} from "./cvms-config.js";
import { LocalSpeech, SpeechQueue, VoiceCapture } from "./cvms-audio.js";

export const CASES = "cvms_cases.json";
const HOURS = 3600000;
export const privacyText = (cfg) =>
  `CVMS is an experimental, local-only voice moderation tool. While it is active, short clips of your speech are processed on Rallybit's Raspberry Pi to check server-configured phrases. Raw audio is kept briefly in memory and is not saved as a recording or sent to an external AI provider. Possible matches create private staff cases containing a short text excerpt for up to ${cfg.retention_hours} hours. Staff log messages are removed at expiry where Discord access permits. Staff must review context before any action. Speech recognition can make mistakes; its score is not proof of misconduct. Consent lasts ${CONSENT_HOURS} hours and may be withdrawn with /cvms consent enabled:false. Monitoring pauses if anyone present has not consented. Privacy: https://rallybit.pages.dev/privacy-policy`;
export function casePayload(guildId, row) {
  const card = embed(
    `CVMS case ${row.id}`,
    row.excerpt
      ? `**Possible phrase match — human review required**\n${text(row.excerpt, 500)}`
      : "No speech excerpt retained.",
  ).addFields(
    { name: "Member", value: `<@${row.user_id}>`, inline: true },
    { name: "Voice channel", value: `<#${row.channel_id}>`, inline: true },
    { name: "Status", value: row.status, inline: true },
    {
      name: "Recognition score",
      value: `${Math.round((row.score ?? 0) * 100)}% (approximate ASR score, not violation confidence)`,
    },
    { name: "Expires", value: `<t:${Math.floor(row.expires_at / 1000)}:R>` },
  );
  const buttons =
    row.status === "pending"
      ? ["confirm", "dismiss"]
      : row.status === "confirmed"
        ? ["warn", "disconnect", "restrict", "dismiss"]
        : [];
  return {
    embeds: [card],
    components: buttons.length
      ? [
          new ActionRowBuilder().addComponents(
            buttons.map((action) =>
              new ButtonBuilder()
                .setCustomId(`cvms:case:${guildId}:${row.id}:${action}`)
                .setLabel(
                  {
                    confirm: "Mark reviewed",
                    dismiss: "Dismiss",
                    warn: "Warn member",
                    disconnect: "Disconnect",
                    restrict: "Restrict this VC (10m)",
                  }[action],
                )
                .setStyle(
                  ["disconnect", "restrict"].includes(action)
                    ? ButtonStyle.Danger
                    : ButtonStyle.Secondary,
                ),
            ),
          ),
        ]
      : [],
    allowedMentions: { parse: [] },
  };
}
export class CvmsService {
  constructor(
    services,
    {
      provider = new LocalSpeech(),
      capture = (options) => new VoiceCapture(options),
      now = () => Date.now(),
      maxSessions = 2,
    } = {},
  ) {
    Object.assign(this, services);
    this.provider = provider;
    this.capture = capture;
    this.now = now;
    this.maxSessions = Math.min(2, Math.max(1, maxSessions));
    this.lastCleanup = 0;
    this.sessions = new Map();
    this.paused = new Set();
    this.pauseVersion = 0;
    this.queue = new KeyedQueue();
    this.requests = new Map();
    this.waiting = new Map();
    this.closed = false;
    this.speech = new SpeechQueue(provider, {
      onError: (_error, row) => {
        const session = this.sessions.get(row.guildId);
        if (session?.id === row.sessionId)
          void this.stop(
            row.guildId,
            "Local speech processing failed. Monitoring stopped.",
            { pause: true },
          ).catch(() => {});
      },
    });
  }
  async config(id) {
    return cvmsConfig((await this.store.read("cvms_settings.json"))[id]);
  }
  async update(id, change) {
    await this.store.update("cvms_settings.json", (data) => {
      const cfg = cvmsConfig(data[id]);
      change(cfg);
      data[id] = cvmsConfig(cfg);
    });
  }
  async beta(id) {
    return (await this.store.read("cvms_beta.json"))[id]?.enabled === true;
  }
  async staff(guild, userId) {
    const member = await guild.members.fetch({ user: userId, force: true });
    if (!(await canModerate(member, "warn", this.store)))
      throw new UserError(
        "You need the configured warning role and Manage Messages.",
      );
    return member;
  }
  async gate(guild, cfg) {
    this.assertOwner?.();
    if (this.closed) throw new UserError("CVMS is stopping.");
    if ((await this.store.read("notice.json")).active)
      throw new UserError("Rallybit is paused for maintenance.");
    await assertAllowed(this.store, guild.id, null);
    if (!(await this.beta(guild.id)))
      throw new UserError(
        "This server has not been admitted to the free CVMS beta.",
      );
    if (!cfg.enabled) throw new UserError("CVMS is disabled for this server.");
    if (!cfg.log_channel_id || !cfg.rules.length)
      throw new UserError(
        "Configure a private log channel and at least one phrase rule first.",
      );
  }
  async voiceChannel(guild, selected) {
    if (!selected?.id) throw new UserError("Choose a voice channel in this server.");
    // Resolve the authoritative guild channel, not a partial interaction object.
    const channel = await guild.channels.fetch(String(selected.id));
    if (!channel || (channel.guild?.id ?? channel.guildId) !== guild.id)
      throw new UserError("That channel is not available in this server.");
    if (channel.type === ChannelType.GuildStageVoice)
      throw Object.assign(new UserError("That is a Stage channel. Choose a standard voice channel; Stage channels are not supported in this beta."), { code: "CVMS_STAGE_CHANNEL" });
    if (channel.type !== ChannelType.GuildVoice)
      throw Object.assign(new UserError("That is not a standard voice channel. Choose a voice channel, not a text channel or category."), { code: "CVMS_NOT_VOICE" });
    return channel;
  }
  async access(guild, channel, cfg) {
    if (!cfg.channel_ids.includes(channel.id))
      throw new UserError(
        "That voice channel has not been enabled for CVMS. An administrator can add it with /cvms channel action:add, or select it with /cvms start.",
      );
    const bot = await guild.members.fetchMe({ force: true });
    channelPermissions(channel, bot, [
      P.ViewChannel,
      P.Connect,
      P.SendMessages,
      P.EmbedLinks,
    ]);
    if (bot.voice?.serverDeaf)
      throw new UserError(
        "Rallybit is server-deafened. A moderator must undeafen it first.",
      );
    const log = await guild.channels.fetch(String(cfg.log_channel_id));
    if (log?.type !== ChannelType.GuildText)
      throw new UserError("The private CVMS log channel is unavailable.");
    channelPermissions(log, bot, [P.ViewChannel, P.SendMessages, P.EmbedLinks]);
    if (log.permissionsFor(guild.roles.everyone)?.has(P.ViewChannel))
      throw new UserError(
        "CVMS needs a staff log channel that is not visible to @everyone.",
      );
    return { bot, log };
  }
  async prepareChannel(guild, selected, user, { continuous = false } = {}) {
    return this.queue.run("sessions", async () => {
      const channel = await this.voiceChannel(guild, selected),
        cfg = await this.config(guild.id),
        actor = await guild.members.fetch({ user: user.id, force: true });
      await this.gate(guild, cfg);
      if (this.sessions.has(guild.id))
        throw new UserError("CVMS is already active. Use /cvms stop before selecting a new session.");
      channelPermissions(channel, actor, [P.ViewChannel, P.Connect]);
      const adding = !cfg.channel_ids.includes(channel.id),
        changingContinuous = continuous &&
          (cfg.mode !== "always" || cfg.continuous_channel_id !== channel.id);
      if ((adding || changingContinuous) && !actor.permissions.has(P.ManageGuild))
        throw new UserError(adding
          ? "That voice channel has not been enabled for CVMS. Ask an administrator to add it with /cvms channel action:add."
          : "Enabling 24/7 monitoring for a channel requires Manage Server. Ask an administrator to configure it first.");
      if (adding && cfg.channel_ids.length >= 20)
        throw new UserError("The 20-channel CVMS limit has been reached. Remove an unused channel first.");
      await this.access(guild, channel, { ...cfg, channel_ids: [...cfg.channel_ids, channel.id] });
      if (adding || changingContinuous)
        await this.update(guild.id, (current) => {
          if (!current.channel_ids.includes(channel.id)) {
            if (current.channel_ids.length >= 20)
              throw new UserError("The 20-channel CVMS limit has been reached.");
            current.channel_ids.push(channel.id);
          }
          if (continuous) {
            current.mode = "always";
            current.continuous_channel_id = channel.id;
          }
        });
      if (continuous) {
        // Arming does not join: eligibility and consent are checked by start.
        await this.resume(guild.id);
      }
      this.waiting.delete(guild.id);
      return channel;
    });
  }
  async resume(guildId) {
    await this.store.update("cvms_pauses.json", (data) => { delete data[guildId]; });
    this.pauseVersion++;
    this.paused.delete(guildId);
  }
  async eligible(guild, selected, cfg) {
    const channel = await this.voiceChannel(guild, selected),
      { log } = await this.access(guild, channel, cfg);
    // Voice state IDs are authoritative even when a member was not included in
    // the initial member cache. Do not mistake an uncached person for consent.
    for (const state of guild.voiceStates?.cache.values() ?? [])
      if (state.channelId === channel.id && !channel.members.has(state.id))
        await guild.members.fetch({ user: state.id, force: true });
    const humans = [...channel.members.values()].filter(
      (member) => !member.user.bot,
    );
    if (!humans.length)
      throw new UserError("No members are present in that voice channel.");
    const records =
      (await this.store.read("cvms_consents.json"))[guild.id] ?? {};
    if (
      humans.some((member) => !hasConsent(records[member.id], cfg, this.now()))
    )
      throw new UserError(
        "Monitoring is paused until every member in the voice channel gives current consent with /cvms consent enabled:true.",
      );
    return { humans, records, log };
  }
  async consentPrompt(i) {
    const cfg = await this.config(i.guildId),
      token = shortId() + shortId(),
      scope = consentScope(cfg);
    if (this.requests.size > 1000)
      for (const [id, row] of this.requests)
        if (row.expires < this.now()) this.requests.delete(id);
    if (this.requests.size >= 1000)
      throw new UserError("Too many pending requests. Try again shortly.");
    this.requests.set(token, {
      guildId: i.guildId,
      userId: i.user.id,
      scope,
      expires: this.now() + 600000,
    });
    return {
      embeds: [embed("CVMS voice processing consent", privacyText(cfg))],
      components: [
        new ActionRowBuilder().addComponents(
          new ButtonBuilder()
            .setCustomId(`cvms:consent:${token}`)
            .setLabel("I agree for 24 hours")
            .setStyle(ButtonStyle.Primary),
        ),
      ],
    };
  }
  async consent(i, token) {
    return this.queue.run(`consent:${i.guildId}:${i.user.id}`, async () => {
      const request = this.requests.get(token),
        cfg = await this.config(i.guildId);
      if (
        !request ||
        request.guildId !== i.guildId ||
        request.userId !== i.user.id ||
        request.expires <= this.now() ||
        request.scope !== consentScope(cfg)
      )
        throw new UserError(
          "This consent request expired or the privacy settings changed. Run /cvms consent again.",
        );
      await this.store.update("cvms_consents.json", (data) => {
        (data[i.guildId] ??= {})[i.user.id] = {
          scope: request.scope,
          granted_at: nowISO(),
          expires_at: this.now() + CONSENT_HOURS * HOURS,
        };
      });
      this.requests.delete(token);
      return {
        content:
          "Consent saved for 24 hours in this server. You can withdraw at any time with /cvms consent enabled:false.",
        components: [],
      };
    });
  }
  async withdraw(guildId, userId) {
    for (const [token, row] of this.requests)
      if (row.guildId === guildId && row.userId === userId)
        this.requests.delete(token);
    const session = this.sessions.get(guildId);
    if (
      session &&
      (session.channel.members.has(userId) || session.captured.has(userId))
    )
      await this.stop(guildId, "A member withdrew consent.", { pause: true });
    await this.queue.run(`consent:${guildId}:${userId}`, () =>
      this.store.update("cvms_consents.json", (data) => {
        delete data[guildId]?.[userId];
      }),
    );
  }
  async start(
    guild,
    channel,
    actor,
    { minutes = 30, source = "manual", targets = [], until = Infinity, continuous = false } = {},
  ) {
    return this.queue.run("sessions", async () => {
      channel = await this.voiceChannel(guild, channel);
      const cfg = await this.config(guild.id);
      await this.gate(guild, cfg);
      if (this.sessions.has(guild.id))
        throw new UserError(
          "CVMS already has a voice session in this server. One bot can only monitor one voice channel per server at a time.",
        );
      if (this.sessions.size >= this.maxSessions)
        throw new UserError(
          "The local CVMS capacity is in use. No audio was captured; try again later.",
        );
      if (source !== "manual" && this.paused.has(guild.id))
        throw new UserError(
          "CVMS was paused. A moderator must explicitly start a new session.",
        );
      const { humans, records } = await this.eligible(guild, channel, cfg);
      const bot = await guild.members.fetchMe({ force: true });
      if (bot.voice?.channelId)
        throw new UserError(
          "Rallybit is already in a voice channel. Stop that connection first.",
        );
      if (!(await this.provider.health()))
        throw new UserError(
          "The local speech service is unavailable. No voice connection was opened.",
        );
      const id = randomUUID(),
        session = {
          id,
          guildId: guild.id,
          channel,
          cfg,
          source,
          continuous: continuous || source === "always",
          targets: new Set(targets),
          captured: new Set(),
          members: new Set(humans.map((m) => m.id)),
          consentExpires: Math.min(
            ...humans.map((m) => Number(records[m.id].expires_at)),
          ),
          endsAt: Math.min(
            continuous || source === "always"
              ? Infinity
              : this.now() + Math.max(5, Math.min(60, minutes)) * 60000,
            until,
          ),
          initializing: true,
          stopped: false,
          notice: null,
          capture: null,
        };
      const notice = embed(
        "CVMS voice monitoring",
        `**${channel.name}** will be monitored using local speech recognition.\n${source === "manual" || source === "always" ? "Consenting participants" : "Selected consenting participants"} are in scope. Possible matches go to private staff review; no automatic punishments are enabled.\nAudio is not saved as recordings. Members can withdraw consent or stop monitoring.\n${Number.isFinite(session.endsAt) ? `Session ends <t:${Math.floor(session.endsAt / 1000)}:R>.` : "24/7 monitoring is enabled while eligibility and everyone's current consent allow it."}\nhttps://rallybit.pages.dev/privacy-policy`,
      );
      const controls = new ActionRowBuilder().addComponents(
        new ButtonBuilder()
          .setCustomId(`cvms:stop:${guild.id}:${id}`)
          .setLabel("Stop monitoring")
          .setStyle(ButtonStyle.Danger),
      );
      // Reserve before awaited disclosure so withdrawal/stop also cancels a
      // session that is still being prepared. No receiver exists at this point.
      this.sessions.set(guild.id, session);
      try {
        // Public disclosure is successfully posted before the receiver is opened.
        session.notice = await channel.send({
          embeds: [notice],
          components: [controls],
          allowedMentions: { parse: [] },
        });
        await this.store.update("cvms_sessions.json", (data) => {
          data[guild.id] = {
            channel_id: channel.id,
            notice_id: session.notice.id,
            started_at: nowISO(),
          };
        });
        if (session.stopped || this.sessions.get(guild.id) !== session)
          throw new UserError("The pending session was stopped.");
        await this.resume(guild.id);
        const latest = await this.config(guild.id);
        await this.gate(guild, latest);
        const fresh = await this.eligible(guild, channel, latest);
        if (
          session.stopped ||
          this.sessions.get(guild.id) !== session ||
          JSON.stringify(latest) !== JSON.stringify(cfg)
        )
          throw new UserError(
            "CVMS settings or consent changed while preparing the session.",
          );
        session.members = new Set(fresh.humans.map((member) => member.id));
        session.consentExpires = Math.min(
          ...fresh.humans.map((member) =>
            Number(fresh.records[member.id].expires_at),
          ),
        );
        const valid = (userId) => {
          if (
            session.stopped ||
            this.closed ||
            this.sessions.get(guild.id) !== session ||
            this.now() >= Math.min(session.endsAt, session.consentExpires)
          )
            return false;
          try {
            this.assertOwner?.();
          } catch {
            return false;
          }
          if (
            [...channel.members.values()].some(
              (m) => !m.user.bot && !session.members.has(m.id),
            )
          )
            return false;
          if (
            [...(guild.voiceStates?.cache.values() ?? [])].some(
              (state) =>
                state.channelId === channel.id &&
                state.id !== guild.client.user.id &&
                !session.members.has(state.id) &&
                !channel.members.get(state.id)?.user.bot,
            )
          )
            return false;
          if (!userId) return true;
          const member = channel.members.get(userId);
          return Boolean(
            member &&
            !member.user.bot &&
            session.members.has(userId) &&
            (!session.targets.size || session.targets.has(userId)),
          );
        };
        session.valid = valid;
        session.initializing = false;
        if (!valid())
          throw new UserError(
            "CVMS eligibility changed before the receiver opened.",
          );
        session.capture = this.capture({
          channel,
          valid,
          onClip: (userId, pcm) => {
            session.captured.add(userId);
            this.speech.submit({
              guildId: guild.id,
              sessionId: id,
              userId,
              pcm,
              valid: () => valid(userId),
              result: (segments) =>
                this.result(guild, session, userId, segments),
            });
          },
          onStop: (reason) => {
            void this.stop(guild.id, reason, { pause: true }).catch(() => {});
          },
        });
        await session.capture.start();
        if (!valid())
          throw new UserError("CVMS eligibility changed while joining.");
        this.waiting.delete(guild.id);
        return session;
      } catch (error) {
        try {
          await this.stop(guild.id, "Voice monitoring could not start.", {
            pause: true,
          });
        } finally {
          if (session.notice)
            await session.notice
              .edit({
                embeds: [
                  embed(
                    "CVMS monitoring did not start",
                    "The pending session was cancelled or could not pass its final checks. No further audio is being captured.",
                  ),
                ],
                components: [],
                allowedMentions: { parse: [] },
              })
              .catch(() => {});
        }
        throw error;
      }
    });
  }
  async stop(guildId, reason, { pause = false } = {}) {
    if (pause) {
      this.pauseVersion++;
      this.paused.add(guildId);
    }
    this.waiting.delete(guildId);
    const session = this.sessions.get(guildId);
    // Invalidate first, including inference already in flight.
    if (session) {
      session.stopped = true;
      this.sessions.delete(guildId);
      this.speech.cancel(session.id);
      session.capture?.stop();
    }
    try {
      await this.store.update("cvms_sessions.json", (data) => {
        delete data[guildId];
      });
      if (pause) {
        await this.store.update("cvms_pauses.json", (data) => {
          data[guildId] = { reason: text(reason, 300), paused_at: nowISO() };
        });
      }
    } finally {
      if (session?.notice)
        await session.notice
          .edit({
            embeds: [embed("CVMS monitoring stopped", text(reason, 1000))],
            components: [],
            allowedMentions: { parse: [] },
          })
          .catch(() => {});
    }
    return Boolean(session);
  }
  async result(guild, session, userId, segments) {
    const cfg = await this.config(guild.id);
    await this.gate(guild, cfg);
    await this.eligible(guild, session.channel, cfg);
    if (
      !session.valid(userId) ||
      consentScope(cfg) !== consentScope(session.cfg)
    )
      return;
    for (const segment of recognitionRuns(segments, cfg.threshold / 100)) {
      if (segment.score < cfg.threshold / 100) continue;
      const matches = matchRules(segment.text, cfg.rules);
      if (!matches.length) continue;
      const key = `${guild.id}:${userId}:${matches[0].id}`;
      if (
        !(await this.store.reserve(
          "cvms_dispatch.json",
          key,
          60000,
          this.now(),
        ))
      )
        continue;
      let row;
      await this.store.update(CASES, (data) => {
        const rows = (data[guild.id] ??= {}),
          recent = Object.values(rows).filter(
            (r) => r.created_epoch > this.now() - HOURS,
          );
        if (recent.length >= 10) return;
        const id = shortId() + shortId();
        row = {
          id,
          user_id: userId,
          channel_id: session.channel.id,
          rule_ids: matches.map((m) => m.id),
          excerpt: text(matches[0].excerpt, 500),
          score: segment.score,
          status: "pending",
          created_at: nowISO(),
          created_epoch: this.now(),
          expires_at: this.now() + cfg.retention_hours * HOURS,
          log_channel_id: cfg.log_channel_id,
          message_id: null,
          delivery_reserved: true,
        };
        rows[id] = row;
      });
      if (!row) return;
      // Check consent again after durable writes and immediately before posting.
      if (!session.valid(userId)) {
        await this.store.update(CASES, (data) => {
          delete data[guild.id]?.[row.id];
        });
        return;
      }
      const log = await guild.channels.fetch(String(cfg.log_channel_id));
      try {
        const message = await log.send({
          ...casePayload(guild.id, row),
          nonce: `cvms-${row.id}`,
          enforceNonce: true,
        });
        await this.store.update(CASES, (data) => {
          if (data[guild.id]?.[row.id])
            data[guild.id][row.id].message_id = message.id;
        });
      } catch (error) {
        await this.store.update(CASES, (data) => {
          if (data[guild.id]?.[row.id])
            data[guild.id][row.id].delivery_failed = true;
        });
        throw error;
      }
      break;
    }
  }
  async refresh(guild, row) {
    if (!row.message_id) return;
    const channel = await guild.channels.fetch(String(row.log_channel_id)),
      message = await channel.messages.fetch(String(row.message_id));
    if (message.author.id !== guild.client.user.id) return;
    await message.edit(casePayload(guild.id, row));
  }
  async review(i, id, action) {
    await this.staff(i.guild, i.user.id);
    return this.queue.run(`case:${i.guildId}:${id}`, async () => {
      let row;
      await this.store.update(CASES, (data) => {
        row = data[i.guildId]?.[id];
        if (!row || row.expires_at <= this.now())
          throw new UserError("That CVMS case has expired or does not exist.");
        if (!["pending", "confirmed"].includes(row.status))
          throw new UserError("That case is already closed.");
        if (!["confirm", "dismiss"].includes(action))
          throw new UserError("Choose confirm or dismiss.");
        Object.assign(row, {
          status: action === "confirm" ? "confirmed" : "dismissed",
          reviewed_by: i.user.id,
          reviewed_at: nowISO(),
        });
        if (action === "dismiss") row.excerpt = "";
        row = structuredClone(row);
      });
      await this.refresh(i.guild, row).catch(() => {});
      return {
        content:
          action === "confirm"
            ? "Case marked reviewed. No punishment has been applied. Staff action buttons are now available."
            : "Case dismissed. The retained excerpt was removed.",
      };
    });
  }
  async action(i, id, action, reason) {
    await this.staff(i.guild, i.user.id);
    return this.queue.run(`case:${i.guildId}:${id}`, async () => {
      const row = (await this.store.read(CASES))[i.guildId]?.[id];
      if (!row || row.expires_at <= this.now() || row.status !== "confirmed")
        throw new UserError(
          "A staff member must mark this case reviewed before taking action.",
        );
      const clean = text(reason, 500);
      if (clean.length < 3)
        throw new UserError("Explain the moderation reason.");
      const actor = await i.guild.members.fetch({
        user: i.user.id,
        force: true,
      });
      if (action === "warn") {
        const result = await this.moderation.execute(
          {
            ...i,
            id: `cvms-${id}-warn`,
            user: i.user,
            guild: i.guild,
            guildId: i.guildId,
          },
          "warn",
          row.user_id,
          { reason: clean },
        );
        await this.store.update(CASES, (data) => {
          Object.assign(data[i.guildId][id], {
            status: "warned",
            action_by: i.user.id,
            action_at: nowISO(),
          });
        });
        await this.refresh(i.guild, { ...row, status: "warned" }).catch(
          () => {},
        );
        return result;
      }
      if (!["disconnect", "restrict"].includes(action))
        throw new UserError("Unsupported CVMS action.");
      if (!actor.permissions.has(P.MoveMembers))
        throw new UserError("You need Move Members.");
      if (
        action === "restrict" &&
        (!actor.permissions.has(P.ManageChannels) ||
          !(await canModerate(actor, "timeout", this.store)))
      )
        throw new UserError(
          "Restricting a voice channel needs Manage Channels and your configured timeout permission.",
        );
      const { target, bot } = await targetMembers(
        i,
        row.user_id,
        P.MoveMembers,
      );
      if (target.voice.channelId !== row.channel_id)
        throw new UserError(
          "That member is no longer in the voice channel from this case.",
        );
      const channel = await i.guild.channels.fetch(row.channel_id);
      if (action === "restrict") {
        channelPermissions(channel, bot, [P.ManageChannels]);
        channelPermissions(channel, actor, [P.ViewChannel, P.ManageChannels]);
      }
      // A reserved action is never blindly replayed after a failed response.
      await this.store.update(CASES, (data) => {
        if (data[i.guildId][id].status !== "confirmed")
          throw new UserError("This case action was already attempted.");
        Object.assign(data[i.guildId][id], {
          status: "action_pending",
          action,
          action_by: i.user.id,
        });
      });
      if (action === "restrict") {
        const overwrite = channel.permissionOverwrites.cache.get(row.user_id),
          previous = overwrite?.allow.has(P.Connect)
            ? true
            : overwrite?.deny.has(P.Connect)
              ? false
              : null;
        await this.store.update("cvms_restrictions.json", (data) => {
          const key = `${i.guildId}:${row.channel_id}:${row.user_id}`;
          if (data[key])
            throw new UserError(
              "That member already has a pending voice restriction.",
            );
          data[key] = {
            guild_id: i.guildId,
            channel_id: row.channel_id,
            user_id: row.user_id,
            previous,
            expires_at: this.now() + 600000,
            status: "pending",
          };
        });
        await channel.permissionOverwrites.edit(
          row.user_id,
          { Connect: false },
          { reason: clean },
        );
      }
      await target.voice.disconnect(clean);
      await this.store.update(CASES, (data) => {
        Object.assign(data[i.guildId][id], {
          status: action === "restrict" ? "restricted" : "disconnected",
          action_at: nowISO(),
        });
      });
      await this.refresh(i.guild, {
        ...row,
        status: action === "restrict" ? "restricted" : "disconnected",
      }).catch(() => {});
      await this.audit(
        i,
        "moderation",
        `CVMS staff ${action}`,
        clean,
        row.user_id,
      );
      return {
        content:
          action === "restrict"
            ? "Member disconnected and restricted from this voice channel for 10 minutes. Other channels and permissions were not changed."
            : "Member disconnected from the voice channel.",
      };
    });
  }
  async addReport(i, report) {
    const cfg = await this.config(i.guildId);
    if (!cfg.report_integration)
      throw new UserError("CVMS report integration is disabled.");
    await this.gate(i.guild, cfg);
    const [actor, target] = await Promise.all([
      i.guild.members.fetch({ user: i.user.id, force: true }),
      i.guild.members.fetch({ user: report.target_id, force: true }),
    ]);
    if (
      !actor.voice?.channelId ||
      actor.voice.channelId !== target.voice?.channelId ||
      !cfg.channel_ids.includes(target.voice.channelId)
    )
      throw new UserError(
        "Voice reports must concern a member in your current configured voice channel.",
      );
    if (
      !(await this.store.reserve(
        "cvms_dispatch.json",
        `report:${i.guildId}`,
        60000,
        this.now(),
      ))
    )
      throw new UserError(
        "A voice report was queued recently. Wait one minute before requesting another monitoring session.",
      );
    await this.store.update("cvms_reports.json", (data) => {
      const rows = (data[i.guildId] ??= {});
      rows[report.target_id] = {
        user_id: report.target_id,
        channel_id: target.voice.channelId,
        report_id: report.report_id,
        expires_at: this.now() + 600000,
      };
    });
    return "CVMS will check eligibility for a short, consent-based report session. A report is not proof of misconduct.";
  }
  async voiceChanged(before, after) {
    const guild = after.guild,
      session = this.sessions.get(guild.id);
    if (!session) return;
    if (after.id === guild.client.user.id) {
      if (after.channelId !== session.channel.id || after.serverDeaf)
        await this.stop(
          guild.id,
          "Rallybit was moved, disconnected or deafened.",
          { pause: true },
        );
      return;
    }
    if (after.member?.user.bot) return;
    if (
      before.channelId !== after.channelId &&
      (before.channelId === session.channel.id ||
        after.channelId === session.channel.id)
    ) {
      // Stop immediately on membership changes. The scheduler can make a new,
      // freshly disclosed session only after every current participant consents.
      await this.stop(
        guild.id,
        "Voice channel membership changed. Consent will be checked again.",
      );
    }
  }
  async tick(client) {
    if (this.closed) return;
    this.assertOwner?.();
    const notices = (await this.store.read("notice.json")).active;
    for (const [gid, session] of this.sessions) {
      if (session.initializing) continue;
      const guild = client.guilds.cache.get(gid);
      try {
        if (!guild || notices)
          throw new Error("Session ended");
        const cfg = await this.config(gid);
        await this.gate(guild, cfg);
        const fresh = await this.eligible(guild, session.channel, cfg);
        session.consentExpires = Math.min(...fresh.humans.map((m) => Number(fresh.records[m.id].expires_at)));
        if (!session.valid() || consentScope(cfg) !== consentScope(session.cfg))
          throw new Error("Privacy settings changed");
      } catch {
        await this.stop(gid, "Monitoring paused or session expired.");
      }
    }
    const pauseVersion = this.pauseVersion,
      pauses = await this.store.read("cvms_pauses.json");
    // Do not revive a stale pause if an explicit resume finished during this read.
    if (pauseVersion === this.pauseVersion)
      for (const gid of Object.keys(pauses)) this.paused.add(gid);
    const now = this.now();
    for (const [token, row] of this.requests)
      if (row.expires <= now) this.requests.delete(token);
    for (const [gid, config] of Object.entries(
      await this.store.read("cvms_settings.json"),
    )) {
      const guild = client.guilds.cache.get(gid),
        cfg = cvmsConfig(config);
      if (
        !guild ||
        !cfg.enabled ||
        notices ||
        this.paused.has(gid) ||
        this.sessions.has(gid) ||
        this.sessions.size >= this.maxSessions
      )
        continue;
      if (!(await this.beta(gid))) continue;
      const reports = cfg.report_integration
        ? Object.values(
            (await this.store.read("cvms_reports.json"))[gid] ?? {},
          ).filter((row) => row.expires_at > now)
        : [];
      const watches =
        cfg.mode === "watch"
          ? Object.values(
              (await this.store.read("cvms_watch.json"))[gid] ?? {},
            ).filter((row) => row.expires_at > now)
          : [];
      let candidate;
      for (const row of [...reports, ...watches]) {
        const member = await guild.members
          .fetch({ user: row.user_id, force: true })
          .catch(() => null);
        if (
          member?.voice?.channelId &&
          cfg.channel_ids.includes(member.voice.channelId)
        ) {
          candidate = {
            channel: member.voice.channel,
            targets: [row.user_id],
            minutes: Math.max(5, Math.ceil((row.expires_at - now) / 60000)),
            until: row.expires_at,
            source: reports.includes(row) ? "report" : "watch",
          };
          break;
        }
      }
      if (!candidate && cfg.mode === "always")
        for (const id of cfg.continuous_channel_id ? [cfg.continuous_channel_id] : cfg.channel_ids) {
          const channel = await guild.channels.fetch(id).catch(() => null);
          if (
            channel?.members &&
            [...channel.members.values()].some((m) => !m.user.bot)
          ) {
            candidate = { channel, targets: [], continuous: true, source: "always" };
            break;
          }
        }
      if (candidate)
        try {
          await this.start(guild, candidate.channel, client.user, candidate);
        } catch (error) {
          const reason = error instanceof UserError ? error.message : "Voice monitoring could not start. Check Rallybit's service logs.";
          if (this.waiting.get(gid) !== reason)
            console.warn(JSON.stringify({ event: "cvms_waiting", guild_id: gid, channel_id: candidate.channel.id, channel_type: candidate.channel.type, code: error.code ?? null, reason: error instanceof UserError ? reason : undefined }));
          this.waiting.set(gid, reason);
          if (!(error instanceof UserError))
            console.warn(
              JSON.stringify({
                event: "cvms_start_failed",
                name: error.name,
                code: error.code,
              }),
            );
        }
      else if (cfg.mode === "always")
        this.waiting.set(gid, "Waiting for members to join the selected voice channel.");
    }
    if (now - this.lastCleanup >= 30000) {
      this.lastCleanup = now;
      await this.cleanup(client);
    }
  }
  async cleanup(client) {
    const now = this.now();
    for (const [key, row] of Object.entries(
      await this.store.read("cvms_restrictions.json"),
    )) {
      if (row.expires_at > now) continue;
      try {
        this.assertOwner?.();
        const guild = client.guilds.cache.get(row.guild_id);
        if (!guild) continue;
        const channel = await guild.channels
          .fetch(row.channel_id)
          .catch((error) => {
            if (error.code === 10003) return null;
            throw error;
          });
        if (channel) {
          const current = channel.permissionOverwrites.cache.get(row.user_id);
          if (current?.deny.has(P.Connect))
            await channel.permissionOverwrites.edit(
              row.user_id,
              { Connect: row.previous },
              { reason: "CVMS temporary voice restriction expired" },
            );
        }
        await this.store.update("cvms_restrictions.json", (data) => {
          delete data[key];
        });
      } catch (error) {
        console.warn(
          JSON.stringify({
            event: "cvms_restriction_retry",
            name: error.name,
            code: error.code,
          }),
        );
      }
    }
    for (const [gid, rows] of Object.entries(await this.store.read(CASES))) {
      const sorted = Object.values(rows).sort(
          (a, b) => b.created_epoch - a.created_epoch,
        ),
        expired = sorted.filter(
          (row, index) => row.expires_at <= now || index >= 250,
        );
      for (const row of expired) {
        // Redact before a potentially unavailable Discord deletion request.
        await this.store.update(CASES, (data) => {
          if (data[gid]?.[row.id])
            Object.assign(data[gid][row.id], {
              excerpt: "",
              rule_ids: [],
              status: "expired",
            });
        });
        try {
          const guild = client.guilds.cache.get(gid);
          if (row.message_id && guild) {
            const channel = await guild.channels.fetch(
                String(row.log_channel_id),
              ),
              message = await channel.messages.fetch(String(row.message_id));
            if (message.author.id === client.user.id) await message.delete();
          } else if (row.message_id) continue;
          await this.store.update(CASES, (data) => {
            delete data[gid]?.[row.id];
          });
        } catch (error) {
          if ([10003, 10008].includes(error.code))
            await this.store.update(CASES, (data) => {
              delete data[gid]?.[row.id];
            });
        }
      }
      const obsolete = sorted.filter(
        (row, index) => row.expires_at < now - 30 * 24 * HOURS || index >= 1000,
      );
      if (obsolete.length)
        await this.store.update(CASES, (data) => {
          for (const row of obsolete)
            if (data[gid]?.[row.id]?.status === "expired")
              delete data[gid][row.id];
        });
    }
    for (const file of [
      "cvms_watch.json",
      "cvms_reports.json",
      "cvms_consents.json",
    ]) {
      const snapshot = await this.store.read(file);
      if (
        Object.values(snapshot).some((rows) =>
          Object.values(rows).some((row) => row.expires_at <= now),
        )
      )
        await this.store.update(file, (data) => {
          for (const rows of Object.values(data))
            for (const [id, row] of Object.entries(rows))
              if (row.expires_at <= now) delete rows[id];
        });
    }
    const dispatch = await this.store.read("cvms_dispatch.json");
    if (Object.values(dispatch).some((expiry) => Number(expiry) < now - HOURS))
      await this.store.update("cvms_dispatch.json", (data) => {
        for (const [key, expiry] of Object.entries(data))
          if (Number(expiry) < now - HOURS) delete data[key];
      });
  }
  async recover(client) {
    for (const [gid, row] of Object.entries(
      await this.store.read("cvms_sessions.json"),
    )) {
      const guild = client.guilds.cache.get(gid);
      if (guild)
        try {
          const channel = await guild.channels.fetch(row.channel_id),
            message = await channel.messages.fetch(row.notice_id);
          if (message.author.id === client.user.id)
            await message.edit({
              embeds: [
                embed(
                  "CVMS session ended",
                  "Rallybit restarted. This previous session is no longer recording. A new session requires fresh eligibility checks and a new notice.",
                ),
              ],
              components: [],
              allowedMentions: { parse: [] },
            });
        } catch {}
    }
    await this.store.write("cvms_sessions.json", {});
  }
  close() {
    this.closed = true;
    this.speech.stop();
    for (const [id, session] of this.sessions) {
      session.stopped = true;
      session.capture?.stop();
      this.sessions.delete(id);
    }
    this.requests.clear();
    this.waiting.clear();
  }
}
