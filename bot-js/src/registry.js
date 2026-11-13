import { readFile } from 'node:fs/promises';

export const catalogue = JSON.parse(await readFile(new URL('../../docs/command-catalog.json', import.meta.url), 'utf8'));

export class CommandRegistry {
  #handlers = new Map();
  register(path, handler) {
    if (!catalogue.commands.some(command => command.path === path)) throw new Error(`Unknown command: ${path}`);
    if (this.#handlers.has(path)) throw new Error(`Duplicate command handler: ${path}`);
    this.#handlers.set(path, handler);
  }
  handler(path) { return this.#handlers.get(path); }
  coverage() {
    const missing = catalogue.commands.filter(command => !this.#handlers.has(command.path)).map(command => command.path);
    return { total: catalogue.commands.length, implemented: this.#handlers.size, missing };
  }
  assertComplete() {
    const report = this.coverage();
    if (report.missing.length) throw new Error(`Migration incomplete: ${report.implemented}/${report.total} handlers; production startup blocked.`);
  }
}

export function commandPath(interaction) {
  return [interaction.commandName, interaction.options.getSubcommandGroup(false), interaction.options.getSubcommand(false)]
    .filter(Boolean).join(' ');
}
