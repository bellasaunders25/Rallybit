import {
  ActionRowBuilder,
  AttachmentBuilder,
  ButtonBuilder,
  ButtonStyle,
  ChannelType,
  MessageFlags,
  ModalBuilder,
  PermissionFlagsBits as P,
  TextInputBuilder,
  TextInputStyle,
} from "discord.js";
import { UserError } from "../access.js";
import { privateReply } from "../components.js";
import {
  embed,
  guildSettings,
  KeyedQueue,
  nowISO,
  shortId,
  text,
  updateSettings,
} from "../helpers.js";
import { messageBlock, messageHistory } from "./channels.js";
import {
  normaliseOption,
  panelOptions,
  panelPayload,
  ROLE_GRANT,
  selectedOption,
  TICKET_DEFAULTS,
  ticketControls,
  validatePanel,
  welcomePayload,
} from "./ticket-ui.js";

const isClosed = (row) => String(row.status).toLowerCase() === "closed";
export function ticketSupportRoles(settings, panel, record) {
  let roles = [...(settings.support_role_ids ?? [])];
  if (panel) {
    if (!panel.options) roles.push(...(panel.support_role_ids ?? []));
    const option = panelOptions(panel).find(
      (row) =>
        row.option_id === String(record.option_id || "DEFAULT").toUpperCase(),
    );
    if (option) roles.push(...option.support_role_ids);
  }
  return [...new Set(roles.map(String).filter((id) => /^\d{15,22}$/.test(id)))];
}
export class TicketService {
  constructor(services) {
    Object.assign(this, services);
    this.queue = new KeyedQueue();
  }
  async settings(id) {
    return guildSettings(
      this.store,
      "ticket_settings.json",
      id,
      TICKET_DEFAULTS,
    );
  }
  async panels(id) {
    return (await this.store.read("ticket_panels.json"))[id] ?? {};
  }
  async tickets(id) {
    return (await this.store.read("open_tickets.json"))[id] ?? {};
  }
  async staff(member, record) {
    if (
      member.id === member.guild.ownerId ||
      member.permissions.has(P.ManageMessages)
    )
      return true;
    const settings = await this.settings(member.guild.id),
      panels = await this.panels(member.guild.id);
    return ticketSupportRoles(
      settings,
      panels[record?.panel_id],
      record ?? {},
    ).some((id) => member.roles.cache.has(id));
  }
  async access(i, mode = "staff") {
    if (
      i.channel?.type !== ChannelType.GuildText ||
      i.channel.guild.id !== i.guildId
    )
      throw new UserError("Use this inside a ticket channel.");
    const record = (await this.tickets(i.guildId))[i.channelId];
    if (!record) throw new UserError("This is not a tracked ticket.");
    const member = await i.guild.members.fetch({
      user: i.user.id,
      force: true,
    });
    if (
      !(await this.staff(member, record)) &&
      !(mode === "owner" && String(record.owner_id) === i.user.id)
    )
      throw new UserError(
        "You need a configured ticket support role or Manage Messages.",
      );
    return record;
  }
  async change(guildId, channelId, patch) {
    return this.store.update("open_tickets.json", (data) => {
      const row = data[guildId]?.[channelId];
      if (!row) throw new UserError("Ticket not found.");
      Object.assign(row, patch);
      return structuredClone(row);
    });
  }
  async history(guildId, record) {
    await this.store.update("ticket_history.json", (data) => {
      const rows = (data[guildId] ??= []);
      const key = `${record.channel_id}:${record.deleted_at ?? record.closed_at ?? ""}`;
      if (
        !rows.some(
          (row) =>
            `${row.channel_id}:${row.deleted_at ?? row.closed_at ?? ""}` ===
            key,
        )
      )
        rows.push(record);
      data[guildId] = rows.slice(-500);
    });
  }
  async refresh(guild, id) {
    if (!id) return;
    const panel = (await this.panels(guild.id))[id];
    if (!panel?.message_id) return;
    const channel = await guild.channels.fetch(String(panel.channel_id)),
      message = await channel.messages.fetch(String(panel.message_id));
    if (message.author.id !== guild.client.user.id)
      throw new UserError("Rallybit can only edit its own ticket panels.");
    const count = Object.values(await this.tickets(guild.id)).filter(
      (row) => row.panel_id === id && !isClosed(row),
    ).length;
    await message.edit(panelPayload(guild, id, panel, count));
  }
  async reconcile(guild, panelId = null, previousPanel = null) {
    return this.queue.run(`reconcile:${guild.id}`, async () => {
      const settings = await this.settings(guild.id),
        panels = await this.panels(guild.id),
        tickets = await this.tickets(guild.id);
      let failures = 0;
      for (const [channelId, record] of Object.entries(tickets)) {
        if (panelId && record.panel_id !== panelId) continue;
        try {
          let channel;
          try {
            channel = await guild.channels.fetch(channelId);
          } catch (error) {
            if (error.code === 10003) continue;
            throw error;
          }
          if (!channel) continue;
          const desired = ticketSupportRoles(
              settings,
              panels[record.panel_id],
              record,
            ),
            old = new Set((record.managed_support_role_ids ?? []).map(String));
          if (previousPanel)
            for (const id of ticketSupportRoles({}, previousPanel, record))
              old.add(id);
          if (!Object.hasOwn(record, "managed_support_role_ids"))
            for (const overwrite of channel.permissionOverwrites.cache.values()) {
              if (
                overwrite.type === 0 &&
                overwrite.id !== guild.id &&
                Object.keys(ROLE_GRANT).every((key) =>
                  overwrite.allow.has(P[key]),
                )
              )
                old.add(overwrite.id);
            }
          for (const id of old)
            if (
              !desired.includes(id) &&
              id !== guild.id &&
              guild.roles.cache.has(id)
            )
              await channel.permissionOverwrites.edit(
                id,
                Object.fromEntries(
                  Object.keys(ROLE_GRANT).map((key) => [key, null]),
                ),
                { reason: "Rallybit ticket support access removed" },
              );
          for (const id of desired) {
            if (id === guild.id || !guild.roles.cache.has(id)) continue;
            const expected = { ...ROLE_GRANT, SendMessages: !isClosed(record) },
              current = channel.permissionOverwrites.cache.get(id);
            if (
              !current ||
              Object.entries(expected).some(([key, value]) =>
                value ? !current.allow.has(P[key]) : !current.deny.has(P[key]),
              )
            )
              await channel.permissionOverwrites.edit(id, expected, {
                reason: "Rallybit ticket support access updated",
              });
          }
          if (
            JSON.stringify([...desired].sort()) !==
            JSON.stringify([...(record.managed_support_role_ids ?? [])].sort())
          )
            await this.change(guild.id, channelId, {
              managed_support_role_ids: desired,
            });
        } catch (error) {
          failures++;
          console.warn(
            JSON.stringify({
              event: "ticket_access_retry",
              channel: channelId,
              code: error.code,
              name: error.name,
            }),
          );
        }
      }
      if (failures)
        throw new UserError(
          `Ticket settings are saved, but access changes could not be applied to ${failures} channels. Check Rallybit's channel permissions; reconciliation will retry automatically.`,
        );
    });
  }
  async create(i, { panelId = "", optionId, category, reason = "" } = {}) {
    return this.queue.run(`create:${i.guildId}:${i.user.id}`, async () => {
      const settings = await this.settings(i.guildId),
        panels = await this.panels(i.guildId),
        tickets = await this.tickets(i.guildId);
      if (
        Object.values(await this.store.read("ticket_jobs.json")).some(
          (job) =>
            job.kind === "create" &&
            job.status === "pending" &&
            String(job.guild_id) === i.guildId &&
            String(job.owner_id) === i.user.id,
        )
      )
        throw new UserError(
          "An earlier ticket request is still being recovered. Ask staff to check it before creating another.",
        );
      let panel = panels[panelId],
        option;
      if (category) {
        const options = Object.values(panels).flatMap(panelOptions),
          active = options.filter((row) => row.enabled);
        const allowed = new Set(active.map((row) => row.category_id));
        if (
          settings.default_category_id &&
          !options.some(
            (row) =>
              row.category_id === String(settings.default_category_id) &&
              !row.enabled,
          )
        )
          allowed.add(String(settings.default_category_id));
        if (!allowed.has(category.id))
          throw new UserError(
            "Choose an enabled ticket category configured by server staff.",
          );
        panel = {
          name: "Direct request",
          category_id: category.id,
          welcome_message: settings.welcome_message,
        };
        option = { option_id: "DIRECT" };
      } else {
        if (!panel) throw new UserError("That ticket panel no longer exists.");
        option = selectedOption(panel, optionId);
        panel = {
          ...panel,
          ...option,
          support_role_ids: option.support_role_ids,
        };
      }
      if (settings.one_ticket_per_member)
        for (const [id, row] of Object.entries(tickets))
          if (String(row.owner_id) === i.user.id && !isClosed(row)) {
            const existing = await i.guild.channels.fetch(id).catch((error) => {
              if (error.code === 10003) return null;
              throw error;
            });
            if (existing)
              throw new UserError(`You already have an open ticket: <#${id}>.`);
          }
      const parent = await i.guild.channels.fetch(
        String(
          category?.id ||
            panel.category_id ||
            settings.default_category_id ||
            "",
        ),
      );
      if (parent?.type !== ChannelType.GuildCategory)
        throw new UserError(
          "The ticket category is not configured or no longer exists.",
        );
      const [member, bot] = await Promise.all([
        i.guild.members.fetch({ user: i.user.id, force: true }),
        i.guild.members.fetchMe({ force: true }),
      ]);
      if (!bot.permissions.has(P.ManageChannels))
        throw new UserError("Rallybit needs Manage Channels.");
      const support = [
        ...new Set(
          [
            ...(settings.support_role_ids ?? []),
            ...(panel.support_role_ids ?? []),
          ].map(String),
        ),
      ].filter((id) => id !== i.guildId && i.guild.roles.cache.has(id));
      const operation = i.id || shortId(),
        record = {
          owner_id: i.user.id,
          panel_id: panelId,
          option_id: option.option_id,
          claimed_by: null,
          status: "Open",
          priority: "Medium",
          reason: text(reason),
          case_id: `T-${shortId()}`,
          managed_support_role_ids: support,
          created_at: nowISO(),
          updated_at: nowISO(),
        };
      await this.store.update("ticket_jobs.json", (data) => {
        if (data[operation])
          throw new UserError(
            "This ticket request was already attempted. Ask staff to check existing tickets before retrying.",
          );
        data[operation] = {
          kind: "create",
          guild_id: i.guildId,
          owner_id: i.user.id,
          record,
          panel,
          status: "pending",
          created_at: nowISO(),
        };
      });
      const name =
        text(
          String(panel.ticket_name || settings.ticket_name)
            .replaceAll("{username}", member.user.username)
            .replaceAll("{displayname}", member.displayName)
            .replaceAll("{id}", member.id)
            .replaceAll("{number}", String(Object.keys(tickets).length + 1))
            .toLowerCase()
            .replace(/[^a-z0-9_-]/g, "-")
            .replace(/-+/g, "-")
            .replace(/^-|-$/g, ""),
          90,
        ) || `ticket-${member.id}`;
      let channel;
      try {
        channel = await i.guild.channels.create({
          name,
          type: ChannelType.GuildText,
          parent: parent.id,
          topic: `Rallybit ticket owner=${member.id} panel=${panelId} operation=${operation}`,
          permissionOverwrites: [
            { id: i.guildId, deny: [P.ViewChannel] },
            {
              id: member.id,
              allow: [
                P.ViewChannel,
                P.SendMessages,
                P.ReadMessageHistory,
                P.AttachFiles,
              ],
            },
            {
              id: bot.id,
              allow: [
                P.ViewChannel,
                P.SendMessages,
                P.ReadMessageHistory,
                P.ManageChannels,
                P.ManageMessages,
              ],
            },
            ...support.map((id) => ({
              id,
              allow: Object.keys(ROLE_GRANT).map((key) => P[key]),
            })),
          ],
          reason: `Ticket opened by ${i.user.id}`,
        });
      } catch (error) {
        if ([50013, 50001, 50035].includes(error.code))
          await this.store.update("ticket_jobs.json", (data) => {
            data[operation].status = "failed";
          });
        throw error;
      }
      try {
        await this.store.update("open_tickets.json", (data) => {
          (data[i.guildId] ??= {})[channel.id] = record;
        });
        const message = await channel.send({
          ...welcomePayload(i.guild, channel, member, panel, record),
          content: [
            `<@${member.id}>`,
            ...support.map((id) => `<@&${id}>`),
          ].join(" "),
          allowedMentions: { parse: [], users: [member.id], roles: support },
          nonce: operation,
          enforceNonce: true,
        });
        await this.change(i.guildId, channel.id, {
          control_message_id: message.id,
        });
        await this.store.update("ticket_jobs.json", (data) => {
          data[operation].status = "completed";
          data[operation].channel_id = channel.id;
        });
      } catch (error) {
        // Keep the durable intent and existing channel. Never silently delete a
        // potentially populated support conversation after an uncertain save.
        throw new UserError(
          `The ticket channel <#${channel.id}> was created, but setup could not finish. Staff should check storage and permissions; do not create a duplicate.`,
        );
      }
      await this.refresh(i.guild, panelId).catch(() => {});
      await this.audit(
        i,
        "tickets",
        "Ticket opened",
        `Case ${record.case_id} opened.`,
        member.id,
      );
      return channel;
    });
  }
  async transcript(channel, limit = 500) {
    const files = [];
    let parts = [],
      bytes = 0;
    const header = `Rallybit ticket transcript\nServer: ${channel.guild.name} (${channel.guild.id})\nChannel: #${channel.name} (${channel.id})\nExported at: ${nowISO()}\nOrder: newest first\n\n`;
    const finish = () => {
      files.push(
        new AttachmentBuilder(Buffer.from(header + parts.join("")), {
          name: `ticket-${channel.id}-part-${files.length + 1}.txt`,
        }),
      );
      parts = [];
      bytes = 0;
    };
    for await (const message of messageHistory(channel, {
      limit: Math.max(1, Math.min(10000, Number(limit) || 500)),
    })) {
      const block = messageBlock(message),
        size = Buffer.byteLength(block);
      if (bytes + size > 7_000_000) {
        finish();
        if (files.length >= 10)
          throw new UserError(
            "This ticket exceeds the direct export limit. Use /channel transcript to stream the complete archive.",
          );
      }
      parts.push(block);
      bytes += size;
    }
    if (parts.length || !files.length) finish();
    return files;
  }
  async archive(channel, record, actor, reason, kind) {
    const settings = await this.settings(channel.guild.id),
      files = await this.transcript(channel, settings.transcript_limit);
    const card = embed(
      `Ticket ${kind}`,
      `Case: ${record.case_id || channel.name}\nChannel: #${channel.name}\nCreator: <@${record.owner_id}>\nBy: <@${actor.id}>\nReason: ${text(reason, 1000)}\nArchived at: ${nowISO()}`,
    );
    const destination = settings.log_channel_id
      ? await channel.guild.channels.fetch(String(settings.log_channel_id))
      : await channel.client.users.fetch(String(record.owner_id));
    if (!destination?.send)
      throw new UserError(
        "Configure an accessible ticket transcript log channel before deleting tickets.",
      );
    await destination.send({
      embeds: [card],
      files,
      allowedMentions: { parse: [] },
    });
  }
  async close(i, reason) {
    return this.queue.run(`ticket:${i.guildId}:${i.channelId}`, async () => {
      const record = await this.access(i, "owner");
      if (isClosed(record))
        throw new UserError("This ticket is already closed.");
      const clean = text(reason);
      if (!clean)
        throw new UserError("Provide a reason for closing this ticket.");
      const settings = await this.settings(i.guildId),
        minutes = Math.max(
          0,
          Math.min(10080, Number(settings.auto_delete_minutes) || 0),
        );
      const changes = {
        status: "Closed",
        closed_by: i.user.id,
        closed_at: nowISO(),
        close_reason: clean,
        delete_at: minutes
          ? new Date(Date.now() + minutes * 60000).toISOString()
          : null,
        updated_at: nowISO(),
        updated_by: i.user.id,
      };
      await this.change(i.guildId, i.channelId, {
        ...changes,
        access_pending: true,
      });
      await i.channel.permissionOverwrites.edit(
        String(record.owner_id),
        { ViewChannel: true, SendMessages: false, ReadMessageHistory: true },
        { reason: `Ticket closed by ${i.user.id}` },
      );
      await this.reconcile(i.guild, record.panel_id || null);
      await this.change(i.guildId, i.channelId, { access_pending: false });
      await this.history(i.guildId, {
        ...record,
        ...changes,
        channel_id: i.channelId,
        channel_name: i.channel.name,
        reason: clean,
      });
      let archived = true;
      try {
        await this.archive(
          i.channel,
          { ...record, ...changes },
          i.user,
          clean,
          "closed",
        );
      } catch {
        archived = false;
      }
      await i.channel.send({
        embeds: [
          embed(
            "This ticket is closed",
            `Reason: ${clean}\n${changes.delete_at ? `Automatic deletion: <t:${Math.floor(Date.parse(changes.delete_at) / 1000)}:R>. A transcript must be delivered before deletion.` : "Staff can reopen or permanently delete it."}${archived ? "" : "\nTranscript delivery failed; the channel has been retained."}`,
          ),
        ],
        components: ticketControls(i.guildId, i.channelId),
        allowedMentions: { parse: [] },
      });
      await this.refresh(i.guild, record.panel_id).catch(() => {});
      return {
        content: `Ticket closed. Reason: ${clean}${archived ? "" : " The transcript could not be delivered; check the transcript log channel."}`,
      };
    });
  }
  async claim(i) {
    const record = await this.access(i);
    if (isClosed(record))
      throw new UserError("Reopen this ticket before claiming it.");
    let duplicate = false;
    await this.store.update("open_tickets.json", (data) => {
      const row = data[i.guildId]?.[i.channelId];
      if (!row) throw new UserError("Ticket no longer tracked.");
      if (isClosed(row))
        throw new UserError("Reopen this ticket before claiming it.");
      if (row.claimed_by && String(row.claimed_by) !== i.user.id)
        throw new UserError(
          "This ticket is already claimed by another staff member.",
        );
      duplicate = String(row.claimed_by) === i.user.id;
      Object.assign(row, {
        claimed_by: i.user.id,
        status: "Claimed",
        updated_at: nowISO(),
        updated_by: i.user.id,
      });
    });
    if (!duplicate) {
      let message;
      try {
        message = await i.channel.send({
          content: `Ticket claimed by <@${i.user.id}>.`,
          allowedMentions: { parse: [] },
        });
      } catch {
        return {
          content:
            "Ticket claimed, but the assignment message could not be posted. Check Send Messages.",
        };
      }
      await this.change(i.guildId, i.channelId, {
        claim_message_id: message.id,
      });
      try {
        await message.pin("Rallybit ticket assignment");
      } catch {
        return {
          content:
            "Ticket claimed. The assignment message could not be pinned; check Manage Messages.",
        };
      }
    }
    return { content: `Ticket claimed by <@${i.user.id}>.` };
  }
  async remove(channel, actor, reason, { onlyIfDue = false } = {}) {
    return this.queue.run(
      `ticket:${channel.guild.id}:${channel.id}`,
      async () => {
        const record = (await this.tickets(channel.guild.id))[channel.id];
        if (!record) throw new UserError("Ticket not found.");
        if (
          onlyIfDue &&
          (!isClosed(record) ||
            !record.delete_at ||
            Date.parse(record.delete_at) > Date.now())
        )
          return false;
        const bot = await channel.guild.members.fetchMe({ force: true });
        if (!channel.permissionsFor(bot)?.has(P.ManageChannels))
          throw new UserError(
            "Rallybit needs Manage Channels to delete this ticket.",
          );
        const job = `delete-${channel.id}`,
          previous = (await this.store.read("ticket_jobs.json"))[job];
        const latest =
          (await channel.messages.fetch({ limit: 1 })).first()?.id ?? null;
        // Reuse a delivered snapshot on an unchanged retry; failed deletion must
        // never flood the log channel with identical transcripts every tick.
        if (
          !previous?.transcript_delivered ||
          previous.last_message_id !== latest
        )
          await this.archive(channel, record, actor, reason, "deleted");
        const history = {
          ...record,
          channel_id: channel.id,
          channel_name: channel.name,
          deleted_by: actor.id,
          deleted_at: previous?.record?.deleted_at ?? nowISO(),
          reason: text(reason),
        };
        await this.store.update("ticket_jobs.json", (data) => {
          data[job] = {
            kind: "delete",
            guild_id: channel.guild.id,
            channel_id: channel.id,
            record: history,
            status: "pending",
            transcript_delivered: true,
            last_message_id: latest,
          };
        });
        await channel.delete(
          `Rallybit ticket deletion by ${actor.id}: ${text(reason, 300)}`,
        );
        await this.history(channel.guild.id, history);
        await this.store.update("open_tickets.json", (data) => {
          delete data[channel.guild.id]?.[channel.id];
        });
        await this.store.update("ticket_jobs.json", (data) => {
          data[job].status = "completed";
        });
        await this.refresh(channel.guild, record.panel_id).catch(() => {});
      },
    );
  }
  async createPanel(guild, channel, category, input) {
    const id = shortId(),
      panel = validatePanel({
        ...input,
        panel_id: id,
        channel_id: channel.id,
        message_id: null,
        category_id: category.id,
        options: input.options ?? [
          {
            name: input.name,
            description: input.option_description,
            emoji: input.option_emoji,
            category_id: category.id,
            support_role_ids: input.support_role_ids ?? [],
          },
        ],
      });
    await this.store.update("ticket_panels.json", (data) => {
      (data[guild.id] ??= {})[id] = panel;
    });
    try {
      const sent = await channel.send(panelPayload(guild, id, panel));
      panel.message_id = sent.id;
      await this.store.update("ticket_panels.json", (data) => {
        data[guild.id][id].message_id = sent.id;
      });
    } catch {
      throw new UserError(
        `Panel ${id} was saved but could not be published. Check the channel, emoji and embed permissions before editing it.`,
      );
    }
    return panel;
  }
  async updatePanel(guild, id, input) {
    id = String(id).toUpperCase();
    const previous = (await this.panels(guild.id))[id];
    if (!previous) throw new UserError("Ticket panel not found.");
    const updated = validatePanel(input, previous);
    await this.store.update("ticket_panels.json", (data) => {
      data[guild.id][id] = updated;
    });
    await this.reconcile(guild, id, previous);
    try {
      await this.refresh(guild, id);
    } catch {
      updated.warning =
        "Settings were saved and support permissions reconciled, but the posted message could not be refreshed.";
    }
    return updated;
  }
  async component(i) {
    const [, namespace, action, guildId, id, owner] = i.customId.split(":");
    if (namespace !== "ticket" || guildId !== i.guildId)
      throw new UserError("This ticket control belongs to another server.");
    if (action === "select") {
      const panel = (await this.panels(guildId))[id];
      if (!panel) throw new UserError("This panel no longer exists.");
      const option = selectedOption(panel, i.values?.[0]);
      return i.showModal(
        new ModalBuilder()
          .setCustomId(`ticketopen:${guildId}:${id}:${option.option_id}`)
          .setTitle(text(option.name, 45))
          .addComponents(
            new ActionRowBuilder().addComponents(
              new TextInputBuilder()
                .setCustomId("reason")
                .setLabel("How can we help?")
                .setStyle(TextInputStyle.Paragraph)
                .setMaxLength(1000)
                .setRequired(true),
            ),
          ),
      );
    }
    if (id !== i.channelId)
      throw new UserError("This control belongs to another ticket.");
    if (action === "close") {
      await this.access(i, "owner");
      return i.showModal(
        new ModalBuilder()
          .setCustomId(`rallybit:ticket:closeform:${guildId}:${id}`)
          .setTitle("Close ticket")
          .addComponents(
            new ActionRowBuilder().addComponents(
              new TextInputBuilder()
                .setCustomId("reason")
                .setLabel("Reason for closing")
                .setStyle(TextInputStyle.Paragraph)
                .setMaxLength(1000)
                .setRequired(true),
            ),
          ),
      );
    }
    if (action === "delete") {
      await this.access(i);
      return privateReply(i, {
        embeds: [
          embed(
            "Delete this ticket permanently?",
            "A final transcript must be delivered before deletion. This cannot be undone.",
          ),
        ],
        components: [
          new ActionRowBuilder().addComponents(
            new ButtonBuilder()
              .setCustomId(
                `rallybit:ticket:confirmdelete:${guildId}:${id}:${i.user.id}`,
              )
              .setLabel("Delete permanently")
              .setStyle(ButtonStyle.Danger),
          ),
        ],
      });
    }
    if (action === "confirmdelete") {
      if (owner !== i.user.id)
        throw new UserError(
          "Only the staff member who opened this confirmation can use it.",
        );
      await this.access(i);
      await i.deferReply({ flags: MessageFlags.Ephemeral });
      await this.remove(i.channel, i.user, "Deleted manually by ticket staff");
      return privateReply(i, { content: "Ticket deleted." });
    }
    await i.deferReply({ flags: MessageFlags.Ephemeral });
    if (action === "closeform")
      return privateReply(
        i,
        await this.close(i, i.fields.getTextInputValue("reason")),
      );
    if (action === "claim") return privateReply(i, await this.claim(i));
    if (action === "transcript") {
      await this.access(i, "owner");
      return privateReply(i, {
        files: await this.transcript(
          i.channel,
          (await this.settings(guildId)).transcript_limit,
        ),
      });
    }
    throw new UserError("Unknown ticket control.");
  }
  async tick(client, assertOwner = () => {}) {
    const jobs = await this.store.read("ticket_jobs.json");
    for (const [key, job] of Object.entries(jobs)) {
      if (job.status !== "pending") continue;
      const guild = client.guilds.cache.get(String(job.guild_id));
      if (!guild) continue;
      assertOwner();
      try {
        if (job.kind === "delete") {
          const exists = await guild.channels
            .fetch(String(job.channel_id))
            .catch((error) => {
              if (error.code === 10003) return null;
              throw error;
            });
          if (!exists) {
            await this.history(guild.id, job.record);
            await this.store.update("open_tickets.json", (data) => {
              delete data[guild.id]?.[job.channel_id];
            });
            await this.store.update("ticket_jobs.json", (data) => {
              data[key].status = "completed";
            });
          }
        } else if (job.kind === "create") {
          const channels = await guild.channels.fetch(),
            channel = [...channels.values()].find((c) =>
              c?.topic?.includes(`operation=${key}`),
            );
          if (channel) {
            await this.store.update("open_tickets.json", (data) => {
              (data[guild.id] ??= {})[channel.id] ??= job.record;
            });
            const member = await guild.members.fetch(String(job.owner_id));
            const recent = await channel.messages.fetch({ limit: 100 });
            const existing = [...recent.values()].find(
              (message) =>
                message.author.id === client.user.id &&
                message.components.some((row) =>
                  row.components.some((c) =>
                    c.customId?.includes(`:${guild.id}:${channel.id}`),
                  ),
                ),
            );
            const control =
              existing ??
              (await channel.send({
                ...welcomePayload(
                  guild,
                  channel,
                  member,
                  job.panel,
                  job.record,
                ),
                allowedMentions: { parse: [] },
              }));
            await this.change(guild.id, channel.id, {
              control_message_id: control.id,
            });
            await this.store.update("ticket_jobs.json", (data) => {
              data[key].status = "completed";
              data[key].channel_id = channel.id;
            });
          }
        }
      } catch (error) {
        console.warn(
          JSON.stringify({
            event: "ticket_job_retry",
            kind: job.kind,
            code: error.code,
            name: error.name,
          }),
        );
      }
    }
    for (const guild of client.guilds.cache.values()) {
      assertOwner();
      await this.reconcile(guild).catch(() => {});
      for (const [id, row] of Object.entries(await this.tickets(guild.id))) {
        try {
          if (row.access_pending) {
            const channel = await guild.channels.fetch(id);
            if (channel) {
              await channel.permissionOverwrites.edit(
                String(row.owner_id),
                {
                  ViewChannel: true,
                  SendMessages: !isClosed(row),
                  ReadMessageHistory: true,
                },
                { reason: "Recover ticket access update" },
              );
              await this.change(guild.id, id, { access_pending: false });
            }
          }
          if (
            isClosed(row) &&
            row.delete_at &&
            Date.parse(row.delete_at) <= Date.now()
          ) {
            assertOwner();
            try {
              const channel = await guild.channels.fetch(id);
              if (channel)
                await this.remove(
                  channel,
                  client.user,
                  "Closed-ticket deletion timer expired",
                  { onlyIfDue: true },
                );
            } catch (error) {
              console.warn(
                JSON.stringify({
                  event: "ticket_delete_retry",
                  channel: id,
                  code: error.code,
                }),
              );
            }
          }
        } catch (error) {
          console.warn(
            JSON.stringify({
              event: "ticket_recovery_retry",
              channel: id,
              code: error.code,
              name: error.name,
            }),
          );
        }
      }
    }
    // Terminal job records are bounded. Pending/uncertain operations are retained.
    await this.store.update("ticket_jobs.json", (data) => {
      const completed = Object.entries(data).filter(([, row]) =>
        ["completed", "failed"].includes(row.status),
      );
      for (const [key] of completed.slice(
        0,
        Math.max(0, completed.length - 500),
      ))
        delete data[key];
    });
  }
}

export function registerTickets(registry, services) {
  const service = new TicketService(services),
    publicPolicy = { guildOnly: true },
    manager = { guildOnly: true, permissions: ["ManageChannels"] };
  registry.register(
    "ticket create",
    async (i) => ({
      content: `Ticket created: <#${(await service.create(i, { category: i.options.getChannel("category", true), reason: i.options.getString("reason", true) })).id}>`,
    }),
    publicPolicy,
  );
  for (const mode of ["setup", "settings"])
    registry.register(
      `ticket ${mode}`,
      async (i) => {
        const category = i.options.getChannel("category", true),
          log = i.options.getChannel("log_channel"),
          role = i.options.getRole("support_role");
        if (
          category.type !== ChannelType.GuildCategory ||
          category.guild.id !== i.guildId
        )
          throw new UserError("Choose a category in this server.");
        if (log && (!log.send || log.guild.id !== i.guildId))
          throw new UserError("Choose a text log channel in this server.");
        if (role?.id === i.guildId)
          throw new UserError("The everyone role cannot be a support role.");
        const patch = {
          default_category_id: category.id,
          log_channel_id: log?.id ?? null,
          support_role_ids: role ? [role.id] : [],
          one_ticket_per_member:
            i.options.getBoolean("one_ticket_per_member") ?? true,
        };
        if (mode === "settings") {
          patch.transcript_limit =
            i.options.getInteger("transcript_limit") ?? 500;
          patch.auto_delete_minutes =
            i.options.getInteger("auto_delete_minutes") ?? 0;
        }
        await updateSettings(
          services.store,
          "ticket_settings.json",
          i.guildId,
          patch,
        );
        await service.reconcile(i.guild);
        return {
          content: "Ticket settings saved and support-role access reconciled.",
        };
      },
      manager,
    );
  registry.register(
    "ticket panel create",
    async (i) => {
      const category = i.options.getChannel("category", true),
        channel = i.options.getChannel("channel", true);
      if (
        category.type !== ChannelType.GuildCategory ||
        !channel.send ||
        channel.guild.id !== i.guildId ||
        category.guild.id !== i.guildId
      )
        throw new UserError(
          "Choose a text channel and category in this server.",
        );
      const role = i.options.getRole("support_role");
      if (role?.id === i.guildId)
        throw new UserError("The everyone role cannot be a support role.");
      const input = {
        name: i.options.getString("name", true),
        title: i.options.getString("title") || "How can we help?",
        description:
          i.options.getString("description") ||
          "Choose a ticket type to speak privately with the support team.",
        support_role_ids: role ? [role.id] : [],
        option_description: i.options.getString("option_description"),
        option_emoji: i.options.getString("option_icon"),
        select_placeholder: i.options.getString("placeholder"),
      };
      for (const [option, field] of [
        ["header_image", "header_image_url"],
        ["thumbnail", "thumbnail_url"],
        ["image", "image_url"],
        ["footer_text", "footer_text"],
        ["footer_icon", "footer_icon_url"],
      ])
        input[field] = i.options.getString(option) || "";
      for (const key of [
        "show_option_details",
        "show_workload",
        "show_guidance",
      ])
        input[key] = i.options.getBoolean(key) ?? true;
      const panel = await service.createPanel(
        i.guild,
        channel,
        category,
        input,
      );
      return {
        content: `Ticket panel \`${panel.panel_id}\` sent to <#${channel.id}>.`,
      };
    },
    manager,
  );
  registry.register(
    "ticket panel add-option",
    async (i) => {
      const id = i.options.getString("panel_id", true).trim().toUpperCase(),
        panel = (await service.panels(i.guildId))[id];
      if (!panel) throw new UserError("Ticket panel not found.");
      const category = i.options.getChannel("category", true),
        role = i.options.getRole("support_role");
      if (
        category.type !== ChannelType.GuildCategory ||
        category.guild.id !== i.guildId
      )
        throw new UserError("Choose a server category.");
      if (role?.id === i.guildId)
        throw new UserError("The everyone role cannot be a support role.");
      const option = normaliseOption({
        option_id: shortId(),
        name: i.options.getString("name", true),
        description: i.options.getString("description", true),
        emoji: i.options.getString("icon"),
        category_id: category.id,
        support_role_ids: role ? [role.id] : [],
      });
      const updated = await service.updatePanel(i.guild, id, {
        options: [...panelOptions(panel), option],
      });
      return {
        content: `Option \`${option.option_id}\` added.${updated.warning ? ` ${updated.warning}` : ""}`,
      };
    },
    manager,
  );
  registry.register(
    "ticket panel remove-option",
    async (i) => {
      const id = i.options.getString("panel_id", true).trim().toUpperCase(),
        option = i.options.getString("option_id", true).trim().toUpperCase(),
        panel = (await service.panels(i.guildId))[id];
      if (!panel) throw new UserError("Ticket panel not found.");
      const options = panelOptions(panel).filter(
        (row) => row.option_id !== option,
      );
      if (options.length === panelOptions(panel).length)
        throw new UserError("Option not found.");
      const updated = await service.updatePanel(i.guild, id, { options });
      return {
        content: `Option removed.${updated.warning ? ` ${updated.warning}` : ""}`,
      };
    },
    manager,
  );
  registry.register(
    "ticket panel list",
    async (i) => ({
      embeds: [
        embed(
          "Ticket panels",
          Object.entries(await service.panels(i.guildId))
            .map(
              ([id, row]) =>
                `\`${id}\` · **${text(row.name, 60)}** · <#${row.channel_id}>\n${panelOptions(
                  row,
                )
                  .map(
                    (o) =>
                      `\`${o.option_id}\` ${o.name}${o.enabled ? "" : " (closed)"}`,
                  )
                  .join(", ")}`,
            )
            .join("\n\n") ||
            "No ticket panels. Core ticket controls are available to configured support roles.",
        ),
      ],
    }),
    manager,
  );
  registry.register(
    "ticket panel delete",
    async (i) => {
      const id = i.options.getString("panel_id", true).trim().toUpperCase(),
        panel = (await service.panels(i.guildId))[id];
      if (!panel) throw new UserError("Panel not found.");
      if (i.options.getBoolean("delete_message") && panel.message_id) {
        const channel = await i.guild.channels.fetch(String(panel.channel_id));
        const message = await channel.messages.fetch(String(panel.message_id));
        if (message.author.id !== i.client.user.id)
          throw new UserError("Rallybit cannot delete another author’s panel.");
        await message.delete();
      }
      await services.store.update("ticket_panels.json", (data) => {
        delete data[i.guildId][id];
      });
      await service.reconcile(i.guild, id, panel);
      return {
        content: `Ticket panel ${id} removed. Existing tickets remain tracked.`,
      };
    },
    manager,
  );
  registry.register(
    "ticket close",
    (i) => service.close(i, i.options.getString("reason", true)),
    publicPolicy,
  );
  registry.register("ticket claim", (i) => service.claim(i), publicPolicy);
  registry.register(
    "ticket unclaim",
    async (i) => {
      await service.access(i);
      await services.store.update("open_tickets.json", (data) => {
        const row = data[i.guildId][i.channelId];
        if (isClosed(row))
          throw new UserError(
            "Reopen this ticket before changing its assignment.",
          );
        if (row.claimed_by && String(row.claimed_by) !== i.user.id)
          throw new UserError(
            "Only the assigned staff member can unclaim this ticket.",
          );
        Object.assign(row, {
          claimed_by: null,
          status: "Open",
          updated_at: nowISO(),
          updated_by: i.user.id,
        });
      });
      return { content: "Ticket assignment removed." };
    },
    publicPolicy,
  );
  registry.register(
    "ticket priority",
    async (i) => {
      await service.access(i);
      const priority = i.options.getString("priority", true);
      if (!["Low", "Medium", "High", "Critical"].includes(priority))
        throw new UserError("Choose a valid priority.");
      await service.change(i.guildId, i.channelId, {
        priority,
        updated_at: nowISO(),
        updated_by: i.user.id,
      });
      return { content: `Ticket priority: ${priority}.` };
    },
    publicPolicy,
  );
  registry.register(
    "ticket status",
    async (i) => {
      const row = await service.access(i, "owner");
      return {
        embeds: [
          embed(
            "Ticket status",
            `Case: ${row.case_id || i.channel.name}\nStatus: ${row.status}\nPriority: ${row.priority || "Medium"}\nCreator: <@${row.owner_id}>\nClaimed by: ${row.claimed_by ? `<@${row.claimed_by}>` : "Unassigned"}${row.delete_at ? `\nDeletion: <t:${Math.floor(Date.parse(row.delete_at) / 1000)}:R>` : ""}`,
          ),
        ],
      };
    },
    publicPolicy,
  );
  registry.register(
    "ticket setstatus",
    async (i) => {
      const row = await service.access(i),
        status = i.options.getString("status", true),
        note = i.options.getString("note");
      if (
        ![
          "Open",
          "Claimed",
          "Pending",
          "Waiting for User",
          "Resolved",
          "Closed",
        ].includes(status)
      )
        throw new UserError("Choose a valid ticket status.");
      if (status === "Closed") return service.close(i, note || "");
      if (isClosed(row))
        throw new UserError(
          "Use /ticket reopen before changing a closed ticket’s status.",
        );
      await services.store.update("open_tickets.json", (data) => {
        const current = data[i.guildId]?.[i.channelId];
        if (!current || isClosed(current))
          throw new UserError(
            "This ticket was closed. Reopen it before changing its status.",
          );
        Object.assign(current, {
          status,
          status_note: text(note),
          ...(status === "Claimed" && !current.claimed_by
            ? { claimed_by: i.user.id }
            : {}),
          updated_at: nowISO(),
          updated_by: i.user.id,
        });
      });
      return { content: `Ticket status: ${status}.` };
    },
    publicPolicy,
  );
  registry.register(
    "ticket reopen",
    async (i) =>
      service.queue.run(`ticket:${i.guildId}:${i.channelId}`, async () => {
        const row = await service.access(i);
        if (!isClosed(row)) throw new UserError("This ticket is not closed.");
        await service.change(i.guildId, i.channelId, {
          status: "Open",
          delete_at: null,
          closed_at: null,
          closed_by: null,
          access_pending: true,
          updated_at: nowISO(),
          updated_by: i.user.id,
        });
        await i.channel.permissionOverwrites.edit(
          String(row.owner_id),
          {
            ViewChannel: true,
            SendMessages: true,
            ReadMessageHistory: true,
            AttachFiles: true,
          },
          { reason: `Ticket reopened by ${i.user.id}` },
        );
        await service.reconcile(i.guild, row.panel_id || null);
        await service.change(i.guildId, i.channelId, { access_pending: false });
        await service.refresh(i.guild, row.panel_id).catch(() => {});
        return { content: "Ticket reopened; automatic deletion cancelled." };
      }),
    publicPolicy,
  );
  for (const action of ["add", "remove"])
    registry.register(
      `ticket ${action}`,
      async (i) => {
        const row = await service.access(i),
          user = i.options.getUser("member", true);
        await i.guild.members.fetch(user.id);
        if (
          action === "remove" &&
          [String(row.owner_id), i.client.user.id].includes(user.id)
        )
          throw new UserError(
            "The ticket owner and Rallybit cannot be removed.",
          );
        if (action === "add")
          await i.channel.permissionOverwrites.edit(
            user.id,
            {
              ViewChannel: true,
              SendMessages: !isClosed(row),
              ReadMessageHistory: true,
              AttachFiles: true,
            },
            { reason: `Ticket member added by ${i.user.id}` },
          );
        else
          await i.channel.permissionOverwrites.edit(
            user.id,
            {
              ViewChannel: null,
              SendMessages: null,
              ReadMessageHistory: null,
              AttachFiles: null,
            },
            { reason: `Ticket member removed by ${i.user.id}` },
          );
        return {
          content: `Member ${action === "add" ? "added to" : "removed from"} the ticket. Independent staff-role access still applies.`,
        };
      },
      publicPolicy,
    );
  registry.register(
    "ticket rename",
    async (i) => {
      await service.access(i);
      const name = text(
        i.options
          .getString("name", true)
          .toLowerCase()
          .replace(/[^a-z0-9_-]/g, "-")
          .replace(/-+/g, "-"),
        90,
      );
      if (!name) throw new UserError("Enter a valid channel name.");
      await i.channel.setName(name, `Ticket renamed by ${i.user.id}`);
      return { content: `Ticket renamed to ${name}.` };
    },
    publicPolicy,
  );
  registry.register(
    "ticket transcript",
    async (i) => {
      await service.access(i, "owner");
      return {
        files: await service.transcript(
          i.channel,
          (await service.settings(i.guildId)).transcript_limit,
        ),
      };
    },
    publicPolicy,
  );
  services.components.register("rallybit:ticket", (i) => service.component(i));
  services.components.register("ticketopen", async (i) => {
    const [, guildId, panelId, optionId] = i.customId.split(":");
    if (guildId !== i.guildId)
      throw new UserError("This request belongs to another server.");
    await i.deferReply({ flags: MessageFlags.Ephemeral });
    const channel = await service.create(i, {
      panelId,
      optionId,
      reason: i.fields.getTextInputValue("reason"),
    });
    return privateReply(i, { content: `Ticket created: <#${channel.id}>.` });
  });
  return service;
}
