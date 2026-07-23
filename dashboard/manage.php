<?php
require_once 'includes/functions.php';
check_login();
$guild_id = preg_replace('/\D/', '', (string)($_GET['id'] ?? ''));
if (!$guild_id) { header('Location: index.php'); exit; }
$guild_name = trim((string)($_GET['name'] ?? 'Server'));
$guilds = discord_api_request('/users/@me/guilds', $_SESSION['access_token']) ?: [];
$allowed = false;
foreach ($guilds as $guild) if ((string)$guild['id'] === $guild_id && has_admin_permission((int)$guild['permissions'])) { $allowed = true; $guild_name = $guild['name']; break; }
if (!$allowed) { http_response_code(403); exit('You do not have permission to manage this server.'); }
$settings_all = load_json_data(SETTINGS_FILE);
$settings = $settings_all[$guild_id] ?? [];
$message = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    validate_csrf_token($_POST['csrf_token'] ?? '');
    $mode = in_array($_POST['reactor_type'] ?? '', ['reaction','button'], true) ? $_POST['reactor_type'] : 'reaction';
    $settings = array_merge($settings, [
        'activity_text' => trim((string)($_POST['activity_text'] ?? '')) ?: "**ACTIVITY CHECK**\nReact below if you're active!",
        'reactor_type' => $mode,
        'reactor' => trim((string)($_POST['reactor'] ?? '✅')) ?: '✅',
        'button_text' => function_exists('mb_substr') ? mb_substr(trim((string)($_POST['button_text'] ?? "I'm Active! ⚡")), 0, 80) : substr(trim((string)($_POST['button_text'] ?? "I'm Active! ⚡")), 0, 80),
        'ping_target' => trim((string)($_POST['ping_target'] ?? '@everyone')) ?: '@everyone',
        'winner_count' => max(1, min(100, (int)($_POST['winner_count'] ?? 3))),
        'check_duration_minutes' => max(1, min(1440, (int)($_POST['check_duration_minutes'] ?? 60))),
        'permitted_role' => preg_replace('/\D/', '', (string)($_POST['permitted_role'] ?? '')) ?: null,
        'log_channel_id' => preg_replace('/\D/', '', (string)($_POST['log_channel_id'] ?? '')) ?: null,
        'auto_enabled' => isset($_POST['auto_enabled']),
        'auto_hours' => max(1, min(168, (int)($_POST['auto_hours'] ?? 1))),
        'auto_channel' => preg_replace('/\D/', '', (string)($_POST['auto_channel'] ?? '')) ?: null,
    ]);
    $settings_all[$guild_id] = $settings;
    $message = save_json_data(SETTINGS_FILE, $settings_all) ? 'Settings saved successfully.' : 'The bot API could not save your settings.';
}
$csrf = get_csrf_token();
$avatar = !empty($_SESSION['avatar']) ? 'https://cdn.discordapp.com/avatars/' . rawurlencode($_SESSION['user_id']) . '/' . rawurlencode($_SESSION['avatar']) . '.png?size=80' : 'https://cdn.discordapp.com/embed/avatars/0.png';
?>
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title><?=htmlspecialchars($guild_name)?> — Rallybit</title><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"><link rel="stylesheet" href="/dashboard/style.css?v=5.6"><link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" sizes="32x32" href="/assets/brand/rallybit-icon-32.png"><link rel="icon" type="image/png" sizes="192x192" href="/assets/brand/rallybit-icon-192.png"><link rel="apple-touch-icon" href="/assets/brand/apple-touch-icon.png"></head><body>
<div class="dash-shell"><?php render_dashboard_sidebar('settings', $guild_id, $guild_name); ?>
<main class="dash-main"><header class="dash-header compact"><div><a class="back-link" href="index.php"><i class="bi bi-arrow-left" aria-hidden="true"></i> All servers</a><span class="kicker">Server configuration</span><h1><?=htmlspecialchars($guild_name)?></h1><p>Every Rallybit feature is enabled for this server.</p></div><span class="access-chip">Complete access</span></header>
<?php if($message):?><div class="alert <?=str_contains($message,'successfully')?'success':'error'?>"><?=htmlspecialchars($message)?></div><?php endif;?>
<form method="post" class="settings-layout"><input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>"><div class="settings-column">
<section class="panel"><div class="panel-heading"><span>01</span><div><h2>Check appearance</h2><p>Choose what members see and how they respond.</p></div></div><label>Activity check message<textarea name="activity_text" rows="6"><?=htmlspecialchars($settings['activity_text'] ?? "**ACTIVITY CHECK**\nReact below if you're active!")?></textarea></label><div class="field-grid"><label>Participation mode<select name="reactor_type"><option value="reaction" <?=($settings['reactor_type']??'reaction')==='reaction'?'selected':''?>>Emoji reaction</option><option value="button" <?=($settings['reactor_type']??'')==='button'?'selected':''?>>Interactive button</option></select></label><label>Reaction emoji<input name="reactor" value="<?=htmlspecialchars($settings['reactor'] ?? '✅')?>"></label></div><label>Button text<input name="button_text" maxlength="80" value="<?=htmlspecialchars($settings['button_text'] ?? "I'm Active! ⚡")?>"><small>Use <code>{count}</code> to display the live participant count.</small></label></section>
<section class="panel"><div class="panel-heading"><span>02</span><div><h2>Check rules</h2><p>Set the audience, duration, and completion target.</p></div></div><div class="field-grid"><label>Winners required<input type="number" min="1" max="100" name="winner_count" value="<?=htmlspecialchars((string)($settings['winner_count'] ?? 3))?>"></label><label>Duration in minutes<input type="number" min="1" max="1440" name="check_duration_minutes" value="<?=htmlspecialchars((string)($settings['check_duration_minutes'] ?? 60))?>"></label></div><label>Ping target<input name="ping_target" value="<?=htmlspecialchars($settings['ping_target'] ?? '@everyone')?>"><small>Use @everyone or a role mention.</small></label><label>Permitted staff role ID<input name="permitted_role" inputmode="numeric" value="<?=htmlspecialchars((string)($settings['permitted_role'] ?? ''))?>"><small>Leave blank to restrict starting checks to administrators.</small></label></section>
</div><div class="settings-column">
<section class="panel"><div class="panel-heading"><span>03</span><div><h2>Automation</h2><p>Run recurring checks without staff reposting them.</p></div></div><label class="toggle-row"><input type="checkbox" name="auto_enabled" <?=!empty($settings['auto_enabled'])?'checked':''?>><span><strong>Enable recurring checks</strong><small>Rallybit will follow the interval below.</small></span></label><div class="field-grid"><label>Interval in hours<input type="number" min="1" max="168" name="auto_hours" value="<?=htmlspecialchars((string)($settings['auto_hours'] ?? 1))?>"></label><label>Automation channel ID<input name="auto_channel" inputmode="numeric" value="<?=htmlspecialchars((string)($settings['auto_channel'] ?? ''))?>"></label></div></section>
<section class="panel"><div class="panel-heading"><span>04</span><div><h2>Reports</h2><p>Send check results and configuration events to a staff channel.</p></div></div><label>Log channel ID<input name="log_channel_id" inputmode="numeric" value="<?=htmlspecialchars((string)($settings['log_channel_id'] ?? ''))?>"></label><a class="button secondary full" href="audit_logs.php?id=<?=urlencode($guild_id)?>&name=<?=urlencode($guild_name)?>">Open audit logs</a></section>
<div class="save-panel"><div><strong>Ready to apply?</strong><p>Changes sync to the bot immediately.</p></div><button class="button primary" type="submit">Save settings</button></div>
</div></form></main></div></body></html>
