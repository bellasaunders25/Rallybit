<?php
require_once 'includes/functions.php';
require_once 'includes/overview_data.php';
check_login();
$guildId = (string)($_GET['id'] ?? '');
if (!preg_match('/^\d{15,22}$/', $guildId)) { header('Location: /dashboard/'); exit; }
$guilds = discord_api_request('/users/@me/guilds', (string)$_SESSION['access_token']) ?: [];
$guild = null;
foreach ($guilds as $candidate) {
    if ((string)($candidate['id'] ?? '') === $guildId && has_admin_permission((int)($candidate['permissions'] ?? 0))) { $guild = $candidate; break; }
}
if (!$guild) { http_response_code(403); exit('You do not have permission to manage this server.'); }
$botGuilds = load_json_data(BOT_GUILDS_FILE);
if (!isset($botGuilds[$guildId])) { header('Location: /dashboard/#servers'); exit; }
$serverChoices = [];
foreach ($guilds as $candidate) {
    $id = (string)($candidate['id'] ?? '');
    if (isset($botGuilds[$id]) && has_admin_permission((int)($candidate['permissions'] ?? 0))) $serverChoices[$id] = (string)$candidate['name'];
}
$_SESSION['dashboard_servers'] = $serverChoices;
$guildName = (string)$guild['name'];
$days = in_array((string)($_GET['days'] ?? '7'), ['7','14','30'], true) ? (int)($_GET['days'] ?? 7) : 7;
$audit = load_json_data(AUDIT_EVENTS_FILE);
$activity = server_overview_activity(is_array($audit[$guildId] ?? null) ? $audit[$guildId] : [], $days);
$chart = overview_chart_points($activity['series']);
$ticketsAll = load_json_data(OPEN_TICKETS_FILE);
$tickets = is_array($ticketsAll[$guildId] ?? null) ? $ticketsAll[$guildId] : [];
$activeTickets = count(array_filter($tickets, static fn($t) => is_array($t) && !in_array($t['status'] ?? 'open', ['closed','deleted'], true)));
$welcome = load_guild_file_settings(WELCOME_SETTINGS_FILE, $guildId);
$levels = load_guild_file_settings(LEVEL_SETTINGS_FILE, $guildId);
$logging = load_guild_file_settings(AUDIT_SETTINGS_FILE, $guildId, ['enabled'=>true]);
$automationsAll = load_json_data(AUTOMATION_SCHEDULES_FILE);
$automations = is_array($automationsAll[$guildId] ?? null) ? $automationsAll[$guildId] : [];
$panelsAll = load_json_data(TICKET_PANELS_FILE);
$panels = is_array($panelsAll[$guildId] ?? null) ? $panelsAll[$guildId] : [];
$modules = [
    ['Moderation', 'bi-shield', 'moderation', true, 'Available'],
    ['Automations', 'bi-lightning-charge', 'automation', count(array_filter($automations, static fn($s) => is_array($s) && !empty($s['enabled']))) > 0, 'Scheduled'],
    ['Welcome', 'bi-person-plus', 'welcome', !empty($welcome['welcome_enabled']), 'Enabled'],
    ['Tickets', 'bi-ticket-perforated', 'tickets', count($panels) > 0, 'Configured'],
    ['Levels & rewards', 'bi-bar-chart', 'levels', !empty($levels['enabled']), 'Enabled'],
    ['Logging', 'bi-file-earmark-text', 'logging', !empty($logging['enabled']), 'Enabled'],
];
$controlUrl = '/dashboard/control.php?id=' . rawurlencode($guildId);
$logsUrl = '/dashboard/audit_logs.php?id=' . rawurlencode($guildId);
?>
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Overview — <?=htmlspecialchars($guildName)?> — Rallybit</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/dashboard/style.css?v=11.0"><link rel="icon" type="image/svg+xml" href="/assets/brand/rallybit-mark.svg"><script src="/assets/theme.js?v=11.0"></script></head>
<body class="server-overview"><div class="dash-shell">
<?php render_dashboard_sidebar('overview', $guildId, $guildName); ?>
<main class="dash-main">
  <header class="server-overview-heading"><div><h1>Overview</h1><p>Manage your Discord server, monitor activity, and configure Rallybit modules.</p></div>
    <form method="get" class="overview-period"><input type="hidden" name="id" value="<?=htmlspecialchars($guildId)?>"><i class="bi bi-calendar3" aria-hidden="true"></i><label class="sr-only" for="overview-days">Activity period</label><select name="days" id="overview-days" data-overview-period><?php foreach ([7,14,30] as $period): ?><option value="<?=$period?>" <?=$days === $period ? 'selected' : ''?>>Last <?=$period?> days</option><?php endforeach; ?></select><noscript><button type="submit">Apply</button></noscript></form>
  </header>
  <section class="server-metrics" aria-label="Server statistics">
    <?php foreach ([['bi-people','Members',number_format((int)($botGuilds[$guildId]['member_count'] ?? 0)),'Current server members'],['bi-shield','Moderation actions',number_format(count($activity['moderation'])),"Recorded in the last {$days} days"],['bi-ticket-perforated','Open tickets',number_format($activeTickets),'Currently open requests'],['bi-bar-chart','Logged events',number_format(count($activity['events'])),"Retained events over {$days} days"]] as [$icon,$label,$value,$note]): ?>
    <article><span><i class="bi <?=$icon?>" aria-hidden="true"></i> <?=$label?></span><strong><?=$value?></strong><small><?=$note?></small></article>
    <?php endforeach; ?>
  </section>
  <section class="server-overview-grid">
    <article class="workspace-panel activity-panel" id="server-activity"><div class="workspace-panel-title"><div><h2>Server Activity</h2><p>Recorded server events per day · UTC</p></div></div>
      <svg class="server-activity-chart" viewBox="0 0 625 255" role="img" aria-labelledby="activity-chart-title"><title id="activity-chart-title">Recorded events per day over the last <?=$days?> days</title><defs><linearGradient id="activity-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--primary)" stop-opacity=".23"/><stop offset="100%" stop-color="var(--primary)" stop-opacity=".02"/></linearGradient></defs>
        <?php for ($line = 0; $line <= 4; $line++): $y = 35 + $line * 43.75; ?><line x1="52" x2="600" y1="<?=$y?>" y2="<?=$y?>" class="chart-gridline"/><text x="38" y="<?=$y + 4?>" text-anchor="end"><?=number_format($chart['max'] * (4 - $line) / 4, $chart['max'] < 4 ? 1 : 0)?></text><?php endfor; ?>
        <polygon points="52,210 <?=$chart['path']?> 600,210" fill="url(#activity-fill)"/><polyline points="<?=$chart['path']?>" fill="none" stroke="var(--primary)" stroke-width="2.5" stroke-linejoin="round"/>
        <?php $seriesKeys = array_keys($activity['series']); foreach ($chart['points'] as $i => [$x,$y]): ?><circle cx="<?=$x?>" cy="<?=$y?>" r="3.5" fill="var(--primary)"><title><?=htmlspecialchars($seriesKeys[$i])?>: <?=$activity['series'][$seriesKeys[$i]]?> events</title></circle><?php if ($i === 0 || $i === count($seriesKeys) - 1 || $i % max(1, (int)ceil($days / 7)) === 0): ?><text x="<?=$x?>" y="240" text-anchor="middle"><?=gmdate('M j', strtotime($seriesKeys[$i]))?></text><?php endif; endforeach; ?>
      </svg>
      <p class="chart-footnote">Based on retained audit history. This is not a count of every message sent.</p>
    </article>
    <article class="workspace-panel modules-panel"><div class="workspace-panel-title"><div><h2>Modules</h2><p>Configure Rallybit features for this server.</p></div></div><div class="overview-module-list">
      <?php foreach ($modules as [$label,$icon,$target,$enabled,$state]): ?><a href="<?=htmlspecialchars($controlUrl . '#' . $target)?>"><i class="bi <?=$icon?>" aria-hidden="true"></i><span><?=$label?></span><small><?=$enabled ? $state : 'Set up'?></small><i class="bi bi-chevron-right module-chevron" aria-hidden="true"></i></a><?php endforeach; ?>
    </div><a class="overview-all-modules" href="<?=htmlspecialchars($controlUrl)?>">Manage all modules <i class="bi bi-arrow-right"></i></a></article>
  </section>
  <section class="server-overview-bottom">
    <article class="workspace-panel"><div class="workspace-panel-title"><h2>Moderation Log</h2><a href="<?=htmlspecialchars($logsUrl . '&type=moderation')?>">View All <i class="bi bi-arrow-right"></i></a></div><?php render_overview_events($activity['moderation']); ?></article>
    <article class="workspace-panel"><div class="workspace-panel-title"><h2>Recent Actions</h2><a href="<?=htmlspecialchars($logsUrl)?>">View All <i class="bi bi-arrow-right"></i></a></div><?php render_overview_events($activity['events']); ?></article>
  </section>
</main></div><script>document.querySelector('[data-overview-period]').addEventListener('change', function () { this.form.requestSubmit(); });</script></body></html>
