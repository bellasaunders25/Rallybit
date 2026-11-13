import { lookup } from "node:dns/promises";
import { request } from "node:https";
import { createHash } from "node:crypto";
import { UserError } from "./access.js";
import { KeyedQueue } from "./helpers.js";
import { ActivityType } from "discord.js";

const rotations = new WeakMap();
export async function applyConfiguredPresence(
  store,
  client,
  { advance = false } = {},
) {
  const cfg = (await store.read("bot_settings.json")).global ?? {};
  const rows =
    Array.isArray(cfg.presences) && cfg.presences.length
      ? cfg.presences
      : [{ text: "Rallybit | {servers} servers", type: "watching" }];
  const index = (rotations.get(client) ?? 0) % rows.length,
    row = rows[index] ?? {},
    status = ["online", "idle", "dnd", "offline"].includes(cfg.presence_status)
      ? cfg.presence_status
      : "online";
  const name = String(row.text || "Rallybit")
    .replaceAll("{servers}", client.guilds.cache.size.toLocaleString("en-GB"))
    .replaceAll(
      "{members}",
      [...client.guilds.cache.values()]
        .reduce((n, g) => n + (g.memberCount || 0), 0)
        .toLocaleString("en-GB"),
    )
    .slice(0, 128);
  client.user.setPresence({
    status: status === "offline" ? "invisible" : status,
    activities: [
      {
        name,
        type:
          {
            watching: ActivityType.Watching,
            playing: ActivityType.Playing,
            listening: ActivityType.Listening,
            competing: ActivityType.Competing,
          }[row.type] ?? ActivityType.Watching,
      },
    ],
  });
  if (advance) rotations.set(client, (index + 1) % rows.length);
}

export function profileInput(input) {
  const name = String(input.name ?? "").trim(),
    avatar = String(input.avatar_url ?? "").trim();
  const status = String(input.status ?? "online").trim();
  if (name && ([...name].length < 2 || [...name].length > 32))
    throw new UserError("Bot names must contain 2 to 32 characters.");
  if (!["online", "idle", "dnd", "offline"].includes(status))
    throw new UserError("Choose online, idle, dnd or offline.");
  if (avatar) avatarURL(avatar);
  return { name, avatar, status };
}
export function avatarURL(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new UserError("Enter a public HTTPS image URL.");
  }
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    (url.port && url.port !== "443") ||
    value.length > 500
  )
    throw new UserError(
      "Enter a public HTTPS image URL without credentials or a custom port.",
    );
  return url;
}
export function publicIPv4(address) {
  const octets = address.split(".").map(Number);
  if (
    octets.length !== 4 ||
    octets.some((n) => !Number.isInteger(n) || n < 0 || n > 255)
  )
    return false;
  const [a, b, c] = octets;
  return !(
    a === 0 ||
    a === 10 ||
    a === 127 ||
    a >= 224 ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && (b === 168 || b === 0 || b === 2)) ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 198 && (b === 18 || b === 19 || (b === 51 && c === 100))) ||
    (a === 203 && b === 0 && c === 113)
  );
}
export async function downloadAvatar(value, redirects = 0) {
  const url = avatarURL(value),
    addresses = await lookup(url.hostname, { all: true, family: 4 });
  if (!addresses.length || addresses.some((row) => !publicIPv4(row.address)))
    throw new UserError(
      "The avatar host must resolve to a public internet address.",
    );
  const response = await new Promise((resolve, reject) => {
    const req = request(
      url,
      {
        headers: { "User-Agent": "Rallybit/JS" },
        lookup: (_host, options, callback) =>
          options.all
            ? callback(null, [addresses[0]])
            : callback(null, addresses[0].address, 4),
        timeout: 12000,
      },
      resolve,
    );
    req.on("error", reject);
    req.on("timeout", () =>
      req.destroy(new UserError("Avatar download timed out.")),
    );
    req.end();
  });
  if ([301, 302, 303, 307, 308].includes(response.statusCode)) {
    response.resume();
    if (++redirects > 3 || !response.headers.location)
      throw new UserError("Too many avatar redirects.");
    return downloadAvatar(
      new URL(response.headers.location, url).href,
      redirects,
    );
  }
  if (
    response.statusCode !== 200 ||
    !/^image\/(png|jpeg|gif|webp)(?:;|$)/i.test(
      response.headers["content-type"] ?? "",
    )
  ) {
    response.destroy();
    throw new UserError(
      "The avatar URL must return a PNG, JPEG, GIF or WebP image.",
    );
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of response) {
    size += chunk.length;
    if (size > 8 * 1024 * 1024) {
      response.destroy();
      throw new UserError("The avatar must be smaller than 8 MB.");
    }
    chunks.push(chunk);
  }
  const data = Buffer.concat(chunks);
  const png = data
    .subarray(0, 8)
    .equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  const jpeg = data[0] === 255 && data[1] === 216 && data[2] === 255;
  const gif = ["GIF87a", "GIF89a"].includes(data.subarray(0, 6).toString());
  const webp =
    data.subarray(0, 4).toString() === "RIFF" &&
    data.subarray(8, 12).toString() === "WEBP";
  if (!png && !jpeg && !gif && !webp)
    throw new UserError("The avatar file did not contain a supported image.");
  return data;
}
export class ProfileService {
  constructor(store, client, download = downloadAvatar) {
    Object.assign(this, { store, client, download });
    this.queue = new KeyedQueue();
  }
  async update(input) {
    const values = profileInput(input);
    return this.queue.run("profile", async () => {
      if (!this.client.user) throw new UserError("The bot is not connected.");
      const previous = await this.store.read("bot_settings.json");
      const settings = structuredClone(previous),
        global = (settings.global ??= {});
      const oldUsername = this.client.user.username;
      const oldAvatar = this.client.user.avatar;
      const oldPresence = this.client.presence?.status ?? "online";
      let avatar;
      if (values.avatar) avatar = await this.download(values.avatar);
      const hash = avatar
        ? createHash("sha256").update(avatar).digest("hex")
        : "";
      Object.assign(global, {
        profile_name: values.name,
        profile_avatar_url: values.avatar,
        presence_status: values.status,
      });
      // Ensure storage works before making an external change.
      await this.store.write("bot_settings.json", settings);
      let identityChanged = false;
      try {
        const changes = {};
        if (values.name && oldUsername !== values.name)
          changes.username = values.name;
        if (avatar && previous.global?.profile_avatar_hash !== hash)
          changes.avatar = avatar;
        if (Object.keys(changes).length) {
          await this.client.user.edit(changes);
          identityChanged = true;
        }
        this.client.user.setPresence({
          status: values.status === "offline" ? "invisible" : values.status,
        });
        if (hash)
          await this.store.update("bot_settings.json", (data) => {
            data.global.profile_avatar_hash = hash;
          });
        return { identity_updated: identityChanged, presence_updated: true };
      } catch (error) {
        await this.store.write("bot_settings.json", previous);
        let rollback = true;
        try {
          if (identityChanged) {
            const changes = { username: oldUsername };
            if (oldAvatar)
              changes.avatar = await this.download(
                `https://cdn.discordapp.com/avatars/${this.client.user.id}/${oldAvatar}.png?size=1024`,
              );
            else changes.avatar = null;
            await this.client.user.edit(changes);
          }
          this.client.user.setPresence({ status: oldPresence });
        } catch {
          rollback = false;
        }
        throw new UserError(
          `Discord rejected the profile update. Saved settings were restored.${rollback ? "" : " The live profile could not be fully restored; contact the bot owner."}`,
        );
      }
    });
  }
}
