import { PermissionFlagsBits as P } from 'discord.js';

export class UserError extends Error {}
export function resolvePlan() { return { plan: 'free', source: 'default' }; }

function hasRole(member, ids) {
  return Array.isArray(ids) && ids.some(id => member.roles.cache.has(String(id)));
}

export async function workforceAccess(member, store) {
  if (member.permissions.has(P.Administrator)) return { staff: true, hr: true };
  const settings = (await store.read('workforce_settings.json'))[member.guild.id] ?? {};
  const hr = settings.hr_role_ids?.length ? hasRole(member, settings.hr_role_ids) : member.permissions.has(P.ManageGuild);
  const staff = hr || (settings.staff_role_ids?.length ? hasRole(member, settings.staff_role_ids) : member.permissions.has(P.ManageMessages));
  return { staff, hr };
}

export async function canModerate(member, action, store) {
  if (member.id === member.guild.ownerId || member.permissions.has(P.Administrator)) return true;
  const permission = { warn: P.ManageMessages, ban: P.BanMembers, kick: P.KickMembers,
    timeout: P.ModerateMembers, nickname: P.ManageNicknames, role: P.ManageRoles }[action];
  if (!permission || !member.permissions.has(permission)) return false;
  const roles = (await store.read('moderation_permissions.json'))[member.guild.id]?.[`${action}_role_ids`] ?? [];
  return !roles.length || hasRole(member, roles);
}

export async function enforceAccess(interaction, policy, store, { ownerId = process.env.OWNER_ID } = {}) {
  if (!policy || policy.guildOnly !== true) throw new Error('Command access policy is not configured');
  if (!interaction.guild) throw new UserError('Use this command in a server.');
  // Refresh both member and server permissions so a removed role takes effect.
  const member = await interaction.guild.members.fetch({ user: interaction.user.id, force: true });
  const admins = await store.read('admins.json', []);
  const ids = Array.isArray(admins) ? admins : (admins.admin_ids ?? admins.admins ?? []);
  const developers = [ownerId, ...ids].filter(Boolean).map(String);
  if (policy.developer && !developers.includes(interaction.user.id)) throw new UserError('This command is restricted to Rallybit developers.');
  if (policy.permissions?.some(name => !Object.hasOwn(P, name) || !member.permissions.has(P[name]))) {
    throw new UserError(`You need ${policy.permissions.join(' and ')} to use this command.`);
  }
  if (policy.workforce) {
    const access = await workforceAccess(member, store);
    if (!access[policy.workforce]) throw new UserError(`You need a configured ${policy.workforce.toUpperCase()} role to use this command.`);
  }
  if (policy.moderation && !await canModerate(member, policy.moderation, store)) throw new UserError('You need the configured moderation role and Discord permission.');
  if (policy.owner && interaction.user.id !== interaction.guild.ownerId && !developers.includes(interaction.user.id)) throw new UserError('Only the server owner can use this command.');
  return member;
}
