import { randomUUID } from "node:crypto";
import { EmbedBuilder, ChannelType } from "discord.js";
import { UserError, workforceAccess } from "../access.js";

const SHIFT_FILE = "staff_shifts.json";
const REQUEST_FILE = "staff_requests.json";
const SETTINGS_FILE = "workforce_settings.json";
const DAY = 86400000;
const staffPolicy = { guildOnly: true, workforce: "staff" };
const hrPolicy = { guildOnly: true, workforce: "hr" };
const proPolicy = { guildOnly: true, moderation: "warn" };
const id = (prefix) =>
  `${prefix}-${randomUUID().replaceAll("-", "").slice(0, 8).toUpperCase()}`;
const numeric = (value) => Math.max(0, Number(value) || 0);
const seconds = (start, end) => Math.max(0, Math.floor((end - start) / 1000));
const parseTime = (value) =>
  value && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;

export function activeSeconds(active, now = Date.now()) {
  const start = parseTime(active?.started_at);
  if (start === null) return 0;
  const breakStart = parseTime(active.break_started_at);
  return Math.max(
    0,
    seconds(start, now) -
      numeric(active.break_seconds) -
      (breakStart === null ? 0 : seconds(breakStart, now)),
  );
}

export function formatDuration(value) {
  const minutes = Math.floor(numeric(value) / 60);
  const days = Math.floor(minutes / 1440),
    hours = Math.floor(minutes / 60) % 24;
  return [
    days ? `${days}d` : "",
    hours || days ? `${hours}h` : "",
    `${minutes % 60}m`,
  ]
    .filter(Boolean)
    .join(" ");
}

function recordFor(data, guildId, userId) {
  const record = ((data[guildId] ??= {})[userId] ??= {
    active: null,
    history: [],
  });
  if (!Array.isArray(record.history)) record.history = [];
  return record;
}
function ensureIds(record) {
  if (!Array.isArray(record.history)) record.history = [];
  const seen = new Set();
  for (const row of record.history) {
    let value = String(row.shift_id || "").toUpperCase();
    if (!value || seen.has(value)) value = id("SH");
    row.shift_id = value;
    seen.add(value);
  }
}
function embed(title, description) {
  return {
    embeds: [
      new EmbedBuilder()
        .setColor(0x7567ee)
        .setTitle(title.slice(0, 256))
        .setDescription(
          String(description || "No records found.").slice(0, 4000),
        )
        .setTimestamp(),
    ],
  };
}
function requestEmbed(row) {
  return new EmbedBuilder()
    .setColor(0x7567ee)
    .setTitle(`${row.type} request · ${row.request_id}`)
    .setDescription(String(row.reason || "No reason provided").slice(0, 2000))
    .addFields(
      { name: "Member", value: `<@${row.user_id}>`, inline: true },
      { name: "Status", value: row.status, inline: true },
      { name: "Duration", value: `${row.duration_days} day(s)`, inline: true },
      {
        name: "Dates",
        value: `${row.start_at.slice(0, 10)} to ${row.end_at.slice(0, 10)}`,
      },
      ...(row.review_note
        ? [{ name: "HR note", value: row.review_note.slice(0, 1000) }]
        : []),
    )
    .setFooter({ text: "Rallybit Staff Operations" })
    .setTimestamp();
}

export function registerWorkforce(
  registry,
  { store, audit, now = () => Date.now() },
) {
  const settingsFor = async (guildId) => ({
    staff_role_ids: [],
    hr_role_ids: [],
    max_loa_days: 90,
    max_roa_days: 30,
    ...((await store.read(SETTINGS_FILE))[guildId] ?? {}),
  });
  const log = async (interaction, title, description, target) => {
    // Logging failure cannot undo an already committed shift operation.
    try {
      const settings = await settingsFor(interaction.guildId);
      await audit(
        interaction,
        "staff",
        title,
        description,
        target,
        settings.shift_log_channel_id,
      );
    } catch (error) {
      console.warn(
        JSON.stringify({ event: "staff_audit_failed", code: error.code }),
      );
    }
  };
  const resolveMember = async (interaction, required = false) => {
    const user =
      interaction.options.getUser("member", required) ?? interaction.user;
    return interaction.guild.members.fetch({ user: user.id, force: true });
  };
  const clockin = async (interaction) => {
    const instant = now();
    await store.update(SHIFT_FILE, (data) => {
      const record = recordFor(data, interaction.guildId, interaction.user.id);
      if (record.active) throw new UserError("You are already clocked in.");
      record.active = {
        started_at: new Date(instant).toISOString(),
        note: (interaction.options.getString("note") ?? "").slice(0, 300),
        break_seconds: 0,
        break_started_at: null,
      };
      record.display_name =
        interaction.member?.displayName ?? interaction.user.username;
    });
    await log(
      interaction,
      "Staff clock in",
      "Shift started.",
      interaction.user.id,
    );
    return embed(
      "Clocked in successfully",
      "You are now on duty. Use /break start to pause your shift timer.",
    );
  };
  const clockout = async (interaction, force = false) => {
    const member = force
      ? await resolveMember(interaction, true)
      : interaction.user;
    const instant = now();
    const duration = await store.update(SHIFT_FILE, (data) => {
      const record = recordFor(data, interaction.guildId, member.id);
      const start = parseTime(record.active?.started_at);
      if (start === null)
        throw new UserError(
          force ? "That member is not clocked in." : "You are not clocked in.",
        );
      const duration = activeSeconds(record.active, instant);
      record.history.push({
        shift_id: id("SH"),
        started_at: record.active.started_at,
        ended_at: new Date(instant).toISOString(),
        seconds: duration,
        break_seconds: Math.max(0, seconds(start, instant) - duration),
        note: String(record.active.note ?? "").slice(0, 300),
        clocked_out_by: interaction.user.id,
      });
      record.history = record.history.slice(-300);
      record.active = null;
      return duration;
    });
    await log(
      interaction,
      force ? "Staff forced clock out" : "Staff clock out",
      `Recorded ${formatDuration(duration)}.${force ? ` Reason: ${interaction.options.getString("reason") || "No reason provided"}` : ""}`,
      member.id,
    );
    return embed(
      "Shift completed",
      `<@${member.id}> worked **${formatDuration(duration)}**.`,
    );
  };
  registry.register("clockin", clockin, staffPolicy);
  registry.register("staff clockin", clockin, proPolicy);
  registry.register(
    "clockout",
    (interaction) => clockout(interaction),
    staffPolicy,
  );
  registry.register(
    "staff clockout",
    (interaction) => clockout(interaction),
    proPolicy,
  );
  registry.register(
    "forceclockout",
    (interaction) => clockout(interaction, true),
    hrPolicy,
  );

  for (const action of ["start", "end"])
    registry.register(
      `break ${action}`,
      async (interaction) => {
        const duration = await store.update(SHIFT_FILE, (data) => {
          const active = recordFor(
            data,
            interaction.guildId,
            interaction.user.id,
          ).active;
          if (!active)
            throw new UserError("Clock in before managing your break.");
          const instant = now();
          if (action === "start") {
            if (active.break_started_at)
              throw new UserError("You are already on a break.");
            active.break_started_at = new Date(instant).toISOString();
            return 0;
          }
          const start = parseTime(active.break_started_at);
          if (start === null)
            throw new UserError("You do not have an active break.");
          const duration = seconds(start, instant);
          active.break_seconds = numeric(active.break_seconds) + duration;
          active.break_started_at = null;
          return duration;
        });
        await log(
          interaction,
          `Staff break ${action === "start" ? "started" : "ended"}`,
          `Break duration: ${formatDuration(duration)}.`,
          interaction.user.id,
        );
        return embed(
          action === "start" ? "Break started" : "Break ended",
          action === "start"
            ? "Your shift timer is paused."
            : `Your ${formatDuration(duration)} break has ended. Your shift timer has resumed.`,
        );
      },
      staffPolicy,
    );

  const status = async (interaction, other = false) => {
    const user = other
      ? (interaction.options.getUser("member") ?? interaction.user)
      : interaction.user;
    const record = (await store.read(SHIFT_FILE))[interaction.guildId]?.[
      user.id
    ];
    const total = (record?.history ?? []).reduce(
      (sum, row) => sum + numeric(row.seconds),
      0,
    );
    return embed(
      "Staff shift status",
      `<@${user.id}> is **${record?.active ? (record.active.break_started_at ? "On Break" : "On Duty") : "Off Duty"}**.\n` +
        `Current shift: ${formatDuration(activeSeconds(record?.active, now()))}\nRecorded total: ${formatDuration(total)}\nCompleted shifts: ${record?.history?.length ?? 0}`,
    );
  };
  registry.register("shift", (interaction) => status(interaction), staffPolicy);
  registry.register(
    "staff status",
    (interaction) => status(interaction, true),
    proPolicy,
  );
  registry.register(
    "duty",
    async (interaction) => {
      const rows = (await store.read(SHIFT_FILE))[interaction.guildId] ?? {};
      return embed(
        "Staff currently on duty",
        Object.entries(rows)
          .filter(([, record]) => record.active)
          .map(
            ([uid, record]) =>
              `<@${uid}> · ${record.active.break_started_at ? "On Break" : "On Duty"} · ${formatDuration(activeSeconds(record.active, now()))}`,
          )
          .join("\n") || "No staff members are clocked in.",
      );
    },
    staffPolicy,
  );

  const hours = async (interaction, other) => {
    const user = other
      ? interaction.options.getUser("member", true)
      : interaction.user;
    const days = other ? (interaction.options.getInteger("days") ?? 30) : 7;
    if (days < 1 || days > 365)
      throw new UserError("Choose a reporting period between 1 and 365 days.");
    const record = (await store.read(SHIFT_FILE))[interaction.guildId]?.[
      user.id
    ];
    const rows = (record?.history ?? []).filter(
      (row) => (parseTime(row.ended_at) ?? 0) >= now() - days * DAY,
    );
    return embed(
      other ? "Staff hours" : "Weekly timesheet",
      `<@${user.id}> worked **${formatDuration(rows.reduce((sum, row) => sum + numeric(row.seconds), 0))}** across **${rows.length}** shifts in the last ${days} days.\n\n` +
        rows
          .slice(-10)
          .map(
            (row) =>
              `${String(row.ended_at).slice(0, 10)} · ${formatDuration(row.seconds)}`,
          )
          .join("\n"),
    );
  };
  registry.register(
    "timesheet",
    (interaction) => hours(interaction, false),
    staffPolicy,
  );
  registry.register(
    "staffhours",
    (interaction) => hours(interaction, true),
    hrPolicy,
  );
  registry.register(
    "staff leaderboard",
    async (interaction) => {
      const days = interaction.options.getInteger("days") ?? 30;
      if (days < 7 || days > 365)
        throw new UserError(
          "Choose a reporting period between 7 and 365 days.",
        );
      const records = (await store.read(SHIFT_FILE))[interaction.guildId] ?? {};
      const rows = Object.entries(records)
        .map(([uid, record]) => {
          const shifts = (record.history ?? []).filter(
            (row) => (parseTime(row.ended_at) ?? 0) >= now() - days * DAY,
          );
          return {
            uid,
            count: shifts.length,
            total: shifts.reduce((sum, row) => sum + numeric(row.seconds), 0),
          };
        })
        .filter((row) => row.count)
        .sort((a, b) => b.total - a.total)
        .slice(0, 10);
      return embed(
        `Staff leaderboard · ${days} days`,
        rows
          .map(
            (row, index) =>
              `**${index + 1}.** <@${row.uid}> · ${formatDuration(row.total)} · ${row.count} shifts`,
          )
          .join("\n"),
      );
    },
    proPolicy,
  );

  registry.register(
    "shifts list",
    async (interaction) => {
      const user = interaction.options.getUser("member", true);
      const limit = interaction.options.getInteger("limit") ?? 10;
      if (limit < 1 || limit > 20)
        throw new UserError("Choose between 1 and 20 shifts.");
      const record = await store.update(SHIFT_FILE, (data) => {
        const row = data[interaction.guildId]?.[user.id];
        if (!row)
          throw new UserError("That member has no saved shift account.");
        ensureIds(row);
        return structuredClone(row);
      });
      return embed(
        "Staff shift records",
        record.history
          .slice(-limit)
          .reverse()
          .map(
            (row) =>
              `\`${row.shift_id}\` · ${String(row.ended_at).slice(0, 10)} · ${formatDuration(row.seconds)}`,
          )
          .join("\n"),
      );
    },
    hrPolicy,
  );
  registry.register(
    "shifts remove",
    async (interaction) => {
      const user = interaction.options.getUser("member", true);
      const shiftId = interaction.options
        .getString("shift_id", true)
        .trim()
        .toUpperCase();
      const reason = interaction.options.getString("reason", true).trim();
      if (!reason || reason.length > 300)
        throw new UserError("Provide a reason up to 300 characters long.");
      const removed = await store.update(SHIFT_FILE, (data) => {
        const record = data[interaction.guildId]?.[user.id];
        const index =
          record?.history?.findIndex(
            (row) => String(row.shift_id).toUpperCase() === shiftId,
          ) ?? -1;
        if (index < 0)
          throw new UserError(
            "That shift ID was not found. Use /shifts list to get a current ID.",
          );
        const [removed] = record.history.splice(index, 1);
        if (!record.history.length && !record.active)
          delete data[interaction.guildId][user.id];
        return removed;
      });
      await log(
        interaction,
        "Staff shift removed",
        `Shift ${shiftId}: ${formatDuration(removed.seconds)}. Reason: ${reason}`,
        user.id,
      );
      return embed(
        "Shift removed",
        `Removed \`${shiftId}\` from <@${user.id}>.`,
      );
    },
    hrPolicy,
  );
  registry.register(
    "shifts clear",
    async (interaction) => {
      if (!interaction.options.getBoolean("confirm"))
        return {
          content:
            "Nothing was removed. Set confirm to True after checking the member and include_active options.",
        };
      const user = interaction.options.getUser("member", true);
      const reason = interaction.options.getString("reason", true).trim();
      const includeActive =
        interaction.options.getBoolean("include_active") ?? false;
      if (!reason || reason.length > 300)
        throw new UserError("Provide a reason up to 300 characters long.");
      const result = await store.update(SHIFT_FILE, (data) => {
        const record = data[interaction.guildId]?.[user.id];
        if (!record)
          throw new UserError("That member has no saved shift account.");
        const result = {
          count: record.history?.length ?? 0,
          activeRemoved: includeActive && Boolean(record.active),
        };
        record.history = [];
        if (includeActive || !record.active)
          delete data[interaction.guildId][user.id];
        return result;
      });
      await log(
        interaction,
        "Staff shift account cleared",
        `${result.count} completed shifts removed. Active shift removed: ${result.activeRemoved}. Reason: ${reason}`,
        user.id,
      );
      return embed(
        "Shift account updated",
        `Removed ${result.count} completed shifts from <@${user.id}>.${result.activeRemoved ? " Their active shift was also removed." : " Their active shift was kept, if present."}`,
      );
    },
    hrPolicy,
  );

  const publishRequest = async (interaction, row, requested) => {
    const settings = await settingsFor(interaction.guildId);
    const destinations = new Map();
    if (requested && settings.request_channel_id)
      destinations.set(String(settings.request_channel_id), true);
    if (
      settings.history_channel_id &&
      !destinations.has(String(settings.history_channel_id))
    )
      destinations.set(String(settings.history_channel_id), false);
    for (const [channelId, ping] of destinations) {
      try {
        const channel = await interaction.guild.channels.fetch(channelId);
        if (channel?.isTextBased())
          await channel.send({
            embeds: [requestEmbed(row)],
            content: ping
              ? (settings.hr_role_ids ?? [])
                  .map((role) => `<@&${role}>`)
                  .join(" ") || undefined
              : undefined,
            allowedMentions: {
              parse: [],
              roles: ping ? settings.hr_role_ids.map(String) : [],
            },
          });
      } catch (error) {
        console.warn(
          JSON.stringify({
            event: "staff_request_delivery_failed",
            channel: channelId,
            code: error.code,
          }),
        );
      }
    }
    await log(
      interaction,
      `${row.type} ${requested ? "requested" : row.status.toLowerCase()}`,
      `Request ${row.request_id}: ${row.status}.`,
      row.user_id,
    );
  };

  for (const kind of ["LOA", "ROA"]) {
    const prefix = kind.toLowerCase();
    registry.register(
      `${prefix} settings`,
      async (interaction) => {
        const request = interaction.options.getChannel("request_channel", true);
        const history = interaction.options.getChannel("history_channel", true);
        const shiftLog = interaction.options.getChannel("shift_log_channel");
        if (
          [request, history, shiftLog]
            .filter(Boolean)
            .some((channel) => channel.type !== ChannelType.GuildText)
        )
          throw new UserError(
            "Select text channels for staff requests and logs.",
          );
        const staff = interaction.options.getRole("staff_role", true),
          hr = interaction.options.getRole("hr_role", true);
        if (
          [staff, hr].some(
            (role) => role.id === interaction.guildId || role.managed,
          )
        )
          throw new UserError(
            "Select ordinary staff roles, not @everyone or managed roles.",
          );
        const maximum =
          interaction.options.getInteger("maximum_days") ??
          (kind === "LOA" ? 90 : 30);
        if (maximum < 1 || maximum > 365)
          throw new UserError(
            "Maximum duration must be between 1 and 365 days.",
          );
        await store.update(SETTINGS_FILE, (data) => {
          const settings = (data[interaction.guildId] ??= {});
          Object.assign(settings, {
            request_channel_id: request.id,
            history_channel_id: history.id,
            staff_role_ids: [staff.id],
            hr_role_ids: [hr.id],
            [`max_${prefix}_days`]: maximum,
          });
          if (shiftLog) settings.shift_log_channel_id = shiftLog.id;
        });
        await log(
          interaction,
          `${kind} settings updated`,
          "Request channels and staff/HR access updated.",
        );
        return {
          content: `${kind} channels, access roles, maximum duration and logging are configured.`,
        };
      },
      hrPolicy,
    );
    registry.register(
      `${prefix} request`,
      async (interaction) => {
        const settings = await settingsFor(interaction.guildId);
        const days = interaction.options.getInteger("duration_days", true);
        if (
          !Number.isInteger(days) ||
          days < 1 ||
          days > settings[`max_${prefix}_days`]
        )
          throw new UserError(
            `${kind} duration must be between 1 and ${settings[`max_${prefix}_days`]} days.`,
          );
        const startDate = interaction.options.getString("start_date");
        const start = startDate
          ? Date.parse(`${startDate}T00:00:00Z`)
          : Math.floor(now() / DAY) * DAY;
        if (
          !Number.isFinite(start) ||
          (startDate &&
            (!/^\d{4}-\d{2}-\d{2}$/.test(startDate) ||
              new Date(start).toISOString().slice(0, 10) !== startDate))
        )
          throw new UserError("Use a valid start date in YYYY-MM-DD format.");
        const reason = interaction.options.getString("reason", true).trim();
        if (!reason || reason.length > 2000)
          throw new UserError("Provide a reason up to 2,000 characters long.");
        const row = {
          request_id: id(kind),
          type: kind,
          user_id: interaction.user.id,
          user_name:
            interaction.member?.displayName ?? interaction.user.username,
          reason,
          duration_days: days,
          start_at: new Date(start).toISOString(),
          end_at: new Date(start + days * DAY).toISOString(),
          status: "Pending",
          created_at: new Date(now()).toISOString(),
          updated_at: new Date(now()).toISOString(),
          updated_by: interaction.user.id,
        };
        await store.update(REQUEST_FILE, (data) => {
          (data[interaction.guildId] ??= {})[row.request_id] = row;
        });
        await publishRequest(interaction, row, true);
        return { embeds: [requestEmbed(row)] };
      },
      staffPolicy,
    );
    registry.register(
      `${prefix} status`,
      async (interaction) => {
        const rows =
          (await store.read(REQUEST_FILE))[interaction.guildId] ?? {};
        const wanted = interaction.options
          .getString("request_id")
          ?.trim()
          .toUpperCase();
        const row = Object.values(rows)
          .filter(
            (row) =>
              row.type === kind &&
              String(row.user_id) === interaction.user.id &&
              (!wanted || row.request_id === wanted),
          )
          .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
        if (!row) throw new UserError(`No matching ${kind} request was found.`);
        return { embeds: [requestEmbed(row)] };
      },
      staffPolicy,
    );
    for (const action of ["cancel", "end", "setstatus"])
      registry.register(
        `${prefix} ${action}`,
        async (interaction) => {
          const requestId = interaction.options
            .getString("request_id", true)
            .trim()
            .toUpperCase();
          const member =
            action === "end"
              ? await interaction.guild.members.fetch({
                  user: interaction.user.id,
                  force: true,
                })
              : null;
          const hr = member ? (await workforceAccess(member, store)).hr : false;
          const row = await store.update(REQUEST_FILE, (data) => {
            const row = data[interaction.guildId]?.[requestId];
            if (!row || row.type !== kind)
              throw new UserError(`That ${kind} request was not found.`);
            if (
              (action === "cancel" || (action === "end" && !hr)) &&
              String(row.user_id) !== interaction.user.id
            )
              throw new UserError("You can only change your own request.");
            if (action === "cancel") {
              if (!["Pending", "Approved"].includes(row.status))
                throw new UserError("That request can no longer be cancelled.");
              row.status = "Cancelled";
            } else if (action === "end") {
              if (row.status !== "Approved")
                throw new UserError(
                  "Only an approved request can be ended early.",
                );
              row.status = "Ended";
              row.ended_at = new Date(now()).toISOString();
            } else {
              const status = interaction.options.getString("status", true);
              if (!["Pending", "Approved", "Denied"].includes(status))
                throw new UserError("Choose Pending, Approved or Denied.");
              row.status = status;
              row.review_note = (
                interaction.options.getString("note") ?? ""
              ).slice(0, 1000);
            }
            row.updated_at = new Date(now()).toISOString();
            row.updated_by = interaction.user.id;
            return structuredClone(row);
          });
          await publishRequest(interaction, row, false);
          return { embeds: [requestEmbed(row)] };
        },
        action === "setstatus" ? hrPolicy : staffPolicy,
      );
  }
}
