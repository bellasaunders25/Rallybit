<?php
require_once 'includes/functions.php';
check_login();
check_rate_limit(45, 60);

$guildId = preg_replace('/\D/', '', (string)($_GET['id'] ?? ''));
if ($guildId === '') { header('Location: index.php'); exit; }
$guildName = trim((string)($_GET['name'] ?? 'Server'));
$guilds = discord_api_request('/users/@me/guilds', (string)$_SESSION['access_token']) ?: [];
$allowed = is_bot_admin();
foreach ($guilds as $guild) {
    if ((string)($guild['id'] ?? '') === $guildId && has_admin_permission((int)($guild['permissions'] ?? 0))) {
        $allowed = true;
        $guildName = (string)($guild['name'] ?? $guildName);
        break;
    }
}
if (!$allowed) { http_response_code(403); exit('You do not have permission to view these logs.'); }

$eventLabels = [
    'commands'=>'Commands','configuration'=>'Configuration','moderation'=>'Moderation','members'=>'Members',
    'messages'=>'Messages','roles'=>'Roles','channels'=>'Channels','voice'=>'Voice','tickets'=>'Tickets',
    'reports'=>'Reports','staff'=>'Staff operations','security'=>'Security',
];
$allEvents = load_json_data(AUDIT_EVENTS_FILE);
$events = is_array($allEvents[$guildId] ?? null) ? array_values($allEvents[$guildId]) : [];
usort($events, static fn($a,$b) => strcmp((string)($b['timestamp'] ?? ''), (string)($a['timestamp'] ?? '')));
$filter = strtolower(trim((string)($_GET['type'] ?? 'all')));
if ($filter !== 'all' && !isset($eventLabels[$filter])) $filter = 'all';
$visibleEvents = $filter === 'all' ? $events : array_values(array_filter($events, static fn($row) => ($row['type'] ?? '') === $filter));
$lastDay = count(array_filter($events, static fn($row) => (($stamp = strtotime((string)($row['timestamp'] ?? ''))) !== false) && $stamp >= time() - 86400));
$activeTypes = count(array_unique(array_filter(array_map(static fn($row) => (string)($row['type'] ?? ''), $events))));
$settingsDefaults = ['enabled'=>true,'default_channel_id'=>null,'channel_ids'=>[],'enabled_events'=>array_fill_keys(array_keys($eventLabels),true)];
$auditSettings = load_guild_file_settings(AUDIT_SETTINGS_FILE, $guildId, $settingsDefaults);
$configuredChannels = array_filter(array_unique(array_merge([$auditSettings['default_channel_id'] ?? null], array_values($auditSettings['channel_ids'] ?? []))));

$activityData = load_json_data('activity_audit_logs.json');
$activityLogs = is_array($activityData[$guildId] ?? null) ? array_values($activityData[$guildId]) : [];
usort($activityLogs, static fn($a,$b) => strcmp((string)($b['start_time'] ?? $b['timestamp'] ?? ''), (string)($a['start_time'] ?? $a['timestamp'] ?? '')));
?>
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Action logs — <?=htmlspecialchars($guildName)?> — Rallybit</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/dashboard/style.css?v=6.2">
<link rel="icon" href="/favicon.ico" sizes="any">
</head>
<body>
<div class="dash-shell">
<?php render_dashboard_sidebar('logs', $guildId, $guildName); ?>
<main class="dash-main">
<header class="dash-header compact">
<div><a class="back-link" href="control.php?id=<?=urlencode($guildId)?>&name=<?=urlencode($guildName)?>#logging"><i class="bi bi-arrow-left"></i> Logging settings</a><span class="kicker">Server history</span><h1><?=htmlspecialchars($guildName)?> action logs</h1><p>One searchable history of commands, configuration, moderation, members, messages, roles, channels, voice, tickets, reports, staff and security.</p></div>
<span class="access-chip"><i class="bi bi-broadcast"></i> <?=!empty($auditSettings['enabled'])?'Logging enabled':'Logging paused'?></span>
</header>

<section class="audit-stat-strip" aria-label="Logging summary">
<article><span>Total events</span><strong><?=number_format(count($events))?></strong></article>
<article><span>Last 24 hours</span><strong><?=number_format($lastDay)?></strong></article>
<article><span>Active categories</span><strong><?=$activeTypes?> / <?=count($eventLabels)?></strong></article>
<article><span>Discord destinations</span><strong><?=count($configuredChannels)?></strong></article>
</section>

<section class="panel audit-events-panel">
<div class="section-title audit-section-title"><div><span class="kicker">Event stream</span><h2>Recent server actions</h2><p><?=number_format(count($visibleEvents))?> matching records from the latest <?=number_format(count($events))?> retained events.</p></div><a class="button secondary small" href="control.php?id=<?=urlencode($guildId)?>&name=<?=urlencode($guildName)?>#logging"><i class="bi bi-sliders"></i> Configure</a></div>
<nav class="audit-filter-bar" aria-label="Filter action logs">
<a class="<?=$filter==='all'?'active':''?>" href="?<?=http_build_query(['id'=>$guildId,'name'=>$guildName,'type'=>'all'])?>">All</a>
<?php foreach($eventLabels as $key=>$label): ?><a class="<?=$filter===$key?'active':''?>" href="?<?=http_build_query(['id'=>$guildId,'name'=>$guildName,'type'=>$key])?>"><?=htmlspecialchars($label)?></a><?php endforeach; ?>
</nav>
<?php if (!$visibleEvents): ?>
<div class="empty-panel"><span class="empty-icon"><i class="bi bi-journal-x"></i></span><h3>No matching action logs</h3><p>New events will appear here as Rallybit observes or performs actions.</p></div>
<?php else: ?>
<div class="audit-event-list">
<?php foreach(array_slice($visibleEvents,0,250) as $event): $type=(string)($event['type']??'configuration'); $timestamp=strtotime((string)($event['timestamp']??'')); ?>
<article class="audit-event-row">
<span class="audit-event-icon type-<?=htmlspecialchars($type)?>"><i class="bi bi-<?=match($type){'commands'=>'terminal','moderation','security'=>'shield-check','members'=>'people','messages'=>'chat-left-text','roles'=>'person-badge','channels'=>'hash','voice'=>'mic','tickets'=>'ticket-perforated','reports'=>'flag','staff'=>'person-workspace',default=>'sliders'}?>"></i></span>
<div class="audit-event-copy"><div><strong><?=htmlspecialchars((string)($event['title']??($eventLabels[$type]??'Server event')))?></strong><span class="audit-type-label"><?=htmlspecialchars($eventLabels[$type]??ucfirst($type))?></span></div><p><?=htmlspecialchars((string)($event['description']??'No additional details.'))?></p><small><?php if(!empty($event['actor_name'])): ?>By <?=htmlspecialchars((string)$event['actor_name'])?><?php endif; ?><?php if(!empty($event['target'])): ?> · <?=htmlspecialchars((string)$event['target'])?><?php endif; ?><?php if(!empty($event['channel_name'])): ?> · #<?=htmlspecialchars((string)$event['channel_name'])?><?php endif; ?></small></div>
<time datetime="<?=htmlspecialchars((string)($event['timestamp']??''))?>"><?=$timestamp?htmlspecialchars(date('j M Y, H:i',$timestamp)):'Unknown'?></time>
</article>
<?php endforeach; ?>
</div>
<?php endif; ?>
</section>

<section class="panel activity-history-panel">
<div class="section-title"><div><span class="kicker">Participation</span><h2>Activity check history</h2><p><?=number_format(count($activityLogs))?> completed, stopped or active checks.</p></div></div>
<?php if(!$activityLogs): ?><div class="empty-panel"><h3>No activity checks yet</h3><p>Run an activity check and its results will appear here.</p></div><?php else: ?><div class="data-table-wrap"><table class="data-table"><thead><tr><th>Started</th><th>Status</th><th>Host</th><th>Participants</th><th>Winners</th></tr></thead><tbody><?php foreach($activityLogs as $log):$participants=$log['participants']??$log['reactors']??[];$winners=$log['winners']??[];?><tr><td><?=htmlspecialchars((string)($log['start_time']??$log['timestamp']??'Unknown'))?></td><td><?=htmlspecialchars(ucwords(str_replace('_',' ',(string)($log['status']??'completed'))))?></td><td><?=htmlspecialchars((string)($log['starter_name']??$log['host_name']??$log['started_by_name']??$log['starter_id']??$log['host_id']??'Unknown'))?></td><td><?=is_array($participants)?count($participants):(int)$participants?></td><td><?=is_array($winners)?count($winners):(int)$winners?></td></tr><?php endforeach;?></tbody></table></div><?php endif; ?>
</section>
</main>
</div>
</body>
</html>
