<?php
require_once 'includes/functions.php';
if (!DISCORD_CLIENT_ID || !DISCORD_REDIRECT_URI) exit('Discord OAuth is not configured.');
if (isset($_GET['redirect'])) {
    $target = (string)$_GET['redirect'];
    if (str_starts_with($target, '/') && !str_starts_with($target, '//')) $_SESSION['redirect_to'] = $target;
}
$state = bin2hex(random_bytes(24));
$_SESSION['oauth_state'] = $state;
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
  <meta property="og:url" content="https://rallybits.com/dashboard/">
  <meta property="og:image" content="https://rallybits.com/assets/brand/rallybit-social-card.png">
  <meta property="og:image:secure_url" content="https://rallybits.com/assets/brand/rallybit-social-card.png">
  <meta property="og:image:type" content="image/png">
  <meta property="og:image:width" content="1730">
  <meta property="og:image:height" content="909">
  <meta property="og:image:alt" content="Rallybit — One dashboard for your Discord community">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="Rallybit Dashboard">
  <meta name="twitter:description" content="One dashboard for activity checks, moderation, tickets, automation, and community tools.">
  <meta name="twitter:image" content="https://rallybits.com/assets/brand/rallybit-social-card.png">
  <link rel="canonical" href="https://rallybits.com/dashboard/">
  <link rel="icon" href="/favicon.ico" sizes="any">
  <title>Rallybit Dashboard</title>
</head>
<body>
  <main>
    <p>Opening Rallybit sign-in…</p>
    <p><a href="<?=htmlspecialchars($oauthUrl, ENT_QUOTES, 'UTF-8')?>">Continue with Discord</a></p>
  </main>
  <script>window.location.replace(<?=json_encode($oauthUrl, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT)?>);</script>
</body>
</html>
