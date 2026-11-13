import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { PermissionsBitField, PermissionFlagsBits as P } from 'discord.js';
import { RoleService, checkRoleHierarchy, countdownNickname } from '../src/modules/roles.js';
import { JsonStore } from '../src/store.js';

const positioned = (id, position) => ({ id, position, comparePositionTo(other) { return this.position - other.position; } });
async function fixture(run) {
  const folder = await mkdtemp(join(tmpdir(), 'rallybit-roles-'));
  try {
    let instant = Date.parse('2026-11-13T09:00:00Z');
    const store = new JsonStore(folder);
    const guild = { id: 'guild', ownerId: 'owner' };
    const role = positioned('role', 10);
    const member = { id: 'member', guild, user: { bot: false }, displayName: 'Member', nickname: null,
      roles: { highest: positioned('member-role', 5), cache: new Map(),
        add: async role => { member.roles.cache.set(role.id, role); }, remove: async role => { member.roles.cache.delete(role.id); } },
      setNickname: async value => { member.nickname = value; } };
    const actor = { id: 'actor', guild, permissions: new PermissionsBitField([P.ManageRoles, P.ManageNicknames]), roles: { highest: positioned('staff-role', 20) } };
    const bot = { id: 'bot', guild, permissions: new PermissionsBitField([P.ManageRoles, P.ManageNicknames]), roles: { highest: positioned('bot-role', 30) } };
    guild.members = { fetch: async ({ user }) => user === actor.id ? actor : member, fetchMe: async () => bot };
    guild.roles = { fetch: async () => role };
    const service = new RoleService({ store, now: () => instant, audit: async () => {} });
    const interaction = (values = {}) => ({ guildId: guild.id, guild, user: actor, options: {
      getUser: () => member, getRole: () => role, getInteger: name => values[name] ?? 1,
      getString: name => values[name] ?? (name === 'unit' ? 'days' : null),
    } });
    const client = { guilds: { cache: new Map([[guild.id, guild]]) } };
    await run({ store, service, member, role, actor, bot, guild, client, interaction, advance: duration => { instant += duration; } });
  } finally { await rm(folder, { recursive: true }); }
}

test('member and role hierarchy blocks equal/higher roles even for an administrator', () => fixture(async ({ actor, member, role, bot }) => {
  actor.permissions = new PermissionsBitField(P.Administrator);
  role.position = 20;
  assert.throws(() => checkRoleHierarchy(actor, member, role, bot), /below your highest/);
  role.position = 10; member.roles.highest.position = 20;
  assert.throws(() => checkRoleHierarchy(actor, member, role, bot), /equal or higher/);
  actor.id = member.guild.ownerId; member.roles.highest.position = 5;
  assert.doesNotThrow(() => checkRoleHierarchy(actor, member, role, bot));
  role.managed = true;
  assert.throws(() => checkRoleHierarchy(actor, member, role, bot), /managed/);
}));

test('temporary role expiry survives restart and restores original nickname', () => fixture(async ({ store, service, member, role, client, interaction, advance }) => {
  member.nickname = 'Original name';
  await service.execute(interaction({ nickname: 'Duty name' }), 'temporary');
  assert.ok(member.roles.cache.has(role.id)); assert.match(member.nickname, /^Duty name \| 1d$/);
  const row = (await store.read('temporary_roles.json')).records[0];
  advance(86400001);
  const restarted = new RoleService({ store: new JsonStore(store.directory), now: () => Date.parse(row.expires_at) + 1, audit: async () => {} });
  await restarted.tick(client);
  assert.equal(member.roles.cache.has(role.id), false); assert.equal(member.nickname, 'Original name');
  assert.equal((await store.read('temporary_roles.json')).records.length, 0);
}));

test('converting a timed role to permanent cancels expiry and countdown', () => fixture(async ({ store, service, member, role, client, interaction, advance }) => {
  await service.execute(interaction(), 'temporary');
  await service.execute(interaction(), 'permanent');
  assert.equal(member.nickname, null);
  assert.equal((await store.read('temporary_roles.json')).records.length, 0);
  advance(2 * 86400000); await service.tick(client);
  assert.equal(member.roles.cache.has(role.id), true);
}));

test('failed nickname restoration remains durable for retry', () => fixture(async ({ store, service, member, role, client, interaction, advance }) => {
  await service.execute(interaction(), 'temporary');
  member.setNickname = async () => { throw Object.assign(new Error('permissions removed'), { code: 50013 }); };
  advance(86400001); await service.tick(client);
  assert.equal(member.roles.cache.has(role.id), false);
  assert.ok((await store.read('temporary_roles.json')).nickname_restorations['guild:member']);
  member.setNickname = async value => { member.nickname = value; };
  await service.tick(client); assert.equal(member.nickname, null);
  assert.equal(Object.keys((await store.read('temporary_roles.json')).nickname_restorations).length, 0);
}));

test('full disk prevents any role assignment', () => fixture(async ({ service, member, interaction }) => {
  service.store.update = async () => { throw Object.assign(new Error('Disk full'), { code: 'ENOSPC' }); };
  await assert.rejects(service.execute(interaction(), 'temporary'), /Disk full/);
  assert.equal(member.roles.cache.size, 0);
}));

test('countdown nicknames respect Unicode length and fixed month/year durations', () => {
  const now = Date.parse('2026-11-13T09:00:00Z');
  const value = countdownNickname('😀'.repeat(40), new Date(now + 30000).toISOString(), now);
  assert.equal([...value].length, 32); assert.ok(value.endsWith(' | 30s'));
});
