<?php
require_once 'includes/functions.php';
check_login();
if (!is_bot_admin()) { http_response_code(403); exit('Access denied.'); }
$message = '';
$settings = load_json_data(BOT_SETTINGS_FILE);
$notice = load_json_data(NOTICE_FILE);
$bannedUsers = load_json_data(BANNED_USERS_FILE);
$bannedServers = load_json_data(BANNED_SERVERS_FILE);
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    validate_csrf_token($_POST['csrf_token'] ?? '');
    $action = (string)($_POST['action'] ?? '');
    if ($action === 'notice') {
        $notice = ['active' => isset($_POST['notice_active']), 'title' => substr(trim((string)($_POST['notice_title'] ?? '')),0,80), 'message' => substr(trim((string)($_POST['notice_message'] ?? '')),0,400), 'updated_at' => gmdate('c')];
        $message = save_json_data(NOTICE_FILE, $notice) ? 'Notice updated.' : 'Could not update the notice.';
    } elseif ($action === 'command') {
        $key = preg_replace('/[^a-z0-9_]/', '', strtolower((string)($_POST['command_key'] ?? '')));
        if ($key !== '') {
            $settings[$key] = array_merge(is_array($settings[$key] ?? null)?$settings[$key]:[], ['active' => isset($_POST['command_active']), 'is_unlimited' => true]);
            $message = save_json_data(BOT_SETTINGS_FILE, $settings) ? 'Command setting updated.' : 'Could not update command settings.';
        }
    } elseif ($action === 'unban') {
        $kind = $_POST['kind'] === 'server' ? 'server' : 'user';
        $id = preg_replace('/\D/', '', (string)($_POST['target_id'] ?? ''));
        $data = $kind === 'server' ? $bannedServers : $bannedUsers;
        unset($data[$id]);
        $ok = save_json_data($kind === 'server' ? BANNED_SERVERS_FILE : BANNED_USERS_FILE, $data);
        $message = $ok ? ucfirst($kind).' access restored.' : 'Could not update the ban list.';
        if ($kind === 'server') $bannedServers=$data; else $bannedUsers=$data;
    }
}
$avatar=user_avatar_url();
?>
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Developer tools — Rallybit</title><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"><link rel="stylesheet" href="/dashboard/style.css?v=5.6"><link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" sizes="32x32" href="/assets/brand/rallybit-icon-32.png"><link rel="icon" type="image/png" sizes="192x192" href="/assets/brand/rallybit-icon-192.png"><link rel="apple-touch-icon" href="/assets/brand/apple-touch-icon.png"></head><body><div class="dash-shell"><?php render_dashboard_sidebar('admin'); ?><main class="dash-main"><header class="dash-header"><div><span class="kicker">Restricted controls</span><h1>Developer tools</h1><p>Manage service notices, command availability, and access blocks.</p></div></header><?php if($message):?><div class="alert <?=str_contains($message,'Could not')?'error':'success'?>"><?=htmlspecialchars($message)?></div><?php endif;?><div class="admin-grid"><section class="panel"><div class="panel-heading"><span>01</span><div><h2>Service notice</h2><p>Show a product-wide message without editing code.</p></div></div><form method="post"><input type="hidden" name="csrf_token" value="<?=htmlspecialchars(get_csrf_token())?>"><input type="hidden" name="action" value="notice"><div class="switch-row"><div><strong>Display notice</strong><span>Enable the message across supported surfaces.</span></div><input type="checkbox" name="notice_active" <?=!empty($notice['active'])?'checked':''?>></div><label>Title<input name="notice_title" maxlength="80" value="<?=htmlspecialchars((string)($notice['title']??''))?>"></label><label>Message<textarea name="notice_message" maxlength="400"><?=htmlspecialchars((string)($notice['message']??''))?></textarea></label><button class="button primary" type="submit">Save notice</button></form></section><section class="panel"><div class="panel-heading"><span>02</span><div><h2>Command controls</h2><p>Temporarily disable a command during maintenance.</p></div></div><?php foreach($settings as $key=>$value):if($key==='global'||!is_array($value))continue;?><form method="post" class="switch-row"><input type="hidden" name="csrf_token" value="<?=htmlspecialchars(get_csrf_token())?>"><input type="hidden" name="action" value="command"><input type="hidden" name="command_key" value="<?=htmlspecialchars((string)$key)?>"><div><strong>/<?=htmlspecialchars((string)$key)?></strong><span><?=!empty($value['active'])?'Available':'Disabled'?></span></div><input type="checkbox" name="command_active" <?=!empty($value['active'])?'checked':''?> onchange="this.form.submit()"></form><?php endforeach;?></section><section class="panel"><div class="panel-heading"><span>03</span><div><h2>Blocked users</h2><p>Review and restore bot access.</p></div></div><?php if(!$bannedUsers):?><p style="color:var(--muted)">No users are blocked.</p><?php else:foreach($bannedUsers as $id=>$entry):?><form method="post" class="switch-row"><input type="hidden" name="csrf_token" value="<?=htmlspecialchars(get_csrf_token())?>"><input type="hidden" name="action" value="unban"><input type="hidden" name="kind" value="user"><input type="hidden" name="target_id" value="<?=htmlspecialchars((string)$id)?>"><div><strong><?=htmlspecialchars((string)$id)?></strong><span><?=htmlspecialchars((string)($entry['reason']??'No reason provided'))?></span></div><button class="button small secondary">Restore</button></form><?php endforeach;endif;?></section><section class="panel"><div class="panel-heading"><span>04</span><div><h2>Blocked servers</h2><p>Review and restore community access.</p></div></div><?php if(!$bannedServers):?><p style="color:var(--muted)">No servers are blocked.</p><?php else:foreach($bannedServers as $id=>$entry):?><form method="post" class="switch-row"><input type="hidden" name="csrf_token" value="<?=htmlspecialchars(get_csrf_token())?>"><input type="hidden" name="action" value="unban"><input type="hidden" name="kind" value="server"><input type="hidden" name="target_id" value="<?=htmlspecialchars((string)$id)?>"><div><strong><?=htmlspecialchars((string)$id)?></strong><span><?=htmlspecialchars((string)($entry['reason']??'No reason provided'))?></span></div><button class="button small secondary">Restore</button></form><?php endforeach;endif;?></section></div></main></div></body></html>
