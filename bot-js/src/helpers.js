import { randomUUID } from "node:crypto";
import {
  AttachmentBuilder,
  EmbedBuilder,
  PermissionFlagsBits as P,
} from "discord.js";
import { UserError } from "./access.js";

export const nowISO = () => new Date().toISOString();
export const shortId = () =>
  randomUUID().replaceAll("-", "").slice(0, 8).toUpperCase();
export const text = (value, limit = 1000) =>
  String(value ?? "")
    .trim()
    .slice(0, limit);
export const embed = (title, description = "") =>
  new EmbedBuilder()
    .setColor(0x09623f)
    .setTitle(text(title, 256))
    .setDescription(text(description, 4096) || "\u200b")
    .setTimestamp();
export function snowflake(value, label = "ID") {
  const match = String(value ?? "").match(
    /^(?:https:\/\/discord(?:app)?\.com\/channels\/\d+\/\d+\/)?(\d{15,22})\/?$/,
  );
  if (!match)
    throw new UserError(`Enter a valid ${label} or Discord message link.`);
  return match[1];
}
export class KeyedQueue {
  #pending = new Map();
  async run(key, action) {
    const previous = this.#pending.get(key) ?? Promise.resolve();
    const operation = previous.catch(() => {}).then(action);
    this.#pending.set(key, operation);
    try {
      return await operation;
    } finally {
      if (this.#pending.get(key) === operation) this.#pending.delete(key);
    }
  }
}
export async function guildSettings(store, file, id, defaults = {}) {
  return { ...structuredClone(defaults), ...(await store.read(file))[id] };
}
export async function updateSettings(store, file, id, patch) {
  await store.update(file, (data) => {
    data[id] = { ...data[id], ...patch };
  });
}
export function csvFile(name, headings, rows) {
  const cell = (value) => {
    let string = String(value ?? "");
    if (/^[\s]*[=+\-@\t\r]/.test(string)) string = `'${string}`;
    return `"${string.replaceAll('"', '""')}"`;
  };
  return new AttachmentBuilder(
    Buffer.from(
      "\ufeff" +
        [headings, ...rows].map((row) => row.map(cell).join(",")).join("\r\n"),
      "utf8",
    ),
    { name },
  );
}
export function channelPermissions(channel, member, permissions) {
  if (
    !channel ||
    channel.guild?.id !== member.guild.id ||
    !channel.permissionsFor(member)?.has(permissions)
  ) {
    throw new UserError(
      `Required channel permissions: ${permissions.map((p) => Object.entries(P).find(([, value]) => value === p)?.[0] ?? "Access").join(", ")}.`,
    );
  }
}
export async function targetMembers(interaction, id, botPermission = null) {
  const guild = interaction.guild;
  const [actor, target, bot] = await Promise.all([
    guild.members.fetch({ user: interaction.user.id, force: true }),
    guild.members.fetch({ user: id, force: true }),
    guild.members.fetchMe({ force: true }),
  ]);
  if (
    target.id === actor.id ||
    target.id === guild.ownerId ||
    target.id === bot.id
  )
    throw new UserError(
      "You cannot moderate yourself, the server owner or Rallybit.",
    );
  if (
    actor.id !== guild.ownerId &&
    target.roles.highest.comparePositionTo(actor.roles.highest) >= 0
  )
    throw new UserError("That member has an equal or higher role than you.");
  if (target.roles.highest.comparePositionTo(bot.roles.highest) >= 0)
    throw new UserError(
      "That member has an equal or higher role than Rallybit.",
    );
  if (botPermission && !bot.permissions.has(botPermission))
    throw new UserError(
      "Rallybit does not have the required Discord permission.",
    );
  return { actor, target, bot };
}
