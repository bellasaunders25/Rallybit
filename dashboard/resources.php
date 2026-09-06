<?php
require_once 'includes/functions.php';
check_login();

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');

function resource_response(int $status, array $payload): never {
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_SLASHES);
    exit;
}

$guildId = preg_replace('/\D/', '', (string)($_GET['id'] ?? ''));
if ($guildId === '') {
    resource_response(400, ['ok' => false, 'error' => 'A valid server ID is required.']);
}

$guilds = discord_api_request('/users/@me/guilds', (string)$_SESSION['access_token']);
if (!is_array($guilds)) {
    resource_response(502, ['ok' => false, 'error' => 'Discord could not confirm your server access.']);
}

$allowed = false;
foreach ($guilds as $guild) {
    if ((string)($guild['id'] ?? '') === $guildId && has_admin_permission((int)($guild['permissions'] ?? 0))) {
        $allowed = true;
        break;
    }
}
if (!$allowed) {
    resource_response(403, ['ok' => false, 'error' => 'You do not have permission to manage this server.']);
}

$resources = get_guild_resources($guildId);
if (empty($resources['ok']) && empty($resources['channels']) && empty($resources['roles'])) {
    resource_response(502, ['ok' => false, 'error' => 'Rallybit could not refresh this server right now.']);
}

$channels = [];
$categories = [];
foreach (($resources['channels'] ?? []) as $channel) {
    if (!is_array($channel)) continue;
    $id = preg_replace('/\D/', '', (string)($channel['id'] ?? ''));
    $name = trim((string)($channel['name'] ?? ''));
    $type = (string)($channel['type'] ?? '');
    if ($id === '' || $name === '') continue;
    $item = ['id' => $id, 'name' => $name];
    if (in_array($type, ['text', 'news'], true)) $channels[] = $item;
    if ($type === 'category') $categories[] = $item;
}

$botTopRolePosition = (int)($resources['bot_top_role_position'] ?? PHP_INT_MAX);
$roles = [];
foreach (($resources['roles'] ?? []) as $role) {
    if (!is_array($role) || !empty($role['managed']) || (string)($role['id'] ?? '') === $guildId) continue;
    if ((int)($role['position'] ?? 0) >= $botTopRolePosition) continue;
    $id = preg_replace('/\D/', '', (string)($role['id'] ?? ''));
    $name = trim((string)($role['name'] ?? ''));
    if ($id === '' || $name === '') continue;
    $roles[] = [
        'id' => $id,
        'name' => $name,
        'color' => max(0, min(0xFFFFFF, (int)($role['color'] ?? 0))),
    ];
}

resource_response(200, [
    'ok' => true,
    'channels' => $channels,
    'categories' => $categories,
    'roles' => $roles,
]);
