import { randomUUID } from 'node:crypto';
import { PermissionFlagsBits as P } from 'discord.js';
import { UserError } from '../access.js';

const FILE = 'temporary_roles.json';
const DAY = 86400000;
const UNITS = { days: 1, weeks: 7, months: 30, years: 365 };
const policy = { guildOnly: true, permissions: ['ManageRoles'] };
const token = row => row.operation_id ?? `${row.guild_id}:${row.member_id}:${row.role_id}:${row.expires_at}`;

export function checkRoleHierarchy(actor, member, role, bot) {
  if (role.id === member.guild.id || role.managed) throw new UserError('The @everyone role and managed roles cannot be assigned.');
  if (actor.id !== member.guild.ownerId) {
    if (role.comparePositionTo(actor.roles.highest) >= 0) throw new UserError('That role must be below your highest role.');
    if (member.id !== actor.id && member.roles.highest.comparePositionTo(actor.roles.highest) >= 0) throw new UserError('That member has an equal or higher role than you.');
  }
  if (!bot.permissions.has(P.ManageRoles)) throw new UserError('Rallybit needs Manage Roles.');
  if (role.comparePositionTo(bot.roles.highest) >= 0 || (member.id !== bot.id && member.roles.highest.comparePositionTo(bot.roles.highest) >= 0)) {
    throw new UserError('Move Rallybit above the role and member it needs to manage.');
  }
}

export function countdownNickname(base, expiry, now = Date.now()) {
  const seconds = Math.max(0, Math.ceil((Date.parse(expiry) - now) / 1000));
  const label = seconds >= 86400 ? `${Math.ceil(seconds / 86400)}d` : seconds >= 3600
    ? `${Math.ceil(seconds / 3600)}h` : seconds >= 60 ? `${Math.ceil(seconds / 60)}m` : `${seconds}s`;
  const suffix = ` | ${label}`;
  // Discord's nickname limit is 32 Unicode characters, not 32 UTF-16 units.
  return [...String(base || 'Member').replace(/\s+/g, ' ').trim()].slice(0, 32 - suffix.length).join('') + suffix;
}

export class RoleService {
  #queues = new Map();
  constructor({ store, audit, now = () => Date.now() }) { Object.assign(this, { store, audit, now }); }
  async locked(key, operation) {
    const previous = this.#queues.get(key) ?? Promise.resolve();
    const current = previous.catch(() => {}).then(operation);
    this.#queues.set(key, current);
    try { return await current; }
    finally { if (this.#queues.get(key) === current) this.#queues.delete(key); }
  }
  async rows() { return (await this.store.read(FILE)).records ?? []; }
  async nickname(member, original) {
    const rows = (await this.rows()).filter(row => String(row.guild_id) === member.guild.id && String(row.member_id) === member.id
      && (!row.mode || row.mode === 'temporary') && Date.parse(row.expires_at) > this.now());
    rows.sort((a, b) => Date.parse(a.expires_at) - Date.parse(b.expires_at));
    const desired = rows.length ? countdownNickname(rows[0].base_name || member.displayName, rows[0].expires_at, this.now()) : original;
    if (member.nickname !== desired) await member.setNickname(desired ?? null, 'Rallybit temporary role countdown');
  }
  async processRow(guild, row, assertOwner = () => {}) {
    const member = await guild.members.fetch({ user: String(row.member_id), force: true });
    const role = await guild.roles.fetch(String(row.role_id));
    const expired = Date.parse(row.expires_at) <= this.now();
    const removing = row.mode === 'remove' || ((!row.mode || row.mode === 'temporary') && expired);
    if (role) {
      if (!removing && !member.roles.cache.has(role.id)) {
        const actor = await guild.members.fetch({ user: String(row.assigned_by), force: true });
        const bot = await guild.members.fetchMe({ force: true });
        if (!actor.permissions.has(P.ManageRoles)) throw new UserError('The assigning staff member no longer has Manage Roles.');
        checkRoleHierarchy(actor, member, role, bot);
        assertOwner();
        await member.roles.add(role, row.reason || 'Rallybit role assignment');
      } else if (removing && member.roles.cache.has(role.id)) {
        assertOwner();
        await member.roles.remove(role, row.reason || 'Rallybit temporary role expired');
      }
    }
    const finalising = removing || row.mode === 'permanent' || !role;
    const memberKey = `${guild.id}:${member.id}`;
    if (finalising) await this.store.update(FILE, data => {
      data.records = (data.records ?? []).filter(current => token(current) !== token(row));
      data.nickname_restorations ??= {};
      data.nickname_restorations[memberKey] = { guild_id: guild.id, member_id: member.id,
        original_nick: row.original_nick ?? null, operation_id: token(row) };
    });
    assertOwner();
    // Failure is reported and retried by the next tick, including restoration
    // after expiry. Do not silently discard the saved original nickname.
    await this.nickname(member, row.original_nick);
    if (finalising) await this.store.update(FILE, data => {
      if (data.nickname_restorations?.[memberKey]?.operation_id === token(row)) delete data.nickname_restorations[memberKey];
    });
  }
  async execute(interaction, mode) {
    const uid = interaction.options.getUser('member', true).id;
    const roleId = interaction.options.getRole('role', true).id;
    return this.locked(`${interaction.guildId}:${uid}`, async () => {
      const guild = interaction.guild;
      const [actor, member, bot, role] = await Promise.all([
        guild.members.fetch({ user: interaction.user.id, force: true }), guild.members.fetch({ user: uid, force: true }),
        guild.members.fetchMe({ force: true }), guild.roles.fetch(roleId),
      ]);
      if (!role) throw new UserError('That role no longer exists.');
      checkRoleHierarchy(actor, member, role, bot);
      const rows = await this.rows();
      const related = rows.filter(row => String(row.guild_id) === guild.id && String(row.member_id) === uid);
      const existing = related.find(row => String(row.role_id) === roleId);
      const nickname = interaction.options.getString('nickname');
      let expires = this.now();
      if (mode === 'temporary') {
        const amount = interaction.options.getInteger('amount', true), unit = interaction.options.getString('unit', true);
        if (!Object.hasOwn(UNITS, unit) || !Number.isInteger(amount) || amount < 1 || amount > 365 || (unit === 'years' && amount > 10)) throw new UserError('Choose a valid duration, up to 10 years. Months use 30 days and years use 365 days.');
        if (member.user.bot) throw new UserError('Temporary roles can only be assigned to human members.');
        if (!bot.permissions.has(P.ManageNicknames)) throw new UserError('Rallybit needs Manage Nicknames for the countdown.');
        if (nickname && !actor.permissions.has(P.ManageNicknames)) throw new UserError('You need Manage Nicknames to choose a custom nickname.');
        if (member.roles.cache.has(roleId) && !existing) throw new UserError('That member already has this role permanently. Remove it first to make it temporary.');
        expires += amount * UNITS[unit] * DAY;
      }
      const row = { guild_id: guild.id, member_id: uid, role_id: roleId, mode,
        operation_id: randomUUID(), created_at: new Date(this.now()).toISOString(), expires_at: new Date(expires).toISOString(),
        assigned_by: actor.id, original_nick: related.length ? related[0].original_nick ?? null : member.nickname,
        base_name: nickname || related[0]?.base_name || member.displayName,
        reason: (interaction.options.getString('reason') ?? `Role ${mode} by ${actor.id}`).slice(0, 300) };
      // Durable intent first. Discord changes are idempotent and retried safely.
      await this.store.update(FILE, data => {
        data.records = (data.records ?? []).filter(current => !(String(current.guild_id) === guild.id && String(current.member_id) === uid && String(current.role_id) === roleId));
        data.records.push(row);
      });
      try { await this.processRow(guild, row); }
      catch (error) { throw new UserError(`The role change was saved for retry, but Discord has not completed it. Check Rallybit's permissions and role position. (${error.code || 'pending'})`); }
      try { await this.audit(interaction, 'roles', 'Role updated', `${role.name}: ${mode}.`, uid); }
      catch (error) { console.warn(JSON.stringify({ event: 'role_audit_failed', code: error.code })); }
      return { content: mode === 'temporary' ? `<@&${roleId}> assigned to <@${uid}> until <t:${Math.floor(expires / 1000)}:F>.`
        : mode === 'remove' ? `<@&${roleId}> removed from <@${uid}>.` : `<@&${roleId}> permanently assigned to <@${uid}>.` };
    });
  }
  async tick(client, assertOwner = () => {}) {
    for (const snapshot of await this.rows()) {
      const key = `${snapshot.guild_id}:${snapshot.member_id}`;
      await this.locked(key, async () => {
        const row = (await this.rows()).find(current => token(current) === token(snapshot));
        if (!row) return;
        const guild = client.guilds.cache.get(String(row.guild_id));
        if (!guild) return;
        try { assertOwner(); await this.processRow(guild, row, assertOwner); }
        catch (error) { console.warn(JSON.stringify({ event: 'temporary_role_retry', guild: guild.id, code: error.code })); }
      });
    }
    const restorations = (await this.store.read(FILE)).nickname_restorations ?? {};
    for (const [key, row] of Object.entries(restorations)) await this.locked(key, async () => {
      try {
        const guild = client.guilds.cache.get(String(row.guild_id));
        if (!guild) return;
        const member = await guild.members.fetch({ user: String(row.member_id), force: true });
        assertOwner(); await this.nickname(member, row.original_nick);
        await this.store.update(FILE, data => { delete data.nickname_restorations?.[key]; });
      } catch (error) { console.warn(JSON.stringify({ event: 'nickname_restore_retry', guild: row.guild_id, code: error.code })); }
    });
  }
}

export function registerRoles(registry, services) {
  const roles = new RoleService(services);
  registry.register('role give', interaction => roles.execute(interaction, 'permanent'), policy);
  registry.register('role remove', interaction => roles.execute(interaction, 'remove'), policy);
  registry.register('role temp', interaction => roles.execute(interaction, 'temporary'), policy);
  return roles;
}
