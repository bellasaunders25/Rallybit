<?php
declare(strict_types=1);

require_once __DIR__ . '/../config.php';
require_once __DIR__ . '/security.php';
check_rate_limit(100, 60);

function api_request(string $endpoint, array $payload = [], int $timeout = 8): ?array {
    if (!function_exists('curl_init') || BOT_API_KEY === '') return null;
    $ch = curl_init(BOT_API_URL . $endpoint);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => json_encode($payload, JSON_UNESCAPED_SLASHES),
        CURLOPT_HTTPHEADER => ['X-Api-Key: ' . BOT_API_KEY, 'Content-Type: application/json'],
        CURLOPT_TIMEOUT => $timeout,
    ]);
    $response = curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);
    if (!is_string($response)) {
        error_log("Rallybit API request failed ({$status}): {$error}");
        return null;
    }
    $decoded = json_decode($response, true, 512, JSON_BIGINT_AS_STRING);
    if ($status < 200 || $status >= 300) {
        $detail = is_array($decoded) ? (string)($decoded['error'] ?? '') : '';
        error_log("Rallybit API request failed ({$status}): {$error} {$detail}");
    }
    return is_array($decoded) ? $decoded : null;
}

function load_json_data(string $filename): array {
    $result = api_request('/api/json/read', ['file' => basename($filename)]);
    return isset($result['data']) && is_array($result['data']) ? $result['data'] : [];
}

function save_json_data(string $filename, array $data): bool {
    $result = api_request('/api/json/write', ['file' => basename($filename), 'data' => $data], 20);
    return !empty($result['ok']);
}

function get_premium_entitlements(): array {
    $result = api_request('/api/premium/entitlements');
    if (isset($result['data']) && is_array($result['data'])) return $result['data'];
    return ['users' => [], 'servers' => [], 'history' => []];
}

function premium_record_active(mixed $record): bool {
    if (!is_array($record) || empty($record['plan'])) return false;
    $expiresAt = trim((string)($record['expires_at'] ?? ''));
    if ($expiresAt === '') return true;
    $timestamp = strtotime($expiresAt);
    return $timestamp !== false && $timestamp > time();
}

function dashboard_plan_for_guild(array $guild, array $entitlements, string $userId, bool $developer = false): array {
    $plans = [
        'free' => ['key' => 'free', 'name' => 'Free', 'rank' => 0],
        'community' => ['key' => 'community', 'name' => 'Community', 'rank' => 10],
        'pro' => ['key' => 'pro', 'name' => 'Pro', 'rank' => 20],
        'network' => ['key' => 'network', 'name' => $developer ? 'Developer preview' : 'Network', 'rank' => 30],
    ];
    if ($developer) return $plans['network'];

    $result = $plans['free'];
    $guildId = (string)($guild['id'] ?? '');
    $servers = is_array($entitlements['servers'] ?? null) ? $entitlements['servers'] : [];
    $users = is_array($entitlements['users'] ?? null) ? $entitlements['users'] : [];
    $serverRecord = $servers[$guildId] ?? null;
    if (premium_record_active($serverRecord)) {
        $serverPlan = strtolower((string)($serverRecord['plan'] ?? ''));
        if (in_array($serverPlan, ['community', 'pro'], true)) $result = $plans[$serverPlan];
    }

    $userRecord = $users[$userId] ?? null;
    if (!empty($guild['owner']) && premium_record_active($userRecord) && strtolower((string)($userRecord['plan'] ?? '')) === 'network') {
        $result = $plans['network'];
    }
    return $result;
}

function discord_oauth_token_request(array $payload): array|false {
    if (!function_exists('curl_init') || DISCORD_CLIENT_ID === '' || DISCORD_CLIENT_SECRET === '') return false;
    $ch = curl_init('https://discord.com/api/oauth2/token');
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => http_build_query($payload + [
            'client_id' => DISCORD_CLIENT_ID,
            'client_secret' => DISCORD_CLIENT_SECRET,
        ]),
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => ['Content-Type: application/x-www-form-urlencoded'],
        CURLOPT_TIMEOUT => 10,
    ]);
    $response = curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($status < 200 || $status >= 300 || !is_string($response)) return false;
    $decoded = json_decode($response, true, 512, JSON_BIGINT_AS_STRING);
    return is_array($decoded) && !empty($decoded['access_token']) ? $decoded : false;
}

function refresh_discord_access_token(): bool {
    $refreshToken = (string)($_SESSION['refresh_token'] ?? '');
    if ($refreshToken === '') return false;
    $token = discord_oauth_token_request([
        'grant_type' => 'refresh_token',
        'refresh_token' => $refreshToken,
    ]);
    if (!$token) return false;
    $_SESSION['access_token'] = (string)$token['access_token'];
    $_SESSION['refresh_token'] = (string)($token['refresh_token'] ?? $refreshToken);
    $_SESSION['token_expires_at'] = time() + max(60, (int)($token['expires_in'] ?? 604800));
    $_SESSION['token_refreshed_at'] = time();
    session_regenerate_id(true);
    return true;
}

function ensure_discord_access_token(): bool {
    if (empty($_SESSION['access_token'])) return false;
    $expiresAt = (int)($_SESSION['token_expires_at'] ?? 0);
    return $expiresAt === 0 || $expiresAt > time() + 120 || refresh_discord_access_token();
}

function discord_api_once(string $endpoint, string $token): array {
    if (!function_exists('curl_init') || $token === '') return ['status' => 0, 'data' => false];
    $ch = curl_init('https://discord.com/api/v10' . $endpoint);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => ['Authorization: Bearer ' . $token, 'Content-Type: application/json'],
        CURLOPT_USERAGENT => 'DiscordBot (Rallybit Dashboard, 2.0)',
        CURLOPT_TIMEOUT => 8,
    ]);
    $response = curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if (!is_string($response)) return ['status' => $status, 'data' => false];
    $decoded = json_decode($response, true, 512, JSON_BIGINT_AS_STRING);
    return ['status' => $status, 'data' => is_array($decoded) ? $decoded : false];
}

function discord_api_request(string $endpoint, string $token): array|false {
    $isSessionToken = !empty($_SESSION['access_token']) && hash_equals((string)$_SESSION['access_token'], $token);
    if ($isSessionToken) {
        if (!ensure_discord_access_token()) return false;
        $token = (string)$_SESSION['access_token'];
    }
    $result = discord_api_once($endpoint, $token);
    if ($result['status'] === 401 && $isSessionToken && refresh_discord_access_token()) {
        $result = discord_api_once($endpoint, (string)$_SESSION['access_token']);
    }
    return $result['status'] >= 200 && $result['status'] < 300 && is_array($result['data']) ? $result['data'] : false;
}

function has_admin_permission(int $permissions): bool {
    return ($permissions & 0x8) === 0x8;
}

function is_logged_in(): bool {
    return !empty($_SESSION['user_id']);
}

function is_bot_admin(): bool {
    $userId = (string)($_SESSION['user_id'] ?? '');
    if ($userId !== '' && MASTER_ADMIN_ID !== '' && hash_equals((string)MASTER_ADMIN_ID, $userId)) return true;
    $admins = load_json_data(ADMINS_FILE);
    $ids = $admins['admin_ids'] ?? $admins['admins'] ?? $admins;
    return is_array($ids) && in_array($userId, array_map('strval', $ids), true);
}

function check_vps_connection(): bool {
    if (!function_exists('curl_init')) return false;
    $ch = curl_init(BOT_API_URL . '/api/health');
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 4]);
    curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return $status === 200;
}

function get_detailed_health(): array {
    if (!function_exists('curl_init')) return ['status' => 'offline'];
    $ch = curl_init(BOT_API_URL . '/api/health');
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 5]);
    $response = curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($status !== 200 || !is_string($response)) return ['status' => 'offline'];
    $decoded = json_decode($response, true, 512, JSON_BIGINT_AS_STRING);
    return is_array($decoded) ? $decoded : ['status' => 'offline'];
}

function user_avatar_url(int $size = 96): string {
    if (!empty($_SESSION['avatar']) && !empty($_SESSION['user_id'])) {
        return 'https://cdn.discordapp.com/avatars/' . rawurlencode((string)$_SESSION['user_id']) . '/' . rawurlencode((string)$_SESSION['avatar']) . '.png?size=' . $size;
    }
    return 'https://cdn.discordapp.com/embed/avatars/0.png';
}

function guild_icon_url(array $guild, int $size = 256): string {
    if (!empty($guild['icon'])) {
        return 'https://cdn.discordapp.com/icons/' . rawurlencode((string)$guild['id']) . '/' . rawurlencode((string)$guild['icon']) . '.png?size=' . $size;
    }
    $id = (int)($guild['id'] ?? 0);
    return 'https://cdn.discordapp.com/embed/avatars/' . (($id >> 22) % 6) . '.png';
}


function get_guild_resources(string $guildId): array {
    $result = api_request('/api/guild/resources', ['guild_id' => $guildId], 15);
    return is_array($result) ? $result : ['channels' => [], 'roles' => []];
}

function run_bot_action(string $guildId, string $actorId, string $action, array $params = []): array {
    $result = api_request('/api/dashboard/action', [
        'guild_id' => $guildId,
        'actor_id' => $actorId,
        'action' => $action,
        'params' => $params,
    ], 50);
    return is_array($result) ? $result : ['error' => 'The bot API did not respond.'];
}

function save_guild_file_settings(string $filename, string $guildId, array $settings): bool {
    $all = load_json_data($filename);
    $all[$guildId] = $settings;
    return save_json_data($filename, $all);
}

function load_guild_file_settings(string $filename, string $guildId, array $defaults = []): array {
    $all = load_json_data($filename);
    $saved = $all[$guildId] ?? [];
    return is_array($saved) ? array_replace_recursive($defaults, $saved) : $defaults;
}

require_once __DIR__ . '/dashboard_sidebar.php';
