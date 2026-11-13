import { EmbedBuilder, MessageFlags } from 'discord.js';
import { commandPath } from './registry.js';

export function createDispatcher(registry, store) {
  return async interaction => {
    if (!interaction.isChatInputCommand() && !interaction.isContextMenuCommand()) return;
    try {
      // Notice precedes every command, including commands requiring a paid plan.
      const notice = await store.read('notice.json');
      if (notice.active) {
        await interaction.reply({
          flags: MessageFlags.Ephemeral,
          embeds: [new EmbedBuilder().setColor(0xF0B232)
            .setTitle(String(notice.title || 'Rallybit maintenance').slice(0, 256))
            .setDescription(String(notice.message || 'Commands are temporarily paused. Please try again later.').slice(0, 4096))],
          allowedMentions: { parse: [] },
        });
        return;
      }
      const path = commandPath(interaction);
      const handler = registry.handler(path);
      if (!handler) throw new Error('This command has not passed migration verification.');
      const privateReply = interaction.options.getBoolean('private') ?? false;
      await interaction.deferReply(privateReply ? { flags: MessageFlags.Ephemeral } : {});
      const result = await handler(interaction);
      await interaction.editReply({ ...result, allowedMentions: { parse: [] } });
    } catch (error) {
      console.error(JSON.stringify({ event: 'command_failed', code: error.code, name: error.name }));
      const content = /ENOSPC|EACCES|storage|JSON/i.test(`${error.code} ${error.message}`)
        ? 'Rallybit could not save this change. Please contact the bot owner.'
        : String(error.message || 'The command could not be completed.').slice(0, 1500);
      if (interaction.deferred || interaction.replied) await interaction.editReply({ content, embeds: [], allowedMentions: { parse: [] } });
      else await interaction.reply({ content, flags: MessageFlags.Ephemeral, allowedMentions: { parse: [] } });
    }
  };
}
