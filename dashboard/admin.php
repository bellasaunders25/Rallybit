<?php
declare(strict_types=1);

require_once 'includes/functions.php';
check_login();
if (!is_bot_admin()) {
    http_response_code(403);
    exit('Access denied.');
}

$message = '';
$messageType = 'success';
$settings = load_json_data(BOT_SETTINGS_FILE);
$notice = load_json_data(NOTICE_FILE);
$bannedUsers = load_json_data(BANNED_USERS_FILE);
$bannedServers = load_json_data(BANNED_SERVERS_FILE);

function api_error(?array $result, string $fallback): string {
    return is_array($result) && !empty($result['error']) ? (string)$result['error'] : $fallback;
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    validate_csrf_token($_POST['csrf_token'] ?? '');
    $action = (string)($_POST['action'] ?? '');

    if ($action === 'notice') {
        $active = isset($_POST['notice_active']);
        $title = substr(trim((string)($_POST['notice_title'] ?? '')), 0, 80);
        $body = substr(trim((string)($_POST['notice_message'] ?? '')), 0, 400);
        if ($active && $title === '') $title = 'Rallybit is temporarily unavailable';
        if ($active && $body === '') $body = 'Commands are temporarily paused while maintenance is completed. Please try again later.';
        $notice = [
            'active' => $active,
            'title' => $title,
            'message' => $body,
            'updated_at' => gmdate('c'),
            'updated_by' => (string)($_SESSION['user_id'] ?? ''),
        ];
        if (save_json_data(NOTICE_FILE, $notice)) {
            $message = $active ? 'Service notice enabled. Every bot command is now paused.' : 'Service notice disabled. Commands are available again.';
        } else {
            $message = 'Could not update the service notice.';
            $messageType = 'error';
        }
    } elseif ($action === 'profile') {
        $status = strtolower(trim((string)($_POST['presence_status'] ?? 'online')));
        if (!in_array($status, ['online', 'idle', 'dnd', 'offline'], true)) $status = 'online';
        $result = api_request('/api/bot/profile', [
            'name' => substr(trim((string)($_POST['profile_name'] ?? '')), 0, 32),
            'avatar_url' => substr(trim((string)($_POST['profile_avatar_url'] ?? '')), 0, 500),
            'status' => $status,
        ], 45);
        if (!empty($result['ok'])) {
            $message = 'Shared bot profile and presence updated.';
            $settings = load_json_data(BOT_SETTINGS_FILE);
        } else {
            $message = api_error($result, 'The bot did not accept the profile update.');
            $messageType = 'error';
        }
    } elseif ($action === 'command') {
        $key = preg_replace('/[^a-z0-9_]/', '', strtolower((string)($_POST['command_key'] ?? '')));
        if ($key !== '') {
            $settings[$key] = array_merge(is_array($settings[$key] ?? null) ? $settings[$key] : [], [
                'active' => isset($_POST['command_active']),
                'is_unlimited' => true,
            ]);
            if (save_json_data(BOT_SETTINGS_FILE, $settings)) {
                $message = 'Command setting updated.';
            } else {
                $message = 'Could not update command settings.';
                $messageType = 'error';
            }
        }
    } elseif ($action === 'unban') {
        $kind = ($_POST['kind'] ?? '') === 'server' ? 'server' : 'user';
        $id = preg_replace('/\D/', '', (string)($_POST['target_id'] ?? ''));
        $data = $kind === 'server' ? $bannedServers : $bannedUsers;
        unset($data[$id]);
        $ok = save_json_data($kind === 'server' ? BANNED_SERVERS_FILE : BANNED_USERS_FILE, $data);
        if ($ok) {
            $message = ucfirst($kind).' access restored.';
            if ($kind === 'server') $bannedServers = $data; else $bannedUsers = $data;
        } else {
            $message = 'Could not update the ban list.';
            $messageType = 'error';
        }
    }
}

$globalSettings = is_array($settings['global'] ?? null) ? $settings['global'] : [];
$csrf = get_csrf_token();
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Developer tools — Rallybit</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/assets/vendor/bootstrap-icons/bootstrap-icons.min.css?v=1.0">
  <link rel="stylesheet" href="/dashboard/style.css?v=10.0">
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="icon" type="image/png" sizes="32x32" href="/assets/brand/rallybit-mark-32.png">
<script src="/assets/theme.js?v=10.0"></script><link rel="icon" type="image/svg+xml" href="/assets/brand/rallybit-mark.svg"></head>
<body>
<div class="dash-shell">
  <?php render_dashboard_sidebar('admin'); ?>
  <main class="dash-main">
    <header class="dash-header">
      <div><span class="kicker">Restricted controls</span><h1>Developer tools</h1><p>Manage service availability, the shared bot profile and access blocks.</p></div>
    </header>

    <?php if ($message !== ''): ?>
      <div class="alert <?=$messageType === 'error' ? 'error' : 'success'?>"><?=htmlspecialchars($message)?></div>
    <?php endif; ?>

    <div class="admin-grid">
      <section class="panel">
        <div class="panel-heading"><span>01</span><div><h2>Service notice</h2><p>Pause every slash command and show one clear message.</p></div></div>
        <form method="post">
          <input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
          <input type="hidden" name="action" value="notice">
          <div class="switch-row"><div><strong>Pause every command</strong><span>Command attempts receive this notice privately.</span></div><input type="checkbox" name="notice_active" <?=!empty($notice['active']) ? 'checked' : ''?>></div>
          <label>Notice title<input name="notice_title" maxlength="80" value="<?=htmlspecialchars((string)($notice['title'] ?? ''))?>" placeholder="Rallybit is temporarily unavailable"></label>
          <label>Notice message<textarea name="notice_message" maxlength="400" rows="4" placeholder="Explain why commands are paused and when users should try again."><?=htmlspecialchars((string)($notice['message'] ?? ''))?></textarea></label>
          <button class="button primary" type="submit"><i class="bi bi-megaphone"></i> Save notice</button>
        </form>
      </section>

      <section class="panel">
        <div class="panel-heading"><span>02</span><div><h2>Shared bot profile</h2><p>Apply a name, profile picture, and presence immediately.</p></div></div>
        <div class="permission-note"><i class="bi bi-globe2"></i><p>These identity changes affect the shared Rallybit application in every server. Per-customer branding will use dedicated or self-hosted instances and is coming soon.</p></div>
        <form method="post">
          <input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
          <input type="hidden" name="action" value="profile">
          <label>Bot name<input name="profile_name" minlength="2" maxlength="32" value="<?=htmlspecialchars((string)($globalSettings['profile_name'] ?? ''))?>" placeholder="Rallybit"></label>
          <label>Profile picture URL<input type="url" name="profile_avatar_url" maxlength="500" value="<?=htmlspecialchars((string)($globalSettings['profile_avatar_url'] ?? ''))?>" placeholder="https://cdn.example.com/rallybit.png"><small>Use a public HTTPS image smaller than 8 MB.</small></label>
          <label>Status<select name="presence_status">
            <?php foreach (['online' => 'Online', 'idle' => 'Idle', 'dnd' => 'Do Not Disturb', 'offline' => 'Offline'] as $value => $label): ?>
              <option value="<?=$value?>" <?=($globalSettings['presence_status'] ?? 'online') === $value ? 'selected' : ''?>><?=$label?></option>
            <?php endforeach; ?>
          </select></label>
          <button class="button primary" type="submit"><i class="bi bi-person-badge"></i> Update bot profile</button>
        </form>
      </section>

      <section class="panel">
        <div class="panel-heading"><span>04</span><div><h2>Command controls</h2><p>Temporarily disable an individual legacy command.</p></div></div>
        <?php foreach ($settings as $key => $value): if ($key === 'global' || !is_array($value)) continue; ?>
          <form method="post" class="switch-row"><input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>"><input type="hidden" name="action" value="command"><input type="hidden" name="command_key" value="<?=htmlspecialchars((string)$key)?>"><div><strong>/<?=htmlspecialchars((string)$key)?></strong><span><?=!empty($value['active']) ? 'Available' : 'Disabled'?></span></div><input type="checkbox" name="command_active" <?=!empty($value['active']) ? 'checked' : ''?> onchange="this.form.submit()"></form>
        <?php endforeach; ?>
      </section>

      <section class="panel">
        <div class="panel-heading"><span>05</span><div><h2>Access blocks</h2><p>Review and restore blocked users and servers.</p></div></div>
        <h3 class="admin-list-title">Users</h3>
        <?php if (!$bannedUsers): ?><p class="admin-empty">No users are blocked.</p><?php else: foreach ($bannedUsers as $id => $entry): ?>
          <form method="post" class="switch-row"><input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>"><input type="hidden" name="action" value="unban"><input type="hidden" name="kind" value="user"><input type="hidden" name="target_id" value="<?=htmlspecialchars((string)$id)?>"><div><strong><?=htmlspecialchars((string)$id)?></strong><span><?=htmlspecialchars((string)($entry['reason'] ?? 'No reason provided'))?></span></div><button class="button small secondary">Restore</button></form>
        <?php endforeach; endif; ?>
        <h3 class="admin-list-title">Servers</h3>
        <?php if (!$bannedServers): ?><p class="admin-empty">No servers are blocked.</p><?php else: foreach ($bannedServers as $id => $entry): ?>
          <form method="post" class="switch-row"><input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>"><input type="hidden" name="action" value="unban"><input type="hidden" name="kind" value="server"><input type="hidden" name="target_id" value="<?=htmlspecialchars((string)$id)?>"><div><strong><?=htmlspecialchars((string)$id)?></strong><span><?=htmlspecialchars((string)($entry['reason'] ?? 'No reason provided'))?></span></div><button class="button small secondary">Restore</button></form>
        <?php endforeach; endif; ?>
      </section>
    </div>
  </main>
</div>
</body>
</html>
