import { createHash } from "node:crypto";
import { UserError } from "../access.js";

export const CVMS_DEFAULTS = {
  enabled: false,
  mode: "manual",
  channel_ids: [],
  log_channel_id: null,
  threshold: 75,
  retention_hours: 24,
  report_integration: false,
  rules: [],
};
export const CONSENT_HOURS = 24;
export function cvmsConfig(row = {}) {
  const result = { ...structuredClone(CVMS_DEFAULTS), ...row };
  if (!["manual", "watch", "always"].includes(result.mode))
    throw new UserError("Choose manual, watch or always mode.");
  result.channel_ids = [
    ...new Set((result.channel_ids ?? []).map(String)),
  ].slice(0, 20);
  result.rules = Array.isArray(result.rules) ? result.rules.slice(0, 50) : [];
  result.threshold = Math.max(50, Math.min(99, Number(result.threshold) || 75));
  result.retention_hours = Math.max(
    1,
    Math.min(24, Number(result.retention_hours) || 24),
  );
  return result;
}
export function consentScope(config) {
  return createHash("sha256")
    .update(
      JSON.stringify({
        version: "local-review-only-v1",
        retention_hours: config.retention_hours,
      }),
    )
    .digest("hex")
    .slice(0, 16);
}
export function hasConsent(record, config, now = Date.now()) {
  return (
    record?.scope === consentScope(config) && Number(record.expires_at) > now
  );
}
export function normalizeSpeech(value) {
  return String(value ?? "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[\u200b-\u200f\u202a-\u202e\u2060-\u206f]/g, "")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
    .replace(/\s+/g, " ");
}
export function matchRules(value, rules) {
  const normalized = normalizeSpeech(value),
    padded = ` ${normalized} `,
    matches = [];
  for (const rule of rules) {
    if (rule.enabled === false) continue;
    const phrase = normalizeSpeech(rule.phrase);
    if (!phrase) continue;
    const index = padded.indexOf(` ${phrase} `);
    if (index < 0) continue;
    matches.push({
      id: rule.id,
      phrase: rule.phrase,
      excerpt: normalized.slice(
        Math.max(0, index - 100),
        Math.min(normalized.length, index + phrase.length + 100),
      ),
    });
  }
  return matches;
}
export function parseTranscript(payload) {
  const segments = Array.isArray(payload?.segments) ? payload.segments : [],
    eligible = [];
  for (const row of segments) {
    if (
      typeof row.avg_logprob !== "number" ||
      typeof row.no_speech_prob !== "number"
    )
      continue;
    const log = Number(row.avg_logprob),
      noSpeech = Number(row.no_speech_prob);
    if (
      !Number.isFinite(log) ||
      !Number.isFinite(noSpeech) ||
      noSpeech < 0 ||
      noSpeech > 0.6 ||
      log > 0
    )
      continue;
    const score = Math.min(1, Math.max(0, Math.exp(log)));
    const text = String(row.text ?? "").trim();
    if (text && text.length < 6000) eligible.push({ text, score });
  }
  return eligible;
}
export function pcmWav(pcm) {
  if (
    !Buffer.isBuffer(pcm) ||
    !pcm.length ||
    pcm.length % 2 ||
    pcm.length > 8 * 16000 * 2
  )
    throw new UserError("Invalid CVMS audio clip.");
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(pcm.length + 36, 4);
  header.write("WAVEfmt ", 8);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(16000, 24);
  header.writeUInt32LE(32000, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([header, pcm]);
}
export function audible(pcm) {
  if (pcm.length < 12800) return false;
  let energy = 0;
  for (let n = 0; n < pcm.length; n += 2) {
    const sample = pcm.readInt16LE(n);
    energy += sample * sample;
  }
  return Math.sqrt(energy / (pcm.length / 2)) >= 180;
}
