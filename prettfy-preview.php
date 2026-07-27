<?php
declare(strict_types=1);

header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');
header('X-Robots-Tag: noindex, nofollow, noarchive');

$token = $_GET['token'] ?? '';
if (!is_string($token) || preg_match('/^[a-f0-9]{32}$/D', $token) !== 1) {
    http_response_code(400);
    echo '<!doctype html><title>Invalid review</title><p>This Prettfy review link is invalid.</p>';
    exit;
}

$previewDirectory = __DIR__ . '/rallybitbot/data/prettfy_previews';
$previewPath = $previewDirectory . '/' . $token . '.html';
if (!is_file($previewPath)) {
    http_response_code(404);
    echo '<!doctype html><title>Review expired</title><p>This Prettfy review has expired or has already been completed.</p>';
    exit;
}

$modifiedAt = filemtime($previewPath);
if ($modifiedAt === false || $modifiedAt < time() - 3600) {
    @unlink($previewPath);
    http_response_code(410);
    echo '<!doctype html><title>Review expired</title><p>This Prettfy review has expired. Start the command again for a new review.</p>';
    exit;
}

readfile($previewPath);
