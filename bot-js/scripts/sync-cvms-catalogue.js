// Generated command artifacts share one definition; existing command contracts
// remain unchanged except the optional voice-report switch.
import { readFile, writeFile } from "node:fs/promises";
import { CVMS_COMMAND } from "../src/cvms-command.js";
const catalogueURL = new URL(
    "../../docs/command-catalog.json",
    import.meta.url,
  ),
  contractURL = new URL("../contracts/commands.json", import.meta.url);
const catalogue = JSON.parse(await readFile(catalogueURL, "utf8")),
  contract = JSON.parse(await readFile(contractURL, "utf8"));
const types = {
  3: "string",
  4: "integer",
  5: "boolean",
  6: "user",
  7: "channel",
  8: "role",
};
const parameter = (option) => ({
  name: option.name,
  description: option.description,
  required: Boolean(option.required),
  type: types[option.type],
  default: option.required ? "..." : option.type === 5 ? false : null,
  min_value: option.min_value ?? null,
  max_value: option.max_value ?? null,
  min_length: option.min_length ?? null,
  max_length: option.max_length ?? null,
  choices: option.choices ?? [],
});
catalogue.commands = catalogue.commands.filter((row) => row.root !== "cvms");
for (const sub of CVMS_COMMAND.options)
  catalogue.commands.push({
    path: `cvms ${sub.name}`,
    root: "cvms",
    description: sub.description,
    group_descriptions: [CVMS_COMMAND.description],
    guild_only: true,
    default_permissions: null,
    parameters: sub.options.map(parameter),
  });
const voice = {
  type: 5,
  name: "voice",
  description: "Mark this as a report about your current voice channel.",
  required: false,
};
const report = catalogue.commands.find((row) => row.path === "report user");
if (!report.parameters.some((row) => row.name === "voice"))
  report.parameters.splice(
    report.parameters.findIndex((row) => row.name === "private"),
    0,
    parameter(voice),
  );
contract.registration = contract.registration.filter(
  (row) => row.name !== "cvms",
);
contract.registration.push(CVMS_COMMAND);
const reportRegistration = contract.registration
  .find((row) => row.name === "report")
  .options.find((row) => row.name === "user");
if (!reportRegistration.options.some((row) => row.name === "voice"))
  reportRegistration.options.splice(
    reportRegistration.options.findIndex((row) => row.name === "private"),
    0,
    voice,
  );
contract.paths = catalogue.commands.map((row) => row.path).sort();
for (const object of [catalogue, contract]) {
  object.root_count = contract.registration.length;
  object.command_count = catalogue.commands.length;
}
await writeFile(catalogueURL, JSON.stringify(catalogue, null, 2) + "\n");
await writeFile(contractURL, JSON.stringify(contract, null, 2) + "\n");
console.log(
  `Generated ${catalogue.command_count} command paths in ${catalogue.root_count} root groups.`,
);
