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
    } elseif ($action === 'grant') {
        $subjectType = (string)($_POST['subject_type'] ?? 'server');
        $subjectId = preg_replace('/\D/', '', (string)($_POST['subject_id'] ?? ''));
        $plan = strtolower((string)($_POST['plan'] ?? ''));
        $expiresRaw = trim((string)($_POST['expires_at'] ?? ''));
        $expiresAt = null;
        if ($expiresRaw !== '') {
            try {
                $expiry = new DateTimeImmutable($expiresRaw, new DateTimeZone('UTC'));
                if ($expiry <= new DateTimeImmutable('now', new DateTimeZone('UTC'))) {
                    throw new RuntimeException('Expiration must be in the future.');
                }
                $expiresAt = $expiry->setTimezone(new DateTimeZone('UTC'))->format(DateTimeInterface::ATOM);
            } catch (Throwable $error) {
                $message = $error->getMessage() ?: 'Enter a valid future expiration.';
                $messageType = 'error';
            }
        }
        if ($messageType !== 'error') {
            $result = api_request('/api/premium/grant', [
                'subject_type' => $subjectType,
                'subject_id' => $subjectId,
                'plan' => $plan,
                'expires_at' => $expiresAt,
                'actor_id' => (string)($_SESSION['user_id'] ?? ''),
            ], 20);
            if (!empty($result['ok'])) {
                $message = ucfirst($plan).' preview granted successfully.';
            } else {
                $message = api_error($result, 'Could not grant that plan.');
                $messageType = 'error';
            }
        }
    } elseif ($action === 'revoke') {
        $result = api_request('/api/premium/revoke', [
            'subject_type' => (string)($_POST['subject_type'] ?? ''),
            'subject_id' => preg_replace('/\D/', '', (string)($_POST['subject_id'] ?? '')),
            'actor_id' => (string)($_SESSION['user_id'] ?? ''),
        ], 20);
        if (!empty($result['ok'])) {
            $message = 'Preview access revoked.';
        } else {
            $message = api_error($result, 'Could not revoke that plan.');
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

$premium = get_premium_entitlements();
$globalSettings = is_array($settings['global'] ?? null) ? $settings['global'] : [];
$csrf = get_csrf_token();
$entitlementRows = [];
foreach (['servers' => 'server', 'users' => 'user'] as $bucket => $type) {
    foreach (($premium[$bucket] ?? []) as $id => $record) {
        if (!is_array($record)) continue;
        $entitlementRows[] = ['type' => $type, 'id' => (string)$id] + $record;
    }
}
usort($entitlementRows, fn(array $a, array $b): int => strcmp((string)($b['granted_at'] ?? ''), (string)($a['granted_at'] ?? '')));
$history = is_array($premium['history'] ?? null) ? array_slice(array_reverse($premium['history']), 0, 12) : [];
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
  <link rel="stylesheet" href="/dashboard/style.css?v=5.7">
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="icon" type="image/png" sizes="32x32" href="/assets/brand/rallybit-icon-32.png">
</head>
<body>
<div class="dash-shell">
  <?php render_dashboard_sidebar('admin'); ?>
  <main class="dash-main">
    <header class="dash-header">
      <div><span class="kicker">Restricted controls</span><h1>Developer tools</h1><p>Manage service availability, the shared bot profile, preview plans, and access blocks.</p></div>
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

      <section class="panel admin-wide">
        <div class="panel-heading"><span>03</span><div><h2>Premium previews</h2><p>Grant working previews while public paid plans remain Coming soon.</p></div></div>
        <div class="entitlement-builders">
          <form method="post" class="admin-subpanel">
            <input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>"><input type="hidden" name="action" value="grant"><input type="hidden" name="subject_type" value="server">
            <div class="subpanel-title"><i class="bi bi-hdd-rack"></i><div><strong>Server plan</strong><span>Community or Pro for one server.</span></div></div>
            <label>Server ID<input name="subject_id" inputmode="numeric" pattern="[0-9]{15,22}" required placeholder="123456789012345678"></label>
            <label>Plan<select name="plan"><option value="community">Community</option><option value="pro">Pro</option></select></label>
            <label>Expires (UTC)<input type="datetime-local" name="expires_at"><small>Leave blank for no expiration.</small></label>
            <button class="button primary" type="submit"><i class="bi bi-plus-circle"></i> Grant server preview</button>
          </form>
          <form method="post" class="admin-subpanel">
            <input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>"><input type="hidden" name="action" value="grant"><input type="hidden" name="subject_type" value="user"><input type="hidden" name="plan" value="network">
            <div class="subpanel-title"><i class="bi bi-diagram-3"></i><div><strong>Network owner plan</strong><span>Unlimited servers actually owned by this user.</span></div></div>
            <label>Discord user ID<input name="subject_id" inputmode="numeric" pattern="[0-9]{15,22}" required placeholder="123456789012345678"></label>
            <label>Plan<input value="Network" disabled></label>
            <label>Expires (UTC)<input type="datetime-local" name="expires_at"><small>Leave blank for no expiration.</small></label>
            <button class="button primary" type="submit"><i class="bi bi-plus-circle"></i> Grant Network preview</button>
          </form>
        </div>

        <div class="admin-table-heading"><div><h3>Current entitlements</h3><p>Expired records remain visible until revoked.</p></div><span><?=number_format(count($entitlementRows))?> total</span></div>
        <?php if (!$entitlementRows): ?>
          <div class="compact-empty"><span><i class="bi bi-key"></i></span><strong>No preview grants</strong><p>Developer accounts still have automatic Network preview access.</p></div>
        <?php else: ?>
          <div class="data-table-wrap"><table class="data-table premium-table"><thead><tr><th>Subject</th><th>Plan</th><th>Granted</th><th>Expiration</th><th>Status</th><th></th></tr></thead><tbody>
          <?php foreach ($entitlementRows as $row):
              $expiry = !empty($row['expires_at']) ? strtotime((string)$row['expires_at']) : false;
              $expired = $expiry !== false && $expiry <= time(); ?>
            <tr><td><strong><?=ucfirst(htmlspecialchars($row['type']))?></strong><small><?=htmlspecialchars($row['id'])?></small></td><td><?=ucfirst(htmlspecialchars((string)($row['plan'] ?? 'free')))?></td><td><?=!empty($row['granted_at']) ? htmlspecialchars(gmdate('d M Y H:i', strtotime((string)$row['granted_at']))) : 'Unknown'?></td><td><?=$expiry ? htmlspecialchars(gmdate('d M Y H:i', $expiry)).' UTC' : 'Never'?></td><td><span class="state-pill <?=$expired ? 'expired' : 'active'?>"><?=$expired ? 'Expired' : 'Active'?></span></td><td><form method="post"><input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>"><input type="hidden" name="action" value="revoke"><input type="hidden" name="subject_type" value="<?=htmlspecialchars($row['type'])?>"><input type="hidden" name="subject_id" value="<?=htmlspecialchars($row['id'])?>"><button class="icon-button danger" type="submit" aria-label="Revoke entitlement"><i class="bi bi-trash3"></i></button></form></td></tr>
          <?php endforeach; ?>
          </tbody></table></div>
        <?php endif; ?>

        <?php if ($history): ?>
          <details class="grant-history"><summary>Recent grant history</summary><div class="history-list">
          <?php foreach ($history as $entry): ?>
            <div><i class="bi bi-clock-history"></i><span><strong><?=ucfirst(htmlspecialchars((string)($entry['action'] ?? 'updated')))?> <?=ucfirst(htmlspecialchars((string)($entry['plan'] ?? 'plan')))?></strong><small><?=htmlspecialchars((string)($entry['subject_type'] ?? 'subject'))?> <?=htmlspecialchars((string)($entry['subject_id'] ?? ''))?> · <?=htmlspecialchars((string)($entry['timestamp'] ?? ''))?></small></span></div>
          <?php endforeach; ?>
          </div></details>
        <?php endif; ?>
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
