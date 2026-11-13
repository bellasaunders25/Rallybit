import { AttachmentBuilder, ChannelType, PermissionFlagsBits as P } from 'discord.js';
import { mkdtemp, open, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { UserError } from '../access.js';
import { channelPermissions, embed, nowISO, snowflake, text } from '../helpers.js';

export async function* messageHistory(channel, { before, after, limit = Infinity } = {}) {
  let remaining = limit, cursor = before;
  while (remaining > 0) {
    const page = await channel.messages.fetch({ limit: Math.min(100, remaining), ...(cursor ? { before: cursor } : {}), cache: false });
    const rows = [...page.values()].sort((a,b) => BigInt(a.id) > BigInt(b.id) ? -1 : 1);
    if (!rows.length) return;
    let reached = false;
    for (const row of rows) {
      if (after && BigInt(row.id) <= BigInt(after)) { reached = true; break; }
      yield row;
      if (--remaining <= 0) return;
    }
    const next = rows.at(-1).id;
    if (reached || next === cursor || rows.length < 100) return;
    cursor = next;
  }
}
export async function rangeMessages(channel, start, finish) {
  const ids = [snowflake(start, 'start message ID'), snowflake(finish, 'finish message ID')].sort((a,b) => BigInt(a) < BigInt(b) ? -1 : 1);
  const first = await channel.messages.fetch(ids[0]);
  if (ids[0] === ids[1]) return [first];
  const last = await channel.messages.fetch(ids[1]);
  const result = [first, last];
  for await (const message of messageHistory(channel, { before: last.id, after: first.id })) result.push(message);
  return result;
}
export async function deleteMessages(channel, messages, reason, now = Date.now()) {
  const unique = [...new Map(messages.map(message => [message.id, message])).values()];
  const recent = [], older = [];
  for (const message of unique) (message.createdTimestamp > now - 14 * 86400000 + 120000 ? recent : older).push(message);
  let deleted = 0, failed = 0;
  const one = async message => {
    try { await channel.messages.delete(message.id, reason); deleted++; }
    catch (error) { if ([50013,50001].includes(error.code)) throw error; failed++; }
  };
  for (let index = 0; index < recent.length; index += 100) {
    const batch = recent.slice(index, index + 100);
    if (batch.length === 1) { await one(batch[0]); continue; }
    try { const result = await channel.bulkDelete(batch.map(row => row.id), false, reason); deleted += result.size; }
    catch (error) { if ([50013,50001].includes(error.code)) throw error; for (const row of batch) await one(row); }
  }
  for (const row of older) await one(row);
  return { deleted, failed };
}
export function messageBlock(message) {
  return [
    `[${new Date(message.createdTimestamp).toISOString()}] ${message.author?.tag ?? message.author?.username ?? 'Unknown'} (ID: ${message.author?.id ?? 'Unknown'})`,
    `Message ID: ${message.id}`, `Message link: ${message.url}`, `Edited: ${message.editedTimestamp ? new Date(message.editedTimestamp).toISOString() : 'No'}`,
    `Reply to: ${message.reference?.messageId ?? 'None'}`, `Content:\n${message.content || '(no text content)'}`,
    `Attachments:\n${[...(message.attachments?.values() ?? [])].map(a => `${a.name} (${a.size} bytes): ${a.url}`).join('\n') || 'None'}`,
    `Stickers: ${[...(message.stickers?.values() ?? [])].map(s => `${s.name}: ${s.url}`).join(', ') || 'None'}`,
    `Embeds: ${JSON.stringify(message.embeds?.map(e => e.toJSON()) ?? [])}`,
    `Reactions: ${[...(message.reactions?.cache.values() ?? [])].map(r => `${r.emoji} x${r.count}`).join(', ') || 'None'}`,
    `Poll: ${message.poll ? JSON.stringify(message.poll.toJSON?.() ?? { question: message.poll.question?.text }) : 'None'}`, '-'.repeat(88), '',
  ].join('\n');
}
export class ChannelService {
  constructor({ audit }) { this.audit = audit; }
  async purge(i, params = {}) {
    const channel = params.channel ?? i.options.getChannel('channel') ?? i.channel;
    if (channel?.type !== ChannelType.GuildText || channel.guild.id !== i.guildId) throw new UserError('Choose a text channel in this server.');
    const [actor, bot] = await Promise.all([i.guild.members.fetch({ user: i.user.id, force: true }), i.guild.members.fetchMe({ force: true })]);
    channelPermissions(channel, actor, [P.ViewChannel, P.ReadMessageHistory, P.ManageMessages]);
    channelPermissions(channel, bot, [P.ViewChannel, P.ReadMessageHistory, P.ManageMessages]);
    const start = params.start ?? i.options.getString('start_message_id'), finish = params.finish ?? i.options.getString('finish_message_id');
    const member = params.member ?? i.options.getUser('member'), amount = params.amount ?? i.options.getInteger('amount');
    let messages;
    if (start || finish) {
      if (!start || !finish || member || amount !== null && amount !== undefined) throw new UserError('Provide both message IDs; do not combine a range with a member or amount.');
      messages = await rangeMessages(channel, start, finish);
    } else {
      if (!member && !amount) throw new UserError('Choose a message range, a member, or an amount.');
      if (amount !== null && amount !== undefined && (!Number.isInteger(amount) || amount < 1 || amount > 1000)) throw new UserError('Amount must be between 1 and 1,000.');
      messages = [];
      for await (const message of messageHistory(channel, { limit: member ? Infinity : amount })) {
        if (!member || message.author.id === member.id) messages.push(message);
        if (amount && messages.length >= amount) break;
      }
    }
    const result = await deleteMessages(channel, messages, text(params.reason ?? i.options.getString('reason') ?? `Rallybit purge by ${i.user.id}`, 400));
    await this.audit(i, 'moderation', 'Messages purged', `${result.deleted} deleted; ${result.failed} failed in <#${channel.id}>.`);
    return { content: `Deleted **${result.deleted}** messages in <#${channel.id}>.${result.failed ? ` ${result.failed} could not be deleted.` : ''}` };
  }
  async transcript(i) {
    const source = i.options.getChannel('source', true), destination = i.options.getChannel('destination', true);
    if (source.type !== ChannelType.GuildText || ![ChannelType.GuildText, ChannelType.GuildForum].includes(destination.type) || source.guild.id !== i.guildId || destination.guild.id !== i.guildId) throw new UserError('Choose a source text channel and a text or forum destination in this server.');
    const [actor, bot] = await Promise.all([i.guild.members.fetch({ user: i.user.id, force: true }), i.guild.members.fetchMe({ force: true })]);
    channelPermissions(source, actor, [P.ViewChannel, P.ReadMessageHistory]); channelPermissions(source, bot, [P.ViewChannel, P.ReadMessageHistory]);
    channelPermissions(destination, actor, [P.ViewChannel]);
    channelPermissions(destination, bot, [P.ViewChannel, P.AttachFiles, P.EmbedLinks, ...(destination.type === ChannelType.GuildForum ? [P.SendMessages, P.SendMessagesInThreads] : [P.SendMessages])]);
    const date = nowISO(), directory = await mkdtemp(join(tmpdir(), 'rallybit-transcript-'));
    const sizeLimit = Math.min(7_500_000, Math.max(500_000, (i.guild.maximumUploadLimit ?? 8_000_000) - 100_000));
    let part = 0, count = 0, bytes = 0, handle, path, target = destination, url = '';
    const create = async () => {
      part++; path = join(directory, `archive-${source.id}-part-${String(part).padStart(3,'0')}.txt`);
      handle = await open(path, 'wx', 0o600);
      const header = `Rallybit channel archive\nServer: ${i.guild.name} (${i.guildId})\nChannel: #${source.name} (${source.id})\nArchived at: ${date}\nArchived by: ${i.user.tag ?? i.user.username} (${i.user.id})\nOrder: newest first\nPart: ${part}\n${'='.repeat(88)}\n`;
      await handle.write(header); bytes = Buffer.byteLength(header);
    };
    const publish = async () => {
      await handle.close(); handle = null;
      const payload = { files: [new AttachmentBuilder(path)], allowedMentions: { parse: [] } };
      if (target.type === ChannelType.GuildForum) {
        target = await destination.threads.create({ name: text(`Archive — ${source.name} — ${date.slice(0,10)}`, 100), message: payload, reason: `Channel archive by ${i.user.id}` });
        url = `https://discord.com/channels/${i.guildId}/${target.id}`;
      } else { const sent = await target.send(payload); url ||= sent.url; }
      await rm(path);
    };
    try {
      await create();
      for await (const message of messageHistory(source)) {
        const block = Buffer.from(messageBlock(message), 'utf8');
        if (bytes + block.length > sizeLimit && count) { await publish(); await create(); }
        await handle.write(block); bytes += block.length; count++;
      }
      await publish();
      await target.send({ embeds: [embed('Channel archived', `Source: <#${source.id}>\nArchived at: ${date}\nArchived by: <@${i.user.id}>\nMessages: ${count}\nFiles: ${part}`)], allowedMentions: { parse: [] } });
      await this.audit(i, 'channels', 'Channel archived', `${count} available messages archived in ${part} files.`, '', destination.id);
      return { content: `Archived **${count}** messages from <#${source.id}>: ${url}` };
    } finally { await handle?.close(); await rm(directory, { recursive: true, force: true }); }
  }
}
export function registerChannels(registry, services) {
  const service = new ChannelService(services);
  registry.register('channel purge', i => service.purge(i), { guildOnly: true, moderation: 'warn' });
  registry.register('channel transcript', i => service.transcript(i), { guildOnly: true, moderation: 'warn' });
  return service;
}
