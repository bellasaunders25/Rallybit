import {
  joinVoiceChannel,
  VoiceConnectionStatus,
  entersState,
  EndBehaviorType,
} from "@discordjs/voice";
import OpusScript from "opusscript";
import { UserError } from "../access.js";
import { audible, pcmWav, parseTranscript } from "./cvms-config.js";

export function localEndpoint(raw) {
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new UserError("CVMS local speech service is not configured.");
  }
  if (
    url.protocol !== "http:" ||
    !["cvms-asr", "localhost", "127.0.0.1", "[::1]"].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !["", "/", "/inference"].includes(url.pathname)
  )
    throw new UserError(
      "CVMS only permits its local speech service. External audio providers are disabled.",
    );
  url.pathname = "/inference";
  return url;
}
async function limitedJSON(response) {
  let bytes = 0;
  const parts = [];
  for await (const chunk of response.body) {
    bytes += chunk.length;
    if (bytes > 128000) throw new Error("CVMS response too large");
    parts.push(chunk);
  }
  return JSON.parse(Buffer.concat(parts).toString());
}
export class LocalSpeech {
  constructor({
    url = process.env.CVMS_ASR_URL || "http://cvms-asr:8081",
    fetch: transport = globalThis.fetch,
  } = {}) {
    try {
      this.url = localEndpoint(url);
    } catch {
      this.url = null;
    }
    this.fetch = transport;
  }
  async health() {
    if (!this.url) return false;
    try {
      const url = new URL("/health", this.url),
        r = await this.fetch(url, {
          redirect: "error",
          signal: AbortSignal.timeout(2500),
        });
      return r.ok && (await limitedJSON(r)).status === "ok";
    } catch {
      return false;
    }
  }
  async transcribe(wav, signal) {
    if (!this.url)
      throw new UserError(
        "CVMS only supports its configured local speech service.",
      );
    const body = new FormData();
    body.set("file", new Blob([wav], { type: "audio/wav" }), "clip.wav");
    body.set("response_format", "verbose_json");
    body.set("temperature", "0");
    body.set("temperature_inc", "0");
    body.set("language", "en");
    body.set("no_context", "true");
    const response = await this.fetch(this.url, {
      method: "POST",
      body,
      redirect: "error",
      signal: AbortSignal.any([signal, AbortSignal.timeout(45000)]),
    });
    if (!response.ok) throw new Error("Local speech service unavailable");
    return parseTranscript(await limitedJSON(response));
  }
}
/** Bounded, single-inference queue. Audio is never written to bot storage. */
export class SpeechQueue {
  constructor(provider, { limit = 2, onError = () => {} } = {}) {
    this.provider = provider;
    this.limit = limit;
    this.pending = [];
    this.current = null;
    this.running = false;
    this.closed = false;
    this.onError = onError;
    this.dropped = 0;
  }
  submit(job) {
    let valid = false;
    try {
      valid = job.valid();
    } catch {}
    if (this.closed || !valid || this.pending.length >= this.limit) {
      job.pcm.fill(0);
      this.dropped++;
      return false;
    }
    this.pending.push({
      ...job,
      queuedAt: Date.now(),
      controller: new AbortController(),
    });
    void this.drain();
    return true;
  }
  cancel(sessionId) {
    if (this.current?.sessionId === sessionId) this.current.controller.abort();
    this.pending = this.pending.filter((row) => {
      if (row.sessionId !== sessionId) return true;
      row.controller.abort();
      row.pcm.fill(0);
      return false;
    });
  }
  async drain() {
    if (this.running) return;
    this.running = true;
    try {
      while (this.pending.length && !this.closed) {
        const row = this.pending.shift();
        this.current = row;
        let wav;
        try {
          if (!row.valid() || Date.now() - row.queuedAt > 15000) continue;
          wav = pcmWav(row.pcm);
          row.pcm.fill(0);
          const result = await this.provider.transcribe(
            wav,
            row.controller.signal,
          );
          if (!row.controller.signal.aborted && row.valid())
            await row.result(result);
        } catch (error) {
          if (!row.controller.signal.aborted) this.onError(error, row);
        } finally {
          row.pcm.fill(0);
          wav?.fill(0);
          this.current = null;
        }
      }
    } finally {
      this.running = false;
    }
  }
  stop() {
    this.closed = true;
    this.current?.controller.abort();
    for (const row of this.pending) {
      row.controller.abort();
      row.pcm.fill(0);
    }
    this.pending = [];
  }
}
export class VoiceCapture {
  constructor({ channel, valid, onClip, onStop }) {
    Object.assign(this, { channel, valid, onClip, onStop });
    this.closed = false;
    this.captures = new Map();
    this.connection = null;
  }
  async start() {
    if (!this.valid())
      throw new UserError("The CVMS session is no longer eligible.");
    this.connection = joinVoiceChannel({
      guildId: this.channel.guild.id,
      channelId: this.channel.id,
      adapterCreator: this.channel.guild.voiceAdapterCreator,
      group: "cvms",
      selfDeaf: false,
      selfMute: true,
      daveEncryption: true,
    });
    this.connection.on("error", () => this.onStop("Voice connection error"));
    this.connection.on(VoiceConnectionStatus.Disconnected, () =>
      this.onStop("Voice connection ended"),
    );
    await entersState(this.connection, VoiceConnectionStatus.Ready, 20000);
    if (!this.valid()) {
      this.stop();
      throw new UserError("CVMS eligibility changed while joining.");
    }
    this.connection.receiver.speaking.on("start", (userId) =>
      this.speaking(userId),
    );
  }
  speaking(userId) {
    if (
      this.closed ||
      this.captures.has(userId) ||
      this.captures.size >= 2 ||
      !this.valid(userId)
    )
      return;
    let decoder;
    try {
      decoder = new OpusScript(16000, 1, OpusScript.Application.VOIP);
    } catch {
      this.onStop("Voice decoder unavailable");
      return;
    }
    const stream = this.connection.receiver.subscribe(userId, {
        end: { behavior: EndBehaviorType.AfterSilence, duration: 1000 },
      }),
      row = { stream, decoder, parts: [], bytes: 0 };
    this.captures.set(userId, row);
    const clear = () => {
      for (const part of row.parts) part.fill(0);
      row.parts = [];
      row.bytes = 0;
    };
    const flush = () => {
      if (!row.bytes) return;
      const pcm = Buffer.concat(row.parts);
      clear();
      if (!this.closed && this.valid(userId) && audible(pcm))
        this.onClip(userId, pcm);
      else pcm.fill(0);
    };
    let ended = false;
    const end = () => {
      if (ended) return;
      ended = true;
      flush();
      decoder.delete();
      this.captures.delete(userId);
    };
    stream.on("data", (packet) => {
      if (this.closed || !this.valid(userId)) {
        clear();
        stream.destroy();
        return;
      }
      try {
        if (packet.length > 3830) throw new Error("Invalid Opus packet");
        const pcm = decoder.decode(packet);
        // Some Opus packets can span more than one clip; consume only bounded
        // slices while retaining the receive stream for continuous speakers.
        for (let n = 0; n < pcm.length;) {
          const count = Math.min(pcm.length - n, 256000 - row.bytes);
          row.parts.push(Buffer.from(pcm.subarray(n, n + count)));
          row.bytes += count;
          n += count;
          if (row.bytes === 256000) flush();
        }
        pcm.fill(0);
      } catch {
        clear();
        stream.destroy();
      }
    });
    stream.once("end", end);
    stream.once("close", end);
    stream.on("error", () => {
      clear();
      end();
    });
  }
  stop() {
    if (this.closed) return;
    this.closed = true;
    for (const row of this.captures.values()) {
      for (const part of row.parts) part.fill(0);
      row.parts = [];
      row.bytes = 0;
      row.stream.destroy();
    }
    this.captures.clear();
    this.connection?.destroy();
  }
}
