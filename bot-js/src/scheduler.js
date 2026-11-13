export class DurableScheduler {
  #running = new Set();
  constructor({ store, leases, now = () => Date.now() }) {
    Object.assign(this, { store, leases, now });
  }
  async dispatch({
    key,
    intervalMs,
    cooldownKey = key,
    minimumGapMs = 300000,
    send,
  }) {
    if (!Number.isSafeInteger(intervalMs) || intervalMs < 60000)
      throw new Error("Invalid schedule interval");
    if (!Number.isSafeInteger(minimumGapMs) || minimumGapMs < 0)
      throw new Error("Invalid cooldown");
    if (this.#running.has(key)) return { status: "busy" };
    const lease = this.leases.acquire(`schedule:${cooldownKey}`, 30000);
    if (!lease) return { status: "busy" };
    this.#running.add(key);
    try {
      const reserved = await this.store.update(
        "scheduler_dispatch.json",
        (data) => {
          const instant = this.now();
          const jobs = (data.jobs ??= {}),
            cooldowns = (data.cooldowns ??= {});
          if (
            Number(jobs[key]?.next_run ?? 0) > instant ||
            Number(cooldowns[cooldownKey] ?? 0) > instant
          )
            return false;
          jobs[key] = {
            next_run: instant + intervalMs,
            reserved_at: new Date(instant).toISOString(),
          };
          cooldowns[cooldownKey] = instant + minimumGapMs;
          return true;
        },
      );
      if (!reserved) return { status: "not-due" };
      // No catch-up replay: a failed or uncertain send consumes this interval.
      if (!this.leases.renew(lease))
        throw new Error("Schedule ownership lost before sending");
      const result = await send(() => {
        if (!this.leases.held(lease))
          throw new Error("Schedule ownership lost");
      });
      return { status: "sent", result };
    } finally {
      this.#running.delete(key);
      this.leases.release(lease);
    }
  }
}
