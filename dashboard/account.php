<?php
require_once 'includes/functions.php';
check_login();
check_rate_limit(30, 60);
$userId = (string)$_SESSION['user_id'];
$stats = load_json_data('global_stats.json');
$profile = is_array($stats[$userId] ?? null) ? $stats[$userId] : [];
$message = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    validate_csrf_token($_POST['csrf_token'] ?? '');
    $bio = substr(trim((string)($_POST['bio'] ?? '')), 0, 180);
    $website = trim((string)($_POST['website'] ?? ''));
    if ($website !== '' && (!filter_var($website, FILTER_VALIDATE_URL) || !in_array(strtolower((string)parse_url($website, PHP_URL_SCHEME)), ['http', 'https'], true))) {
        $message = 'Please enter a valid http:// or https:// website URL.';
    } else {
        $profile = array_merge($profile, [
            'username' => (string)$_SESSION['username'],
            'display_name' => (string)($_SESSION['global_name'] ?? $_SESSION['username']),
            'bio' => $bio,
            'website' => $website,
        ]);
        $stats[$userId] = $profile;
        $message = save_json_data('global_stats.json', $stats) ? 'Profile saved successfully.' : 'The profile service could not save your changes.';
    }
}
$avatar = user_avatar_url();
?>
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Account — Rallybit</title><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"><link rel="stylesheet" href="/dashboard/style.css?v=5.6"><link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" sizes="32x32" href="/assets/brand/rallybit-icon-32.png"><link rel="icon" type="image/png" sizes="192x192" href="/assets/brand/rallybit-icon-192.png"><link rel="apple-touch-icon" href="/assets/brand/apple-touch-icon.png"></head><body><div class="dash-shell"><?php render_dashboard_sidebar('account'); ?><main class="dash-main"><header class="dash-header"><div><span class="kicker">Personal profile</span><h1>Your account</h1><p>Manage the public details attached to your Rallybit participation profile.</p></div></header><?php if($message):?><div class="alert <?=str_contains($message,'successfully')?'success':'error'?>"><?=htmlspecialchars($message)?></div><?php endif;?><section class="profile-card"><div class="profile-head"><img src="<?=htmlspecialchars($avatar)?>" alt=""><div><h2><?=htmlspecialchars((string)($_SESSION['global_name'] ?? $_SESSION['username']))?></h2><p>@<?=htmlspecialchars((string)$_SESSION['username'])?> · Discord ID <?=htmlspecialchars($userId)?></p></div></div><form method="post"><input type="hidden" name="csrf_token" value="<?=htmlspecialchars(get_csrf_token())?>"><label>Short bio<textarea name="bio" maxlength="180" rows="4" placeholder="Tell communities a little about yourself."><?=htmlspecialchars((string)($profile['bio'] ?? ''))?></textarea></label><label>Website<input type="url" name="website" value="<?=htmlspecialchars((string)($profile['website'] ?? ''))?>" placeholder="https://example.com"></label><div class="form-actions"><button class="button primary" type="submit">Save profile</button><a class="button secondary" href="profile.php?u=<?=urlencode($userId)?>">View public profile</a></div></form></section></main></div></body></html>
