import { ActionRowBuilder, ButtonBuilder, ButtonStyle, MessageFlags, ModalBuilder, PermissionFlagsBits as P, TextInputBuilder, TextInputStyle } from 'discord.js';
import { UserError, canModerate } from '../access.js';
import { embed, KeyedQueue, nowISO, shortId, snowflake, targetMembers, text } from '../helpers.js';
import { checkRoleHierarchy } from './roles.js';
import { privateReply } from '../components.js';

const actions = {
  warn: ['Warn', 'warn'], unwarn: ['Remove warning', 'warn'], warnings: ['Warnings', 'warn'], history: ['History', 'warn'],
  timeout: ['Timeout', 'timeout'], untimeout: ['End timeout', 'timeout'], kick: ['Kick', 'kick'], ban: ['Ban', 'ban'],
  nickname: ['Nickname', 'nickname'], role_add: ['Add role', 'role'], role_remove: ['Remove role', 'role'], reports: ['Reports', 'warn'],
};
export function parseDuration(value) {
  const match = String(value).trim().match(/^(\d+)\s*(s|m|h|d|w)$/i);
  const duration = match ? Number(match[1]) * ({ s: 1000, m: 60000, h: 3600000, d: 86400000, w: 604800000 })[match[2].toLowerCase()] : NaN;
  if (!Number.isSafeInteger(duration) || duration < 1000 || duration > 28 * 86400000) throw new UserError('Use a duration such as 10m, 2h or 1d, up to 28 days.');
  return duration;
}
export class ModerationService {
  constructor({ store, audit }) { Object.assign(this, { store, audit }); this.queue = new KeyedQueue(); }
  async history(interaction, targetId, action, reason, details = '') {
    await this.store.update('moderation_history.json', data => {
      const records = (data[interaction.guildId] ??= {})[targetId] ??= [];
      records.push({ action, moderator_id: interaction.user.id, moderator_name: interaction.user.username, reason: text(reason, 500), details: text(details, 500), timestamp: nowISO() });
      data[interaction.guildId][targetId] = records.slice(-100);
    });
    await this.audit(interaction, 'moderation', action, `${text(reason, 500)}${details ? `\n${text(details, 500)}` : ''}`, targetId);
  }
  async warnings(guildId, id) { return (await this.store.read('moderation_warnings.json'))[guildId]?.[id] ?? []; }
  async execute(i, action, targetId, params = {}) {
    return this.queue.run(`${i.guildId}:${targetId}`, async () => {
      const permission = actions[action]?.[1];
      const actor = await i.guild.members.fetch({ user: i.user.id, force: true });
      if (!permission || !await canModerate(actor, permission, this.store)) throw new UserError('You need the configured moderation role and Discord permission.');
      const botPermission = { timeout: P.ModerateMembers, untimeout: P.ModerateMembers, kick: P.KickMembers, ban: P.BanMembers, nickname: P.ManageNicknames, role_add: P.ManageRoles, role_remove: P.ManageRoles }[action];
      const { target, bot } = await targetMembers(i, targetId, botPermission);
      const reason = text(params.reason || 'Moderation through Rallybit', 500);
      let details = '';
      const external = ['timeout','untimeout','kick','ban','nickname','role_add','role_remove'].includes(action);
      const operationId = i.id || shortId();
      if (external) await this.store.update('moderation_operations.json', data => {
        if (data[operationId]) throw new UserError('This request was already attempted. Check the member and moderation history before trying a new request.');
        data[operationId] = { guild_id: i.guildId, target_id: targetId, action, moderator_id: i.user.id, reason, timestamp: nowISO(), status: 'pending' };
      });
      if (action === 'warn') {
        const warning = { id: shortId(), reason, moderator_id: i.user.id, moderator_name: i.user.username, created_at: nowISO(), active: true, request_id: i.id };
        await this.store.update('moderation_warnings.json', data => {
          const rows = (data[i.guildId] ??= {})[targetId] ??= [];
          const existing = i.id && rows.find(row => row.request_id === i.id);
          if (existing) { warning.id = existing.id; return; }
          rows.push(warning);
        });
        details = `Warning ID: ${warning.id}`;
        await target.send({ content: `You received a warning in **${i.guild.name}**.\n${reason}\nWarning ID: ${warning.id}`, allowedMentions: { parse: [] } }).catch(() => {});
      } else if (action === 'unwarn') {
        await this.store.update('moderation_warnings.json', data => {
          const rows = data[i.guildId]?.[targetId] ?? [];
          const key = text(params.warning_id || 'LATEST', 20).toUpperCase();
          const active = rows.filter(row => row.active !== false);
          const row = ['LATEST', 'LAST'].includes(key) ? active.at(-1) : active.find(row => String(row.id).toUpperCase() === key);
          if (!row) throw new UserError('No active warning matched that ID.');
          Object.assign(row, { active: false, removed_at: nowISO(), removed_by: i.user.id });
          details = `Warning ID: ${row.id}`;
        });
      } else if (action === 'timeout') {
        if (target.permissions.has(P.Administrator)) throw new UserError('Discord does not allow timeouts for administrators.');
        await target.timeout(parseDuration(params.duration || '10m'), reason);
      } else if (action === 'untimeout') await target.timeout(null, reason);
      else if (action === 'kick') await target.kick(reason);
      else if (action === 'ban') await target.ban({ reason, deleteMessageSeconds: 0 });
      else if (action === 'nickname') await target.setNickname(text(params.nickname, 32) || null, reason);
      else if (action === 'role_add' || action === 'role_remove') {
        const role = await i.guild.roles.fetch(snowflake(params.role_id, 'role ID'));
        if (!role) throw new UserError('Role not found.');
        checkRoleHierarchy(actor, target, role, bot);
        await target.roles[action === 'role_add' ? 'add' : 'remove'](role, reason);
      } else throw new UserError('Unknown moderation action.');
      try {
        await this.history(i, targetId, actions[action][0], reason, details);
        if (external) await this.store.update('moderation_operations.json', data => { data[operationId].status = 'completed'; });
      } catch (error) {
        console.warn(JSON.stringify({ event: 'moderation_record_failed', code: error.code, operation: operationId }));
        return { content: `${actions[action][0]} was performed for <@${targetId}>, but its final log could not be saved. Do not repeat the action; contact the bot owner.` };
      }
      return { content: `${actions[action][0]} completed for <@${targetId}>.${details ? ` ${details}` : ''}` };
    });
  }
  async panel(i, targetId) {
    const [actor, member] = await Promise.all([i.guild.members.fetch({ user: i.user.id, force: true }), i.guild.members.fetch({ user: targetId, force: true })]);
    const available = [];
    for (const [action, [label, permission]] of Object.entries(actions)) {
      if (await canModerate(actor, permission, this.store)) available.push(new ButtonBuilder().setCustomId(`mod:${i.user.id}:${targetId}:${action}`).setLabel(label).setStyle(['kick', 'ban'].includes(action) ? ButtonStyle.Danger : ButtonStyle.Secondary));
    }
    if (!available.length) throw new UserError('You do not have access to the moderation panel.');
    available.push(new ButtonBuilder().setCustomId(`mod:${i.user.id}:${targetId}:refresh`).setLabel('Refresh').setStyle(ButtonStyle.Secondary));
    const components = [];
    for (let index = 0; index < available.length; index += 5) components.push(new ActionRowBuilder().addComponents(available.slice(index, index + 5)));
    const warnings = (await this.warnings(i.guildId, targetId)).filter(row => row.active !== false);
    return { embeds: [embed('Member moderation', `<@${targetId}> · \`${targetId}\``).setThumbnail(member.displayAvatarURL()).addFields(
      { name: 'Member', value: text(member.displayName, 1024), inline: true }, { name: 'Active warnings', value: String(warnings.length), inline: true },
      { name: 'Timeout', value: member.communicationDisabledUntilTimestamp > Date.now() ? `<t:${Math.floor(member.communicationDisabledUntilTimestamp / 1000)}:R>` : 'None', inline: true })], components };
  }
  async view(i, action, id) {
    if (action === 'warnings') {
      const rows = (await this.warnings(i.guildId, id)).filter(row => row.active !== false).slice(-15);
      return { embeds: [embed('Active warnings', rows.map(row => `\`${row.id}\` — ${text(row.reason, 180)}`).join('\n') || 'No active warnings.')] };
    }
    if (action === 'history') {
      const rows = (await this.store.read('moderation_history.json'))[i.guildId]?.[id] ?? [];
      return { embeds: [embed('Moderation history', rows.slice(-15).reverse().map(row => `**${text(row.action, 60)}** — ${text(row.reason, 140)}`).join('\n') || 'No history.')] };
    }
    const rows = Object.entries((await this.store.read('reports.json'))[i.guildId] ?? {}).filter(([, row]) => String(row.target_id) === id).slice(-15).reverse();
    return { embeds: [embed('Member reports', rows.map(([key, row]) => `\`${key}\` **${row.status}** — ${text(row.reason, 160)}`).join('\n') || 'No reports.')] };
  }
  async component(i) {
    const [prefix, owner, target, action] = i.customId.split(':');
    if (!i.guild || owner !== i.user.id) throw new UserError('Only the moderator who opened this panel can use it.');
    const actor = await i.guild.members.fetch({ user: i.user.id, force: true });
    if (action === 'refresh') {
      await i.deferUpdate(); return i.editReply(await this.panel(i, target));
    }
    if (!actions[action] || !await canModerate(actor, actions[action][1], this.store)) throw new UserError('Your access to this moderation action has been removed.');
    if (['warnings', 'history', 'reports'].includes(action)) return privateReply(i, await this.view(i, action, target));
    if (prefix === 'mod' && action !== 'untimeout') {
      const modal = new ModalBuilder().setCustomId(`modform:${owner}:${target}:${action}`).setTitle(actions[action][0]);
      const fields = [['reason', 'Reason', false]];
      if (action === 'unwarn') fields.unshift(['warning_id', 'Warning ID, or LATEST', true]);
      if (action === 'timeout') fields.unshift(['duration', 'Duration (10m, 2h, 1d)', true]);
      if (action === 'nickname') fields.unshift(['nickname', 'Nickname (blank to reset)', false]);
      if (action.startsWith('role_')) fields.unshift(['role_id', 'Role ID', true]);
      if (['kick','ban'].includes(action)) fields.unshift(['confirmation', `Type ${action.toUpperCase()} to confirm`, true]);
      modal.addComponents(fields.map(([name, label, required]) => new ActionRowBuilder().addComponents(new TextInputBuilder().setCustomId(name).setLabel(label).setStyle(TextInputStyle.Short).setRequired(required).setMaxLength(name === 'reason' ? 500 : 80))));
      return i.showModal(modal);
    }
    const values = {};
    if (prefix === 'modform') for (const [key, field] of i.fields.fields) values[key] = field.value;
    if (['kick','ban'].includes(action) && values.confirmation !== action.toUpperCase()) throw new UserError('Confirmation did not match; nothing was changed.');
    await i.deferReply({ flags: MessageFlags.Ephemeral });
    return privateReply(i, await this.execute(i, action, target, values));
  }
}
export function registerModeration(registry, services) {
  const service = new ModerationService(services);
  registry.register('mod panel', i => service.panel(i, i.options.getUser('member', true).id), { guildOnly: true });
  registry.register('mod warnings', i => service.view(i, 'warnings', i.options.getUser('member', true).id), { guildOnly: true, moderation: 'warn' });
  for (const action of ['warn','unwarn']) registry.register(`mod ${action}`, i => service.execute(i, action, i.options.getUser('member', true).id,
    { reason: i.options.getString('reason'), warning_id: i.options.getString('warning_id') }), { guildOnly: true, moderation: 'warn' });
  registry.register('mod unban', async i => {
    const id = snowflake(i.options.getString('user_id', true), 'user ID');
    const bot = await i.guild.members.fetchMe({ force: true });
    if (!bot.permissions.has(P.BanMembers)) throw new UserError('Rallybit needs Ban Members.');
    const reason = i.options.getString('reason') || 'Unbanned through Rallybit';
    await i.guild.bans.remove(id, reason); await service.history(i, id, 'Unban', reason);
    return { content: `<@${id}> was unbanned.` };
  }, { guildOnly: true, moderation: 'ban' });
  services.components.register('mod', i => service.component(i));
  services.components.register('modform', i => service.component(i));
  return service;
}
