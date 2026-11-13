import { Client, Events, GatewayIntentBits } from 'discord.js';
import { createApp } from './app.js';
import { createDispatcher } from './runtime.js';
import { LeaseStore } from './lease.js';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';

const app = createApp({ dataDirectory: process.env.RALLYBIT_DATA_DIR ?? './data',
  dashboardUrl: process.env.DASHBOARD_URL || 'https://rallybit.pages.dev/dashboard/' });
// Never replace the live bot with a runtime that drops existing commands.
app.registry.assertComplete();
if (!process.env.DISCORD_BOT_TOKEN) throw new Error('DISCORD_BOT_TOKEN is required');
await mkdir(app.store.directory, { recursive: true });
const leases = new LeaseStore(join(app.store.directory, 'runtime.sqlite'));
const lease = leases.acquire('discord-runtime', 30000);
if (!lease) { leases.close(); throw new Error('Another Rallybit JavaScript runtime owns this data directory'); }
const client = new Client({ intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMembers] });
const dispatcher = createDispatcher(app.registry, app.store);
client.on(Events.InteractionCreate, interaction => {
  if (stopped || !leases.held(lease)) { client.destroy(); return; }
  return dispatcher(interaction);
});
client.on(Events.Error, error => console.error(error.name, error.code));
let stopped = false;
const shutdown = () => {
  if (stopped) return;
  stopped = true; clearInterval(heartbeat); clearInterval(maintenance); client.destroy();
  try { leases.release(lease); } finally { leases.close(); }
};
const heartbeat = setInterval(() => {
  try { if (!leases.renew(lease)) shutdown(); }
  catch (error) { console.error(JSON.stringify({ event: 'runtime_lease_failed', code: error.code })); shutdown(); }
}, 10000);
heartbeat.unref();
let maintaining = false;
const maintenance = setInterval(async () => {
  if (stopped || maintaining || !client.isReady()) return;
  maintaining = true;
  try {
    await app.roles.tick(client, () => { if (stopped || !leases.held(lease)) throw new Error('Runtime ownership lost'); });
  } catch (error) { console.warn(JSON.stringify({ event: 'maintenance_failed', code: error.code })); }
  finally { maintaining = false; }
}, 30000);
maintenance.unref();
for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, shutdown);
try { await client.login(process.env.DISCORD_BOT_TOKEN); }
catch (error) { shutdown(); throw error; }
