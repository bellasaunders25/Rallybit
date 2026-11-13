import { CommandRegistry } from './registry.js';
import { JsonStore } from './store.js';
import { registerSecurity } from './modules/security.js';
import { registerUtility } from './modules/utility.js';
import { registerWorkforce } from './modules/workforce.js';
import { createAudit } from './audit.js';
import { registerRoles } from './modules/roles.js';
import { ComponentRouter } from './components.js';
import { registerModeration } from './modules/moderation.js';
import { registerReports } from './modules/reports.js';
import { registerChannels } from './modules/channels.js';
import { registerLogging } from './modules/logging.js';
import { registerCommunityUtility } from './modules/community-utility.js';
import { registerOperations } from './modules/operations.js';

export function createApp({ dataDirectory, dashboardUrl = 'https://rallybit.pages.dev/dashboard/' }) {
  const store = new JsonStore(dataDirectory);
  const registry = new CommandRegistry();
  registerSecurity(registry, { store });
  registerUtility(registry, { dashboardUrl });
  const components = new ComponentRouter();
  const services = { store, audit: createAudit(store), components };
  registerWorkforce(registry, services);
  const roles = registerRoles(registry, services);
  const moderation = registerModeration(registry, services);
  const reports = registerReports(registry, services);
  const channels = registerChannels(registry, services);
  const logging = registerLogging(registry, services);
  const utility = registerCommunityUtility(registry, services);
  const operations = registerOperations(registry, services);
  return { store, registry, roles, components, moderation, reports, channels, logging, utility, operations };
}
