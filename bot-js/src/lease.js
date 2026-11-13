import { DatabaseSync } from "node:sqlite";
import { randomUUID } from "node:crypto";

// A shared SQLite compare-and-set lease prevents two JavaScript workers from
// owning one runtime or scheduled job. Clock is injectable for recovery tests.
export class LeaseStore {
  constructor(path, { now = () => Date.now() } = {}) {
    this.now = now;
    this.db = new DatabaseSync(path);
    this.db.exec(
      "PRAGMA busy_timeout=5000; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;",
    );
    this.db.exec(
      "CREATE TABLE IF NOT EXISTS leases (resource TEXT PRIMARY KEY, owner TEXT NOT NULL, expires INTEGER NOT NULL)",
    );
  }
  acquire(resource, ttlMs, owner = randomUUID()) {
    if (!Number.isSafeInteger(ttlMs) || ttlMs < 1000)
      throw new Error("Invalid lease duration");
    const instant = this.now();
    const row = this.db
      .prepare(
        "INSERT INTO leases(resource,owner,expires) VALUES(?,?,?) ON CONFLICT(resource) DO UPDATE SET owner=excluded.owner,expires=excluded.expires WHERE leases.expires<=? RETURNING owner",
      )
      .get(resource, owner, instant + ttlMs, instant);
    return row ? { resource, owner, ttlMs } : null;
  }
  renew(lease) {
    const instant = this.now();
    return Boolean(
      this.db
        .prepare(
          "UPDATE leases SET expires=? WHERE resource=? AND owner=? AND expires>? RETURNING owner",
        )
        .get(instant + lease.ttlMs, lease.resource, lease.owner, instant),
    );
  }
  held(lease) {
    return Boolean(
      this.db
        .prepare(
          "SELECT owner FROM leases WHERE resource=? AND owner=? AND expires>?",
        )
        .get(lease.resource, lease.owner, this.now()),
    );
  }
  release(lease) {
    this.db
      .prepare("DELETE FROM leases WHERE resource=? AND owner=?")
      .run(lease.resource, lease.owner);
  }
  close() {
    this.db.close();
  }
}
