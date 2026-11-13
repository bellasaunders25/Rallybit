import {
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  PermissionFlagsBits as P,
  MessageFlags,
} from "discord.js";
import { readFile } from "node:fs/promises";
import { randomInt } from "node:crypto";
import { UserError } from "../access.js";
import {
  embed,
  guildSettings,
  KeyedQueue,
  nowISO,
  shortId,
  text,
} from "../helpers.js";
import { privateReply } from "../components.js";
import { emojiKey } from "./member-tools.js";

export const CONTENT = JSON.parse(
  await readFile(new URL("../content/community.json", import.meta.url), "utf8"),
);
export const EVENT_FILES = {
  activity: "active_checks.json",
  quiz: "active_quizzes.json",
  pulse: "active_pulses.json",
  giveaway: "active_giveaways.json",
};
export const ACTIVITY_DEFAULTS = {
  activity_text: "**ACTIVITY CHECK**\nConfirm below if you are active!",
  reactor_type: "reaction",
  reactor: "✅",
  button_text: "I'm Active!",
  ping_target: "@everyone",
  winner_count: 3,
  check_duration_minutes: 60,
  permitted_role: null,
  auto_enabled: false,
  auto_hours: 1,
  auto_channel: null,
};
export const epoch = (value) =>
  typeof value === "number" || /^\d+(?:\.\d+)?$/.test(String(value))
    ? Number(value)
    : Date.parse(
        /^\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?$/.test(String(value))
          ? `${value}Z`
          : value,
      ) / 1000;
const sessionId = (kind, row) =>
  String(
    row[
      {
        activity: "check_id",
        quiz: "quiz_id",
        pulse: "pulse_id",
        giveaway: "giveaway_id",
      }[kind]
    ],
  );
export function sample(entries, count) {
  const pool = [...new Set(entries)];
  for (let n = pool.length - 1; n > 0; n--) {
    const selected = randomInt(n + 1);
    [pool[n], pool[selected]] = [pool[selected], pool[n]];
  }
  return pool.slice(0, Math.max(0, Math.min(count, pool.length)));
}
export function eventPayload(kind, row, ended = false) {
  const id = sessionId(kind, row),
    end = Math.floor(epoch(row.end_time)),
    components = [];
  let card;
  if (kind === "activity") {
    const settings = { ...ACTIVITY_DEFAULTS, ...row.settings_snapshot },
      people = row.winners ?? [];
    card = embed(
      ended ? "Activity check ended" : "Activity check",
      ended
        ? people
            .map((p, index) => `${index + 1}. <@${p.user_id}>`)
            .join("\n") || "No recorded participants."
        : text(
            String(
              settings.activity_text || ACTIVITY_DEFAULTS.activity_text,
            ).replaceAll("{ping}", ""),
            3300,
          ),
    );
    card.addFields(
      {
        name: "Participants",
        value: `${people.length}/${settings.winner_count}`,
        inline: true,
      },
      {
        name: ended ? "Result" : "Ends",
        value: ended
          ? text(row.final_status || "Completed", 100)
          : `<t:${end}:R>`,
        inline: true,
      },
    );
    if (settings.reactor_type === "button")
      components.push(
        new ActionRowBuilder().addComponents(
          new ButtonBuilder()
            .setCustomId("ac_join_btn")
            .setStyle(ButtonStyle.Success)
            .setLabel(
              text(
                String(settings.button_text || "I'm Active!").replaceAll(
                  "{count}",
                  String(people.length),
                ),
                80,
              ),
            )
            .setDisabled(ended),
        ),
      );
  } else if (kind === "quiz") {
    const question = row.question;
    card = embed(ended ? "Quiz ended" : "Community quiz", question.question);
    if (ended)
      card.addFields(
        {
          name: "Answer",
          value: text(question.choices[question.answer], 1024),
        },
        {
          name: "Explanation",
          value: text(question.explanation || "Round complete.", 1024),
        },
        {
          name: "Responses",
          value: String(Object.keys(row.answers ?? {}).length),
          inline: true,
        },
      );
    else
      card.addFields(
        { name: "Ends", value: `<t:${end}:R>`, inline: true },
        { name: "Category", value: question.category, inline: true },
      );
    for (let n = 0; n < question.choices.length; n += 2)
      components.push(
        new ActionRowBuilder().addComponents(
          question.choices.slice(n, n + 2).map((choice, index) =>
            new ButtonBuilder()
              .setCustomId(`rallybit:quiz:${id}:${n + index}`)
              .setLabel(
                text(`${String.fromCharCode(65 + n + index)}. ${choice}`, 80),
              )
              .setStyle(
                ended && n + index === question.answer
                  ? ButtonStyle.Success
                  : ButtonStyle.Primary,
              )
              .setDisabled(ended),
          ),
        ),
      );
  } else if (kind === "pulse") {
    const counts = { great: 0, good: 0, okay: 0, rough: 0 };
    for (const response of Object.values(row.responses ?? {}))
      if (Object.hasOwn(counts, response)) counts[response]++;
    card = embed(
      ended ? "Community pulse ended" : "Community pulse",
      text(row.prompt || "How is everyone feeling right now?", 3000),
    );
    if (ended)
      card.addFields(
        ...Object.entries(counts).map(([mood, count]) => ({
          name: mood[0].toUpperCase() + mood.slice(1),
          value: String(count),
          inline: true,
        })),
      );
    else
      card.addFields(
        { name: "Ends", value: `<t:${end}:R>` },
        {
          name: "Responses",
          value: String(Object.keys(row.responses ?? {}).length),
        },
      );
    components.push(
      new ActionRowBuilder().addComponents(
        ["great", "good", "okay", "rough"].map((mood) =>
          new ButtonBuilder()
            .setCustomId(`rallybit:pulse:${id}:${mood}`)
            .setLabel(mood[0].toUpperCase() + mood.slice(1))
            .setStyle(ButtonStyle.Secondary)
            .setDisabled(ended),
        ),
      ),
    );
  } else {
    card = embed(ended ? "Giveaway ended" : "Giveaway", text(row.prize, 2500));
    if (ended)
      card.addFields({
        name: "Winners",
        value:
          (row.winner_ids ?? []).map((id) => `<@${id}>`).join(", ") ||
          "No eligible entrants.",
      });
    else
      card.addFields(
        { name: "Ends", value: `<t:${end}:R>`, inline: true },
        { name: "Winners", value: String(row.winner_count), inline: true },
        {
          name: "Entries",
          value: String(row.entries?.length ?? 0),
          inline: true,
        },
      );
    if (row.required_role_id)
      card.addFields({
        name: "Required role",
        value: `<@&${row.required_role_id}>`,
      });
    components.push(
      new ActionRowBuilder().addComponents(
        new ButtonBuilder()
          .setCustomId(`rallybit:giveaway:${id}:enter`)
          .setLabel(ended ? "Giveaway ended" : "Enter / leave giveaway")
          .setStyle(ButtonStyle.Success)
          .setDisabled(ended),
      ),
    );
  }
  card.setFooter({ text: `Rallybit · ${kind} · ${id}` });
  return { embeds: [card], components, allowedMentions: { parse: [] } };
}
export class EventService {
  constructor(services) {
    Object.assign(this, services);
    this.queue = new KeyedQueue();
    this.dirty = new Set();
  }
  async rows(kind) {
    return this.store.read(EVENT_FILES[kind]);
  }
  async ping(channel, id) {
    if (!id) return { content: null, allowedMentions: { parse: [] } };
    const role = await channel.guild.roles.fetch(String(id));
    if (!role || role.id === channel.guild.id || role.managed)
      return { content: null, allowedMentions: { parse: [] } };
    const bot = await channel.guild.members.fetchMe();
    if (
      !role.mentionable &&
      !channel.permissionsFor(bot)?.has(P.MentionEveryone)
    )
      return { content: null, allowedMentions: { parse: [] } };
    return {
      content: `<@&${role.id}>`,
      allowedMentions: { parse: [], roles: [role.id] },
    };
  }
  async start(kind, guild, channel, actor, options = {}) {
    if (!EVENT_FILES[kind] || !channel?.send || channel.guild?.id !== guild.id)
      throw new UserError("Choose a text channel in this server.");
    return this.queue.run(`start:${kind}:${guild.id}`, async () => {
      const active = await this.rows(kind);
      if (
        kind !== "giveaway" &&
        active[guild.id] &&
        !["failed", "completed"].includes(active[guild.id].phase)
      )
        throw new UserError(`A ${kind} is already active in this server.`);
      if (kind === "quiz" && options.automatic)
        await this.store.update("quiz_dispatch.json", (data) => {
          const previous = Number(data[guild.id] ?? 0);
          if (Date.now() / 1000 - previous < 300)
            throw new UserError(
              "An automatic quiz ran recently. The next scheduled interval will be used.",
            );
          data[guild.id] = Date.now() / 1000;
        });
      const id = shortId(),
        now = Date.now() / 1000,
        record = {
          _native: 1,
          phase: "creating",
          guild_id: guild.id,
          channel_id: channel.id,
          message_id: null,
          starter_id: actor.id,
          host_id: actor.id,
          started_at: nowISO(),
          started_epoch: now,
          start_time: now,
          automatic: Boolean(options.automatic),
        };
      let duration;
      if (kind === "activity") {
        const settings = {
          ...ACTIVITY_DEFAULTS,
          ...(await guildSettings(
            this.store,
            "activity_settings.json",
            guild.id,
          )),
          ...options.settings,
        };
        duration =
          Math.max(
            1,
            Math.min(1440, Number(settings.check_duration_minutes) || 60),
          ) * 60;
        Object.assign(record, {
          check_id: id,
          settings_snapshot: settings,
          winners: [],
        });
        if (
          settings.reactor_type !== "button" &&
          !channel
            .permissionsFor(await guild.members.fetchMe())
            ?.has(P.AddReactions)
        )
          throw new UserError(
            "Rallybit needs Add Reactions before starting a reaction-based activity check.",
          );
      } else if (kind === "quiz") {
        const pool = CONTENT.QUESTION_BANK.filter(
            (row) =>
              !options.category ||
              options.category === "mixed" ||
              row.category === options.category,
          ),
          questions = pool.length ? pool : CONTENT.QUESTION_BANK;
        duration = Math.max(
          15,
          Math.min(120, Number(options.duration_seconds) || 30),
        );
        Object.assign(record, {
          quiz_id: id,
          question: structuredClone(questions[randomInt(questions.length)]),
          duration,
          answers: {},
        });
      } else if (kind === "pulse") {
        duration =
          Math.max(1, Math.min(1440, Number(options.duration_minutes) || 10)) *
          60;
        Object.assign(record, {
          pulse_id: id,
          duration_minutes: duration / 60,
          prompt: text(
            options.prompt || "How is everyone feeling right now?",
            3000,
          ),
          responses: {},
        });
      } else {
        duration =
          Math.max(1, Math.min(43200, Number(options.duration_minutes) || 60)) *
          60;
        Object.assign(record, {
          giveaway_id: id,
          prize: text(options.prize || "Community giveaway", 1000),
          winner_count: Math.max(1, Math.min(20, Number(options.winners) || 1)),
          required_role_id: options.required_role_id || null,
          entries: [],
        });
      }
      record.end_time = now + duration;
      const key = kind === "giveaway" ? id : guild.id;
      await this.store.update(EVENT_FILES[kind], (data) => {
        data[key] = record;
      });
      let mention = await this.ping(channel, options.ping_role_id);
      if (kind === "activity" && !options.ping_role_id) {
        const target = record.settings_snapshot.ping_target;
        if (/^<@&\d{15,22}>$/.test(target))
          mention = await this.ping(channel, target.slice(3, -1));
        else if (
          ["@everyone", "@here"].includes(target) &&
          (await guild.members.fetchMe()).permissions.has(P.MentionEveryone)
        )
          mention = {
            content: target,
            allowedMentions: { parse: ["everyone"] },
          };
      }
      try {
        const message = await channel.send({
          ...eventPayload(kind, record),
          ...mention,
          nonce: id,
          enforceNonce: true,
        });
        record.message_id = message.id;
        record.phase = "active";
        await this.store.update(EVENT_FILES[kind], (data) => {
          data[key] = record;
        });
        if (
          kind === "activity" &&
          record.settings_snapshot.reactor_type !== "button"
        )
          await message.react(record.settings_snapshot.reactor || "✅");
      } catch (error) {
        if ([50013, 50001, 50035].includes(error.code))
          await this.store.update(EVENT_FILES[kind], (data) => {
            if (!data[key].message_id) data[key].phase = "failed";
          });
        throw new UserError(
          `The ${kind} could not be confirmed as started. Check the channel before retrying; Rallybit will not replay an uncertain announcement.`,
        );
      }
      return record;
    });
  }
  async participation(guildId, row) {
    for (const person of row.winners ?? []) {
      if (!person.logged_native) continue;
      const date = new Date(Number(person.time) * 1000).toISOString();
      await this.store.update("activity_log.json", (data) => {
        const entries = ((data[guildId] ??= {})[person.user_id] ??= []);
        if (!entries.includes(date)) entries.push(date);
      });
      await this.store.update("global_stats.json", (data) => {
        const record = (data[person.user_id] ??= {
            wins: 0,
            checkins: 0,
            streak: 0,
            recent_wins: 0,
          }),
          checks = (record.recorded_activity_checks ??= []);
        if (!checks.includes(row.check_id)) {
          record.checkins = (Number(record.checkins) || 0) + 1;
          checks.push(row.check_id);
          record.recorded_activity_checks = checks.slice(-5000);
        }
        Object.assign(record, {
          username: person.name,
          display_name: person.name,
        });
      });
    }
  }
  async vote(kind, i, id, choice) {
    await i.deferReply({ flags: MessageFlags.Ephemeral });
    const key = kind === "giveaway" ? id : i.guildId;
    return this.queue.run(`vote:${kind}:${key}`, async () => {
      let response = "",
        snapshot;
      const member = await i.guild.members.fetch({
        user: i.user.id,
        force: true,
      });
      if (member.user.bot) throw new UserError("Bots cannot participate.");
      await this.store.update(EVENT_FILES[kind], (data) => {
        const row = data[key];
        if (
          !row ||
          String(row.guild_id ?? i.guildId) !== i.guildId ||
          sessionId(kind, row) !== String(id) ||
          String(row.message_id) !== i.message.id ||
          row.phase !== "active" ||
          epoch(row.end_time) <= Date.now() / 1000
        )
          throw new UserError(
            "This event has ended or is no longer available.",
          );
        if (kind === "activity") {
          if (String(row.starter_id) === i.user.id)
            throw new UserError(
              "You cannot participate in your own activity check.",
            );
          row.winners ??= [];
          if (
            row.winners.some((person) => String(person.user_id) === i.user.id)
          )
            throw new UserError("Your participation is already recorded.");
          if (
            row.winners.length >=
            Number(row.settings_snapshot?.winner_count ?? 3)
          )
            throw new UserError("This check is full.");
          row.winners.push({
            user_id: i.user.id,
            name: member.user.username,
            time: Date.now() / 1000,
            logged_native: true,
          });
          response = "Your activity check-in was recorded.";
        } else if (kind === "quiz") {
          row.answers ??= {};
          if (Object.hasOwn(row.answers, i.user.id))
            throw new UserError("You already answered this quiz.");
          const answer = Number(choice);
          if (
            !Number.isInteger(answer) ||
            answer < 0 ||
            answer >= row.question.choices.length
          )
            throw new UserError("Invalid answer.");
          row.answers[i.user.id] = {
            name: member.displayName,
            answer,
            elapsed: Math.max(
              0,
              Date.now() / 1000 -
                Number(row.started_epoch ?? epoch(row.started_at)),
            ),
            submitted_at: nowISO(),
          };
          response = "Your answer was recorded.";
        } else if (kind === "pulse") {
          if (!["great", "good", "okay", "rough"].includes(choice))
            throw new UserError("Invalid pulse response.");
          (row.responses ??= {})[i.user.id] = choice;
          response = "Your anonymous pulse response was recorded.";
        } else {
          if (
            row.required_role_id &&
            !member.roles.cache.has(String(row.required_role_id))
          )
            throw new UserError("You need the required role to enter.");
          const entries = new Set((row.entries ?? []).map(String));
          if (entries.has(i.user.id)) {
            entries.delete(i.user.id);
            response = "You left the giveaway.";
          } else {
            entries.add(i.user.id);
            response = "You entered the giveaway.";
          }
          row.entries = [...entries];
        }
        snapshot = structuredClone(row);
      });
      if (kind === "activity") await this.participation(i.guildId, snapshot);
      this.dirty.add(`${kind}:${key}`);
      return privateReply(i, { content: response });
    });
  }
  async reaction(reaction, user) {
    if (user.bot || !reaction.message.guild) return;
    const row = (await this.rows("activity"))[reaction.message.guildId];
    if (
      !row ||
      String(row.message_id) !== reaction.message.id ||
      row.settings_snapshot?.reactor_type === "button" ||
      emojiKey(row.settings_snapshot?.reactor || "✅") !==
        emojiKey(reaction.emoji)
    )
      return;
    const fake = {
      guild: reaction.message.guild,
      guildId: reaction.message.guildId,
      user,
      message: reaction.message,
      deferReply: async () => {},
      deferred: true,
      editReply: async () => {},
    };
    await this.vote("activity", fake, sessionId("activity", row));
  }
  async eligible(guild, entries, required) {
    const result = [];
    for (let n = 0; n < entries.length; n += 5) {
      const members = await Promise.all(
        entries.slice(n, n + 5).map((id) =>
          guild.members
            .fetch({ user: String(id), force: true })
            .catch((error) => {
              if (error.code === 10007) return null;
              throw error;
            }),
        ),
      );
      for (const member of members)
        if (
          member &&
          !member.user.bot &&
          (!required || member.roles.cache.has(String(required)))
        )
          result.push(member.id);
    }
    return result;
  }
  async finish(kind, guild, key, reason = "time", assertOwner = () => {}) {
    return this.queue.run(`vote:${kind}:${key}`, async () => {
      let row = (await this.rows(kind))[key];
      if (!row || ["completed", "failed"].includes(row.phase)) return;
      const id = sessionId(kind, row);
      assertOwner();
      if (row.phase !== "finalizing") {
        const patch = {
          phase: "finalizing",
          reason,
          final_status:
            kind === "activity"
              ? reason === "cancelled"
                ? "cancelled"
                : reason === "time"
                  ? (row.winners?.length ?? 0) >=
                    Number(row.settings_snapshot?.winner_count ?? 3)
                    ? "completed"
                    : "timed_out"
                  : "manually_stopped"
              : reason,
        };
        if (kind === "giveaway") {
          patch.entrant_ids = await this.eligible(
            guild,
            row.entries ?? [],
            row.required_role_id,
          );
          patch.winner_ids = sample(
            patch.entrant_ids,
            Number(row.winner_count) || 1,
          );
        }
        await this.store.update(EVENT_FILES[kind], (data) => {
          Object.assign(data[key], patch);
        });
        row = { ...row, ...patch };
      }
      if (kind === "activity") {
        await this.participation(guild.id, row);
        await this.store.update("global_stats.json", (data) => {
          for (const person of row.final_status === "cancelled"
            ? []
            : (row.winners ?? [])) {
            const record = (data[person.user_id] ??= {
                wins: 0,
                checkins: 0,
                streak: 0,
                recent_wins: 0,
              }),
              awarded = (record.awarded_activity_checks ??= []);
            if (awarded.includes(id)) continue;
            record.wins = (Number(record.wins) || 0) + 1;
            record.recent_wins = (Number(record.recent_wins) || 0) + 1;
            record.streak = (Number(record.streak) || 0) + 1;
            awarded.push(id);
            record.awarded_activity_checks = awarded.slice(-5000);
          }
        });
        await this.store.update("activity_audit_logs.json", (data) => {
          (data[guild.id] ??= {})[id] = {
            check_id: id,
            channel_id: row.channel_id,
            message_id: row.message_id,
            starter_id: row.starter_id,
            start_time: new Date(
              (epoch(row.start_time) || Date.now() / 1000) * 1000,
            ).toISOString(),
            end_time: nowISO(),
            status: row.final_status,
            participants: row.winners ?? [],
          };
        });
      } else if (kind === "quiz") {
        await this.store.update("quiz_stats.json", (data) => {
          const processed = (data.__processed_quizzes__ ??= []);
          if (processed.includes(id)) return;
          const records = (data[guild.id] ??= {});
          for (const [uid, answer] of Object.entries(row.answers ?? {})) {
            const record = (records[uid] ??= {});
            for (const field of [
              "correct",
              "answered",
              "points",
              "streak",
              "best_streak",
            ])
              record[field] = Number(record[field]) || 0;
            record.answered++;
            record.display_name = answer.name ?? record.display_name;
            if (Number(answer.answer) === Number(row.question.answer)) {
              record.correct++;
              record.points +=
                100 +
                Math.max(
                  0,
                  Math.floor(Number(row.duration) - Number(answer.elapsed)),
                );
              record.streak++;
              record.best_streak = Math.max(record.best_streak, record.streak);
            } else record.streak = 0;
          }
          processed.push(id);
          data.__processed_quizzes__ = processed.slice(-10000);
        });
        await this.history("quiz_history.json", guild.id, "quiz_id", id, {
          quiz_id: id,
          channel_id: row.channel_id,
          question: row.question.question,
          category: row.question.category,
          correct_answer: row.question.choices[row.question.answer],
          responses: Object.keys(row.answers ?? {}).length,
          correct_responses: Object.values(row.answers ?? {}).filter(
            (a) => Number(a.answer) === Number(row.question.answer),
          ).length,
          started_at: row.started_at,
          ended_at: nowISO(),
          automatic: row.automatic,
        });
      } else if (kind === "pulse") {
        const counts = { great: 0, good: 0, okay: 0, rough: 0 };
        for (const mood of Object.values(row.responses ?? {}))
          if (Object.hasOwn(counts, mood)) counts[mood]++;
        await this.history("pulse_history.json", guild.id, "pulse_id", id, {
          pulse_id: id,
          channel_id: row.channel_id,
          prompt: row.prompt,
          counts,
          total: Object.values(row.responses ?? {}).length,
          started_at: row.started_at,
          ended_at: nowISO(),
          reason: row.reason,
        });
      } else
        await this.history(
          "giveaway_history.json",
          guild.id,
          "giveaway_id",
          id,
          {
            giveaway_id: id,
            channel_id: row.channel_id,
            prize: row.prize,
            host_id: row.host_id,
            required_role_id: row.required_role_id,
            entries: row.entrant_ids?.length ?? 0,
            entrant_ids: row.entrant_ids ?? [],
            winner_ids: row.winner_ids ?? [],
            ended_at: nowISO(),
            reason: row.reason,
          },
        );
      assertOwner();
      try {
        const channel = await guild.channels.fetch(String(row.channel_id));
        if (channel && row.message_id) {
          const message = await channel.messages.fetch(String(row.message_id));
          await message.edit(eventPayload(kind, row, true));
        }
        if (
          channel &&
          kind === "giveaway" &&
          row.winner_ids?.length &&
          !row.announcement_reserved
        ) {
          await this.store.update(EVENT_FILES[kind], (data) => {
            data[key].announcement_reserved = true;
          });
          assertOwner();
          await channel.send({
            content: `Congratulations ${row.winner_ids.map((uid) => `<@${uid}>`).join(", ")}! You won **${text(row.prize, 1000)}**.`,
            allowedMentions: { parse: [], users: row.winner_ids },
            nonce: `result-${id}`,
            enforceNonce: true,
          });
        }
      } catch (error) {
        if (![10003, 10008, 50001, 50013].includes(error.code)) throw error;
      }
      await this.store.update(EVENT_FILES[kind], (data) => {
        if (data[key] && sessionId(kind, data[key]) === id) delete data[key];
      });
      this.dirty.delete(`${kind}:${key}`);
    });
  }
  async history(file, guildId, field, id, record) {
    await this.store.update(file, (data) => {
      const rows = (data[guildId] ??= []);
      if (!rows.some((row) => String(row[field]) === id)) rows.push(record);
      data[guildId] = rows.slice(-250);
    });
  }
  async deleted(message) {
    if (!message.guild) return;
    const row = (await this.rows("activity"))[message.guildId];
    if (row && String(row.message_id) === message.id)
      await this.finish(
        "activity",
        message.guild,
        message.guildId,
        "cancelled",
      );
  }
  async tick(client, assertOwner = () => {}) {
    for (const kind of Object.keys(EVENT_FILES))
      for (const [key, snapshot] of Object.entries(await this.rows(kind))) {
        const guild = client.guilds.cache.get(String(snapshot.guild_id ?? key));
        if (!guild) continue;
        assertOwner();
        try {
          if (snapshot.phase === "failed") {
            await this.store.update(EVENT_FILES[kind], (data) => {
              if (data[key]?.phase === "failed") delete data[key];
            });
            continue;
          }
          if (!snapshot.message_id) {
            const channel = await guild.channels.fetch(
                String(snapshot.channel_id),
              ),
              recent = await channel.messages.fetch({ limit: 100 }),
              id = sessionId(kind, snapshot);
            const message = [...recent.values()].find(
              (m) =>
                m.author.id === client.user.id &&
                m.embeds.some(
                  (e) => e.footer?.text === `Rallybit · ${kind} · ${id}`,
                ),
            );
            if (message)
              await this.store.update(EVENT_FILES[kind], (data) => {
                Object.assign(data[key], {
                  message_id: message.id,
                  phase: "active",
                });
              });
            else
              await this.store.update(EVENT_FILES[kind], (data) => {
                data[key].phase = "failed";
              });
            continue;
          }
          if (
            snapshot.phase === "finalizing" ||
            epoch(snapshot.end_time) <= Date.now() / 1000 ||
            (kind === "activity" &&
              (snapshot.winners?.length ?? 0) >=
                Number(snapshot.settings_snapshot?.winner_count ?? 3))
          )
            await this.finish(kind, guild, key, "time", assertOwner);
          else if (this.dirty.has(`${kind}:${key}`)) {
            const channel = await guild.channels.fetch(
                String(snapshot.channel_id),
              ),
              message = await channel.messages.fetch(
                String(snapshot.message_id),
              );
            assertOwner();
            await message.edit(eventPayload(kind, snapshot));
            this.dirty.delete(`${kind}:${key}`);
          }
        } catch (error) {
          console.warn(
            JSON.stringify({
              event: "timed_event_retry",
              kind,
              code: error.code,
              name: error.name,
            }),
          );
        }
      }
  }
  registerComponents(components) {
    for (const kind of ["quiz", "pulse", "giveaway"])
      components.register(`rallybit:${kind}`, async (i) => {
        const [, , id, choice] = i.customId.split(":");
        return this.vote(kind, i, id, choice);
      });
    components.register("ac_join_btn", async (i) => {
      const row = (await this.rows("activity"))[i.guildId];
      if (!row) throw new UserError("This activity check ended.");
      return this.vote("activity", i, String(row.check_id));
    });
  }
}
