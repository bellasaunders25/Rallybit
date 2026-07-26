<?php
/** Rallybit dashboard configuration. Secrets are read from the root .env file. */
function load_env_file(string $path): void {
    if (!is_file($path)) return;
    foreach (file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $line = trim($line);
        if ($line === '' || str_starts_with($line, '#') || !str_contains($line, '=')) continue;
        [$name, $value] = array_map('trim', explode('=', $line, 2));
        $value = trim($value, "\"'");
        if (getenv($name) === false) putenv("{$name}={$value}");
        $_ENV[$name] = $_ENV[$name] ?? $value;
    }
}
load_env_file(dirname(__DIR__) . '/.env');

function env_value(string $key, string $default = ''): string {
    $value = getenv($key);
    return $value === false ? $default : trim($value);
}

define('DATA_DIR', dirname(__DIR__) . '/data');
define('DISCORD_CLIENT_ID', env_value('DISCORD_CLIENT_ID'));
define('DISCORD_CLIENT_SECRET', env_value('DISCORD_CLIENT_SECRET'));
define('DISCORD_REDIRECT_URI', env_value('DISCORD_REDIRECT_URI'));
define('MASTER_ADMIN_ID', env_value('MASTER_ADMIN_ID'));
define('BOT_API_URL', rtrim(env_value('BOT_API_URL', 'http://127.0.0.1:8080'), '/'));
define('BOT_API_KEY', env_value('BOT_API_KEY'));
define('DASHBOARD_SESSION_DAYS', max(1, min(90, (int)env_value('DASHBOARD_SESSION_DAYS', '30'))));

define('SETTINGS_FILE', 'activity_settings.json');

define('QUIZ_SETTINGS_FILE', 'quiz_settings.json');
define('COMMUNITY_SETTINGS_FILE', 'community_settings.json');
define('SECURITY_SETTINGS_FILE', 'security_settings.json');
define('MODERATION_PERMISSIONS_FILE', 'moderation_permissions.json');
define('GIVEAWAY_SETTINGS_FILE', 'giveaway_settings.json');
define('WELCOME_SETTINGS_FILE', 'welcome_settings.json');
define('INVITE_TRACKING_FILE', 'invite_tracking.json');
define('LEVEL_SETTINGS_FILE', 'level_settings.json');
define('LEVEL_STATS_FILE', 'level_stats.json');
define('AUTOROLE_SETTINGS_FILE', 'autorole_settings.json');
define('REACTION_ROLES_FILE', 'reaction_roles.json');
define('VERIFICATION_SETTINGS_FILE', 'verification_settings.json');
define('TICKET_SETTINGS_FILE', 'ticket_settings.json');
define('TICKET_PANELS_FILE', 'ticket_panels.json');
define('OPEN_TICKETS_FILE', 'open_tickets.json');
define('AUTOMATION_SCHEDULES_FILE', 'automation_schedules.json');
define('GIVEAWAY_HISTORY_FILE', 'giveaway_history.json');
define('TICKET_HISTORY_FILE', 'ticket_history.json');
define('REPORT_SETTINGS_FILE', 'report_settings.json');
define('REPORTS_FILE', 'reports.json');
define('REVIEW_SETTINGS_FILE', 'review_settings.json');
define('BANNED_SERVERS_FILE', 'banned_servers.json');
define('BANNED_USERS_FILE', 'banned_users.json');
define('BOT_SETTINGS_FILE', 'bot_settings.json');
define('LOG_FILE', 'activity_log.json');
define('NOTICE_FILE', 'notice.json');
define('PREMIUM_ENTITLEMENTS_FILE', 'premium_entitlements.json');
define('STAFF_SHIFTS_FILE', 'staff_shifts.json');
define('LIMITS_FILE', 'limits.json');
define('BOT_GUILDS_FILE', 'bot_guilds.json');
define('ADMINS_FILE', 'admins.json');
define('BADGES_FILE', 'badges.json');

function check_login(): void {
    if (empty($_SESSION['user_id'])) {
        header('Location: login.php');
        exit;
    }
    if (function_exists('ensure_discord_access_token') && !ensure_discord_access_token()) {
        $_SESSION = [];
        session_regenerate_id(true);
        $redirect = (string)($_SERVER['REQUEST_URI'] ?? '/dashboard/index.php');
        if (!str_starts_with($redirect, '/dashboard/') || str_starts_with($redirect, '//')) $redirect = '/dashboard/index.php';
        header('Location: login.php?redirect=' . rawurlencode($redirect));
        exit;
    }
}
