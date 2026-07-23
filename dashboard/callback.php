<?php
require_once 'includes/functions.php';
if (empty($_GET['code']) || empty($_GET['state']) || !hash_equals((string)($_SESSION['oauth_state'] ?? ''), (string)$_GET['state'])) {
    http_response_code(400); exit('Invalid OAuth request. Please try signing in again.');
}
unset($_SESSION['oauth_state']);
if (DISCORD_CLIENT_SECRET === '') exit('Discord OAuth is not configured.');
$ch = curl_init('https://discord.com/api/oauth2/token');
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_POSTFIELDS => http_build_query([
        'client_id' => DISCORD_CLIENT_ID,
        'client_secret' => DISCORD_CLIENT_SECRET,
        'grant_type' => 'authorization_code',
        'code' => (string)$_GET['code'],
        'redirect_uri' => DISCORD_REDIRECT_URI,
    ]),
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER => ['Content-Type: application/x-www-form-urlencoded'],
    CURLOPT_TIMEOUT => 10,
]);
$response = curl_exec($ch);
$status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);
$token = json_decode((string)$response, true);
if ($status !== 200 || empty($token['access_token'])) { http_response_code(502); exit('Discord sign-in failed. Please try again.'); }
$user = discord_api_request('/users/@me', (string)$token['access_token']);
if (!$user || empty($user['id'])) { http_response_code(502); exit('Unable to load your Discord profile.'); }
session_regenerate_id(true);
$_SESSION['access_token'] = (string)$token['access_token'];
$_SESSION['user_id'] = (string)$user['id'];
$_SESSION['username'] = (string)($user['username'] ?? 'Discord user');
$_SESSION['global_name'] = (string)($user['global_name'] ?? $user['username'] ?? 'Discord user');
$_SESSION['avatar'] = $user['avatar'] ?? null;
$redirect = (string)($_SESSION['redirect_to'] ?? 'index.php');
unset($_SESSION['redirect_to']);
if (str_contains($redirect, "\r") || str_contains($redirect, "\n") || str_starts_with($redirect, '//')) {
    $redirect = 'index.php';
}
if (str_starts_with($redirect, '/')) {
    if (!str_starts_with($redirect, '/dashboard/')) $redirect = '/dashboard/index.php';
} elseif (!preg_match('/^[a-zA-Z0-9_\/.?=&%-]+$/', $redirect)) {
    $redirect = 'index.php';
}
header('Location: ' . $redirect); exit;
