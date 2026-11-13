import { PermissionFlagsBits as P } from 'discord.js';
import { UserError, canModerate } from '../access.js';
import { embed, guildSettings, nowISO, shortId, text, updateSettings } from '../helpers.js';

const statuses = ['Pending', 'Claimed', 'Under Review', 'Resolved', 'Dismissed', 'Closed'];
const open = ['Pending', 'Claimed', 'Under Review', 'Delivery pending', 'Delivery failed'];
const staff = { guildOnly: true, moderation: 'warn', private: true };
function reportEmbed(id, row) {
  const card = embed(`Member report ${id}`, row.reason).addFields(
    { name: 'Reported member', value: `<@${row.target_id}>`, inline: true }, { name: 'Reported by', value: `<@${row.reporter_id}>`, inline: true },
    { name: 'Status', value: row.status, inline: true });
  if (row.details) card.addFields({ name: 'Details', value: text(row.details, 1024) });
  if (row.evidence) card.addFields({ name: 'Evidence', value: text(row.evidence, 1024) });
  return card;
}
export function reportSummary(entries) {
  return entries.slice(-20).reverse().map(([id,row]) => `\`${id}\` **${text(row.status, 30)}** · <@${row.target_id}> — ${text(row.reason, 100)}`).join('\n') || 'No matching reports.';
}
export class ReportService {
  constructor({ store, audit }) { Object.assign(this, { store, audit }); }
  async records(id) { return (await this.store.read('reports.json'))[id] ?? {}; }
  async submit(i) {
    const member = i.options.getUser('member', true);
    if (member.id === i.user.id || member.bot) throw new UserError('You cannot report yourself or a bot.');
    await i.guild.members.fetch({ user: member.id, force: true });
    const settings = await guildSettings(this.store, 'report_settings.json', i.guildId);
    const channel = settings.channel_id && await i.guild.channels.fetch(String(settings.channel_id));
    if (!channel?.isTextBased() || !channel.send) throw new UserError('Ask an administrator to configure a report channel.');
    const id = shortId();
    const row = { report_id: id, target_id: member.id, reporter_id: i.user.id, reason: text(i.options.getString('reason', true)),
      details: text(i.options.getString('details'), 1500), evidence: text(i.options.getString('evidence')), status: 'Delivery pending',
      claimed_by: null, resolution: null, created_at: nowISO(), updated_at: nowISO(), updated_by: i.user.id, request_id: i.id };
    if (!row.reason) throw new UserError('Provide a reason for the report.');
    await this.store.update('reports.json', data => { (data[i.guildId] ??= {})[id] = row; });
    try {
      const message = await channel.send({ embeds: [reportEmbed(id, { ...row, status: 'Pending' })], allowedMentions: { parse: [] } });
      await this.store.update('reports.json', data => Object.assign(data[i.guildId][id], { status: 'Pending', message_id: message.id, channel_id: channel.id }));
    } catch (error) {
      await this.store.update('reports.json', data => { data[i.guildId][id].status = 'Delivery failed'; });
      throw new UserError(`Report ${id} was saved, but delivery failed. Ask staff to check /report list and the report channel permissions.`);
    }
    await this.audit(i, 'reports', 'Member report submitted', `Report ${id} submitted for staff review.`, member.id);
    return { content: `Your report was submitted privately. Reference: \`${id}\`` };
  }
  async change(i, operation) {
    const id = text(i.options.getString('report_id', true), 20).toUpperCase();
    let changed;
    await this.store.update('reports.json', data => {
      const row = data[i.guildId]?.[id];
      if (!row) throw new UserError('Report not found.');
      let status = { claim: 'Claimed', close: 'Closed', reopen: 'Under Review' }[operation];
      if (operation === 'setstatus') status = i.options.getString('status', true);
      if (!statuses.includes(status)) throw new UserError('Choose a valid report status.');
      if (operation === 'claim' && row.claimed_by && String(row.claimed_by) !== i.user.id) throw new UserError('That report is already claimed by another moderator.');
      if (status === 'Claimed') row.claimed_by ??= i.user.id;
      row.status = status;
      row.resolution = text(i.options.getString('note') || i.options.getString('resolution') || row.resolution);
      row.updated_at = nowISO(); row.updated_by = i.user.id;
      changed = structuredClone(row);
    });
    if (changed.channel_id && changed.message_id) {
      try { const channel = await i.guild.channels.fetch(String(changed.channel_id)); const message = await channel.messages.fetch(String(changed.message_id)); await message.edit({ embeds: [reportEmbed(id, changed)], allowedMentions: { parse: [] } }); }
      catch (error) { console.warn(JSON.stringify({ event: 'report_message_update_failed', code: error.code })); }
    }
    await this.audit(i, 'reports', 'Report updated', `${id}: ${changed.status}`, changed.target_id);
    return { content: `Report \`${id}\` is now **${changed.status}**.` };
  }
}
export function registerReports(registry, services) {
  const service = new ReportService(services);
  registry.register('report user', i => service.submit(i), { guildOnly: true, private: true });
  registry.register('report settings', async i => {
    const channel = i.options.getChannel('channel', true);
    if (channel.guild?.id !== i.guildId || !channel.send) throw new UserError('Choose a text channel in this server.');
    await updateSettings(services.store, 'report_settings.json', i.guildId, { channel_id: channel.id });
    return { content: `New member reports will be sent to <#${channel.id}>.` };
  }, staff);
  registry.register('report list', async i => ({ embeds: [embed('Open reports', reportSummary(Object.entries(await service.records(i.guildId)).filter(([, row]) => open.includes(row.status))))] }), staff);
  for (const action of ['claim','setstatus','close','reopen']) registry.register(`report ${action}`, i => service.change(i, action), staff);
  registry.register('report history', async i => ({ embeds: [embed('Member report history', reportSummary(Object.entries(await service.records(i.guildId)).filter(([,row]) => String(row.target_id) === i.options.getUser('member', true).id)))] }), staff);
  registry.register('report status', async i => {
    const id = text(i.options.getString('report_id', true), 20).toUpperCase();
    const row = (await service.records(i.guildId))[id];
    const member = await i.guild.members.fetch({ user: i.user.id, force: true });
    if (!row || (String(row.reporter_id) !== i.user.id && !await canModerate(member, 'warn', services.store))) throw new UserError('That report was not found or is not yours.');
    return { embeds: [reportEmbed(id, row)] };
  }, { guildOnly: true, private: true });
  registry.register('review', async i => {
    const type = i.options.getString('type', true), user = i.options.getUser('user', true), stars = i.options.getInteger('stars', true);
    const reason = text(i.options.getString('reason', true), 1001);
    if (!['staff','member'].includes(type) || !Number.isInteger(stars) || stars < 1 || stars > 5) throw new UserError('Select staff or member and a rating from 1 to 5.');
    if (user.id === i.user.id || user.bot) throw new UserError('You cannot review yourself or a bot.');
    if (reason.length < 3 || reason.length > 1000) throw new UserError('Review reasons must contain 3 to 1,000 characters.');
    await i.guild.members.fetch({ user: user.id, force: true });
    const settings = await guildSettings(services.store, 'review_settings.json', i.guildId);
    const channel = settings[`${type}_channel_id`] && await i.guild.channels.fetch(String(settings[`${type}_channel_id`]));
    if (!channel?.send) throw new UserError(`Ask an administrator to configure a ${type} review channel.`);
    const card = embed(`${type === 'staff' ? 'Staff' : 'Member'} review`, reason).setThumbnail(user.displayAvatarURL()).addFields(
      { name: 'Reviewed user', value: `<@${user.id}>\n\`${user.id}\``, inline: true }, { name: 'Reviewer', value: `<@${i.user.id}>\n\`${i.user.id}\``, inline: true },
      { name: 'Score', value: `${stars}/5 ${'★'.repeat(stars)}${'☆'.repeat(5-stars)}`, inline: true });
    if (i.guild.bannerURL()) card.setImage(i.guild.bannerURL());
    const message = await channel.send({ embeds: [card], allowedMentions: { parse: [] } });
    await services.audit(i, 'reports', 'Review posted', `${type} review, ${stars}/5.`, user.id);
    return { content: `Your review was posted: ${message.url}` };
  }, { guildOnly: true });
  return service;
}
