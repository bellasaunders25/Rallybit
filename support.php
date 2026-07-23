<?php
require_once __DIR__ . '/dashboard/config.php';
$url = env_value('SUPPORT_SERVER_URL');
if ($url === '' || !filter_var($url, FILTER_VALIDATE_URL)) { header('Location: /docs/faq.html', true, 302); exit; }
header('Location: ' . $url, true, 302);
exit;
