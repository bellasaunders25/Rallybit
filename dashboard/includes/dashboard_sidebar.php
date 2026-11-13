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
    <link rel="stylesheet" href="/dashboard/workspace.css?v=11.0">
    <aside class="dash-sidebar">
        <a class="dash-brand plan-brand plan-<?=htmlspecialchars($planKey)?>" href="/index.html">
            <img class="plan-logo" src="/assets/brand/rallybit-mark.svg" alt="">
            <span>Rallybit</span>
        </a>

        <?php if ($loggedIn): ?>
            <nav aria-label="Workspace navigation">
                <?php $link('overview', $guildId === null ? '/dashboard/' : '/dashboard/overview.php' . $guildQuery, 'bi-house', 'Overview'); ?>
                <?php $link('servers', '/dashboard/#servers', 'bi-hdd-rack', 'Servers', $serverCount); ?>
                <?php if ($guildId === null) $link('account', '/dashboard/account.php', 'bi-person', 'Account'); ?>
            </nav>

            <?php if ($guildId !== null): ?>
                <nav aria-label="Server navigation">
                    <?php $link('control', '/dashboard/control.php' . $guildQuery, 'bi-grid', 'Modules'); ?>
                    <?php $link('moderation', '/dashboard/control.php' . $guildQuery . '#moderation', 'bi-shield', 'Moderation'); ?>
                    <?php $link('tickets', '/dashboard/control.php' . $guildQuery . '#tickets', 'bi-ticket-perforated', 'Tickets'); ?>
                    <?php $link('welcome', '/dashboard/control.php' . $guildQuery . '#welcome', 'bi-person-plus', 'Welcome'); ?>
                    <?php $link('automation', '/dashboard/control.php' . $guildQuery . '#automation', 'bi-lightning-charge', 'Automations'); ?>
                    <?php $link('logs', '/dashboard/audit_logs.php' . $guildQuery, 'bi-journal-text', 'Logs'); ?>
                    <?php $link('analytics', '/dashboard/overview.php' . $guildQuery . '#server-activity', 'bi-bar-chart', 'Analytics'); ?>
                    <?php $link('settings', '/dashboard/manage.php' . $guildQuery, 'bi-gear', 'Settings'); ?>
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

        <div class="sidebar-appearance"><button class="theme-toggle" type="button" data-theme-toggle><i class="bi bi-moon" aria-hidden="true"></i><span data-theme-label>Dark mode</span></button></div>

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
    <?php if ($loggedIn): ?>
    <header class="workspace-topbar">
        <div class="workspace-switcher"><i class="bi bi-discord" aria-hidden="true"></i>
            <label class="sr-only" for="workspace-server">Select server</label>
            <select id="workspace-server" data-workspace-server>
                <option value="">All servers</option>
                <?php
                $serverChoices = is_array($_SESSION['dashboard_servers'] ?? null) ? $_SESSION['dashboard_servers'] : [];
                if ($guildId !== null) $serverChoices[$guildId] = $guildName ?? 'Server';
                foreach ($serverChoices as $choiceId => $choiceName):
                    if (!preg_match('/^\d{15,22}$/', (string)$choiceId)) continue;
                ?>
                <option value="<?=htmlspecialchars((string)$choiceId)?>" <?=$guildId === (string)$choiceId ? 'selected' : ''?>><?=htmlspecialchars((string)$choiceName)?></option>
                <?php endforeach; ?>
            </select>
        </div>
        <form class="workspace-search" action="/docs/commands.html" method="get" role="search">
            <i class="bi bi-search" aria-hidden="true"></i><input type="search" name="q" placeholder="Search commands…" aria-label="Search commands">
            <kbd aria-hidden="true">/</kbd>
        </form>
        <a class="workspace-status" href="/dashboard/status.php" aria-label="Service status" title="Service status"><i class="bi bi-bell" aria-hidden="true"></i></a>
        <a class="workspace-avatar" href="/dashboard/account.php" aria-label="Your account"><img src="<?=htmlspecialchars(user_avatar_url(96))?>" alt=""></a>
    </header>
    <script src="/dashboard/workspace.js?v=11.0" defer></script>
    <?php endif; ?>
    <?php
}
