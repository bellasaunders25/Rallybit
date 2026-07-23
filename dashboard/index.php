<?php
require_once 'includes/functions.php';
check_login();

$allGuilds = discord_api_request('/users/@me/guilds', (string)$_SESSION['access_token']) ?: [];
$adminGuilds = array_values(array_filter(
    $allGuilds,
    fn(array $guild): bool => has_admin_permission((int)($guild['permissions'] ?? 0))
));

$botGuilds = load_json_data(BOT_GUILDS_FILE);
unset($botGuilds['__SYSTEM__']);
$joinedIds = array_map('strval', array_keys($botGuilds));
$joined = [];
$missing = [];
$guildNames = [];

foreach ($adminGuilds as $guild) {
    $guildId = (string)($guild['id'] ?? '');
    if ($guildId === '') continue;
    $guildNames[$guildId] = (string)($guild['name'] ?? 'Discord server');
    if (in_array($guildId, $joinedIds, true)) {
        $joined[] = $guild;
    } else {
        $missing[] = $guild;
    }
}

$avatar = user_avatar_url(96);
$displayName = (string)($_SESSION['global_name'] ?? $_SESSION['username'] ?? 'Member');
$username = (string)($_SESSION['username'] ?? 'member');

$health = get_detailed_health();
$botOnline = ($health['status'] ?? '') === 'healthy' || !empty($health['ok']);
$latency = isset($health['bot']['latency_ms']) && is_numeric($health['bot']['latency_ms'])
    ? (int)$health['bot']['latency_ms']
    : null;

$globalStats = load_json_data('global_stats.json');
$totalCheckins = 0;
$totalWins = 0;
foreach ($globalStats as $record) {
    if (!is_array($record)) continue;
    $totalCheckins += max(0, (int)($record['checkins'] ?? 0));
    $totalWins += max(0, (int)($record['wins'] ?? 0));
}

$auditByGuild = load_json_data('activity_audit_logs.json');
$recentChecks = [];
$completedChecks = 0;
$totalChecks = 0;
$today = new DateTimeImmutable('today');
$dayKeys = [];
$dayLabels = [];
$activityByDay = [];

for ($offset = 6; $offset >= 0; $offset--) {
    $day = $today->modify("-{$offset} days");
    $key = $day->format('Y-m-d');
    $dayKeys[] = $key;
    $dayLabels[$key] = $day->format('D');
    $activityByDay[$key] = 0;
}

foreach ($joinedIds as $guildId) {
    $logs = $auditByGuild[$guildId] ?? [];
    if (!is_array($logs)) continue;

    foreach ($logs as $log) {
        if (!is_array($log)) continue;
        $totalChecks++;
        $status = strtolower(trim((string)($log['status'] ?? 'completed')));
        if (in_array($status, ['completed', 'complete', 'finished', 'success'], true)) {
            $completedChecks++;
        }

        $participantsValue = $log['participants'] ?? $log['reactors'] ?? [];
        $participantCount = is_array($participantsValue)
            ? count($participantsValue)
            : max(0, (int)$participantsValue);

        $rawTime = trim((string)($log['start_time'] ?? $log['timestamp'] ?? ''));
        $timestamp = 0;
        $dateKey = '';
        if ($rawTime !== '') {
            try {
                $date = new DateTimeImmutable($rawTime);
                $timestamp = $date->getTimestamp();
                $dateKey = $date->format('Y-m-d');
            } catch (Throwable) {
                $parsed = strtotime($rawTime);
                if ($parsed !== false) {
                    $timestamp = $parsed;
                    $dateKey = date('Y-m-d', $parsed);
                }
            }
        }

        if ($dateKey !== '' && array_key_exists($dateKey, $activityByDay)) {
            $activityByDay[$dateKey] += max(1, $participantCount);
        }

        $recentChecks[] = [
            'guild_id' => $guildId,
            'guild_name' => $guildNames[$guildId] ?? 'Connected server',
            'status' => $status !== '' ? $status : 'completed',
            'participants' => $participantCount,
            'timestamp' => $timestamp,
            'time_label' => $rawTime !== '' ? $rawTime : 'Recently',
        ];
    }
}

usort($recentChecks, fn(array $a, array $b): int => ($b['timestamp'] <=> $a['timestamp']));
$recentChecks = array_slice($recentChecks, 0, 5);
$maxActivity = max(1, ...array_values($activityByDay));
$successRate = $totalChecks > 0 ? (int)round(($completedChecks / $totalChecks) * 100) : 100;

function dashboard_status_label(string $status): string {
    return match ($status) {
        'active', 'running', 'in_progress' => 'Running',
        'stopped', 'cancelled', 'canceled' => 'Stopped',
        'failed', 'error' => 'Failed',
        default => 'Completed',
    };
}

function dashboard_status_class(string $status): string {
    return match ($status) {
        'active', 'running', 'in_progress' => 'running',
        'stopped', 'cancelled', 'canceled' => 'stopped',
        'failed', 'error' => 'failed',
        default => 'completed',
    };
}

function dashboard_time_ago(int $timestamp, string $fallback): string {
    if ($timestamp <= 0) return $fallback;
    $seconds = max(0, time() - $timestamp);
    if ($seconds < 60) return 'Just now';
    if ($seconds < 3600) return floor($seconds / 60) . 'm ago';
    if ($seconds < 86400) return floor($seconds / 3600) . 'h ago';
    if ($seconds < 604800) return floor($seconds / 86400) . 'd ago';
    return date('j M', $timestamp);
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Dashboard — Rallybit</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/dashboard/style.css?v=5.6">
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="icon" type="image/png" sizes="32x32" href="/assets/brand/rallybit-icon-32.png">
  <link rel="icon" type="image/png" sizes="192x192" href="/assets/brand/rallybit-icon-192.png">
  <link rel="apple-touch-icon" href="/assets/brand/apple-touch-icon.png">
</head>
<body class="dashboard-home">
<div class="dash-shell">
  <?php render_dashboard_sidebar('overview', null, null, count($joined)); ?>

  <main class="dash-main overview-main">
    <header class="overview-topbar">
      <div>
        <span class="kicker">Overview</span>
        <h1>Community dashboard</h1>
        <p>Welcome back, <?=htmlspecialchars($displayName)?>. Here is what is happening across your Rallybit servers.</p>
      </div>
      <div class="topbar-actions">
        <span class="bot-status <?=$botOnline ? 'online' : 'offline'?>">
          <i></i><?= $botOnline ? 'Bot online' : 'Bot offline' ?>
          <?php if ($botOnline && $latency !== null): ?><small><?=$latency?>ms</small><?php endif; ?>
        </span>
        <a class="icon-button" href="status.php" aria-label="View status" title="View status"><i class="bi bi-arrow-clockwise"></i></a>
        <a class="button primary add-bot-button" href="https://discord.com/oauth2/authorize?client_id=<?=urlencode(DISCORD_CLIENT_ID)?>&permissions=1100451671286&scope=bot%20applications.commands">Add Rallybit</a>
      </div>
    </header>

    <section class="overview-stats" aria-label="Dashboard statistics">
      <article class="overview-stat-card">
        <div class="stat-icon purple"><i class="bi bi-hdd-rack"></i></div>
        <div><span>Connected servers</span><strong><?=number_format(count($joined))?></strong><small><b>Ready</b> to manage</small></div>
      </article>
      <article class="overview-stat-card">
        <div class="stat-icon blue"><i class="bi bi-people"></i></div>
        <div><span>Total check-ins</span><strong><?=number_format($totalCheckins)?></strong><small><b><?=number_format($totalWins)?></b> wins recorded</small></div>
      </article>
      <article class="overview-stat-card">
        <div class="stat-icon green"><i class="bi bi-check2-circle"></i></div>
        <div><span>Checks completed</span><strong><?=number_format($completedChecks)?></strong><small><b><?=$successRate?>%</b> completion rate</small></div>
      </article>
      <article class="overview-stat-card">
        <div class="stat-icon orange"><i class="bi bi-lightning-charge"></i></div>
        <div><span>Available servers</span><strong><?=number_format(count($adminGuilds))?></strong><small><b><?=number_format(count($missing))?></b> ready to connect</small></div>
      </article>
    </section>

    <section class="overview-grid">
      <article class="dashboard-panel activity-panel">
        <div class="panel-topline">
          <div>
            <h2>Activity overview</h2>
            <p>Participation recorded during the last seven days.</p>
          </div>
          <span class="panel-filter">Last 7 days <i class="bi bi-chevron-down" aria-hidden="true"></i></span>
        </div>
        <div class="activity-chart" aria-label="Seven-day activity chart">
          <div class="chart-scale" aria-hidden="true"><span><?=$maxActivity?></span><span><?=max(1, (int)round($maxActivity / 2))?></span><span>0</span></div>
          <div class="chart-bars">
            <?php foreach ($dayKeys as $index => $dayKey):
              $value = $activityByDay[$dayKey];
              $height = $value > 0 ? max(12, (int)round(($value / $maxActivity) * 100)) : 5;
            ?>
              <div class="chart-column" title="<?=htmlspecialchars($dayLabels[$dayKey])?>: <?=number_format($value)?> activity points">
                <span class="chart-value"><?=number_format($value)?></span>
                <i style="--bar-height: <?=$height?>%; --delay: <?=($index * 70)?>ms"></i>
                <small><?=htmlspecialchars(substr($dayLabels[$dayKey], 0, 1))?></small>
              </div>
            <?php endforeach; ?>
          </div>
        </div>
      </article>

      <article class="dashboard-panel recent-panel">
        <div class="panel-topline">
          <div><h2>Recent checks</h2><p>Latest activity across connected servers.</p></div>
          <?php if ($joined): ?><a href="audit_logs.php?id=<?=urlencode((string)$joined[0]['id'])?>&name=<?=urlencode((string)$joined[0]['name'])?>">View all</a><?php endif; ?>
        </div>
        <div class="recent-check-list">
          <?php if ($recentChecks): ?>
            <?php foreach ($recentChecks as $check):
              $statusClass = dashboard_status_class($check['status']);
            ?>
              <a class="recent-check-item" href="audit_logs.php?id=<?=urlencode($check['guild_id'])?>&name=<?=urlencode($check['guild_name'])?>">
                <span class="check-state <?=$statusClass?>"><i class="bi <?= $statusClass === 'running' ? 'bi-arrow-repeat' : ($statusClass === 'completed' ? 'bi-check2' : 'bi-exclamation-lg') ?>"></i></span>
                <span class="check-copy">
                  <strong><?=htmlspecialchars($check['guild_name'])?></strong>
                  <small><?=number_format($check['participants'])?> participants · <?=htmlspecialchars(dashboard_time_ago($check['timestamp'], $check['time_label']))?></small>
                </span>
                <em class="check-pill <?=$statusClass?>"><?=htmlspecialchars(dashboard_status_label($check['status']))?></em>
              </a>
            <?php endforeach; ?>
          <?php else: ?>
            <div class="compact-empty">
              <span><i class="bi bi-check2"></i></span>
              <strong>No checks recorded yet</strong>
              <p>Run your first activity check and it will appear here.</p>
            </div>
          <?php endif; ?>
        </div>
      </article>
    </section>

    <section id="servers" class="server-section">
      <div class="section-title dashboard-section-title">
        <div><span class="kicker">Your workspace</span><h2>Connected servers</h2><p>Select a server to manage checks, automation, logs, and participation settings.</p></div>
        <span class="server-total"><?=count($joined)?> connected</span>
      </div>

      <?php if ($joined): ?>
        <div class="server-grid modern-server-grid">
          <?php foreach ($joined as $guild): $icon = guild_icon_url($guild); ?>
            <article class="server-card modern-server-card">
              <div class="server-blur" style="background-image:url('<?=htmlspecialchars($icon)?>')"></div>
              <div class="server-content">
                <div class="server-icon-wrap"><img src="<?=htmlspecialchars($icon)?>" alt=""><i></i></div>
                <div class="server-meta">
                  <h3><?=htmlspecialchars((string)$guild['name'])?></h3>
                  <span><i class="inline-online-dot"></i> Rallybit connected</span>
                </div>
                <a class="button small primary" href="manage.php?id=<?=urlencode((string)$guild['id'])?>&name=<?=urlencode((string)$guild['name'])?>">Manage</a>
              </div>
            </article>
          <?php endforeach; ?>
        </div>
      <?php else: ?>
        <div class="empty-panel dashboard-empty">
          <span class="empty-icon"><i class="bi bi-hdd-rack"></i></span>
          <h3>No connected servers yet</h3>
          <p>Add Rallybit to a server you administer, then refresh this page.</p>
          <a class="button primary" href="https://discord.com/oauth2/authorize?client_id=<?=urlencode(DISCORD_CLIENT_ID)?>&permissions=1100451671286&scope=bot%20applications.commands">Add your first server</a>
        </div>
      <?php endif; ?>
    </section>

    <?php if ($missing): ?>
      <section class="server-section other-servers-section">
        <div class="section-title dashboard-section-title">
          <div><h2>Available servers</h2><p>You administer these servers, but Rallybit is not connected yet.</p></div>
        </div>
        <div class="server-grid modern-server-grid muted-grid">
          <?php foreach ($missing as $guild): $icon = guild_icon_url($guild); ?>
            <article class="server-card modern-server-card disconnected">
              <div class="server-content">
                <div class="server-icon-wrap"><img src="<?=htmlspecialchars($icon)?>" alt=""></div>
                <div class="server-meta"><h3><?=htmlspecialchars((string)$guild['name'])?></h3><span>Not connected</span></div>
                <a class="button small secondary" href="https://discord.com/oauth2/authorize?client_id=<?=urlencode(DISCORD_CLIENT_ID)?>&permissions=1100451671286&scope=bot%20applications.commands&guild_id=<?=urlencode((string)$guild['id'])?>&disable_guild_select=true">Connect</a>
              </div>
            </article>
          <?php endforeach; ?>
        </div>
      </section>
    <?php endif; ?>
  </main>
</div>
<script>
  document.body.classList.add('ready');
  document.querySelectorAll('.chart-column').forEach((column) => {
    column.addEventListener('mouseenter', () => column.classList.add('is-hovered'));
    column.addEventListener('mouseleave', () => column.classList.remove('is-hovered'));
  });
</script>
</body>
</html>
