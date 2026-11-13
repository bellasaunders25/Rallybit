import { CommandRegistry } from './registry.js';
import { JsonStore } from './store.js';
import { registerSecurity } from './modules/security.js';
import { registerUtility } from './modules/utility.js';
import { registerWorkforce } from './modules/workforce.js';
import { createAudit } from './audit.js';
import { registerRoles } from './modules/roles.js';

export function createApp({ dataDirectory, dashboardUrl = 'https://rallybit.pages.dev/dashboard/' }) {
  const store = new JsonStore(dataDirectory);
  const registry = new CommandRegistry();
  registerSecurity(registry, { store });
  registerUtility(registry, { dashboardUrl });
  const services = { store, audit: createAudit(store) };
  registerWorkforce(registry, services);
  const roles = registerRoles(registry, services);
  return { store, registry, roles };
}
