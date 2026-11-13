import { CommandRegistry } from "./registry.js";
import { JsonStore } from "./store.js";
import { registerSecurity } from "./modules/security.js";
import { registerUtility } from "./modules/utility.js";
import { registerWorkforce } from "./modules/workforce.js";
import { createAudit } from "./audit.js";
import { registerRoles } from "./modules/roles.js";
import { ComponentRouter } from "./components.js";
import { registerModeration } from "./modules/moderation.js";
import { registerReports } from "./modules/reports.js";
import { registerChannels } from "./modules/channels.js";
import { registerLogging } from "./modules/logging.js";
import { registerCommunityUtility } from "./modules/community-utility.js";
import { registerOperations } from "./modules/operations.js";
import { registerTickets } from "./modules/tickets.js";
import { registerMemberTools } from "./modules/member-tools.js";
import { registerWelcome } from "./modules/welcome.js";
import { registerSnipes } from "./modules/snipes.js";
import { registerSecurityEngine } from "./modules/security-engine.js";
import { registerCampaigns } from "./modules/campaigns.js";
import { registerPrettfy } from "./modules/prettfy.js";
import { registerCvms } from "./modules/cvms-commands.js";

export function createApp({
  dataDirectory,
  dashboardUrl = "https://rallybit.pages.dev/dashboard/",
}) {
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
  const tickets = registerTickets(registry, services);
  const members = registerMemberTools(registry, services);
  const welcome = registerWelcome(registry, services);
  const snipes = registerSnipes(registry);
  const security = registerSecurityEngine(registry, services);
  const campaigns = registerCampaigns(registry, services);
  const prettfy = registerPrettfy(registry, services, { dashboardUrl });
  const cvms = registerCvms(registry, { ...services, moderation, reports });
  reports.onVoiceReport = (interaction, report) =>
    cvms.addReport(interaction, report);
  return {
    store,
    registry,
    audit: services.audit,
    roles,
    components,
    moderation,
    reports,
    channels,
    logging,
    utility,
    operations,
    tickets,
    members,
    welcome,
    snipes,
    security,
    campaigns,
    prettfy,
    cvms,
  };
}
