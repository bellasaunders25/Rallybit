import { readFile } from "node:fs/promises";

export const catalogue = JSON.parse(
  await readFile(
    new URL("../../docs/command-catalog.json", import.meta.url),
    "utf8",
  ),
);
export const contract = JSON.parse(
  await readFile(
    new URL("../contracts/commands.json", import.meta.url),
    "utf8",
  ),
);

export class CommandRegistry {
  #handlers = new Map();
  #policies = new Map();
  register(path, handler, policy) {
    if (!catalogue.commands.some((command) => command.path === path))
      throw new Error(`Unknown command: ${path}`);
    if (this.#handlers.has(path))
      throw new Error(`Duplicate command handler: ${path}`);
    if (typeof handler !== "function" || !policy || policy.guildOnly !== true)
      throw new Error(`Explicit access policy required: ${path}`);
    this.#handlers.set(path, handler);
    this.#policies.set(path, Object.freeze(structuredClone(policy)));
  }
  handler(path) {
    return this.#handlers.get(path);
  }
  policy(path) {
    return this.#policies.get(path);
  }
  coverage() {
    const missing = catalogue.commands
      .filter((command) => !this.#handlers.has(command.path))
      .map((command) => command.path);
    return {
      total: catalogue.commands.length,
      implemented: this.#handlers.size,
      missing,
    };
  }
  assertComplete() {
    const report = this.coverage();
    if (report.missing.length)
      throw new Error(
        `Migration incomplete: ${report.implemented}/${report.total} handlers; production startup blocked.`,
      );
    if (
      JSON.stringify([...contract.paths].sort()) !==
      JSON.stringify(catalogue.commands.map((row) => row.path).sort())
    ) {
      throw new Error(
        "Discord registration contract differs from command inventory",
      );
    }
  }
  registration() {
    this.assertComplete();
    return structuredClone(contract.registration);
  }
}

export function commandPath(interaction) {
  if (!interaction.isChatInputCommand()) return interaction.commandName;
  return [
    interaction.commandName,
    interaction.options.getSubcommandGroup(false),
    interaction.options.getSubcommand(false),
  ]
    .filter(Boolean)
    .join(" ");
}
