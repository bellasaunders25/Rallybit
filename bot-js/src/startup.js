import { Client, Events } from "discord.js";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { once } from "node:events";
import { createApp } from "./app.js";
import { createDispatcher } from "./runtime.js";
import { LeaseStore } from "./lease.js";
import { createApi, syncGuildCache } from "./api.js";
import { createDashboardActions } from "./dashboard-actions.js";
import { ProfileService, applyConfiguredPresence } from "./profile.js";
import {
  bindGateway,
  GATEWAY_OPTIONS,
  maintenance,
  statusSample,
} from "./gateway.js";
import { UserError } from "./access.js";

export function validateRuntimeConfig(env) {
  if (env.RALLYBIT_JS_CUTOVER_APPROVED !== "verified")
    throw new Error(
      "The JavaScript cutover must pass the runtime, dashboard and recovery checklist before it can connect.",
    );
  if (!env.DISCORD_BOT_TOKEN) throw new Error("DISCORD_BOT_TOKEN is required");
  if (!env.BOT_API_SECRET || env.BOT_API_SECRET.length < 16)
    throw new Error("BOT_API_SECRET must contain at least 16 characters");
  const shardCount = Number(env.SHARD_COUNT || 1),
    port = Number(env.API_PORT || 8080);
  if (!Number.isInteger(shardCount) || shardCount < 1 || shardCount > 1000)
    throw new Error("Invalid SHARD_COUNT");
  if (!Number.isInteger(port) || port < 0 || port > 65535)
    throw new Error("Invalid API_PORT");
  return {
    shardCount,
    port,
    host: env.API_HOST || "127.0.0.1",
    directory: env.RALLYBIT_DATA_DIR || "./data",
  };
}
export async function startRuntime({
  env = process.env,
  client: providedClient,
  onRestart = async () => {
    throw new UserError(
      "Runtime restart is not connected to a process supervisor.",
    );
  },
} = {}) {
  const config = validateRuntimeConfig(env),
    app = createApp({
      dataDirectory: config.directory,
      dashboardUrl:
        env.DASHBOARD_URL || "https://rallybit.pages.dev/dashboard/",
    });
  app.registry.assertComplete();
  const actions = createDashboardActions(app);
  await mkdir(config.directory, { recursive: true });
  const leases = new LeaseStore(join(config.directory, "runtime.sqlite")),
    lease = leases.acquire("discord-runtime", 30000);
  if (!lease) {
    leases.close();
    throw new Error(
      "Another Rallybit JavaScript runtime owns this data directory",
    );
  }
  const client =
    providedClient ??
    new Client({
      ...GATEWAY_OPTIONS,
      shards: "auto",
      shardCount: config.shardCount,
    });
  let stopped = false,
    ready = false,
    registered = false,
    initializing = false,
    stopPromise,
    api,
    started = false;
  let resolveReady, rejectReady;
  const initialReady = new Promise((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = reject;
  });
  initialReady.catch(() => {});
  const timers = [],
    tasks = new Set();
  const track = (promise) => {
    tasks.add(promise);
    promise.finally(() => tasks.delete(promise)).catch(() => {});
    return promise;
  };
  const assertOwner = () => {
    if (stopped || !leases.held(lease))
      throw new Error("Runtime ownership lost");
  };
  app.assertOwner = assertOwner;
  app.campaigns.attachScheduler(leases);
  const stop = () =>
    (stopPromise ??= (async () => {
      stopped = true;
      ready = false;
      for (const timer of timers) clearInterval(timer);
      app.prettfy.stop();
      app.cvms.close();
      if (api) {
        api.close();
        api.closeIdleConnections?.();
      }
      await client.destroy();
      const waiting = Promise.allSettled([...tasks]);
      let deadline;
      const finished = await Promise.race([
        waiting.then(() => true),
        new Promise((resolve) => {
          deadline = setTimeout(() => resolve(false), 15000);
        }),
      ]);
      clearTimeout(deadline);
      if (finished) leases.release(lease);
      leases.close();
      if (!finished)
        throw new Error(
          "Runtime stopped with pending work; lease retained until expiry",
        );
    })());
  try {
    await app.operations.backup.recover();
    await app.prettfy.cleanup();
    const profile = new ProfileService(app.store, client);
    api = createApi({
      store: app.store,
      client,
      secret: env.BOT_API_SECRET,
      topggSecret: env.TOPGG_WEBHOOK_TOKEN || "",
      actions,
      profile,
      isReady: () => ready && client.isReady() && !stopped,
      restartShard: async (id) => {
        if (id !== 0 || config.shardCount !== 1)
          throw new UserError(
            "This deployment supports restarting its single shard (0).",
          );
        assertOwner();
        const timer = setTimeout(() => {
          onRestart().catch((error) =>
            console.error(
              JSON.stringify({
                event: "restart_failed",
                code: error.code,
                name: error.name,
              }),
            ),
          );
        }, 500);
        timer.unref();
      },
    });
    api.requestTimeout = 30000;
    api.headersTimeout = 15000;
    api.listen(config.port, config.host);
    await once(api, "listening");
    const dispatcher = createDispatcher(app.registry, app.store, {
      components: app.components,
    });
    client.on(Events.InteractionCreate, (i) =>
      track(
        (async () => {
          assertOwner();
          if (!ready && !(await app.store.read("notice.json")).active) {
            await i.reply({
              content: "Rallybit is reconnecting. Please try again shortly.",
              flags: 64,
              allowedMentions: { parse: [] },
            });
            return;
          }
          await dispatcher(i);
        })().catch((error) =>
          console.warn(
            JSON.stringify({
              event: "interaction_dispatch_failed",
              code: error.code,
              name: error.name,
            }),
          ),
        ),
      ),
    );
    client.on(Events.Error, (error) =>
      console.error(
        JSON.stringify({
          event: "discord_error",
          name: error.name,
          code: error.code,
        }),
      ),
    );
    bindGateway(app, client, {
      assertOwner,
      track,
      onReady: async () => {
        if (initializing || stopped) return;
        initializing = true;
        ready = false;
        try {
          assertOwner();
          if (!registered) {
            await client.application.commands.set(app.registry.registration());
            registered = true;
          }
          const saved =
            (await app.store.read("bot_settings.json")).global ?? {};
          const identity = {
            name: env.BOT_PROFILE_NAME || saved.profile_name || "",
            avatar_url:
              env.BOT_PROFILE_AVATAR_URL || saved.profile_avatar_url || "",
            status:
              saved.presence_status || env.BOT_PRESENCE_STATUS || "online",
          };
          try {
            await profile.update(identity);
          } catch (error) {
            console.warn(
              JSON.stringify({
                event: "startup_profile_rejected",
                name: error.name,
                code: error.code,
              }),
            );
          }
          await applyConfiguredPresence(app.store, client);
          for (const guild of client.guilds.cache.values())
            await app.welcome.refresh(guild);
          await syncGuildCache(client, app.store);
          await app.cvms.recover(client);
          assertOwner();
          ready = true;
          await statusSample(app, client);
          console.log(
            JSON.stringify({
              event: "runtime_ready",
              runtime: "discord.js",
              commands: app.registry.coverage().implemented,
              servers: client.guilds.cache.size,
            }),
          );
          resolveReady();
        } catch (error) {
          ready = false;
          rejectReady(error);
          if (started) {
            const timer = setTimeout(() => onRestart().catch(() => {}), 500);
            timer.unref();
          }
          throw error;
        } finally {
          initializing = false;
        }
      },
    });
    const periodic = (milliseconds, name, action) => {
      let running = false;
      const timer = setInterval(() => {
        if (stopped || running || !ready || !client.isReady()) return;
        running = true;
        track(
          (async () => {
            try {
              assertOwner();
              await action();
            } catch (error) {
              console.warn(
                JSON.stringify({
                  event: "periodic_failed",
                  service: name,
                  code: error.code,
                  name: error.name,
                }),
              );
            } finally {
              running = false;
            }
          })(),
        );
      }, milliseconds);
      timers.push(timer);
      timer.unref();
    };
    const heartbeat = setInterval(() => {
      try {
        if (!leases.renew(lease)) stop().catch(() => {});
      } catch (error) {
        console.error(
          JSON.stringify({ event: "runtime_lease_failed", code: error.code }),
        );
        stop().catch(() => {});
      }
    }, 10000);
    timers.push(heartbeat);
    heartbeat.unref();
    periodic(5000, "fast", () => maintenance(app, client, assertOwner, "fast"));
    periodic(30000, "slow", () => maintenance(app, client, assertOwner));
    periodic(1800000, "status", () => statusSample(app, client));
    periodic(60000, "presence", () =>
      applyConfiguredPresence(app.store, client, { advance: true }),
    );
    await client.login(env.DISCORD_BOT_TOKEN);
    let readyTimeout;
    try {
      await Promise.race([
        initialReady,
        new Promise((_, reject) => {
          readyTimeout = setTimeout(
            () => reject(new Error("Discord initialization timed out")),
            120000,
          );
        }),
      ]);
    } finally {
      clearTimeout(readyTimeout);
    }
    started = true;
    return { app, client, api, stop, isReady: () => ready && client.isReady() };
  } catch (error) {
    await stop().catch(() => {});
    throw error;
  }
}
