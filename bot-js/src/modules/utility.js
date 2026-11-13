export function registerUtility(registry, { dashboardUrl }) {
  const publicPolicy = { guildOnly: true };
  registry.register(
    "dashboard",
    async () => ({ content: dashboardUrl }),
    publicPolicy,
  );
  registry.register(
    "help",
    async () => ({
      content: `${new URL("/docs/commands.html", dashboardUrl)} — syntax, options, permissions and examples for every command.`,
    }),
    publicPolicy,
  );
  registry.register(
    "support",
    async () => ({ content: `${new URL("/docs/faq.html", dashboardUrl)}` }),
    publicPolicy,
  );
}
