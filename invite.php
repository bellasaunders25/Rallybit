<?php
require_once __DIR__ . '/dashboard/config.php';
if (DISCORD_CLIENT_ID === '') { http_response_code(503); exit('Rallybit invite is not configured yet.'); }
$query = http_build_query([
  'client_id' => DISCORD_CLIENT_ID,
  'permissions' => '1100451671286',
  'scope' => 'bot applications.commands',
]);
$inviteUrl = 'https://discord.com/oauth2/authorize?' . $query;
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="Add Rallybit to your Discord community for activity checks, moderation, tickets, automation, and more.">
  <meta name="theme-color" content="#4f46e5">
  <meta name="robots" content="noindex,nofollow">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="Rallybit">
  <meta property="og:title" content="Add Rallybit to Discord">
  <meta property="og:description" content="Activity checks, moderation, tickets, automation, and community tools in one configurable bot.">
  <meta property="og:url" content="https://raspberrypi.tail5cb034.ts.net/invite.php">
  <meta property="og:image" content="https://raspberrypi.tail5cb034.ts.net/assets/brand/rallybit-social-card.png">
  <meta property="og:image:secure_url" content="https://raspberrypi.tail5cb034.ts.net/assets/brand/rallybit-social-card.png">
  <meta property="og:image:type" content="image/png">
  <meta property="og:image:width" content="1730">
  <meta property="og:image:height" content="909">
  <meta property="og:image:alt" content="Rallybit — One dashboard for your Discord community">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="Add Rallybit to Discord">
  <meta name="twitter:description" content="Activity checks, moderation, tickets, automation, and community tools in one configurable bot.">
  <meta name="twitter:image" content="https://raspberrypi.tail5cb034.ts.net/assets/brand/rallybit-social-card.png">
  <link rel="canonical" href="https://raspberrypi.tail5cb034.ts.net/invite.php">
  <link rel="icon" href="/favicon.ico" sizes="any">
  <title>Add Rallybit to Discord</title>
</head>
<body>
  <main>
    <p>Opening the Rallybit invite…</p>
    <p><a href="<?=htmlspecialchars($inviteUrl, ENT_QUOTES, 'UTF-8')?>">Continue to Discord</a></p>
  </main>
  <script>window.location.replace(<?=json_encode($inviteUrl, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT)?>);</script>
</body>
</html>
