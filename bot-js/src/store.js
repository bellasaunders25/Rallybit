import { mkdir, open, readFile, rename, unlink } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { randomUUID } from 'node:crypto';

// One process owns a data directory. Every read/modify/write transaction is
// serialised per file and completed durably before callers perform side effects.
export class JsonStore {
  #queues = new Map();
  constructor(directory) { this.directory = resolve(directory); }
  path(name) {
    if (!/^[a-z][a-z0-9_]*\.json$/.test(name)) throw new Error('Invalid storage file');
    return resolve(this.directory, name);
  }
  async read(name, fallback = {}) {
    try {
      return JSON.parse(await readFile(this.path(name), 'utf8'), (_key, value, context) =>
        typeof value === 'number' && Number.isInteger(value) && !Number.isSafeInteger(value)
          ? context.source : value);
    }
    catch (error) {
      if (error.code === 'ENOENT') return structuredClone(fallback);
      throw error;
    }
  }
  async update(name, change) {
    const previous = this.#queues.get(name) ?? Promise.resolve();
    const operation = previous.catch(() => {}).then(async () => {
      const path = this.path(name);
      await mkdir(dirname(path), { recursive: true });
      const data = await this.read(name);
      const result = await change(data);
      const temporary = `${path}.${randomUUID()}.tmp`;
      let handle;
      try {
        handle = await open(temporary, 'wx', 0o600);
        await handle.writeFile(JSON.stringify(data, null, 2), 'utf8');
        await handle.sync();
        await handle.close(); handle = undefined;
        await rename(temporary, path);
      } finally {
        await handle?.close();
        await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
      }
      return result;
    });
    this.#queues.set(name, operation);
    try { return await operation; }
    finally { if (this.#queues.get(name) === operation) this.#queues.delete(name); }
  }
  async reserve(name, key, intervalMs, now = Date.now()) {
    return this.update(name, data => {
      if (Number(data[key] ?? 0) > now) return false;
      data[key] = now + intervalMs;
      return true;
    });
  }
}
