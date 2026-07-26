<?php

declare(strict_types=1);

function render_dashboard_sidebar(
    string $active = 'overview',
    ?string $guildId = null,
    ?string $guildName = null,
    ?int $serverCount = null,
    ?array $plan = null
): void {
    $loggedIn = is_logged_in();
    $displayName = (string)($_SESSION['global_name'] ?? $_SESSION['username'] ?? 'Member');
    $username = (string)($_SESSION['username'] ?? 'member');
    $plan = $plan ?? (is_array($_SESSION['dashboard_plan'] ?? null) ? $_SESSION['dashboard_plan'] : ['key' => 'free', 'name' => 'Free']);
    $planKey = in_array(($plan['key'] ?? 'free'), ['free', 'community', 'pro', 'network'], true) ? (string)$plan['key'] : 'free';
    $guildQuery = $guildId !== null
        ? '?' . http_build_query(['id' => $guildId, 'name' => $guildName ?? 'Server'])
        : '';

    $link = static function (string $key, string $href, string $icon, string $label, ?int $count = null) use ($active): void {
        $activeClass = $active === $key ? ' class="active" aria-current="page"' : '';
        ?>
        <a<?=$activeClass?> href="<?=htmlspecialchars($href)?>">
            <span class="nav-icon" aria-hidden="true"><i class="bi <?=htmlspecialchars($icon)?>"></i></span>
            <span><?=htmlspecialchars($label)?></span>
            <?php if ($count !== null): ?><b class="nav-count"><?=number_format($count)?></b><?php endif; ?>
        </a>
        <?php
    };
    ?>
    <aside class="dash-sidebar">
        <a class="dash-brand plan-brand plan-<?=htmlspecialchars($planKey)?>" href="/index.html">
            <img class="plan-logo" src="/assets/brand/rallybit-icon.png" alt="">
            <span>Rallybit<small><?=htmlspecialchars((string)($plan['name'] ?? 'Free'))?> plan</small></span>
        </a>

        <?php if ($loggedIn): ?>
            <span class="sidebar-section-label">Workspace</span>
            <nav aria-label="Workspace navigation">
                <?php $link('overview', '/dashboard/', 'bi-house', 'Overview'); ?>
                <?php $link('servers', '/dashboard/#servers', 'bi-hdd-rack', 'Servers', $serverCount); ?>
                <?php $link('account', '/dashboard/account.php', 'bi-person', 'Account'); ?>
            </nav>

            <?php if ($guildId !== null): ?>
                <span class="sidebar-section-label sidebar-section-spaced">Current server</span>
                <nav aria-label="Server navigation">
                    <?php $link('settings', '/dashboard/manage.php' . $guildQuery, 'bi-sliders', 'Activity settings'); ?>
                    <?php $link('control', '/dashboard/control.php' . $guildQuery, 'bi-grid', 'Control center'); ?>
                    <?php $link('logs', '/dashboard/audit_logs.php' . $guildQuery, 'bi-clock-history', 'Audit logs'); ?>
                </nav>
            <?php endif; ?>
        <?php endif; ?>

        <span class="sidebar-section-label sidebar-section-spaced">Resources</span>
        <nav aria-label="Resource navigation">
            <?php $link('status', '/dashboard/status.php', 'bi-activity', 'Status'); ?>
            <?php $link('docs', '/docs/', 'bi-book', 'Documentation'); ?>
            <?php if ($loggedIn && is_bot_admin()): ?>
                <?php $link('admin', '/dashboard/admin.php', 'bi-tools', 'Developer tools'); ?>
            <?php endif; ?>
        </nav>

        <?php if ($loggedIn): ?>
            <div class="sidebar-user">
                <img src="<?=htmlspecialchars(user_avatar_url(96))?>" alt="">
                <div>
                    <strong><?=htmlspecialchars($displayName)?></strong>
                    <span>@<?=htmlspecialchars($username)?></span>
                </div>
                <a class="sidebar-logout" href="/dashboard/logout.php" aria-label="Sign out" title="Sign out"><i class="bi bi-box-arrow-right"></i></a>
            </div>
        <?php endif; ?>
    </aside>
    <?php
}
