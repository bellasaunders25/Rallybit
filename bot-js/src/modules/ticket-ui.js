import {
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  EmbedBuilder,
  StringSelectMenuBuilder,
} from "discord.js";
import { UserError } from "../access.js";
import { embed, shortId, text } from "../helpers.js";

export const TICKET_DEFAULTS = {
  default_category_id: null,
  log_channel_id: null,
  support_role_ids: [],
  one_ticket_per_member: true,
  transcript_limit: 500,
  auto_delete_minutes: 0,
  ticket_name: "ticket-{username}",
  welcome_message:
    "Thanks for reaching out, {user}. A team member will be with you shortly. Please explain what you need help with and include useful context.",
};
export const ROLE_GRANT = {
  ViewChannel: true,
  SendMessages: true,
  ReadMessageHistory: true,
  ManageMessages: true,
};
export function mediaURL(value) {
  const raw = text(value, 2048);
  if (!raw) return null;
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new UserError("Panel media must use a valid HTTPS URL.");
  }
  if (url.protocol !== "https:" || url.username || url.password)
    throw new UserError("Panel media must use a valid HTTPS URL.");
  return url.href;
}
export function normaliseOption(row, fallback = "DEFAULT") {
  const ids = Array.isArray(row.support_role_ids)
    ? row.support_role_ids
    : row.support_role_ids
      ? [row.support_role_ids]
      : [];
  const name = text(row.name || "Support", 100);
  return {
    option_id: text(row.option_id || fallback, 32)
      .toUpperCase()
      .replace(/[^A-Z0-9_-]/g, ""),
    name,
    description: text(
      row.description || "Speak privately with the support team.",
      100,
    ),
    emoji: text(row.emoji, 100),
    enabled: row.enabled !== false,
    category_id: row.category_id ? String(row.category_id) : null,
    support_role_ids: [
      ...new Set(ids.map(String).filter((id) => /^\d{15,22}$/.test(id))),
    ],
    ticket_name: text(row.ticket_name, 90),
    ticket_title: text(row.ticket_title || `${name} ticket`, 256),
    welcome_message: text(
      row.welcome_message || TICKET_DEFAULTS.welcome_message,
      4000,
    ),
  };
}
export function panelOptions(panel) {
  if (Array.isArray(panel.options) && panel.options.length)
    return panel.options
      .slice(0, 25)
      .filter((row) => row && typeof row === "object")
      .map((row, index) => normaliseOption(row, `OPTION-${index + 1}`));
  return [
    normaliseOption({
      ...panel,
      option_id: "DEFAULT",
      emoji: panel.button_emoji,
      name: panel.name || panel.button_label,
      description: panel.option_description,
    }),
  ];
}
export function selectedOption(panel, id) {
  const options = panelOptions(panel);
  const selected = id
    ? options.find((row) => row.option_id === String(id).toUpperCase())
    : options.length === 1
      ? options[0]
      : null;
  if (!selected)
    throw new UserError(
      "That ticket option no longer exists. Refresh the panel.",
    );
  if (!selected.enabled) throw new UserError("This ticket option is closed.");
  return selected;
}
export function optionEmoji(value, guild) {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  const custom = raw.match(/^<(a?):([A-Za-z0-9_]+):(\d{15,22})>$/);
  const id = custom?.[3] ?? (/^\d{15,22}$/.test(raw) ? raw : null);
  if (id) {
    const cached =
      guild?.client?.emojis?.cache.get(id) ?? guild?.emojis?.cache.get(id);
    return {
      id,
      name: cached?.name ?? custom?.[2] ?? "ticket_option",
      animated: cached?.animated ?? Boolean(custom?.[1]),
    };
  }
  return /[\p{Extended_Pictographic}\p{Regional_Indicator}\u20e3]/u.test(raw)
    ? { name: raw }
    : null;
}
export function ticketControls(guildId, channelId) {
  return [
    new ActionRowBuilder().addComponents(
      ["claim", "transcript", "close", "delete"].map((action) =>
        new ButtonBuilder()
          .setCustomId(`rallybit:ticket:${action}:${guildId}:${channelId}`)
          .setLabel(action[0].toUpperCase() + action.slice(1))
          .setStyle(
            ["close", "delete"].includes(action)
              ? ButtonStyle.Danger
              : ButtonStyle.Secondary,
          ),
      ),
    ),
  ];
}
export function panelPayload(guild, id, panel, workload = 0) {
  const color = /^#?[0-9a-f]{6}$/i.test(String(panel.color ?? ""))
    ? parseInt(String(panel.color).replace("#", ""), 16)
    : 0x09623f;
  let description = text(
    panel.description ||
      "Choose a ticket type to speak privately with the support team.",
    1800,
  );
  if (panel.show_workload !== false)
    description += `\n\n**Current workload**\n${workload ? `${workload} active tickets` : "No tickets waiting"}`;
  if (panel.show_guidance !== false)
    description +=
      "\n\n**Before opening**\nChoose the closest option, describe the issue and avoid duplicate tickets.";
  const card = new EmbedBuilder()
    .setColor(color)
    .setTitle(text(panel.title || "Support centre", 256))
    .setDescription(text(description, 4096));
  if (panel.show_timestamp !== false) card.setTimestamp();
  if (panel.show_author && panel.author_name)
    card.setAuthor({
      name: text(panel.author_name, 256),
      ...(mediaURL(panel.author_icon_url)
        ? { iconURL: mediaURL(panel.author_icon_url) }
        : {}),
    });
  if (panel.footer_text)
    card.setFooter({
      text: text(panel.footer_text, 1500),
      ...(mediaURL(panel.footer_icon_url)
        ? { iconURL: mediaURL(panel.footer_icon_url) }
        : {}),
    });
  if (panel.thumbnail_url) card.setThumbnail(mediaURL(panel.thumbnail_url));
  if (panel.image_url) card.setImage(mediaURL(panel.image_url));
  const options = panelOptions(panel);
  let budget =
    (card.data.description?.length ?? 0) +
    (card.data.title?.length ?? 0) +
    (card.data.footer?.text?.length ?? 0) +
    (card.data.author?.name?.length ?? 0);
  if (panel.show_option_details !== false)
    for (const row of options) {
      const name = text(`${row.name}${row.enabled ? "" : " — Closed"}`, 180),
        value = text(
          row.description +
            (row.enabled ? "" : "\nThis option is not accepting new tickets."),
          200,
        );
      if (budget + name.length + value.length > 5800) break;
      card.addFields({ name, value });
      budget += name.length + value.length;
    }
  const embeds = [];
  if (panel.header_image_url)
    embeds.push(
      new EmbedBuilder()
        .setColor(color)
        .setImage(mediaURL(panel.header_image_url)),
    );
  embeds.push(card);
  const enabled = options.filter((row) => row.enabled),
    select = new StringSelectMenuBuilder()
      .setCustomId(`rallybit:ticket:select:${guild.id}:${id}`)
      .setPlaceholder(
        text(panel.select_placeholder || "Select a ticket type…", 150),
      )
      .setMinValues(1)
      .setMaxValues(1);
  if (!enabled.length)
    select
      .setDisabled(true)
      .addOptions({ label: "No ticket types are open", value: "CLOSED" });
  else
    select.addOptions(
      enabled.map((row) => ({
        label: row.name,
        value: row.option_id,
        description: row.description,
        ...(optionEmoji(row.emoji, guild)
          ? { emoji: optionEmoji(row.emoji, guild) }
          : {}),
      })),
    );
  return {
    embeds,
    components: [new ActionRowBuilder().addComponents(select)],
    allowedMentions: { parse: [] },
  };
}
export function validatePanel(input, previous = {}) {
  const panel = { ...previous, ...input };
  for (const field of [
    "author_icon_url",
    "header_image_url",
    "thumbnail_url",
    "image_url",
    "footer_icon_url",
  ])
    panel[field] = mediaURL(panel[field]);
  if (!/^#?[0-9a-f]{6}$/i.test(String(panel.color ?? "#09623f")))
    throw new UserError("Panel colour must be a six-digit hex colour.");
  const saved = new Map(
    panelOptions(previous).map((row) => [row.option_id, row]),
  );
  const incoming = Array.isArray(input.options) ? input.options : panel.options;
  if (!Array.isArray(incoming) || !incoming.length || incoming.length > 25)
    throw new UserError(
      "A ticket panel needs between 1 and 25 dropdown options.",
    );
  const ids = new Set();
  panel.options = incoming.map((row, index) => {
    const id = String(row.option_id || shortId()).toUpperCase();
    const item = normaliseOption(
      { ...saved.get(id), ...row, option_id: id },
      `OPTION-${index + 1}`,
    );
    if (ids.has(item.option_id))
      throw new UserError("Ticket option IDs must be unique.");
    ids.add(item.option_id);
    return item;
  });
  panel.name = text(panel.name || panel.options[0].name, 80);
  panel.category_id = panel.options[0].category_id;
  panel.support_role_ids = [];
  panel.title = text(panel.title || "Support centre", 256);
  panel.description = text(panel.description, 4000);
  return panel;
}
export function welcomePayload(guild, channel, member, panel, record) {
  const description = String(
    panel.welcome_message || TICKET_DEFAULTS.welcome_message,
  )
    .replaceAll("{user}", `<@${member.id}>`)
    .replaceAll("{username}", member.displayName)
    .replaceAll("{server}", guild.name);
  const card = embed(
    panel.ticket_title || "Welcome to your ticket",
    description,
  ).addFields(
    { name: "Case", value: record.case_id, inline: true },
    { name: "Category", value: panel.name || "Support", inline: true },
    { name: "Creator", value: `<@${member.id}>`, inline: true },
  );
  if (record.reason)
    card.addFields({ name: "Your request", value: text(record.reason, 1000) });
  if (guild.iconURL()) card.setThumbnail(guild.iconURL());
  if (panel.image_url) card.setImage(mediaURL(panel.image_url));
  return { embeds: [card], components: ticketControls(guild.id, channel.id) };
}
