const COMMAND_CATEGORIES = [
  { id: 'getting-started', label: 'Getting started', roots: ['about', 'help', 'support', 'dashboard', 'settings'] },
  { id: 'activity', label: 'Activity & profiles', roots: ['activitycheck', 'endactivitycheck', 'setactivitytext', 'setreactor', 'setping', 'setwinner', 'setduration', 'setperm', 'setlogs', 'setauto', 'startauto', 'stopauto', 'setmode', 'setbuttontext', 'leaderboard', 'userleaderboard', 'profile', 'inactive', 'checkinactive', 'afk'] },
  { id: 'community', label: 'Community', roots: ['quiz', 'community', 'giveaway', 'automation'] },
  { id: 'moderation', label: 'Moderation', roots: ['mod', 'role', 'report', 'review', 'channel', 'snipe', 'editsnipe', 'clearsnipes'] },
  { id: 'tickets', label: 'Tickets', roots: ['ticket'] },
  { id: 'staff', label: 'Staff management', roots: ['staff', 'clockin', 'clockout', 'shift', 'timesheet', 'staffhours', 'duty', 'forceclockout', 'loa', 'roa', 'break', 'shifts'] },
  { id: 'members', label: 'Members & messages', roots: ['level', 'autorole', 'reactionrole', 'verification', 'welcome', 'goodbye', 'invites'] },
  { id: 'security', label: 'Security & logging', roots: ['logs', 'security', 'security-trap', 'security-agegate', 'security-modules', 'security-antispam', 'security-automod', 'security-trust', 'security-logs', 'security-antibot'] },
  { id: 'premium', label: 'Premium previews', roots: ['premium', 'insights', 'case', 'backup', 'network', 'prettfy'] },
  { id: 'developer', label: 'Developer tools', roots: ['addadmin', 'banuser', 'banserver', 'syncguilds'] }
];

const MANAGE_SERVER_PREFIXES = [
  'automation add', 'automation remove', 'automation list', 'automation toggle', 'automation run', 'automation clear',
  'giveaway start', 'giveaway end', 'giveaway reroll', 'giveaway settings',
  'quiz setup', 'quiz auto', 'quiz pingrole',
  'community pingrole', 'logs ', 'welcome ', 'goodbye ',
  'insights ', 'backup ', 'level setup', 'level setxp',
  'security overview', 'security audit', 'security history', 'security-agegate ',
  'security-antibot', 'security-antispam', 'security-automod ', 'security-logs channel',
  'security-logs toggle', 'security-logs events', 'security-modules ', 'security-trap ', 'security-trust add',
  'security-trust remove', 'security-trust list'
];

const ADMINISTRATOR_PATHS = new Set([
  'setlogs', 'setactivitytext', 'setreactor', 'setping', 'setwinner', 'setduration',
  'setperm', 'setauto', 'startauto', 'stopauto', 'setmode', 'setbuttontext',
  'security system', 'security lockdown', 'security panic', 'security clearinvites',
  'security-trust adminbypass', 'security-logs ownerdm'
]);

const MANAGE_ROLES_PREFIXES = [
  'role ', 'autorole add', 'autorole remove', 'autorole clear',
  'reactionrole add', 'reactionrole remove', 'level reward add', 'level reward remove',
  'verification disable', 'security quarantine', 'security release'
];

const TICKET_SETUP_PREFIXES = [
  'ticket setup', 'ticket settings', 'ticket panel create', 'ticket panel add-option',
  'ticket panel remove-option', 'ticket panel delete'
];

const STAFF_PATH_PREFIXES = [
  'clockin', 'clockout', 'shift', 'timesheet', 'duty', 'break start', 'break end',
  'loa request', 'loa status', 'loa cancel', 'roa request', 'roa status', 'roa cancel'
];

const HR_PATH_PREFIXES = [
  'staffhours', 'forceclockout', 'loa setstatus', 'loa settings', 'roa setstatus',
  'roa settings', 'shifts list', 'shifts remove', 'shifts clear'
];

function startsWithAny(value, prefixes) {
  return prefixes.some(prefix => value === prefix || value.startsWith(prefix));
}

function categoryFor(root) {
  return COMMAND_CATEGORIES.find(category => category.roots.includes(root))
    || { id: 'other', label: 'Other commands', roots: [] };
}

function planFor(path) {
  if (path.startsWith('network ')) return 'Network';
  if (path === 'prettfy' || path.startsWith('backup ') || path === 'insights export' || path === 'case export' || path.startsWith('staff ')) return 'Pro';
  if (path.startsWith('case ') || path === 'insights overview') return 'Community';
  return 'Free';
}

function accessFor(path) {
  if (['addadmin', 'banuser', 'banserver', 'syncguilds'].includes(path)) return 'Rallybit developer only';
  if (path.startsWith('network ')) return 'Server owner with Network';
  if (ADMINISTRATOR_PATHS.has(path)) return 'Administrator';
  if (startsWithAny(path, TICKET_SETUP_PREFIXES)) return 'Manage Channels';
  if (path === 'verification setup') return 'Manage Roles and Manage Channels';
  if (startsWithAny(path, MANAGE_ROLES_PREFIXES)) return 'Manage Roles';
  if (path.startsWith('channel ')) return 'Manage Messages';
  if (path === 'security purgeuser') return 'Ban Members';
  if (path === 'prettfy') return 'Manage Channels and Manage Roles';
  if (path === 'quiz start' || path === 'quiz stop' || path === 'community pulse' || path === 'community stoppulse' || path === 'clearsnipes') return 'Manage Messages';
  if (startsWithAny(path, HR_PATH_PREFIXES)) return 'Configured HR role';
  if (startsWithAny(path, STAFF_PATH_PREFIXES) || path === 'loa end' || path === 'roa end' || path.startsWith('staff ')) return 'Configured staff role';
  if (path.startsWith('case ')) return 'Configured staff role';
  if (path === 'mod panel') return 'Configured moderation role';
  if (path === 'mod warn' || path === 'mod warnings' || path === 'mod unwarn') return 'Configured warning role';
  if (path === 'mod unban') return 'Configured ban role';
  if (path.startsWith('report settings') || path.startsWith('report list') || path.startsWith('report claim') || path.startsWith('report setstatus') || path.startsWith('report close') || path.startsWith('report reopen') || path.startsWith('report history')) return 'Configured report staff role';
  if (path === 'activitycheck' || path === 'endactivitycheck') return 'Configured activity role or Administrator';
  if (path.startsWith('ticket claim') || path.startsWith('ticket unclaim') || path.startsWith('ticket priority') || path.startsWith('ticket setstatus') || path.startsWith('ticket reopen') || path.startsWith('ticket add') || path.startsWith('ticket remove') || path.startsWith('ticket rename') || path.startsWith('ticket transcript')) return 'Configured ticket support role';
  if (startsWithAny(path, MANAGE_SERVER_PREFIXES)) return 'Manage Server';
  return 'Everyone';
}

function optionPlaceholder(option) {
  if (option.choices && option.choices.length) return String(option.choices[0].value);
  if (option.type === 'user') return '@member';
  if (option.type === 'role') return '@role';
  if (option.type === 'channel') return '#channel';
  if (option.type === 'boolean') return option.name === 'private' ? 'true' : 'true';
  if (option.type === 'integer') {
    if (option.name.includes('star')) return '5';
    if (option.name.includes('level')) return '10';
    if (option.name.includes('amount') || option.name.includes('count') || option.name.includes('limit')) return '10';
    return String(option.min_value != null ? option.min_value : 1);
  }

  const name = option.name.toLowerCase();
  if (name.includes('reason')) return 'Clear reason';
  if (name.includes('message_id') || name === 'messageid') return '123456789012345678';
  if (name.endsWith('_id') || name === 'id') return '123456789012345678';
  if (name.includes('date') || name.includes('until') || name.includes('expires')) return '2026-09-30';
  if (name.includes('duration')) return '1h';
  if (name.includes('url') || name.includes('image')) return 'https://example.com/image.png';
  if (name.includes('emoji')) return '✅';
  if (name.includes('name') || name.includes('title') || name.includes('label')) return 'Example name';
  if (name.includes('description')) return 'A clear description';
  if (name.includes('message') || name.includes('text') || name.includes('content')) return 'Your message';
  if (name.includes('status')) return 'active';
  if (name.includes('type')) return 'staff';
  return 'value';
}

function usageFor(command, includeExamples = false) {
  const options = command.parameters || [];
  const rendered = options.map(option => {
    const value = includeExamples ? optionPlaceholder(option) : `<${option.type}>`;
    const named = `${option.name}:${value}`;
    return option.required ? named : `[${named}]`;
  });
  return `/${command.path}${rendered.length ? ` ${rendered.join(' ')}` : ''}`;
}

function formatType(type) {
  const labels = { string: 'Text', integer: 'Whole number', boolean: 'True or false', user: 'Server member', role: 'Server role', channel: 'Server channel' };
  return labels[type] || type;
}

function formatRules(option) {
  const rules = [];
  if (option.choices && option.choices.length) {
    rules.push(`Choose: ${option.choices.map(choice => choice.name).join(', ')}`);
  }
  if (option.min_value != null || option.max_value != null) {
    if (option.min_value != null && option.max_value != null) rules.push(`${option.min_value}–${option.max_value}`);
    else if (option.min_value != null) rules.push(`Minimum ${option.min_value}`);
    else rules.push(`Maximum ${option.max_value}`);
  }
  if (option.min_length != null || option.max_length != null) {
    if (option.min_length != null && option.max_length != null) rules.push(`${option.min_length}–${option.max_length} characters`);
    else if (option.min_length != null) rules.push(`At least ${option.min_length} characters`);
    else rules.push(`Up to ${option.max_length} characters`);
  }
  if (option.default !== null && option.default !== undefined) rules.push(`Default: ${String(option.default)}`);
  return rules.join(' · ') || 'Discord validates this option';
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function addBadge(holder, text, kind) {
  holder.append(el('span', `command-badge ${kind || ''}`.trim(), text));
}

function renderOptions(command) {
  const wrap = el('div', 'option-table-wrap');
  const table = el('table', 'option-table');
  const caption = el('caption', '', 'Options');
  table.append(caption);
  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  ['Option', 'Required', 'What to enter', 'Rules'].forEach(label => headRow.append(el('th', '', label)));
  head.append(headRow);
  table.append(head);
  const body = document.createElement('tbody');
  (command.parameters || []).forEach(option => {
    const row = document.createElement('tr');
    const optionCell = document.createElement('td');
    optionCell.append(el('code', '', option.name));
    row.append(optionCell);
    row.append(el('td', '', option.required ? 'Yes' : 'No'));
    const descriptionCell = document.createElement('td');
    descriptionCell.append(el('span', 'option-type', formatType(option.type)));
    descriptionCell.append(document.createTextNode(option.description ? ` — ${option.description}` : ''));
    row.append(descriptionCell);
    row.append(el('td', 'option-rules', formatRules(option)));
    body.append(row);
  });
  table.append(body);
  wrap.append(table);
  return wrap;
}

function renderCommand(command) {
  const details = el('details', 'command-reference');
  details.id = `cmd-${command.path.replace(/\s+/g, '-')}`;
  const category = categoryFor(command.root);
  const plan = planFor(command.path);
  const access = accessFor(command.path);
  details.dataset.category = category.id;
  details.dataset.search = [
    command.path, command.description, category.label, plan, access,
    ...(command.parameters || []).flatMap(option => [option.name, option.description, option.type])
  ].filter(Boolean).join(' ').toLowerCase();

  const summary = document.createElement('summary');
  const titleBox = el('span', 'command-summary-main');
  titleBox.append(el('code', 'command-name', `/${command.path}`));
  titleBox.append(el('span', 'command-one-line', command.description || 'Rallybit command.'));
  const badges = el('span', 'command-summary-badges');
  addBadge(badges, plan, `plan-${plan.toLowerCase()}`);
  addBadge(badges, access, access === 'Everyone' ? 'access-everyone' : 'access-restricted');
  summary.append(titleBox, badges);
  details.append(summary);

  const body = el('div', 'command-reference-body');
  const purpose = el('section', 'command-purpose');
  purpose.append(el('h3', '', 'What it does'));
  purpose.append(el('p', '', command.description || 'Runs this Rallybit command.'));
  body.append(purpose);

  const usageBlock = el('section', 'command-usage');
  usageBlock.append(el('h3', '', 'Usage'));
  const usageCode = el('code', '', usageFor(command));
  usageBlock.append(usageCode);
  const syntaxNote = el('p', 'syntax-note', 'Required options are shown normally. Optional options are wrapped in square brackets.');
  usageBlock.append(syntaxNote);
  body.append(usageBlock);

  const steps = el('section', 'command-howto');
  steps.append(el('h3', '', 'How to use it'));
  const list = document.createElement('ol');
  const commandChoice = command.path.includes(' ')
    ? `Type /${command.root}, then choose ${command.path.slice(command.root.length + 1)}.`
    : `Type /${command.path} in a server channel.`;
  list.append(el('li', '', commandChoice));
  const required = (command.parameters || []).filter(option => option.required);
  const optional = (command.parameters || []).filter(option => !option.required && option.name !== 'private');
  if (required.length) {
    list.append(el('li', '', `Complete the required ${required.length === 1 ? 'option' : 'options'}: ${required.map(option => option.name).join(', ')}. Discord will check that each value is the correct type.`));
  } else {
    list.append(el('li', '', 'Review the command before sending it. This command has no required options.'));
  }
  if (optional.length) {
    list.append(el('li', '', `Add any optional details you need (${optional.map(option => option.name).join(', ')}), then choose whether the reply should be private and submit the command.`));
  } else {
    list.append(el('li', '', 'Set private to True if only you should see the response, then submit the command.'));
  }
  list.append(el('li', '', `Rallybit checks your access and server setup, then ${(command.description || 'runs the command').replace(/^./, character => character.toLowerCase())}`));
  steps.append(list);
  body.append(steps);

  body.append(renderOptions(command));

  const requirements = el('section', 'command-requirements');
  requirements.append(el('h3', '', 'Access and requirements'));
  const requirementGrid = el('dl', 'requirement-grid');
  [['Plan', plan], ['Who can run it', access], ['Where', 'A Discord server'], ['Reply visibility', 'Public by default; private:true is user-only']].forEach(([term, value]) => {
    requirementGrid.append(el('dt', '', term), el('dd', '', value));
  });
  requirements.append(requirementGrid);
  const requirementNote = el('p', 'requirement-note', access === 'Everyone'
    ? 'No special member permission is required, although the server feature may need to be configured first.'
    : `Discord or Rallybit must recognise the listed access: ${access}. The bot also needs the relevant channel, role, or moderation permission.`);
  requirements.append(requirementNote);
  body.append(requirements);

  const example = el('section', 'command-example');
  const exampleHeading = el('div', 'example-heading');
  exampleHeading.append(el('h3', '', 'Example'));
  const copyButton = el('button', 'copy-command', 'Copy example');
  copyButton.type = 'button';
  exampleHeading.append(copyButton);
  const exampleText = usageFor(command, true);
  const pre = document.createElement('pre');
  pre.append(el('code', '', exampleText));
  example.append(exampleHeading, pre);
  const note = el('p', 'example-note', 'In Discord, options appear as separate fields. Replace the example values with the member, role, channel, text, or number you need.');
  example.append(note);
  copyButton.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(exampleText);
      copyButton.textContent = 'Copied';
      setTimeout(() => { copyButton.textContent = 'Copy example'; }, 1600);
    } catch {
      copyButton.textContent = 'Select the example';
    }
  });
  body.append(example);
  details.append(body);
  return details;
}

async function initialiseSidebar() {
  const holder = document.querySelector('[data-doc-sidebar]');
  if (holder) {
    try {
      const response = await fetch('/docs/includes/sidebar.html?v=7.0');
      if (!response.ok) throw new Error('Sidebar unavailable');
      holder.outerHTML = await response.text();
    } catch (error) {
      console.warn(error);
    }
  }
  const current = location.pathname.replace(/index\.php$/, '');
  document.querySelectorAll('.docs-nav a').forEach(link => {
    const path = new URL(link.href).pathname.replace(/index\.php$/, '');
    if (path === current) link.classList.add('active');
  });

  const input = document.querySelector('[data-doc-search]');
  if (input) {
    input.addEventListener('input', () => {
      const query = input.value.toLowerCase().trim();
      document.querySelectorAll('[data-searchable]').forEach(item => {
        item.hidden = Boolean(query) && !item.textContent.toLowerCase().includes(query);
      });
    });
  }
}

async function initialiseCommandCatalogue() {
  const holder = document.querySelector('[data-command-catalog]');
  if (!holder) return;
  const search = document.querySelector('[data-command-search]');
  const filters = document.querySelector('[data-command-filters]');
  const count = document.querySelector('[data-command-count]');
  const empty = document.querySelector('[data-command-empty]');
  const clear = document.querySelector('[data-command-clear]');

  try {
    const response = await fetch('/docs/command-catalog.json?v=9.0');
    if (!response.ok) throw new Error(`Catalogue request failed with ${response.status}`);
    const catalogue = await response.json();
    const commands = Array.isArray(catalogue.commands) ? catalogue.commands : [];
    holder.replaceChildren();

    let selectedCategory = 'all';
    const categoryMap = new Map();
    commands.forEach(command => {
      const category = categoryFor(command.root);
      if (!categoryMap.has(category.id)) categoryMap.set(category.id, { ...category, commands: [] });
      categoryMap.get(category.id).commands.push(command);
    });

    const filterDefinitions = [{ id: 'all', label: 'All commands', commands: commands }, ...COMMAND_CATEGORIES.filter(category => categoryMap.has(category.id)).map(category => categoryMap.get(category.id))];
    filterDefinitions.forEach((category, index) => {
      const button = el('button', `command-filter${index === 0 ? ' active' : ''}`, `${category.label} (${category.commands.length})`);
      button.type = 'button';
      button.dataset.category = category.id;
      button.setAttribute('aria-pressed', index === 0 ? 'true' : 'false');
      filters.append(button);
    });

    [...COMMAND_CATEGORIES, { id: 'other', label: 'Other commands', roots: [] }].forEach(definition => {
      const category = categoryMap.get(definition.id);
      if (!category) return;
      const section = el('section', 'command-category');
      section.dataset.categorySection = category.id;
      const heading = el('div', 'command-category-heading');
      const title = el('h2', '', category.label);
      title.id = `category-${category.id}`;
      heading.append(title, el('span', '', `${category.commands.length} ${category.commands.length === 1 ? 'command' : 'commands'}`));
      section.append(heading);
      const list = el('div', 'command-reference-list');
      category.commands.forEach(command => list.append(renderCommand(command)));
      section.append(list);
      holder.append(section);
    });

    const applyFilter = () => {
      const query = (search?.value || '').trim().toLowerCase();
      let visible = 0;
      holder.querySelectorAll('.command-reference').forEach(command => {
        const matchesCategory = selectedCategory === 'all' || command.dataset.category === selectedCategory;
        const matchesSearch = !query || command.dataset.search.includes(query);
        command.hidden = !(matchesCategory && matchesSearch);
        if (!command.hidden) {
          visible += 1;
          if (query) command.open = true;
        }
      });
      holder.querySelectorAll('[data-category-section]').forEach(section => {
        section.hidden = !Array.from(section.querySelectorAll('.command-reference')).some(command => !command.hidden);
      });
      count.textContent = `${visible} of ${commands.length} ${commands.length === 1 ? 'command' : 'commands'}`;
      empty.hidden = visible !== 0;
      clear.hidden = !query && selectedCategory === 'all';
    };

    filters.addEventListener('click', event => {
      const button = event.target.closest('[data-category]');
      if (!button) return;
      selectedCategory = button.dataset.category;
      filters.querySelectorAll('[data-category]').forEach(candidate => {
        const active = candidate === button;
        candidate.classList.toggle('active', active);
        candidate.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      applyFilter();
    });
    search?.addEventListener('input', applyFilter);
    clear?.addEventListener('click', () => {
      if (search) search.value = '';
      selectedCategory = 'all';
      filters.querySelectorAll('[data-category]').forEach(candidate => {
        const active = candidate.dataset.category === 'all';
        candidate.classList.toggle('active', active);
        candidate.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      applyFilter();
      search?.focus();
    });
    applyFilter();

    if (location.hash) {
      const target = document.getElementById(location.hash.slice(1));
      if (target?.classList.contains('command-reference')) {
        target.open = true;
        requestAnimationFrame(() => target.scrollIntoView({ block: 'start' }));
      }
    }
  } catch (error) {
    console.error(error);
    holder.replaceChildren();
    const failure = el('div', 'catalog-error');
    failure.append(el('h3', '', 'The command reference could not be loaded.'));
    failure.append(el('p', '', 'Refresh this page to try again. Rallybit commands are not affected.'));
    holder.append(failure);
    count.textContent = 'Reference unavailable';
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  await initialiseSidebar();
  await initialiseCommandCatalogue();
});
