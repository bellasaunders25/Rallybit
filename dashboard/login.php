<?php
require_once 'config.php';
if (session_status() === PHP_SESSION_NONE) session_start();
if (!DISCORD_CLIENT_ID || !DISCORD_REDIRECT_URI) exit('Discord OAuth is not configured.');
if (isset($_GET['redirect'])) {
    $target = (string)$_GET['redirect'];
    if (str_starts_with($target, '/') && !str_starts_with($target, '//')) $_SESSION['redirect_to'] = $target;
}
$state = bin2hex(random_bytes(24));
$_SESSION['oauth_state'] = $state;
$params = ['client_id'=>DISCORD_CLIENT_ID,'redirect_uri'=>DISCORD_REDIRECT_URI,'response_type'=>'code','scope'=>'identify guilds','state'=>$state];
header('Location: https://discord.com/oauth2/authorize?' . http_build_query($params));
exit;
