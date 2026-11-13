import {
  ActionRowBuilder,
  AttachmentBuilder,
  ButtonBuilder,
  ButtonStyle,
} from "discord.js";
import { UserError, workforceAccess } from "../access.js";
import { embed, guildSettings, nowISO, text } from "../helpers.js";
import { activeSeconds, formatDuration } from "./workforce.js";
import { syncGuildCache } from "../api.js";

export function registerCommunityUtility(registry, services) {
  const { store } = services,
    publicPolicy = { guildOnly: true };
  registry.register(
    "afk",
    async (i) => {
      const reason = text(i.options.getString("reason") || "AFK", 300);
      await store.update("afk_status.json", (data) => {
        (data[i.guildId] ??= {})[i.user.id] = { reason, since: nowISO() };
      });
      return { content: `You are now marked AFK: ${reason}` };
    },
    publicPolicy,
  );
  registry.register(
    "about",
    async (i) => ({
      embeds: [
        embed(
          "Rallybit",
          "Moderation, tickets, activity checks, automation and community management. All features are free.",
        ).addFields(
          { name: "Runtime", value: "discord.js", inline: true },
          {
            name: "Servers",
            value: String(i.client.guilds.cache.size),
            inline: true,
          },
        ),
      ],
    }),
    publicPolicy,
  );
  registry.register(
    "settings",
    async (i) => {
      const activity = await guildSettings(
          store,
          "activity_settings.json",
          i.guildId,
        ),
        quiz = await guildSettings(store, "quiz_settings.json", i.guildId);
      return {
        embeds: [
          embed(
            "Server configuration",
            `Server: ${i.guild.name}\nEvery Rallybit feature is free.`,
          ).addFields(
            {
              name: "Activity checks",
              value: `Winners: ${activity.winner_count ?? 3}\nDuration: ${activity.check_duration_minutes ?? 60} minutes\nMode: ${activity.reactor_type ?? "reaction"}`,
              inline: true,
            },
            {
              name: "Automation",
              value: `Activity: ${activity.auto_enabled ? "Enabled" : "Disabled"}\nQuizzes: ${quiz.enabled ? "Enabled" : "Disabled"}`,
              inline: true,
            },
          ),
        ],
      };
    },
    publicPolicy,
  );
  registry.register(
    "leaderboard",
    async (i) => {
      let scores;
      if (i.options.getString("category") === "staff") {
        const actor = await i.guild.members.fetch({
          user: i.user.id,
          force: true,
        });
        if (!(await workforceAccess(actor, store)).staff)
          throw new UserError(
            "You need a configured staff role for staff-hours leaderboards.",
          );
        scores = Object.entries(
          (await store.read("staff_shifts.json"))[i.guildId] ?? {},
        ).map(([id, row]) => [
          id,
          (row.history ?? []).reduce(
            (sum, s) => sum + Math.max(0, Number(s.seconds) || 0),
            0,
          ) + activeSeconds(row.active),
        ]);
        scores.sort((a, b) => b[1] - a[1]);
        return {
          embeds: [
            embed(
              "Staff-hours leaderboard",
              scores
                .slice(0, 10)
                .map(
                  ([id, score], index) =>
                    `${index + 1}. <@${id}> · ${formatDuration(score)}`,
                )
                .join("\n") || "No shifts recorded.",
            ),
          ],
        };
      }
      scores = Object.entries(
        (await store.read("activity_log.json"))[i.guildId] ?? {},
      )
        .map(([id, rows]) => [id, Array.isArray(rows) ? rows.length : 0])
        .sort((a, b) => b[1] - a[1]);
      return {
        embeds: [
          embed(
            "Activity leaderboard",
            scores
              .slice(0, 10)
              .map(
                ([id, score], index) =>
                  `${index + 1}. <@${id}> · ${score} check-ins`,
              )
              .join("\n") || "No check-ins recorded.",
          ),
        ],
      };
    },
    publicPolicy,
  );
  registry.register(
    "userleaderboard",
    async () => ({
      embeds: [
        embed(
          "Global activity wins",
          Object.entries(await store.read("global_stats.json"))
            .sort((a, b) => (b[1].wins ?? 0) - (a[1].wins ?? 0))
            .slice(0, 10)
            .map(
              ([id, row], index) =>
                `${index + 1}. <@${id}> · ${row.wins ?? 0} wins`,
            )
            .join("\n") || "No wins recorded.",
        ),
      ],
    }),
    publicPolicy,
  );
  registry.register(
    "profile",
    async (i) => {
      const user = i.options.getUser("user", true);
      let profile;
      await store.update("global_stats.json", (data) => {
        profile = data[user.id] ??= { wins: 0, checkins: 0 };
        Object.assign(profile, {
          username: user.username,
          display_name: user.globalName || user.username,
          avatar_url: user.displayAvatarURL(),
        });
      });
      const custom = (await store.read("badges.json"))[user.id] ?? [];
      const badgeConfig = await store.read("win_badges.json", []);
      const badges = [
        ...(Array.isArray(badgeConfig) ? badgeConfig : [])
          .filter(
            (row) => (Number(profile.wins) || 0) >= (Number(row.wins) || 0),
          )
          .sort((a, b) => (Number(a.wins) || 0) - (Number(b.wins) || 0))
          .map((row) => row.badge),
        ...(Array.isArray(custom) ? custom : [custom]),
      ].filter(Boolean);
      const ranks = [
        [500, "Celestial"],
        [250, "Godlike"],
        [100, "Activity Legend"],
        [50, "Activity Master"],
        [25, "Elite Member"],
        [10, "Hyper Active"],
        [0, "Newcomer"],
      ];
      const rank = ranks.find(
        ([minimum]) => Math.max(0, Number(profile.wins) || 0) >= minimum,
      )[1];
      const url = new URL(
        `/id/${encodeURIComponent(user.username)}`,
        process.env.DASHBOARD_URL || "https://rallybit.pages.dev/dashboard/",
      ).href;
      return {
        embeds: [
          embed(`${user.globalName || user.username} · ${rank}`)
            .setThumbnail(user.displayAvatarURL())
            .addFields(
              { name: "Wins", value: String(profile.wins ?? 0), inline: true },
              {
                name: "Check-ins",
                value: String(profile.checkins ?? 0),
                inline: true,
              },
              {
                name: "Badges",
                value: text(badges.join(" ") || "No badges earned yet.", 1024),
              },
            ),
        ],
        components: [
          new ActionRowBuilder().addComponents(
            new ButtonBuilder()
              .setStyle(ButtonStyle.Link)
              .setLabel("View profile")
              .setURL(url),
          ),
        ],
      };
    },
    publicPolicy,
  );
  const inactive = async (i, full = false) => {
    const log = (await store.read("activity_log.json"))[i.guildId] ?? {},
      lastSeen = (await store.read("last_seen.json"))[i.guildId] ?? {};
    const days = full ? null : (i.options.getInteger("days") ?? 7);
    const threshold = days ? Date.now() - days * 86400000 : null;
    const members = await i.guild.members.fetch(),
      rows = [];
    for (const member of members.values()) {
      if (member.user.bot) continue;
      const latest = (
        Array.isArray(log[member.id]) ? log[member.id] : []
      ).reduce(
        (current, value) => Math.max(current, Date.parse(value) || 0),
        Date.parse(lastSeen[member.id]) || 0,
      );
      if (full ? !Object.hasOwn(log, member.id) : latest < threshold)
        rows.push(`${member.user.tag ?? member.user.username} (${member.id})`);
    }
    return {
      content: `${rows.length} inactive members${days ? ` over ${days} days` : " with no recorded check-in"}.`,
      ...(rows.length
        ? {
            files: [
              new AttachmentBuilder(Buffer.from(rows.join("\n")), {
                name: "inactive-members.txt",
              }),
            ],
          }
        : {}),
    };
  };
  registry.register("inactive", (i) => inactive(i), {
    guildOnly: true,
    permissions: ["Administrator"],
    private: true,
  });
  registry.register("checkinactive", (i) => inactive(i, true), {
    guildOnly: true,
    permissions: ["Administrator"],
    private: true,
  });
  registry.register(
    "addadmin",
    async (i) => {
      if (i.user.id !== process.env.OWNER_ID)
        throw new UserError("Only the bot owner can add developers.");
      const id = i.options.getUser("user", true).id;
      await store.update(
        "admins.json",
        (data) => {
          const ids = Array.isArray(data) ? data : (data.admin_ids ??= []);
          if (!ids.includes(id)) ids.push(id);
        },
        [],
      );
      return { content: `<@${id}> is now a Rallybit developer.` };
    },
    { guildOnly: true, developer: true, private: true },
  );
  for (const type of ["user", "server"])
    registry.register(
      `ban${type}`,
      async (i) => {
        const id =
          type === "user"
            ? i.options.getUser("user", true).id
            : i.options.getString("server_id", true).trim();
        if (!/^\d{15,22}$/.test(id))
          throw new UserError("Enter a valid Discord ID.");
        await store.update(
          `banned_${type === "user" ? "users" : "servers"}.json`,
          (data) => {
            data[id] = {
              reason: text(
                i.options.getString("reason") || "Global bot ban",
                300,
              ),
              moderator_id: i.user.id,
            };
          },
        );
        return { content: `Blocked ${type} ${id} from Rallybit.` };
      },
      { guildOnly: true, developer: true, private: true },
    );
  registry.register(
    "syncguilds",
    async (i) => {
      await syncGuildCache(i.client, store);
      return { content: "Dashboard server cache refreshed." };
    },
    { guildOnly: true, developer: true, private: true },
  );
  return {
    async onMessage(message) {
      if (!message.guild || message.author.bot) return;
      let removed,
        notices = [];
      const current = (await store.read("afk_status.json"))[message.guildId];
      if (!current) return;
      if (current[message.author.id])
        await store.update("afk_status.json", (data) => {
          const rows = data[message.guildId];
          if (rows) {
            removed = rows[message.author.id];
            delete rows[message.author.id];
          }
        });
      notices = [...message.mentions.users.values()]
        .slice(0, 10)
        .flatMap((user) =>
          current[user.id] && user.id !== message.author.id
            ? [`${user.username} is AFK: ${text(current[user.id].reason, 180)}`]
            : [],
        );
      if (removed || notices.length)
        await message.reply({
          content: text(
            [
              removed ? "Welcome back. Your AFK status was removed." : "",
              ...notices,
            ]
              .filter(Boolean)
              .join("\n"),
            1900,
          ),
          allowedMentions: { parse: [], repliedUser: false },
        });
    },
  };
}
