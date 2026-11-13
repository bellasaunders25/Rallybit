<?php
declare(strict_types=1);

/** Summarise retained audit records, not an invented message/member history. */
function server_overview_activity(array $records, int $days, ?DateTimeImmutable $now = null): array {
    $zone = new DateTimeZone('UTC');
    $now = ($now ?? new DateTimeImmutable('now', $zone))->setTimezone($zone);
    $series = [];
    for ($offset = $days - 1; $offset >= 0; $offset--) {
        $series[$now->modify("-{$offset} days")->format('Y-m-d')] = 0;
    }
    $events = [];
    $moderation = [];
    foreach ($records as $row) {
        if (!is_array($row)) continue;
        try { $date = new DateTimeImmutable((string)($row['timestamp'] ?? ''), $zone); }
        catch (Throwable) { continue; }
        if (empty($row['timestamp']) || $date > $now) continue;
        $key = $date->setTimezone($zone)->format('Y-m-d');
        if (!array_key_exists($key, $series)) continue;
        $series[$key]++;
        $events[] = $row;
        if (in_array($row['type'] ?? '', ['moderation', 'security'], true)) $moderation[] = $row;
    }
    $sort = static fn(array $a, array $b): int => strtotime($b['timestamp']) <=> strtotime($a['timestamp']);
    usort($events, $sort);
    usort($moderation, $sort);
    return ['series' => $series, 'events' => $events, 'moderation' => $moderation];
}

function overview_chart_points(array $series): array {
    $max = max(1, ...array_values($series));
    $points = [];
    foreach (array_values($series) as $index => $value) {
        $points[] = [round(52 + $index * 548 / max(1, count($series) - 1), 2), round(210 - $value / $max * 175, 2)];
    }
    return ['max' => $max, 'points' => $points, 'path' => implode(' ', array_map(static fn($p) => implode(',', $p), $points))];
}

function render_overview_events(array $events): void {
    $icons = ['moderation'=>'bi-shield-exclamation', 'security'=>'bi-shield-lock', 'configuration'=>'bi-gear', 'messages'=>'bi-chat-left-text', 'members'=>'bi-person', 'tickets'=>'bi-ticket-perforated', 'roles'=>'bi-people', 'commands'=>'bi-terminal'];
    if (!$events) { echo '<div class="overview-no-events"><i class="bi bi-check2-circle"></i><p>No events recorded in this period.</p></div>'; return; }
    foreach (array_slice($events, 0, 5) as $event) {
        $icon = $icons[$event['type'] ?? ''] ?? 'bi-activity';
        $stamp = strtotime((string)$event['timestamp']);
        $description = (string)($event['actor_name'] ?? '');
        if (!empty($event['description'])) $description .= ($description !== '' ? ' · ' : '') . (string)$event['description'];
        ?>
        <div class="overview-event <?=in_array($event['type'] ?? '', ['moderation','security'], true) ? 'event-moderation' : ''?>"><span class="overview-event-icon"><i class="bi <?=htmlspecialchars($icon)?>" aria-hidden="true"></i></span><div><strong><?=htmlspecialchars((string)($event['title'] ?? 'Server action'))?></strong><p><?=htmlspecialchars($description)?></p></div><time datetime="<?=htmlspecialchars((string)$event['timestamp'])?>" title="<?=gmdate('j M Y H:i', $stamp)?> UTC"><?=gmdate('j M, H:i', $stamp)?></time></div>
        <?php
    }
}
