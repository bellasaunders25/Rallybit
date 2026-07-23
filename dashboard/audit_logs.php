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
        $allowed = true; $guildName = (string)($guild['name'] ?? $guildName); break;
    }
}
if (!$allowed) { http_response_code(403); exit('You do not have permission to view these logs.'); }
$allLogs = load_json_data('activity_audit_logs.json');
$logs = is_array($allLogs[$guildId] ?? null) ? array_values($allLogs[$guildId]) : [];
usort($logs, fn($a,$b) => strcmp((string)($b['start_time'] ?? $b['timestamp'] ?? ''), (string)($a['start_time'] ?? $a['timestamp'] ?? '')));
$avatar = user_avatar_url();
?>
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Audit logs — <?=htmlspecialchars($guildName)?> — Rallybit</title><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"><link rel="stylesheet" href="/dashboard/style.css?v=5.6"><link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" sizes="32x32" href="/assets/brand/rallybit-icon-32.png"><link rel="icon" type="image/png" sizes="192x192" href="/assets/brand/rallybit-icon-192.png"><link rel="apple-touch-icon" href="/assets/brand/apple-touch-icon.png"></head><body><div class="dash-shell"><?php render_dashboard_sidebar('logs', $guildId, $guildName); ?><main class="dash-main"><header class="dash-header"><div><a class="back-link" href="manage.php?id=<?=urlencode($guildId)?>&name=<?=urlencode($guildName)?>"><i class="bi bi-arrow-left" aria-hidden="true"></i> Server settings</a><span class="kicker">Participation history</span><h1><?=htmlspecialchars($guildName)?> logs</h1><p>Review completed, stopped, and active checks with participant totals.</p></div></header><section class="panel"><div class="section-title"><div><h2>Activity checks</h2><p><?=count($logs)?> records available.</p></div></div><?php if(!$logs):?><div class="empty-panel"><h3>No audit records yet</h3><p>Run an activity check and its results will appear here.</p></div><?php else:?><div class="data-table-wrap"><table class="data-table"><thead><tr><th>Started</th><th>Status</th><th>Host</th><th>Participants</th><th>Winners</th></tr></thead><tbody><?php foreach($logs as $log):$participants=$log['participants']??$log['reactors']??[];$winners=$log['winners']??[];?><tr><td><?=htmlspecialchars((string)($log['start_time']??$log['timestamp']??'Unknown'))?></td><td><?=htmlspecialchars(ucwords(str_replace('_',' ',(string)($log['status']??'completed'))))?></td><td><?=htmlspecialchars((string)($log['starter_name']??$log['host_name']??$log['started_by_name']??$log['starter_id']??$log['host_id']??'Unknown'))?></td><td><?=is_array($participants)?count($participants):(int)$participants?></td><td><?=is_array($winners)?count($winners):(int)$winners?></td></tr><?php endforeach;?></tbody></table></div><?php endif;?></section></main></div></body></html>
