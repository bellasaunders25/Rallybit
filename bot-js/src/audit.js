import { EmbedBuilder } from 'discord.js';

export function createAudit(store) {
  return async (interaction, type, title, description, target = '', extraChannel = null) => {
    const settings = (await store.read('audit_settings.json'))[interaction.guildId] ?? {};
    if (settings.enabled === false || settings.enabled_events?.[type] === false) return;
    const event = {
      timestamp: new Date().toISOString(), type, title: title.slice(0, 256), description: description.slice(0, 4000),
      actor_id: interaction.user?.id ?? '', actor_name: interaction.user?.username ?? 'Not identified', target: String(target).slice(0, 250),
      channel_id: interaction.channelId ?? '', channel_name: interaction.channel?.name ?? '',
    };
    await store.update('audit_events.json', data => {
      const rows = data[interaction.guildId] ??= [];
      rows.push(event); data[interaction.guildId] = rows.slice(-1500);
    });
    const destinations = new Set([extraChannel, settings.channel_ids?.[type] || settings.default_channel_id].filter(Boolean).map(String));
    const embed = new EmbedBuilder().setTitle(event.title).setDescription(event.description)
      .setColor(0x45C486).setTimestamp().setFooter({ text: `Rallybit · ${interaction.guildId}` })
      .addFields({ name: 'Performed by', value: interaction.user?.id ? `<@${interaction.user.id}>` : 'Not identified', inline: true });
    if (target) embed.addFields({ name: 'Target', value: /^\d{15,22}$/.test(String(target)) ? `<@${target}>` : String(target).slice(0, 1024), inline: true });
    for (const id of destinations) {
      try {
        const channel = await interaction.guild.channels.fetch(id);
        if (channel?.isTextBased()) await channel.send({ embeds: [embed], allowedMentions: { parse: [] } });
      } catch (error) { console.warn(JSON.stringify({ event: 'audit_delivery_failed', channel: id, code: error.code })); }
    }
  };
}
