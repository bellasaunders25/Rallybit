export function registerUtility(registry, { dashboardUrl }) {
  registry.register('dashboard', async () => ({ content: dashboardUrl }));
  registry.register('help', async () => ({ content: `${new URL('/docs/commands.html', dashboardUrl)} — syntax, options, permissions and examples for every command.` }));
  registry.register('support', async () => ({ content: `${new URL('/docs/faq.html', dashboardUrl)}` }));
}
