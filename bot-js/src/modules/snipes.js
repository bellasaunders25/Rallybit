import { UserError } from "../access.js";
import { embed, text } from "../helpers.js";
export class SnipeService {
  constructor() {
    this.deleted = new Map();
    this.edited = new Map();
  }
  prune() {
    for (const map of [this.deleted, this.edited])
      for (const [id, rows] of map) {
        const valid = rows
          .filter((row) => row.when > Date.now() - 7200000)
          .slice(0, 10);
        if (valid.length) map.set(id, valid);
        else map.delete(id);
      }
    for (const map of [this.deleted, this.edited])
      while (map.size > 5000) map.delete(map.keys().next().value);
  }
  record(message, after = null) {
    if (
      !message.guild ||
      !message.author ||
      message.author.bot ||
      (after && message.content === after.content)
    )
      return;
    const map = after ? this.edited : this.deleted;
    const row = {
      author_id: message.author.id,
      author_name: message.author.tag ?? message.author.username,
      avatar: message.author.displayAvatarURL(),
      before: text(message.content || "(no cached text)", 4000),
      after: text(after?.content || "", 1024),
      attachments: [...(message.attachments?.values() ?? [])]
        .slice(0, 5)
        .map((a) => a.url),
      when: Date.now(),
    };
    map.set(
      message.channelId,
      [row, ...(map.get(message.channelId) ?? [])].slice(0, 10),
    );
    this.prune();
  }
}
export function registerSnipes(registry) {
  const service = new SnipeService();
  for (const command of ["snipe", "editsnipe"])
    registry.register(
      command,
      async (i) => {
        service.prune();
        const rows =
            (command === "snipe" ? service.deleted : service.edited).get(
              i.channelId,
            ) ?? [],
          index = i.options.getInteger("index") ?? 1,
          row = rows[index - 1];
        if (!row)
          throw new UserError("There is no cached message at that position.");
        const card = embed(
          command === "snipe" ? "Deleted message" : "Edited message",
          command === "snipe" ? row.before : "",
        )
          .setAuthor({ name: row.author_name, iconURL: row.avatar })
          .setFooter({
            text: "Held in memory for up to 2 hours; cleared on restart.",
          });
        if (command === "editsnipe")
          card.addFields(
            { name: "Before", value: text(row.before, 1024) },
            { name: "After", value: row.after || "(empty)" },
          );
        if (row.attachments.length)
          card.addFields({
            name: "Attachments",
            value: text(row.attachments.join("\n"), 1024),
          });
        return { embeds: [card] };
      },
      { guildOnly: true },
    );
  registry.register(
    "clearsnipes",
    async (i) => {
      service.deleted.delete(i.channelId);
      service.edited.delete(i.channelId);
      return {
        content: "Cached deleted and edited messages cleared for this channel.",
      };
    },
    { guildOnly: true, permissions: ["ManageMessages"] },
  );
  return service;
}
