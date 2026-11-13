import { Client, Events, GatewayIntentBits } from 'discord.js';
import { createApp } from './app.js';
import { createDispatcher } from './runtime.js';

const app = createApp({ dataDirectory: process.env.RALLYBIT_DATA_DIR ?? './data' });
// Never replace the live bot with a runtime that drops existing commands.
app.registry.assertComplete();
if (!process.env.DISCORD_BOT_TOKEN) throw new Error('DISCORD_BOT_TOKEN is required');
const client = new Client({ intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMembers] });
client.on(Events.InteractionCreate, createDispatcher(app.registry, app.store));
client.on(Events.Error, error => console.error(error.name, error.code));
for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => client.destroy());
await client.login(process.env.DISCORD_BOT_TOKEN);
