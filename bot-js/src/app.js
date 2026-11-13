import { CommandRegistry } from './registry.js';
import { JsonStore } from './store.js';
import { registerSecurity } from './modules/security.js';
import { registerUtility } from './modules/utility.js';

export function createApp({ dataDirectory, dashboardUrl = 'https://rallybit.pages.dev/dashboard/' }) {
  const store = new JsonStore(dataDirectory);
  const registry = new CommandRegistry();
  registerSecurity(registry, { store });
  registerUtility(registry, { dashboardUrl });
  return { store, registry };
}
