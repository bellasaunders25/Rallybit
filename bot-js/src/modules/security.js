import { PermissionFlagsBits } from "discord.js";
import { UserError } from "../access.js";

export function registerSecurity(registry, { store }) {
  const policy = { guildOnly: true, permissions: ["ManageGuild"] };
  const requireManager = (interaction) => {
    if (
      !interaction.inGuild() ||
      !interaction.memberPermissions?.has(PermissionFlagsBits.ManageGuild)
    ) {
      throw new UserError("You need Manage Server to configure security.");
    }
  };
  registry.register(
    "security-agegate disable",
    async (interaction) => {
      requireManager(interaction);
      await store.update("security_settings.json", (data) => {
        const settings = (data[interaction.guildId] ??= {});
        const modules = (settings.modules ??= {});
        (modules.age_gate ??= {}).enabled = false;
        (settings.agegate ??= {}).enabled = false;
      });
      return { content: "Account age gate disabled and saved." };
    },
    policy,
  );
  registry.register(
    "security-agegate configure",
    async (interaction) => {
      requireManager(interaction);
      const days = interaction.options.getInteger("minimum_days", true);
      if (days < 1 || days > 3650)
        throw new UserError("Choose an account age between 1 and 3650 days.");
      await store.update("security_settings.json", (data) => {
        const settings = (data[interaction.guildId] ??= {});
        const modules = (settings.modules ??= {});
        modules.age_gate = {
          ...modules.age_gate,
          enabled: true,
          min_days: days,
          dm_member: interaction.options.getBoolean("dm_member") ?? true,
          appeal_url: interaction.options.getString("appeal_url") ?? "",
          dm_message: interaction.options.getString("dm_message") ?? "",
        };
        settings.agegate = {
          ...settings.agegate,
          enabled: true,
          minimum_days: days,
          dm_member: modules.age_gate.dm_member,
        };
      });
      return { content: `Account age gate enabled at ${days} days and saved.` };
    },
    policy,
  );
  registry.register(
    "security-agegate status",
    async (interaction) => {
      requireManager(interaction);
      const data = await store.read("security_settings.json");
      const age = data[interaction.guildId]?.modules?.age_gate;
      return {
        content: `Account age gate: ${age?.enabled ? "Enabled" : "Disabled"}\nMinimum age: ${age?.min_days ?? 7} days`,
      };
    },
    policy,
  );
}
