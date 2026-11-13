const str = (name, description, required = false, extra = {}) => ({
  type: 3,
  name,
  description,
  required,
  ...extra,
});
const bool = (name, description, required = false) => ({
  type: 5,
  name,
  description,
  required,
});
const channel = (name, description, required, types) => ({
  type: 7,
  name,
  description,
  required,
  channel_types: types,
});
const integer = (name, description, min, max) => ({
  type: 4,
  name,
  description,
  required: false,
  min_value: min,
  max_value: max,
});
const member = {
  type: 6,
  name: "member",
  description: "The member concerned.",
  required: true,
};
const choice = (values) => values.map((value) => ({ name: value, value }));
const action = (values) =>
  str("action", "Operation to perform.", true, { choices: choice(values) });
const privateOption = bool("private", "Show command replies only to you.");
const sub = (name, description, options = []) => ({
  type: 1,
  name,
  description,
  options: [...options, privateOption],
});
export const CVMS_COMMAND = {
  type: 1,
  name: "cvms",
  description: "Free local Community Voice Moderation Services beta.",
  dm_permission: false,
  contexts: [0],
  integration_types: [0],
  default_member_permissions: null,
  options: [
    sub(
      "status",
      "Show CVMS configuration, current coverage and local engine readiness.",
    ),
    sub("beta", "Developer: admit or remove this server from the free beta.", [
      bool("enabled", "Whether this server has beta access.", true),
    ]),
    sub(
      "setup",
      "Configure CVMS. It remains review-only and requires member consent.",
      [
        channel(
          "log_channel",
          "A private staff text channel for short-lived cases.",
          true,
          [0],
        ),
        bool("enabled", "Enable CVMS for this beta server.", true),
        str("mode", "How eligible sessions begin.", false, {
          choices: choice(["manual", "watch", "always"]),
        }),
        integer(
          "threshold",
          "Minimum approximate recognition score; not proof of misconduct.",
          50,
          99,
        ),
        integer(
          "retention_hours",
          "Keep staff case excerpts for this many hours.",
          1,
          24,
        ),
      ],
    ),
    sub("channel", "Manage the voice channels CVMS may monitor.", [
      action(["add", "remove", "list"]),
      channel(
        "channel",
        "A standard voice channel in this server.",
        false,
        [2],
      ),
    ]),
    sub(
      "rule",
      "Manage literal phrase rules; matches always require human review.",
      [
        action(["add", "remove", "list"]),
        str("phrase", "Phrase to flag, not a regular expression.", false, {
          min_length: 3,
          max_length: 120,
        }),
        str("rule_id", "ID of the rule to remove.", false, { max_length: 32 }),
      ],
    ),
    sub("watch", "Manage time-limited, consent-based voice watches.", [
      action(["add", "remove", "list"]),
      { ...member, required: false },
      integer("minutes", "Watch duration, with no secret monitoring.", 5, 1440),
      str("reason", "Moderation reason for the watch.", false, {
        min_length: 3,
        max_length: 500,
      }),
    ]),
    sub("reports", "Enable or disable consent-based voice-report sessions.", [
      bool("enabled", "Enable the voice reporting integration.", true),
    ]),
    sub(
      "consent",
      "Read the disclosure and consent for 24 hours, or withdraw immediately.",
      [
        bool(
          "enabled",
          "True opens the disclosure; false withdraws consent.",
          true,
        ),
      ],
    ),
    sub(
      "start",
      "Start a disclosed session after all present members consent.",
      [
        channel(
          "channel",
          "Voice channel; omitted uses your current channel.",
          false,
          [2],
        ),
        integer(
          "minutes",
          "Timed session length; omit when using 24/7 monitoring.",
          5,
          60,
        ),
        bool("continuous", "Monitor this channel 24/7 when consent allows (Manage Server)."),
      ],
    ),
    sub(
      "stop",
      "Stop monitoring; available to current participants and authorised staff.",
    ),
    sub("report", "Report a member in your current configured voice channel.", [
      member,
      str("reason", "Explain what happened; a report is not proof.", true, {
        min_length: 3,
        max_length: 1000,
      }),
    ]),
    sub("cases", "Staff: list recent CVMS cases privately."),
    sub(
      "review",
      "Staff: mark a case reviewed or dismiss it without automatic punishment.",
      [
        str("case_id", "Case ID.", true, { max_length: 32 }),
        action(["confirm", "dismiss"]),
      ],
    ),
    sub(
      "privacy",
      "Explain voice consent, local processing and evidence retention.",
    ),
  ],
};
