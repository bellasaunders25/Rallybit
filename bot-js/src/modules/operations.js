import { AttachmentBuilder } from "discord.js";
import { UserError } from "../access.js";
import {
  csvFile,
  embed,
  guildSettings,
  KeyedQueue,
  nowISO,
  shortId,
  text,
  updateSettings,
} from "../helpers.js";

const CONFIG = {
  activity: "activity_settings.json",
  quizzes: "quiz_settings.json",
  community: "community_settings.json",
  giveaways: "giveaway_settings.json",
  welcomes: "welcome_settings.json",
  levels: "level_settings.json",
  autoroles: "autorole_settings.json",
  reaction_roles: "reaction_roles.json",
  verification: "verification_settings.json",
  tickets: "ticket_settings.json",
  reports: "report_settings.json",
  reviews: "review_settings.json",
  moderation_permissions: "moderation_permissions.json",
  security: "security_settings.json",
  automation: "automation_schedules.json",
  logging: "audit_settings.json",
  workforce: "workforce_settings.json",
};
const DAY = 86400000;
const inPeriod = (date, days) => Date.parse(date) >= Date.now() - days * DAY;
export function caseAction(value) {
  const action = String(value ?? "")
    .toLowerCase()
    .replaceAll(" ", "_");
  return (
    {
      warning_added: "warn",
      warning_removed: "unwarn",
      warn: "warn",
      remove_warning: "unwarn",
      end_timeout: "untimeout",
      add_role: "role_add",
      remove_role: "role_remove",
      clear_messages: "clear",
    }[action] ?? action
  );
}
export async function caseRows(
  store,
  guildId,
  { days, member, moderator, action } = {},
) {
  const records = (await store.read("moderation_history.json"))[guildId] ?? {};
  return Object.entries(records)
    .flatMap(([id, rows]) =>
      Array.isArray(rows) ? rows.map((row) => ({ ...row, target_id: id })) : [],
    )
    .filter(
      (row) =>
        (!days || inPeriod(row.timestamp, days)) &&
        (!member || String(row.target_id) === member) &&
        (!moderator || String(row.moderator_id) === moderator) &&
        (!action || caseAction(row.action) === caseAction(action)),
    )
    .sort(
      (a, b) => (Date.parse(b.timestamp) || 0) - (Date.parse(a.timestamp) || 0),
    );
}
const caseDescription = (rows) =>
  rows
    .slice(0, 20)
    .map(
      (row) =>
        `**${text(row.action, 50)}** · <@${row.target_id}> · by <@${row.moderator_id}>\n${text(row.reason || row.details, 100)}`,
    )
    .join("\n\n") || "No matching cases.";
export class BackupService {
  constructor(store) {
    this.store = store;
    this.queue = new KeyedQueue();
  }
  async records(id) {
    return (await this.store.read("premium_backups.json"))[id] ?? {};
  }
  async sections(id) {
    const result = {};
    for (const [name, file] of Object.entries(CONFIG))
      result[name] = (await this.store.read(file))[id] ?? null;
    return result;
  }
  async create(guild, actorId, label = "") {
    const [channels, roles] = await Promise.all([
      guild.channels.fetch(),
      guild.roles.fetch(),
    ]);
    const record = {
      id: shortId(),
      label: text(label || "Manual snapshot", 80),
      created_at: nowISO(),
      created_by: actorId,
      settings: await this.sections(guild.id),
      structure: {
        channels: [...channels.values()]
          .filter(Boolean)
          .map((c) => ({
            id: c.id,
            name: c.name,
            type: String(c.type),
            category_id: c.parentId,
            position: c.rawPosition ?? 0,
          })),
        roles: [...roles.values()]
          .filter((r) => r.id !== guild.id && !r.managed)
          .map((r) => ({
            id: r.id,
            name: r.name,
            position: r.position,
            permissions: String(r.permissions.bitfield),
          })),
      },
    };
    await this.store.update("premium_backups.json", (data) => {
      const rows = Object.values(data[guild.id] ?? {});
      rows.push(record);
      rows.sort((a, b) => b.created_at.localeCompare(a.created_at));
      data[guild.id] = Object.fromEntries(
        rows.slice(0, 10).map((row) => [row.id, row]),
      );
    });
    return record;
  }
  async restoreSections(id, settings) {
    for (const [name, value] of Object.entries(settings)) {
      const file = CONFIG[name];
      if (!file) continue;
      await this.store.update(file, (data) => {
        if (value === null) delete data[id];
        else data[id] = structuredClone(value);
      });
    }
  }
  async recover() {
    const journal = await this.store.read("backup_restore_journal.json");
    if (journal.status !== "pending") return;
    await this.restoreSections(journal.guild_id, journal.before);
    await this.store.write("backup_restore_journal.json", {
      status: "rolled_back",
      recovered_at: nowISO(),
    });
  }
  async restore(guildId, record) {
    return this.queue.run("restore", async () => {
      await this.recover();
      const before = await this.sections(guildId);
      await this.store.write("backup_restore_journal.json", {
        status: "pending",
        guild_id: guildId,
        backup_id: record.id,
        before,
        started_at: nowISO(),
      });
      try {
        await this.restoreSections(guildId, record.settings);
        await this.store.write("backup_restore_journal.json", {
          status: "completed",
          guild_id: guildId,
          backup_id: record.id,
          finished_at: nowISO(),
        });
      } catch (error) {
        try {
          await this.restoreSections(guildId, before);
          await this.store.write("backup_restore_journal.json", {
            status: "rolled_back",
            guild_id: guildId,
          });
        } catch {
          throw new UserError(
            "Restore failed and rollback could not finish. Stop the bot and repair storage before restarting; the recovery journal was retained.",
          );
        }
        throw new UserError(
          "Restore failed. The previous settings were restored.",
        );
      }
      return Object.keys(record.settings).filter((name) => CONFIG[name]).length;
    });
  }
}
export function registerOperations(registry, services) {
  const { store } = services,
    staff = { guildOnly: true, moderation: "warn", private: true },
    manager = { guildOnly: true, permissions: ["ManageGuild"], private: true };
  const backup = new BackupService(store);
  for (const command of ["status", "plans"])
    registry.register(
      `premium ${command}`,
      async () => ({
        embeds: [
          embed(
            "Rallybit is free",
            "Every Rallybit command and feature is included. There are no subscriptions or paid plans.",
          ),
        ],
      }),
      { guildOnly: true },
    );
  const collect = async (i, days) => {
    const activities = Object.values(
      (await store.read("activity_audit_logs.json"))[i.guildId] ?? {},
    ).filter((row) => row && inPeriod(row.start_time ?? row.timestamp, days));
    const moderation = await caseRows(store, i.guildId, { days });
    const tickets = (
      (await store.read("ticket_history.json"))[i.guildId] ?? []
    ).filter((row) =>
      inPeriod(row.closed_at ?? row.deleted_at ?? row.created_at, days),
    );
    const shifts = Object.entries(
      (await store.read("staff_shifts.json"))[i.guildId] ?? {},
    ).flatMap(([id, row]) =>
      (row.history ?? [])
        .filter((s) => inPeriod(s.ended_at, days))
        .map((s) => ({ ...s, user_id: id })),
    );
    const opened = Object.values(
      (await store.read("open_tickets.json"))[i.guildId] ?? {},
    ).filter(
      (row) => String(row.status ?? "open").toLowerCase() === "open",
    ).length;
    return { activities, moderation, tickets, shifts, opened };
  };
  registry.register(
    "insights overview",
    async (i) => {
      const days = i.options.getInteger("days") ?? 30,
        data = await collect(i, days);
      return {
        embeds: [
          embed(`Server insights · ${days} days`).addFields(
            {
              name: "Activity checks",
              value: String(data.activities.length),
              inline: true,
            },
            {
              name: "Moderation actions",
              value: String(data.moderation.length),
              inline: true,
            },
            {
              name: "Tickets closed or archived",
              value: String(data.tickets.length),
              inline: true,
            },
            { name: "Open tickets", value: String(data.opened), inline: true },
            {
              name: "Completed staff shifts",
              value: String(data.shifts.length),
              inline: true,
            },
            {
              name: "Recorded staff hours",
              value: (
                data.shifts.reduce((n, s) => n + (Number(s.seconds) || 0), 0) /
                3600
              ).toFixed(2),
              inline: true,
            },
          ),
        ],
      };
    },
    manager,
  );
  registry.register(
    "insights export",
    async (i) => {
      const days = i.options.getInteger("days") ?? 30,
        data = await collect(i, days);
      const rows = [
        ...data.activities.map((row) => [
          "activity",
          row.start_time ?? row.timestamp,
          "",
          row.started_by ?? row.host_id,
          "check",
          Array.isArray(row.participants) ? row.participants.length : 0,
          row.status,
        ]),
        ...data.moderation.map((row) => [
          "moderation",
          row.timestamp,
          row.target_id,
          row.moderator_id,
          row.action,
          "",
          row.reason,
        ]),
        ...data.tickets.map((row) => [
          "ticket",
          row.closed_at ?? row.deleted_at ?? row.created_at,
          row.owner_id ?? row.user_id,
          row.closed_by,
          row.status,
          "",
          row.reason,
        ]),
        ...data.shifts.map((row) => [
          "shift",
          row.ended_at,
          row.user_id,
          "",
          "completed",
          row.seconds,
          "",
        ]),
      ];
      return {
        files: [
          csvFile(
            `rallybit-insights-${i.guildId}.csv`,
            [
              "Category",
              "Timestamp",
              "Member ID",
              "Actor ID",
              "Action",
              "Value",
              "Details",
            ],
            rows,
          ),
        ],
      };
    },
    manager,
  );
  for (const operation of ["member", "recent", "stats", "export"])
    registry.register(
      `case ${operation}`,
      async (i) => {
        const days =
          i.options.getInteger("days") ??
          (operation === "export" ? 90 : operation === "stats" ? 30 : null);
        const rows = await caseRows(store, i.guildId, {
          days,
          member: i.options.getUser("member")?.id,
          moderator: i.options.getUser("moderator")?.id,
          action: i.options.getString("action"),
        });
        if (operation === "export")
          return {
            files: [
              csvFile(
                `rallybit-cases-${i.guildId}.csv`,
                [
                  "Timestamp",
                  "Action",
                  "Member ID",
                  "Moderator ID",
                  "Moderator",
                  "Reason",
                  "Details",
                ],
                rows.map((row) => [
                  row.timestamp,
                  row.action,
                  row.target_id,
                  row.moderator_id,
                  row.moderator_name,
                  row.reason,
                  row.details,
                ]),
              ),
            ],
          };
        if (operation === "stats") {
          const actions = new Map(),
            moderators = new Map();
          for (const row of rows) {
            const key = caseAction(row.action);
            actions.set(key, (actions.get(key) ?? 0) + 1);
            moderators.set(
              row.moderator_id,
              (moderators.get(row.moderator_id) ?? 0) + 1,
            );
          }
          return {
            embeds: [
              embed(
                `Moderation workload · ${days} days`,
                `${rows.length} recorded actions.`,
              ).addFields(
                {
                  name: "Actions",
                  value:
                    [...actions]
                      .map(([action, count]) => `${action}: ${count}`)
                      .join("\n") || "No actions",
                },
                {
                  name: "Moderators",
                  value:
                    [...moderators]
                      .sort((a, b) => b[1] - a[1])
                      .slice(0, 10)
                      .map(([id, count]) => `<@${id}>: ${count}`)
                      .join("\n") || "No moderators",
                },
              ),
            ],
          };
        }
        return {
          embeds: [
            embed(
              "Moderation cases",
              caseDescription(
                rows.slice(0, i.options.getInteger("limit") ?? 10),
              ),
            ),
          ],
        };
      },
      staff,
    );
  registry.register(
    "backup create",
    async (i) => {
      const row = await backup.create(
        i.guild,
        i.user.id,
        i.options.getString("label"),
      );
      return {
        content: `Configuration backup \`${row.id}\` created. This snapshots Rallybit settings; it does not recreate channels or roles.`,
      };
    },
    manager,
  );
  registry.register(
    "backup list",
    async (i) => ({
      embeds: [
        embed(
          "Configuration backups",
          Object.values(await backup.records(i.guildId))
            .sort((a, b) => b.created_at.localeCompare(a.created_at))
            .map(
              (row) =>
                `\`${row.id}\` · ${text(row.label, 80)} · ${row.created_at}`,
            )
            .join("\n") || "No backups. Your newest 10 snapshots are retained.",
        ),
      ],
    }),
    manager,
  );
  for (const operation of ["inspect", "drift", "restore", "delete"])
    registry.register(
      `backup ${operation}`,
      async (i) => {
        const id = text(
            i.options.getString("backup_id", true),
            20,
          ).toUpperCase(),
          row = (await backup.records(i.guildId))[id];
        if (!row) throw new UserError("Backup not found in this server.");
        if (operation === "inspect")
          return {
            content: `Backup ${id} · ${row.label} · ${row.created_at}`,
            files: [
              new AttachmentBuilder(Buffer.from(JSON.stringify(row, null, 2)), {
                name: `rallybit-backup-${id}.json`,
              }),
            ],
          };
        if (operation === "drift") {
          const current = await backup.sections(i.guildId),
            changed = Object.keys(row.settings).filter(
              (name) =>
                JSON.stringify(row.settings[name]) !==
                JSON.stringify(current[name]),
            );
          const [channels, roles] = await Promise.all([
            i.guild.channels.fetch(),
            i.guild.roles.fetch(),
          ]);
          const missingChannels = (row.structure?.channels ?? []).filter(
            (c) => !channels.has(String(c.id)),
          ).length;
          const missingRoles = (row.structure?.roles ?? []).filter(
            (r) => !roles.has(String(r.id)),
          ).length;
          return {
            embeds: [
              embed(
                "Configuration drift",
                `Changed modules: ${changed.join(", ") || "None"}\nMissing channels: ${missingChannels}\nMissing roles: ${missingRoles}\nStructure comparisons are read-only. Restore changes Rallybit settings only.`,
              ),
            ],
          };
        }
        if (!i.options.getBoolean("confirm", true))
          throw new UserError("Set confirm to true. Nothing was changed.");
        if (operation === "delete") {
          await store.update("premium_backups.json", (data) => {
            delete data[i.guildId][id];
          });
          return { content: `Backup ${id} deleted.` };
        }
        const count = await backup.restore(i.guildId, row);
        await services.audit(
          i,
          "configuration",
          "Configuration restored",
          `${id}: ${count} Rallybit modules restored. Channel and role permissions were not changed.`,
        );
        return {
          content: `Restored ${count} Rallybit module settings from backup ${id}.`,
        };
      },
      manager,
    );
  const ownerPolicy = { guildOnly: true, owner: true, private: true };
  const owned = async (i) => {
    if (i.guild.ownerId !== i.user.id)
      throw new UserError(
        "Network operations must be run by the server owner.",
      );
    return [...i.client.guilds.cache.values()].filter(
      (g) => g.ownerId === i.user.id,
    );
  };
  registry.register(
    "network channel",
    async (i) => {
      await owned(i);
      const channel = i.options.getChannel("destination");
      if (channel && (!channel.send || channel.guild.id !== i.guildId))
        throw new UserError("Choose a text channel in this server.");
      await updateSettings(store, "network_settings.json", i.guildId, {
        channel_id: channel?.id ?? null,
      });
      return {
        content: channel
          ? `Network broadcasts will use <#${channel.id}>.`
          : "Network broadcast destination removed.",
      };
    },
    ownerPolicy,
  );
  registry.register(
    "network overview",
    async (i) => {
      const guilds = await owned(i),
        settings = await store.read("network_settings.json");
      return {
        embeds: [
          embed(
            "Your server network",
            guilds
              .map(
                (g) =>
                  `**${text(g.name, 80)}** · ${g.memberCount} members · ${settings[g.id]?.channel_id ? `<#${settings[g.id].channel_id}>` : "No destination"}`,
              )
              .join("\n"),
          ),
        ],
      };
    },
    ownerPolicy,
  );
  registry.register(
    "network broadcast",
    async (i) => {
      const guilds = await owned(i),
        settings = await store.read("network_settings.json");
      if (!(await store.reserve("network_dispatch.json", i.user.id, 900000)))
        throw new UserError(
          "A broadcast was recently attempted. Wait 15 minutes before sending another.",
        );
      const card = embed(
        i.options.getString("title", true),
        i.options.getString("message", true),
      );
      let delivered = 0,
        failed = 0;
      for (const guild of guilds) {
        if (!settings[guild.id]?.channel_id) continue;
        // Ownership is re-fetched before each cross-server send.
        const current = await guild.fetch();
        if (current.ownerId !== i.user.id) continue;
        try {
          const channel = await guild.channels.fetch(
            String(settings[guild.id].channel_id),
          );
          await channel.send({
            embeds: [card],
            allowedMentions: { parse: [] },
          });
          delivered++;
        } catch {
          failed++;
        }
      }
      return {
        content: `Broadcast delivered to ${delivered} owned servers; ${failed} deliveries failed. Missing destinations were skipped. No mass mentions were allowed.`,
      };
    },
    ownerPolicy,
  );
  registry.register(
    "network export",
    async (i) => {
      const guilds = await owned(i),
        settings = await store.read("network_settings.json");
      return {
        files: [
          csvFile(
            `rallybit-network-${i.user.id}.csv`,
            ["Server ID", "Server", "Members", "Broadcast channel"],
            guilds.map((g) => [
              g.id,
              g.name,
              g.memberCount,
              settings[g.id]?.channel_id,
            ]),
          ),
        ],
      };
    },
    ownerPolicy,
  );
  return { backup };
}
