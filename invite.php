<?php
require_once __DIR__ . '/dashboard/config.php';
if (DISCORD_CLIENT_ID === '') { http_response_code(503); exit('Rallybit invite is not configured yet.'); }
$query = http_build_query([
  'client_id' => DISCORD_CLIENT_ID,
  'permissions' => '1100451671286',
  'scope' => 'bot applications.commands',
]);
header('Location: https://discord.com/oauth2/authorize?' . $query, true, 302);
exit;
