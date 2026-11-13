import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PermissionFlagsBits as P, PermissionsBitField } from 'discord.js';
import { resolvePlan, enforceAccess, workforceAccess } from '../src/access.js';
import { CommandRegistry, contract, catalogue } from '../src/registry.js';
import { createDispatcher } from '../src/runtime.js';

const now = Date.parse('2026-11-13T09:00:00Z');
const grants = { users: { owner: { plan: 'network', expires_at: '2026-12-01T00:00:00Z' }, admin: { plan: 'network' } },
  servers: { a: { plan: 'pro' }, b: { plan: 'pro', expires_at: '2026-11-13T09:00:00Z' } } };
test('all features are free regardless of past grants, expiration or server ownership', async () => {
  for (const userId of ['staff', 'admin', 'dev']) {
    for (const guildId of ['one', 'two', 'a', 'b']) {
      assert.equal(resolvePlan({ userId, guildId, ownerId: 'different', grants, now }).plan, 'free');
    }
  }
  const member = { permissions: new PermissionsBitField(), roles: { cache: new Map() } };
  const interaction = { user: { id: 'staff' }, guildId: 'g', guild: { ownerId: 'owner', members: { fetch: async () => member } } };
  await enforceAccess(interaction, { guildOnly: true, plan: 'pro' }, { read: async name => { assert.notEqual(name, 'premium_entitlements.json'); return {}; } });
});

test('staff permissions use configured roles and HR fallback without widening Pro permissions', async () => {
  const member = { id: 'staff', guild: { id: 'guild', ownerId: 'owner' }, permissions: new PermissionsBitField(P.ManageMessages), roles: { cache: new Map([['staff-role', {}]]) } };
  const data = { workforce_settings: { guild: { staff_role_ids: ['staff-role'], hr_role_ids: ['hr-role'] } } };
  const store = { read: async name => data[name.replace('.json', '')] ?? {} };
  assert.deepEqual(await workforceAccess(member, store), { hr: false, staff: true });
  member.roles.cache.clear();
  assert.deepEqual(await workforceAccess(member, store), { hr: false, staff: false });
  member.roles.cache.set('hr-role', {});
  assert.deepEqual(await workforceAccess(member, store), { hr: true, staff: true });
});

test('denied access never invokes a handler and never publishes the error', async () => {
  let called = false, deleted = false, privateError;
  const store = { read: async () => ({}) };
  const registry = { handler: () => async () => { called = true; }, policy: () => ({ guildOnly: true, permissions: ['ManageRoles'] }) };
  const dispatcher = createDispatcher(registry, store);
  const member = { permissions: new PermissionsBitField(), roles: { cache: new Map() } };
  const interaction = { isChatInputCommand: () => true, commandName: 'role',
    options: { getSubcommandGroup: () => null, getSubcommand: () => 'give', getBoolean: () => false },
    user: { id: 'staff' }, guildId: 'guild', guild: { ownerId: 'owner', members: { fetch: async ({ force }) => { assert.equal(force, true); return member; } } },
    deferReply: async () => { interaction.deferred = true; }, deleteReply: async () => { deleted = true; },
    followUp: async payload => { privateError = payload; } };
  await dispatcher(interaction);
  assert.equal(called, false); assert.equal(deleted, true); assert.equal(privateError.flags, 64);
  assert.match(privateError.content, /ManageRoles/);
});

test('all existing command paths, option names, types, choices and boundaries are retained', () => {
  const leaves = new Map();
  function walk(command, parents = []) {
    const path = [...parents, command.name];
    const nested = (command.options ?? []).filter(option => option.type === 1 || option.type === 2);
    if (nested.length) nested.forEach(child => walk(child, path));
    else leaves.set(path.join(' '), command.options ?? []);
  }
  contract.registration.forEach(command => walk(command));
  assert.equal(contract.root_count, 77); assert.equal(leaves.size, 202);
  assert.deepEqual([...leaves.keys()].sort(), [...contract.paths].sort());
  const types = { string: 3, integer: 4, boolean: 5, user: 6, channel: 7, role: 8 };
  for (const command of catalogue.commands) {
    const options = leaves.get(command.path); assert.ok(options, command.path);
    assert.deepEqual(options.map(option => option.name), command.parameters.map(option => option.name), command.path);
    for (const parameter of command.parameters) {
      const option = options.find(option => option.name === parameter.name);
      assert.equal(option.type, types[parameter.type], `${command.path} ${parameter.name}`);
      assert.equal(Boolean(option.required), parameter.required);
      assert.deepEqual(option.choices ?? [], parameter.choices ?? []);
      for (const name of ['min_value', 'max_value', 'min_length', 'max_length']) {
        if (parameter[name] != null) assert.equal(option[name], parameter[name]);
      }
    }
  }
  assert.throws(() => new CommandRegistry().registration(), /Migration incomplete/);
  assert.throws(() => new CommandRegistry().register('clockin', async () => {}), /access policy/);
});
