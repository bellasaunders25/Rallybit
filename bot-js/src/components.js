import { MessageFlags } from "discord.js";
import { UserError } from "./access.js";

export class ComponentRouter {
  #handlers = new Map();
  register(prefix, handler) {
    if (this.#handlers.has(prefix))
      throw new Error(`Duplicate component prefix: ${prefix}`);
    this.#handlers.set(prefix, handler);
  }
  async dispatch(interaction) {
    const prefix = [...this.#handlers.keys()]
      .sort((a, b) => b.length - a.length)
      .find(
        (key) =>
          interaction.customId === key ||
          interaction.customId?.startsWith(`${key}:`),
      );
    const handler = this.#handlers.get(prefix);
    if (!handler)
      throw new UserError(
        "This control is no longer available. Run the command again.",
      );
    return handler(interaction);
  }
}
export const privateReply = (interaction, result) =>
  interaction.deferred || interaction.replied
    ? interaction.editReply({ ...result, allowedMentions: { parse: [] } })
    : interaction.reply({
        ...result,
        flags: MessageFlags.Ephemeral,
        allowedMentions: { parse: [] },
      });
