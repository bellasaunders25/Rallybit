import { EmbedBuilder, MessageFlags } from 'discord.js';
import { commandPath } from './registry.js';
import { enforceAccess, UserError } from './access.js';
import { createAudit } from './audit.js';

export function createDispatcher(registry, store, { components } = {}) {
  const audit = createAudit(store);
  return async interaction => {
    const command = interaction.isChatInputCommand() || interaction.isContextMenuCommand();
    if (!command && (!interaction.customId || !components)) return;
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
      if (!command) { await components.dispatch(interaction); return; }
      const path = commandPath(interaction);
      const handler = registry.handler(path);
      if (!handler) throw new Error('This command has not passed migration verification.');
      const privateReply = registry.policy(path)?.private === true || (interaction.isChatInputCommand() ? interaction.options.getBoolean('private') ?? false : false);
      await interaction.deferReply(privateReply ? { flags: MessageFlags.Ephemeral } : {});
      await enforceAccess(interaction, registry.policy(path), store);
      const result = await handler(interaction);
      await interaction.editReply({ ...result, allowedMentions: { parse: [] } });
      try { await audit(interaction, 'commands', 'Command used', `/${path} was used.`); }
      catch (auditError) { console.warn(JSON.stringify({ event: 'command_audit_failed', code: auditError.code })); }
    } catch (error) {
      console.error(JSON.stringify({ event: 'command_failed', code: error.code, name: error.name }));
      const content = /ENOSPC|EACCES|storage|JSON/i.test(`${error.code} ${error.message}`)
        ? 'Rallybit could not save this change. Please contact the bot owner.'
        : error instanceof UserError ? error.message : 'The command could not be completed. The incident has been logged.';
      try {
        if (interaction.deferred || interaction.replied) {
          await interaction.deleteReply().catch(() => {});
          await interaction.followUp({ content, flags: MessageFlags.Ephemeral, allowedMentions: { parse: [] } });
        } else await interaction.reply({ content, flags: MessageFlags.Ephemeral, allowedMentions: { parse: [] } });
      } catch (replyError) { console.error(JSON.stringify({ event: 'error_reply_failed', code: replyError.code })); }
    }
  };
}
