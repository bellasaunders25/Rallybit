<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
// Isolated view fixture: no Discord credentials, production settings or writes.
require_once __DIR__ . '/../dashboard/includes/overview_data.php';
require_once __DIR__ . '/../dashboard/includes/dashboard_sidebar.php';
$_SESSION = ['user_id'=>'123456789012345678','username'=>'Test user','access_token'=>'test-only'];
$_GET = ['id'=>'123456789012345679'];
$checkedLogin = false;
function check_login(): void { $GLOBALS['checkedLogin'] = true; }
function discord_api_request(string $endpoint, string $token): array { return [['id'=>'123456789012345679','name'=>'Example <server>','permissions'=>8]]; }
function has_admin_permission(int $permissions): bool { return ($permissions & 8) === 8; }
function is_logged_in(): bool { return true; }
function is_bot_admin(): bool { return false; }
function user_avatar_url(int $size): string { return '/assets/brand/rallybit-mark.svg'; }
foreach (['BOT_GUILDS_FILE','AUDIT_EVENTS_FILE','OPEN_TICKETS_FILE','WELCOME_SETTINGS_FILE','LEVEL_SETTINGS_FILE','AUDIT_SETTINGS_FILE','AUTOMATION_SCHEDULES_FILE','TICKET_PANELS_FILE'] as $name) define($name, $name);
function load_json_data(string $filename): array {
    return match ($filename) {
        BOT_GUILDS_FILE => ['123456789012345679'=>['member_count'=>42]],
        AUDIT_EVENTS_FILE => ['123456789012345679'=>[['timestamp'=>gmdate('c',time()-60),'type'=>'moderation','title'=>'Test warning','actor_name'=>'Test staff']]],
        OPEN_TICKETS_FILE => ['123456789012345679'=>[['status'=>'open'],['status'=>'closed']]],
        default => [],
    };
}
function load_guild_file_settings(string $filename, string $id, array $defaults = []): array { return $defaults; }
$source = file_get_contents(__DIR__ . '/../dashboard/overview.php');
$source = str_replace(["require_once 'includes/functions.php';", "require_once 'includes/overview_data.php';"], '', $source);
ob_start(); eval('?>' . $source); $html = ob_get_clean();
foreach ([
    $checkedLogin,
    str_contains($html, '<h1>Overview</h1>'),
    str_contains($html, 'Example &lt;server&gt;'),
    !str_contains($html, 'Example <server>'),
    str_contains($html, '<strong>42</strong>'),
    str_contains($html, 'Test warning'),
    str_contains($html, 'workspace-server'),
    str_contains($html, '/docs/commands.html'),
    str_contains($html, 'Server Activity'),
    str_contains($html, 'Recent Actions'),
    str_contains($html, 'Moderation Log'),
    !str_contains($html, 'Buy now'),
] as $index => $condition) { if (!$condition) throw new RuntimeException("Overview render check {$index} failed."); }
echo "Dashboard page render tests passed.\n";
