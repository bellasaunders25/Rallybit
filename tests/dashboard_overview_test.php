<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
require_once __DIR__ . '/../dashboard/includes/overview_data.php';
function expect(bool $condition, string $message): void { if (!$condition) throw new RuntimeException($message); }
$now = new DateTimeImmutable('2026-11-13T12:00:00Z');
$data = server_overview_activity([
    ['timestamp'=>'2026-11-13T10:00:00Z','type'=>'moderation','title'=>'User warned'],
    ['timestamp'=>'2026-11-12T13:00:00+01:00','type'=>'configuration','title'=>'Settings saved'],
    ['timestamp'=>'2026-11-13T13:00:00Z','type'=>'security'], // Future record.
    ['timestamp'=>'2026-11-01T00:00:00Z','type'=>'moderation'], // Outside range.
    ['timestamp'=>'not a date','type'=>'security'],
    ['type'=>'security'],
    null,
], 7, $now);
expect(count($data['series']) === 7, 'Seven UTC days required.');
expect($data['series']['2026-11-13'] === 1, 'Current-day record missing.');
expect($data['series']['2026-11-12'] === 1, 'Timezone conversion failed.');
expect(count($data['events']) === 2, 'Invalid, future or old records counted.');
expect(count($data['moderation']) === 1, 'Moderation filter failed.');
expect($data['events'][0]['title'] === 'User warned', 'Events not newest first.');
$chart = overview_chart_points($data['series']);
expect(count($chart['points']) === 7, 'Missing chart points.');
foreach ($chart['points'] as [$x,$y]) expect($x >= 52 && $x <= 600 && $y >= 35 && $y <= 210, 'Chart point out of bounds.');
$empty = server_overview_activity([], 30, $now);
expect(count($empty['series']) === 30 && array_sum($empty['series']) === 0, 'Empty history must remain empty.');
expect(overview_chart_points($empty['series'])['max'] === 1, 'Empty chart divides by zero.');
ob_start();
render_overview_events([['timestamp'=>'2026-11-13T10:00:00Z','title'=>'<script>alert(1)</script>','actor_name'=>'<img src=x>','description'=>'<svg onload=x>','type'=>'" onclick="x']]);
$html = ob_get_clean();
expect(!str_contains($html, '<script>') && !str_contains($html, '<img') && !str_contains($html, '<svg'), 'Audit output must be escaped.');
ob_start(); render_overview_events([]); $html = ob_get_clean();
expect(str_contains($html, 'No events recorded'), 'Empty state missing.');
echo "Dashboard overview tests passed.\n";
