import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JsonStore } from '../src/store.js';
import { createApp } from '../src/app.js';
import { createDispatcher } from '../src/runtime.js';

test('concurrent reservations produce exactly one winner and survive restart', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'rallybit-js-'));
  try {
    const store = new JsonStore(directory);
    const winners = await Promise.all(Array.from({ length: 30 }, () => store.reserve('dispatch.json', 'guild', 60000)));
    assert.equal(winners.filter(Boolean).length, 1);
    assert.equal(await new JsonStore(directory).reserve('dispatch.json', 'guild', 60000), false);
  } finally { await rm(directory, { recursive: true }); }
});

test('corrupt data is never replaced by defaults', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'rallybit-js-'));
  try {
    await writeFile(join(directory, 'settings.json'), '{broken');
    await assert.rejects(new JsonStore(directory).update('settings.json', data => { data.enabled = false; }), SyntaxError);
  } finally { await rm(directory, { recursive: true }); }
});

test('Python numeric Discord IDs survive JavaScript reads and writes exactly', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'rallybit-js-'));
  try {
    await writeFile(join(directory, 'settings.json'), '{"role_id":1515880613893374106}');
    const store = new JsonStore(directory);
    assert.equal((await store.read('settings.json')).role_id, '1515880613893374106');
    await store.update('settings.json', data => { data.enabled = false; });
    assert.equal((await store.read('settings.json')).role_id, '1515880613893374106');
  } finally { await rm(directory, { recursive: true }); }
});

test('partial command coverage blocks production startup', () => {
  const { registry } = createApp({ dataDirectory: './unused' });
  assert.equal(registry.coverage().total, 202);
  assert.throws(() => registry.assertComplete(), /Migration incomplete/);
});

test('notice blocks execution before deferring with a private embed', async () => {
  let called = false, payload;
  const dispatcher = createDispatcher({ handler: () => { called = true; } }, { read: async () => ({ active: true }) });
  await dispatcher({ isChatInputCommand: () => true, reply: async value => { payload = value; } });
  assert.equal(called, false);
  assert.equal(payload.flags, 64);
  assert.equal(payload.embeds[0].data.title, 'Rallybit maintenance');
});
