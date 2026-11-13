<?php
require_once 'includes/functions.php';
if (!DISCORD_CLIENT_ID || !DISCORD_REDIRECT_URI) exit('Discord OAuth is not configured.');
if (isset($_GET['redirect'])) {
    $target = (string)$_GET['redirect'];
    if (str_starts_with($target, '/') && !str_starts_with($target, '//')) $_SESSION['redirect_to'] = $target;
}
$state = bin2hex(random_bytes(24));
$_SESSION['oauth_state'] = $state;
// Allow parallel sign-in tabs without replacing another valid state.
$_SESSION['oauth_states'] = array_filter($_SESSION['oauth_states'] ?? [], fn($expiry) => (int)$expiry > time());
$_SESSION['oauth_states'][$state] = time() + 600;
$_SESSION['oauth_states'] = array_slice($_SESSION['oauth_states'], -8, null, true);
header('Cache-Control: no-store, private');
session_write_close();
$params = ['client_id'=>DISCORD_CLIENT_ID,'redirect_uri'=>DISCORD_REDIRECT_URI,'response_type'=>'code','scope'=>'identify guilds','state'=>$state];
$oauthUrl = 'https://discord.com/oauth2/authorize?' . http_build_query($params);
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="Sign in to Rallybit to manage activity checks, moderation, tickets, automation, and community tools.">
  <meta name="theme-color" content="#4f46e5">
  <meta name="robots" content="noindex,nofollow">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="Rallybit">
  <meta property="og:title" content="Rallybit Dashboard">
  <meta property="og:description" content="One dashboard for activity checks, moderation, tickets, automation, and community tools.">
  <meta property="og:url" content="https://rallybit.pages.dev/dashboard/">
  <meta property="og:image" content="https://rallybit.pages.dev/assets/brand/rallybit-social-v2.png">
  <meta property="og:image:secure_url" content="https://rallybit.pages.dev/assets/brand/rallybit-social-v2.png">
  <meta property="og:image:type" content="image/png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="Rallybit — One dashboard for your Discord community">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="Rallybit Dashboard">
  <meta name="twitter:description" content="One dashboard for activity checks, moderation, tickets, automation, and community tools.">
  <meta name="twitter:image" content="https://rallybit.pages.dev/assets/brand/rallybit-social-v2.png">
  <link rel="canonical" href="https://rallybit.pages.dev/dashboard/">
  <link rel="icon" href="/favicon.ico" sizes="any">
  <title>Rallybit Dashboard</title>
<script src="/assets/theme.js?v=10.0"></script><link rel="icon" type="image/svg+xml" href="/assets/brand/rallybit-mark.svg"></head>
<body>
  <main>
    <p>Opening Rallybit sign-in…</p>
    <p><a href="<?=htmlspecialchars($oauthUrl, ENT_QUOTES, 'UTF-8')?>">Continue with Discord</a></p>
  </main>
  <script>window.location.replace(<?=json_encode($oauthUrl, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT)?>);</script>
</body>
</html>
