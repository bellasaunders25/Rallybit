<?php
require_once 'includes/functions.php';
check_login();
$guild_id = preg_replace('/\D/', '', (string)($_GET['id'] ?? ''));
if (!$guild_id) { header('Location: index.php'); exit; }
$guild_name = trim((string)($_GET['name'] ?? 'Server'));
$guilds = discord_api_request('/users/@me/guilds', $_SESSION['access_token']) ?: [];
$allowed = false;
foreach ($guilds as $guild) {
    if ((string)$guild['id'] === $guild_id && has_admin_permission((int)$guild['permissions'])) {
        $allowed = true; $guild_name = (string)$guild['name']; break;
    }
}
if (!$allowed) { http_response_code(403); exit('You do not have permission to manage this server.'); }

$resources = get_guild_resources($guild_id);
$channels = array_values(array_filter($resources['channels'] ?? [], static fn($c) => in_array($c['type'] ?? '', ['text','news'], true)));
$categories = array_values(array_filter($resources['channels'] ?? [], static fn($c) => ($c['type'] ?? '') === 'category'));
$botTopRolePosition = (int)($resources['bot_top_role_position'] ?? PHP_INT_MAX);
$guildRoles = array_values(array_filter($resources['roles'] ?? [], static fn($r) => empty($r['managed']) && (string)($r['id'] ?? '') !== $guild_id));
$roles = array_values(array_filter($guildRoles, static fn($r) => (int)($r['position'] ?? 0) < $botTopRolePosition));

$welcomeDefaults = [
    'welcome_enabled'=>false,'welcome_channel_id'=>null,'welcome_message'=>'Welcome {user} to **{server}**! You are member **#{member_count}**.','welcome_embed'=>true,'welcome_dm'=>false,
    'goodbye_enabled'=>false,'goodbye_channel_id'=>null,'goodbye_message'=>'**{username}** has left **{server}**. We now have {member_count} members.','goodbye_embed'=>true,'invite_tracking'=>true,
];
$levelDefaults = ['enabled'=>false,'xp_min'=>15,'xp_max'=>25,'cooldown_seconds'=>60,'announce_channel_id'=>null,'announce_message'=>'🎉 {user} reached **level {level}**!','reward_roles'=>[]];
$verificationDefaults = ['enabled'=>false,'channel_id'=>null,'role_id'=>null,'remove_role_id'=>null,'title'=>'Verify your account','description'=>'Press the button below to gain access to the server.','button_label'=>'Verify'];
$ticketDefaults = ['default_category_id'=>null,'log_channel_id'=>null,'support_role_ids'=>[],'one_ticket_per_member'=>true,'transcript_limit'=>500,'auto_delete_minutes'=>0,'ticket_name'=>'ticket-{username}','welcome_message'=>'Thanks for reaching out, {user}. A team member will be with you shortly. Please explain what you need help with and include any useful context.'];
$securityDefaults = ['agegate'=>['enabled'=>false,'minimum_days'=>7,'dm_member'=>true],'trap'=>['enabled'=>false,'action'=>'ban','channel_name'=>'do-not-text-here'],'modules'=>[]];
$moderationDefaults = ['warn_role_ids'=>[],'timeout_role_ids'=>[],'kick_role_ids'=>[],'ban_role_ids'=>[]];
$reportDefaults = ['channel_id'=>null];
$reviewDefaults = ['staff_channel_id'=>null,'member_channel_id'=>null];

$welcome = load_guild_file_settings(WELCOME_SETTINGS_FILE, $guild_id, $welcomeDefaults);
$levels = load_guild_file_settings(LEVEL_SETTINGS_FILE, $guild_id, $levelDefaults);
$autorolesAll = load_json_data(AUTOROLE_SETTINGS_FILE); $autoroles = array_map('strval', is_array($autorolesAll[$guild_id] ?? null) ? $autorolesAll[$guild_id] : []);
$verification = load_guild_file_settings(VERIFICATION_SETTINGS_FILE, $guild_id, $verificationDefaults);
$tickets = load_guild_file_settings(TICKET_SETTINGS_FILE, $guild_id, $ticketDefaults);
$security = load_guild_file_settings(SECURITY_SETTINGS_FILE, $guild_id, $securityDefaults);
$moderation = load_guild_file_settings(MODERATION_PERMISSIONS_FILE, $guild_id, $moderationDefaults);
$reports = load_guild_file_settings(REPORT_SETTINGS_FILE, $guild_id, $reportDefaults);
$reviews = load_guild_file_settings(REVIEW_SETTINGS_FILE, $guild_id, $reviewDefaults);
$automationsAll = load_json_data(AUTOMATION_SCHEDULES_FILE); $automations = is_array($automationsAll[$guild_id] ?? null) ? $automationsAll[$guild_id] : [];
$message = '';
$error = '';

function clean_id(mixed $value): ?string {
    $id = preg_replace('/\D/', '', (string)$value);
    return $id !== '' ? $id : null;
}
function bool_post(string $key): bool { return isset($_POST[$key]); }
function selected_value(mixed $a, mixed $b): string { return (string)$a === (string)$b ? ' selected' : ''; }

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    validate_csrf_token($_POST['csrf_token'] ?? '');
    $operation = (string)($_POST['operation'] ?? '');
    try {
        if ($operation === 'live_action') {
            $action = trim((string)($_POST['action'] ?? ''));
            $ticketPanelOptions = [];
            $optionNames = is_array($_POST['ticket_option_name'] ?? null) ? $_POST['ticket_option_name'] : [];
            $optionDescriptions = is_array($_POST['ticket_option_description'] ?? null) ? $_POST['ticket_option_description'] : [];
            $optionEmojis = is_array($_POST['ticket_option_emoji'] ?? null) ? $_POST['ticket_option_emoji'] : [];
            $optionCategories = is_array($_POST['ticket_option_category_id'] ?? null) ? $_POST['ticket_option_category_id'] : [];
            $optionRoles = is_array($_POST['ticket_option_support_role_id'] ?? null) ? $_POST['ticket_option_support_role_id'] : [];
            foreach ($optionNames as $index => $rawName) {
                $optionName = trim((string)$rawName);
                if ($optionName === '') continue;
                $ticketPanelOptions[] = [
                    'name'=>function_exists('mb_substr') ? mb_substr($optionName,0,100) : substr($optionName,0,100),
                    'description'=>function_exists('mb_substr') ? mb_substr(trim((string)($optionDescriptions[$index] ?? 'Speak privately with the support team.')),0,100) : substr(trim((string)($optionDescriptions[$index] ?? 'Speak privately with the support team.')),0,100),
                    'emoji'=>function_exists('mb_substr') ? mb_substr(trim((string)($optionEmojis[$index] ?? '')),0,100) : substr(trim((string)($optionEmojis[$index] ?? '')),0,100),
                    'category_id'=>clean_id($optionCategories[$index] ?? ''),
                    'support_role_id'=>clean_id($optionRoles[$index] ?? ''),
                ];
            }
            $params = [
                'channel_id'=>clean_id($_POST['channel_id'] ?? ''),
                'ping_role_id'=>clean_id($_POST['ping_role_id'] ?? ''),
                'required_role_id'=>clean_id($_POST['required_role_id'] ?? ''),
                'category'=>trim((string)($_POST['category'] ?? 'mixed')),
                'duration_seconds'=>max(15,min(120,(int)($_POST['duration_seconds'] ?? 30))),
                'duration_minutes'=>max(1,min(10080,(int)($_POST['duration_minutes'] ?? 10))),
                'winners'=>max(1,min(20,(int)($_POST['winners'] ?? 1))),
                'prize'=>trim((string)($_POST['prize'] ?? 'Community giveaway')),
                'prompt'=>trim((string)($_POST['prompt'] ?? '')),
                'giveaway_id'=>trim((string)($_POST['giveaway_id'] ?? '')),
                'schedule_id'=>trim((string)($_POST['schedule_id'] ?? '')),
                'user_id'=>clean_id($_POST['user_id'] ?? ''),
                'reason'=>trim((string)($_POST['reason'] ?? 'Dashboard action')),
                'minutes'=>max(1,min(40320,(int)($_POST['minutes'] ?? 10))),
                'enabled'=>bool_post('enabled'),
                'action'=>trim((string)($_POST['trap_action'] ?? 'ban')),
                'role_id'=>clean_id($_POST['role_id'] ?? ''),
                'remove_role_id'=>clean_id($_POST['remove_role_id'] ?? ''),
                'support_role_id'=>clean_id($_POST['support_role_id'] ?? ''),
                'category_id'=>clean_id($_POST['category_id'] ?? ''),
                'message_id'=>clean_id($_POST['message_id'] ?? ''),
                'emoji'=>trim((string)($_POST['emoji'] ?? '✅')),
                'xp'=>max(0,(int)($_POST['xp'] ?? 0)),
                'name'=>trim((string)($_POST['name'] ?? 'Support')),
                'title'=>trim((string)($_POST['title'] ?? '')),
                'description'=>trim((string)($_POST['description'] ?? '')),
                'button_label'=>trim((string)($_POST['button_label'] ?? '')),
                'options'=>$ticketPanelOptions,
                'select_placeholder'=>trim((string)($_POST['select_placeholder'] ?? 'Select a ticket type…')),
                'color'=>trim((string)($_POST['panel_color'] ?? '#7C6CFF')),
                'author_name'=>trim((string)($_POST['panel_author_name'] ?? '')),
                'author_icon_url'=>trim((string)($_POST['panel_author_icon_url'] ?? '')),
                'header_image_url'=>trim((string)($_POST['panel_header_image_url'] ?? '')),
                'thumbnail_url'=>trim((string)($_POST['panel_thumbnail_url'] ?? '')),
                'image_url'=>trim((string)($_POST['panel_image_url'] ?? '')),
                'footer_text'=>trim((string)($_POST['panel_footer_text'] ?? '')),
                'footer_icon_url'=>trim((string)($_POST['panel_footer_icon_url'] ?? '')),
                'show_author'=>bool_post('panel_show_author'),
                'show_option_details'=>bool_post('panel_show_option_details'),
                'show_workload'=>bool_post('panel_show_workload'),
                'show_guidance'=>bool_post('panel_show_guidance'),
                'show_timestamp'=>bool_post('panel_show_timestamp'),

            ];
            $result = run_bot_action($guild_id, (string)$_SESSION['user_id'], $action, array_filter($params, static fn($v) => $v !== null && $v !== ''));
            if (!empty($result['ok'])) $message = (string)($result['message'] ?? 'Action completed.');
            else $error = (string)($result['error'] ?? 'The action failed.');
        } elseif ($operation === 'save_welcome') {
            $updatedWelcome = array_replace($welcome, [
                'welcome_enabled'=>bool_post('welcome_enabled'),'welcome_channel_id'=>clean_id($_POST['welcome_channel_id'] ?? ''),'welcome_message'=>trim((string)($_POST['welcome_message'] ?? '')),
                'welcome_embed'=>bool_post('welcome_embed'),'welcome_dm'=>bool_post('welcome_dm'),'goodbye_enabled'=>bool_post('goodbye_enabled'),'goodbye_channel_id'=>clean_id($_POST['goodbye_channel_id'] ?? ''),
                'goodbye_message'=>trim((string)($_POST['goodbye_message'] ?? '')),'goodbye_embed'=>bool_post('goodbye_embed'),'invite_tracking'=>bool_post('invite_tracking'),
            ]);
            if ($updatedWelcome['welcome_enabled'] && !$updatedWelcome['welcome_channel_id']) {
                $error = 'Choose a welcome channel before enabling welcome messages.';
            } elseif ($updatedWelcome['welcome_enabled'] && $updatedWelcome['welcome_message'] === '') {
                $error = 'Enter a welcome message before enabling welcome messages.';
            } elseif ($updatedWelcome['goodbye_enabled'] && !$updatedWelcome['goodbye_channel_id']) {
                $error = 'Choose a goodbye channel before enabling goodbye messages.';
            } elseif ($updatedWelcome['goodbye_enabled'] && $updatedWelcome['goodbye_message'] === '') {
                $error = 'Enter a goodbye message before enabling goodbye messages.';
            } else {
                $welcome = $updatedWelcome;
                if (save_guild_file_settings(WELCOME_SETTINGS_FILE, $guild_id, $welcome)) {
                    $message = 'Welcome, goodbye and invite settings saved.';
                } else {
                    $error = 'Could not save welcome settings.';
                }
            }
        } elseif ($operation === 'save_levels') {
            $updatedLevels = array_replace($levels, [
                'enabled'=>bool_post('level_enabled'),'xp_min'=>max(1,min(100,(int)($_POST['xp_min'] ?? 15))),'xp_max'=>max(1,min(200,(int)($_POST['xp_max'] ?? 25))),
                'cooldown_seconds'=>max(10,min(600,(int)($_POST['cooldown_seconds'] ?? 60))),'announce_channel_id'=>clean_id($_POST['announce_channel_id'] ?? ''),
                'announce_message'=>trim((string)($_POST['announce_message'] ?? '🎉 {user} reached **level {level}**!')),
            ]);
            $rewardLevel = (int)($_POST['reward_level'] ?? 0); $rewardRole = clean_id($_POST['reward_role_id'] ?? '');
            $assignableRoleIds = array_map(static fn($role) => (string)$role['id'], $roles);
            if ($updatedLevels['xp_max'] < $updatedLevels['xp_min']) {
                $error = 'Maximum XP must be greater than or equal to minimum XP.';
            } elseif ($rewardLevel < 0 || $rewardLevel > 1000) {
                $error = 'Reward levels must be between 1 and 1000.';
            } elseif ($rewardRole && !in_array($rewardRole, $assignableRoleIds, true)) {
                $error = 'Rallybit cannot assign that reward role. Move its bot role higher and try again.';
            } else {
                if ($rewardLevel > 0 && $rewardRole) $updatedLevels['reward_roles'][(string)$rewardLevel] = $rewardRole;
                ksort($updatedLevels['reward_roles'], SORT_NUMERIC);
                $levels = $updatedLevels;
                $message = save_guild_file_settings(LEVEL_SETTINGS_FILE, $guild_id, $levels) ? 'Levelling settings saved.' : 'Could not save levelling settings.';
            }
        } elseif ($operation === 'remove_level_reward') {
            $removeLevel = (int)($_POST['reward_level'] ?? 0);
            if ($removeLevel < 1 || $removeLevel > 1000) {
                $error = 'Invalid reward level.';
            } else {
                unset($levels['reward_roles'][(string)$removeLevel]);
                $message = save_guild_file_settings(LEVEL_SETTINGS_FILE, $guild_id, $levels) ? "Level {$removeLevel} reward removed." : 'Could not remove the level reward.';
            }
        } elseif ($operation === 'save_roles') {
            $autoroles = array_values(array_unique(array_filter(array_map('clean_id', $_POST['autorole_ids'] ?? []))));
            $autorolesAll[$guild_id] = $autoroles;
            $message = save_json_data(AUTOROLE_SETTINGS_FILE, $autorolesAll) ? 'Autoroles saved.' : 'Could not save autoroles.';
        } elseif ($operation === 'save_verification') {
            $verification = array_replace($verification, [
                'enabled'=>bool_post('verification_enabled'),'channel_id'=>clean_id($_POST['verification_channel_id'] ?? ''),'role_id'=>clean_id($_POST['verification_role_id'] ?? ''),
                'remove_role_id'=>clean_id($_POST['verification_remove_role_id'] ?? ''),'title'=>trim((string)($_POST['verification_title'] ?? 'Verify your account')),
                'description'=>trim((string)($_POST['verification_description'] ?? 'Press the button below to gain access to the server.')),'button_label'=>trim((string)($_POST['verification_button_label'] ?? 'Verify')),
            ]);
            $message = save_guild_file_settings(VERIFICATION_SETTINGS_FILE, $guild_id, $verification) ? 'Verification settings saved. Use the Discord /verification setup command once to publish a panel, or use the site launcher when available.' : 'Could not save verification settings.';
        } elseif ($operation === 'save_tickets') {
            $supportRoles = array_values(array_unique(array_filter(array_map('clean_id', $_POST['ticket_support_role_ids'] ?? []))));
            $tickets = array_replace($tickets, [
                'default_category_id'=>clean_id($_POST['ticket_category_id'] ?? ''),'log_channel_id'=>clean_id($_POST['ticket_log_channel_id'] ?? ''),'support_role_ids'=>$supportRoles,
                'one_ticket_per_member'=>bool_post('one_ticket_per_member'),'transcript_limit'=>max(50,min(2000,(int)($_POST['transcript_limit'] ?? 500))),
                'auto_delete_minutes'=>max(0,min(10080,(int)($_POST['auto_delete_minutes'] ?? 0))),
                'ticket_name'=>trim((string)($_POST['ticket_name'] ?? 'ticket-{username}')),'welcome_message'=>trim((string)($_POST['ticket_welcome_message'] ?? '')),
            ]);
            $message = save_guild_file_settings(TICKET_SETTINGS_FILE, $guild_id, $tickets) ? 'Ticket settings saved.' : 'Could not save ticket settings.';
        } elseif ($operation === 'save_moderation_permissions') {
            $guildRoleIds = array_map(static fn($role) => (string)$role['id'], $guildRoles);
            foreach (['warn','timeout','kick','ban'] as $moderationAction) {
                $field = "moderation_{$moderationAction}_role_ids";
                $submittedRoleIds = array_values(array_unique(array_filter(array_map('clean_id', $_POST[$field] ?? []))));
                $moderation["{$moderationAction}_role_ids"] = array_values(array_intersect($submittedRoleIds, $guildRoleIds));
            }
            $reports['channel_id'] = clean_id($_POST['report_channel_id'] ?? '');
            $moderationSaved = save_guild_file_settings(MODERATION_PERMISSIONS_FILE, $guild_id, $moderation);
            $reportsSaved = save_guild_file_settings(REPORT_SETTINGS_FILE, $guild_id, $reports);
            $message = $moderationSaved && $reportsSaved ? 'Moderation permissions and report settings saved.' : 'Could not save moderation settings.';
        } elseif ($operation === 'save_reviews') {
            $availableChannelIds = array_map(static fn($channel) => (string)$channel['id'], $channels);
            $staffReviewChannel = clean_id($_POST['staff_review_channel_id'] ?? '');
            $memberReviewChannel = clean_id($_POST['member_review_channel_id'] ?? '');
            if (!$staffReviewChannel || !in_array($staffReviewChannel, $availableChannelIds, true)) {
                $error = 'Choose a valid staff review channel.';
            } elseif (!$memberReviewChannel || !in_array($memberReviewChannel, $availableChannelIds, true)) {
                $error = 'Choose a valid member review channel.';
            } else {
                $reviews = ['staff_channel_id'=>$staffReviewChannel,'member_channel_id'=>$memberReviewChannel];
                $message = save_guild_file_settings(REVIEW_SETTINGS_FILE, $guild_id, $reviews) ? 'Review channels saved.' : 'Could not save review channels.';
            }
        } elseif ($operation === 'save_security') {
            $security['agegate']['enabled'] = bool_post('agegate_enabled');
            $security['agegate']['minimum_days'] = max(0,min(3650,(int)($_POST['agegate_days'] ?? 7)));
            $security['agegate']['dm_member'] = bool_post('agegate_dm');
            $security['trap']['enabled'] = bool_post('trap_enabled');
            $security['trap']['action'] = in_array($_POST['trap_action'] ?? '', ['ban','kick'], true) ? $_POST['trap_action'] : 'ban';
            $security['trap']['channel_name'] = trim((string)($_POST['trap_channel_name'] ?? 'do-not-text-here')) ?: 'do-not-text-here';
            $message = save_guild_file_settings(SECURITY_SETTINGS_FILE, $guild_id, $security) ? 'Security settings saved.' : 'Could not save security settings.';
        } elseif ($operation === 'add_automation') {
            $id = strtoupper(substr(bin2hex(random_bytes(6)), 0, 8));
            $interval = max(5,min(10080,(int)($_POST['automation_interval'] ?? 60)));
            $automations[$id] = [
                'schedule_id'=>$id,'kind'=>in_array($_POST['automation_kind'] ?? '', ['activity','quiz','pulse','icebreaker','giveaway'], true) ? $_POST['automation_kind'] : 'activity',
                'channel_id'=>clean_id($_POST['automation_channel_id'] ?? ''),'interval_minutes'=>$interval,'ping_role_id'=>clean_id($_POST['automation_ping_role_id'] ?? ''),
                'enabled'=>true,'next_run'=>time()+($interval*60),'created_by'=>(string)$_SESSION['user_id'],'created_at'=>gmdate('c'),
                'options'=>['message'=>trim((string)($_POST['automation_message'] ?? '')),'category'=>trim((string)($_POST['automation_category'] ?? 'mixed')),'duration_minutes'=>max(1,(int)($_POST['automation_duration'] ?? 30)),'duration_seconds'=>max(15,min(120,(int)($_POST['automation_duration'] ?? 30))),'winners'=>max(1,min(20,(int)($_POST['automation_winners'] ?? 1)))],
            ];
            $automationsAll[$guild_id] = $automations;
            $message = save_json_data(AUTOMATION_SCHEDULES_FILE, $automationsAll) ? "Automation {$id} added." : 'Could not save automation.';
        } elseif ($operation === 'remove_automation') {
            unset($automations[strtoupper(trim((string)($_POST['automation_id'] ?? '')))]); $automationsAll[$guild_id] = $automations;
            $message = save_json_data(AUTOMATION_SCHEDULES_FILE, $automationsAll) ? 'Automation removed.' : 'Could not update automations.';
        }
    } catch (Throwable $e) { $error = $e->getMessage(); }
}
$csrf = get_csrf_token();
$avatar = user_avatar_url(80);
function channel_picker(array $items, mixed $selected, string $fieldName, string $pickerId, bool $required = false, bool $categories = false): string {
    $selectedId = (string)($selected ?? '');
    $selectedName = '';
    foreach ($items as $item) {
        if ((string)$item['id'] === $selectedId) {
            $selectedName = ($categories ? '' : '#').(string)$item['name'];
            break;
        }
    }
    $placeholder = $categories ? 'Select a category' : 'Select a channel';
    $safeId = htmlspecialchars($pickerId);
    $menuId = $safeId.'-menu';
    $icon = $categories ? 'bi-folder' : 'bi-hash';
    $html = '<div class="channel-picker" data-channel-picker data-required="'.($required ? 'true' : 'false').'" id="'.$safeId.'">';
    $html .= '<input type="hidden" name="'.htmlspecialchars($fieldName).'" value="'.htmlspecialchars($selectedId).'" data-channel-picker-value>';
    $html .= '<button class="channel-picker-trigger" type="button" aria-haspopup="listbox" aria-expanded="false" aria-controls="'.$menuId.'"><span class="channel-picker-current"><i class="bi '.$icon.'" aria-hidden="true"></i><span data-channel-picker-label>'.htmlspecialchars($selectedName ?: $placeholder).'</span></span><i class="bi bi-chevron-down channel-picker-chevron" aria-hidden="true"></i></button>';
    $html .= '<div class="channel-picker-popover" data-channel-picker-popover id="'.$menuId.'" role="listbox" hidden>';
    $html .= '<div class="channel-picker-search"><i class="bi bi-search" aria-hidden="true"></i><input type="search" placeholder="Search '.($categories ? 'categories' : 'channels').'" aria-label="Search '.($categories ? 'categories' : 'channels').'" data-channel-picker-search></div>';
    $html .= '<div class="channel-picker-options">';
    if (!$required) {
        $html .= '<button type="button" class="channel-picker-option" data-channel-option data-value="" data-label="'.$placeholder.'" data-search="none" aria-selected="'.($selectedId === '' ? 'true' : 'false').'"><i class="bi bi-slash-circle" aria-hidden="true"></i><span>None</span><i class="bi bi-check2 channel-picker-check" aria-hidden="true"></i></button>';
    }
    foreach ($items as $item) {
        $id = htmlspecialchars((string)$item['id']);
        $rawName = ($categories ? '' : '#').(string)$item['name'];
        $name = htmlspecialchars($rawName);
        $search = htmlspecialchars(strtolower($rawName));
        $selectedOption = (string)$item['id'] === $selectedId;
        $html .= '<button type="button" class="channel-picker-option" data-channel-option data-value="'.$id.'" data-label="'.$name.'" data-search="'.$search.'" aria-selected="'.($selectedOption ? 'true' : 'false').'"><i class="bi '.$icon.'" aria-hidden="true"></i><span>'.$name.'</span><i class="bi bi-check2 channel-picker-check" aria-hidden="true"></i></button>';
    }
    if (!$items) $html .= '<div class="channel-picker-empty">No '.($categories ? 'categories' : 'channels').' are available.</div>';
    $html .= '<div class="channel-picker-empty" data-channel-picker-no-results hidden>No matching '.($categories ? 'categories' : 'channels').'.</div>';
    $html .= '</div></div><small class="channel-picker-error" data-channel-picker-error hidden>Choose a '.($categories ? 'category' : 'channel').' before continuing.</small></div>';
    return $html;
}
function single_role_picker(array $roles, mixed $selected, string $fieldName, string $pickerId, bool $required = false): string {
    $selectedId = (string)($selected ?? '');
    $selectedName = '';
    $selectedColour = '#667085';
    foreach ($roles as $role) {
        if ((string)$role['id'] === $selectedId) {
            $selectedName = '@'.(string)$role['name'];
            $colourValue = max(0, min(0xFFFFFF, (int)($role['color'] ?? 0)));
            $selectedColour = $colourValue > 0 ? sprintf('#%06X', $colourValue) : '#667085';
            break;
        }
    }
    if ($selectedName === '') $selectedId = '';
    $placeholder = 'Select a role';
    $safeId = htmlspecialchars($pickerId);
    $menuId = $safeId.'-menu';
    $html = '<div class="single-role-picker" data-single-role-picker data-required="'.($required ? 'true' : 'false').'" id="'.$safeId.'">';
    $html .= '<input type="hidden" name="'.htmlspecialchars($fieldName).'" value="'.htmlspecialchars($selectedId).'" data-single-role-picker-value>';
    $html .= '<button class="single-role-picker-trigger" type="button" aria-haspopup="listbox" aria-expanded="false" aria-controls="'.$menuId.'"><span class="single-role-picker-current"><span class="role-colour" data-single-role-picker-colour style="--role-colour:'.htmlspecialchars($selectedColour).'"'.($selectedName === '' ? ' hidden' : '').'></span><i class="bi bi-person-badge" data-single-role-picker-placeholder-icon aria-hidden="true"'.($selectedName !== '' ? ' hidden' : '').'></i><span data-single-role-picker-label>'.htmlspecialchars($selectedName ?: $placeholder).'</span></span><i class="bi bi-chevron-down single-role-picker-chevron" aria-hidden="true"></i></button>';
    $html .= '<div class="single-role-picker-popover" data-single-role-picker-popover id="'.$menuId.'" role="listbox" hidden>';
    $html .= '<div class="single-role-picker-search"><i class="bi bi-search" aria-hidden="true"></i><input type="search" placeholder="Search roles" aria-label="Search roles" data-single-role-picker-search></div>';
    $html .= '<div class="single-role-picker-options">';
    if (!$required) {
        $html .= '<button type="button" class="single-role-picker-option" data-single-role-option data-value="" data-label="'.$placeholder.'" data-colour="" data-search="none" aria-selected="'.($selectedId === '' ? 'true' : 'false').'"><i class="bi bi-slash-circle" aria-hidden="true"></i><span>None</span><i class="bi bi-check2 single-role-picker-check" aria-hidden="true"></i></button>';
    }
    foreach ($roles as $role) {
        $id = htmlspecialchars((string)$role['id']);
        $rawName = '@'.(string)$role['name'];
        $name = htmlspecialchars($rawName);
        $search = htmlspecialchars(strtolower($rawName));
        $colourValue = max(0, min(0xFFFFFF, (int)($role['color'] ?? 0)));
        $colour = $colourValue > 0 ? sprintf('#%06X', $colourValue) : '#667085';
        $selectedOption = (string)$role['id'] === $selectedId;
        $html .= '<button type="button" class="single-role-picker-option" data-single-role-option data-value="'.$id.'" data-label="'.$name.'" data-colour="'.htmlspecialchars($colour).'" data-search="'.$search.'" aria-selected="'.($selectedOption ? 'true' : 'false').'"><span class="role-colour" style="--role-colour:'.htmlspecialchars($colour).'"></span><span>'.$name.'</span><i class="bi bi-check2 single-role-picker-check" aria-hidden="true"></i></button>';
    }
    if (!$roles) $html .= '<div class="single-role-picker-empty">No roles are available.</div>';
    $html .= '<div class="single-role-picker-empty" data-single-role-picker-no-results hidden>No matching roles.</div>';
    $html .= '</div></div><small class="single-role-picker-error" data-single-role-picker-error hidden>Choose a role before continuing.</small></div>';
    return $html;
}
function role_picker(array $roles, array $selected, string $fieldName, string $pickerId): string {
    $selectedIds = array_map('strval', $selected);
    $selectedCount = count(array_intersect($selectedIds, array_map(static fn($role) => (string)$role['id'], $roles)));
    $summary = $selectedCount === 0 ? 'Choose roles' : ($selectedCount === 1 ? '1 role selected' : "{$selectedCount} roles selected");
    $safeId = htmlspecialchars($pickerId);
    $html = '<div class="role-picker" data-role-picker id="'.$safeId.'">';
    $html .= '<button class="role-picker-trigger" type="button" aria-expanded="false"><span data-role-picker-summary>'.htmlspecialchars($summary).'</span><i class="bi bi-chevron-down" aria-hidden="true"></i></button>';
    $html .= '<div class="role-picker-popover" data-role-picker-popover hidden>';
    $html .= '<div class="role-picker-search"><i class="bi bi-search" aria-hidden="true"></i><input type="search" placeholder="Search roles" aria-label="Search roles" data-role-picker-search></div>';
    $html .= '<div class="role-picker-options">';
    foreach ($roles as $role) {
        $id = htmlspecialchars((string)$role['id']);
        $rawName = '@'.(string)$role['name'];
        $name = htmlspecialchars($rawName);
        $search = htmlspecialchars(strtolower($rawName));
        $checked = in_array((string)$role['id'], $selectedIds, true) ? ' checked' : '';
        $colourValue = max(0, min(0xFFFFFF, (int)($role['color'] ?? 0)));
        $colour = $colourValue > 0 ? sprintf('#%06X', $colourValue) : '#667085';
        $html .= '<label class="role-picker-option" data-role-option data-search="'.$search.'">';
        $html .= '<input type="checkbox" name="'.htmlspecialchars($fieldName).'[]" value="'.$id.'"'.$checked.'>';
        $html .= '<span class="role-colour" style="--role-colour:'.htmlspecialchars($colour).'"></span><span class="role-name">'.$name.'</span><i class="bi bi-check2 role-check" aria-hidden="true"></i></label>';
    }
    if (!$roles) $html .= '<div class="role-picker-empty">No roles are available.</div>';
    $html .= '</div><div class="role-picker-footer"><span data-role-picker-count>'.$selectedCount.' selected</span><button type="button" data-role-picker-done>Done</button></div></div></div>';
    return $html;
}
function role_label(array $roles, mixed $roleId): string {
    foreach ($roles as $role) {
        if ((string)$role['id'] === (string)$roleId) return '@'.(string)$role['name'];
    }
    return 'Deleted or unavailable role';
}
?>
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Control Center — <?=htmlspecialchars($guild_name)?>
</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/dashboard/style.css?v=5.9">
<link rel="icon" href="/favicon.ico">
</head>
<body>
<div class="dash-shell">
<?php render_dashboard_sidebar('control', $guild_id, $guild_name); ?>
<main class="dash-main">
<header class="dash-header compact">
<div>
<a class="back-link" href="index.php"><i class="bi bi-arrow-left" aria-hidden="true"></i> All servers</a>
<span class="kicker">Rallybit 8.0</span>
<h1>
<?=htmlspecialchars($guild_name)?> Control Center</h1>
<p>Run Discord commands and configure every server module from the website. Slash commands stay available too.</p>
</div>
<span class="access-chip">Per-server settings</span>
</header>
<?php if($message):?>
<div class="alert success">
<?=htmlspecialchars($message)?>
</div>
<?php endif;?>
<?php if($error):?>
<div class="alert error">
<?=htmlspecialchars($error)?>
</div>
<?php endif;?>

<div class="module-console">
<aside class="module-menu">
<div class="module-menu-title"><span>Server modules</span><small>Choose a tool to configure</small></div>
<button class="active" type="button" data-module-target="actions"><i class="bi bi-lightning-charge"></i><span>Quick actions</span></button>
<button type="button" data-module-target="welcome"><i class="bi bi-person-plus"></i><span>Welcome</span></button>
<button type="button" data-module-target="levels"><i class="bi bi-bar-chart"></i><span>Levels</span></button>
<button type="button" data-module-target="autoroles"><i class="bi bi-people"></i><span>Autoroles</span></button>
<button type="button" data-module-target="verification"><i class="bi bi-patch-check"></i><span>Verification</span></button>
<button type="button" data-module-target="tickets"><i class="bi bi-ticket-perforated"></i><span>Tickets</span></button>
<button type="button" data-module-target="reviews"><i class="bi bi-star"></i><span>Reviews</span></button>
<button type="button" data-module-target="moderation"><i class="bi bi-shield-exclamation"></i><span>Moderation</span></button>
<button type="button" data-module-target="security"><i class="bi bi-shield-check"></i><span>Security</span></button>
<button type="button" data-module-target="automation"><i class="bi bi-clock-history"></i><span>Automation</span></button>
</aside>
<div class="module-content">
<section class="panel command-launcher" data-module-view="actions">
<div class="panel-heading">
<span><i class="bi bi-lightning-charge"></i></span>
<div>
<h2>Quick actions</h2>
<p>Choose an action and only the fields it needs will appear.</p>
</div>
</div>
<form method="post" class="launcher-shell" data-action-form>
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="live_action">
<div class="launcher-primary">
<label>Action<select name="action" required data-action-select>
<optgroup label="Community">
<option value="activity.start">Start activity check</option>
<option value="activity.stop">Stop activity check</option>
<option value="quiz.start">Start quiz</option>
<option value="quiz.stop">Stop quiz</option>
<option value="pulse.start">Start community pulse</option>
<option value="pulse.stop">Stop community pulse</option>
<option value="icebreaker.post">Post icebreaker</option>
</optgroup>
<optgroup label="Giveaways">
<option value="giveaway.start">Start giveaway</option>
<option value="giveaway.end">End giveaway</option>
</optgroup>
<optgroup label="Safety">
<option value="security.trap">Create or repair security trap</option>
<option value="security.lockdown">Lock or unlock server</option>
<option value="moderation.warn">Warn member</option>
<option value="moderation.timeout">Timeout member</option>
<option value="moderation.kick">Kick member</option>
<option value="moderation.ban">Ban member</option>
</optgroup>
<optgroup label="Utilities">
<option value="automation.run">Run automation now</option>
<option value="verification.publish">Publish verification panel</option>
<option value="reactionrole.add">Add reaction role</option>
<option value="level.setxp">Set member XP</option>
</optgroup>
</select>
</label>
<button class="button primary" type="submit">Run action</button>
</div>
<div class="action-fields">
<div class="form-field" data-actions="activity.start,quiz.start,pulse.start,icebreaker.post,giveaway.start,verification.publish,reactionrole.add"><span class="field-label">Channel</span><?=channel_picker($channels,null,'channel_id','quick-action-channel-picker',true)?></div>
<div class="form-field" data-actions="activity.start,quiz.start,pulse.start,icebreaker.post,giveaway.start"><span class="field-label">Ping role</span><?=single_role_picker($roles,null,'ping_role_id','quick-ping-role-picker')?></div>
<div class="form-field" data-actions="giveaway.start"><span class="field-label">Required role</span><?=single_role_picker($roles,null,'required_role_id','giveaway-required-role-picker')?></div>
<label data-actions="quiz.start,icebreaker.post">Category<input name="category" value="mixed">
</label>
<label data-actions="quiz.start">Duration seconds<input type="number" name="duration_seconds" value="30" min="15" max="120">
</label>
<label data-actions="pulse.start,giveaway.start">Duration minutes<input type="number" name="duration_minutes" value="10" min="1" max="10080">
</label>
<label data-actions="giveaway.start">Winners<input type="number" name="winners" value="1" min="1" max="20">
</label>
<label class="wide" data-actions="pulse.start">Pulse question<input name="prompt" placeholder="How is everyone doing today?">
</label>
<label class="wide" data-actions="giveaway.start">Giveaway prize<input name="prize" placeholder="1 month of Discord Nitro">
</label>
<label data-actions="giveaway.end">Giveaway ID<input name="giveaway_id">
</label>
<label data-actions="automation.run">Automation ID<input name="schedule_id">
</label>
<label data-actions="moderation.warn,moderation.timeout,moderation.kick,moderation.ban,level.setxp">Member ID<input name="user_id" inputmode="numeric">
</label>
<label data-actions="moderation.timeout">Timeout minutes<input type="number" name="minutes" value="10" min="1">
</label>
<label data-actions="level.setxp">XP value<input type="number" name="xp" value="0" min="0">
</label>
<div class="form-field" data-actions="verification.publish,reactionrole.add"><span class="field-label">Role</span><?=single_role_picker($roles,null,'role_id','quick-action-role-picker')?></div>
<div class="form-field" data-actions="verification.publish"><span class="field-label">Role removed</span><?=single_role_picker($roles,null,'remove_role_id','quick-remove-role-picker')?></div>
<label data-actions="reactionrole.add">Message ID<input name="message_id" inputmode="numeric">
</label>
<label data-actions="reactionrole.add">Emoji<input name="emoji" value="✅">
</label>
<label data-actions="verification.publish">Panel title<input name="title" value="<?=htmlspecialchars((string)$verification['title'])?>">
</label>
<label data-actions="verification.publish">Button label<input name="button_label" value="<?=htmlspecialchars((string)$verification['button_label'])?>">
</label>
<label class="wide" data-actions="verification.publish">Panel description<textarea name="description" rows="3">
<?=htmlspecialchars((string)$verification['description'])?>
</textarea>
</label>
<label class="wide" data-actions="moderation.warn,moderation.timeout,moderation.kick,moderation.ban">Reason<input name="reason" placeholder="Reason shown in logs">
</label>
<label data-actions="security.trap">Trap action<select name="trap_action">
<option value="ban">Ban</option>
<option value="kick">Kick</option>
</select>
</label>
<label class="toggle-row" data-actions="security.lockdown">
<input type="checkbox" name="enabled" checked>
<span>
<strong>Lock server</strong>
<small>Untick to unlock.</small>
</span>
</label>
</div>
</form>
</section>

<div class="control-sections">
<section class="panel" data-module-view="welcome" hidden>
<div class="panel-heading">
<span><i class="bi bi-person-plus"></i></span>
<div>
<h2>Welcome, goodbye & invite tracking</h2>
<p>Invite Tracker-style join and leave messages with placeholders.</p>
</div>
</div>
<form method="post" class="settings-stack">
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="save_welcome">
<label class="toggle-row">
<input type="checkbox" name="welcome_enabled" <?=!empty($welcome['welcome_enabled'])?'checked':''?>>
<span>
<strong>Enable welcome messages</strong>
</span>
</label>
<div class="form-field"><span class="field-label">Welcome channel</span><?=channel_picker($channels,$welcome['welcome_channel_id'],'welcome_channel_id','welcome-channel-picker')?></div>
<label>Welcome message<textarea name="welcome_message" rows="4">
<?=htmlspecialchars((string)$welcome['welcome_message'])?>
</textarea>
<small>Placeholders: {user}, {username}, {server}, {member_count}, {inviter}, {invite_count}, {account_age_days}</small>
</label>
<div class="field-grid">
<label class="toggle-row">
<input type="checkbox" name="welcome_embed" <?=!empty($welcome['welcome_embed'])?'checked':''?>>
<span>
<strong>Use embed</strong>
</span>
</label>
<label class="toggle-row">
<input type="checkbox" name="welcome_dm" <?=!empty($welcome['welcome_dm'])?'checked':''?>>
<span>
<strong>DM member too</strong>
</span>
</label>
</div>
<label class="toggle-row">
<input type="checkbox" name="invite_tracking" <?=!empty($welcome['invite_tracking'])?'checked':''?>>
<span>
<strong>Track used invites</strong>
</span>
</label>
<hr>
<label class="toggle-row">
<input type="checkbox" name="goodbye_enabled" <?=!empty($welcome['goodbye_enabled'])?'checked':''?>>
<span>
<strong>Enable goodbye messages</strong>
</span>
</label>
<div class="form-field"><span class="field-label">Goodbye channel</span><?=channel_picker($channels,$welcome['goodbye_channel_id'],'goodbye_channel_id','goodbye-channel-picker')?></div>
<label>Goodbye message<textarea name="goodbye_message" rows="3">
<?=htmlspecialchars((string)$welcome['goodbye_message'])?>
</textarea>
</label>
<label class="toggle-row">
<input type="checkbox" name="goodbye_embed" <?=!empty($welcome['goodbye_embed'])?'checked':''?>>
<span>
<strong>Use embed</strong>
</span>
</label>
<button class="button primary" type="submit">Save welcome system</button>
</form>
</section>

<section class="panel" data-module-view="levels" hidden>
<div class="panel-heading">
<span><i class="bi bi-bar-chart"></i></span>
<div>
<h2>Levels and role rewards</h2>
<p>Configure XP and build an Arcane-style reward ladder.</p>
</div>
</div>
<form method="post" class="settings-stack">
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="save_levels">
<label class="toggle-row">
<input type="checkbox" name="level_enabled" <?=!empty($levels['enabled'])?'checked':''?>>
<span>
<strong>Enable levelling</strong>
</span>
</label>
<div class="field-grid">
<label>Minimum XP<input type="number" name="xp_min" value="<?=htmlspecialchars((string)$levels['xp_min'])?>">
</label>
<label>Maximum XP<input type="number" name="xp_max" value="<?=htmlspecialchars((string)$levels['xp_max'])?>">
</label>
<label>Cooldown seconds<input type="number" name="cooldown_seconds" value="<?=htmlspecialchars((string)$levels['cooldown_seconds'])?>">
</label>
<div class="form-field"><span class="field-label">Announcement channel</span><?=channel_picker($channels,$levels['announce_channel_id'],'announce_channel_id','level-announcement-channel-picker')?></div>
</div>
<label>Level-up message<input name="announce_message" value="<?=htmlspecialchars((string)$levels['announce_message'])?>">
</label>
<div class="reward-builder">
<div>
<strong>Add a role reward</strong>
<p>Members receive every configured role at or below their new level.</p>
</div>
<div class="field-grid">
<label>Level<input type="number" name="reward_level" min="1" max="1000" placeholder="5">
</label>
<div class="form-field"><span class="field-label">Reward role</span><?=single_role_picker($roles,null,'reward_role_id','level-reward-role-picker')?></div>
</div>
</div>
<button class="button primary" type="submit"><i class="bi bi-check2"></i> Save levels</button>
</form>
<div class="reward-list">
<div class="reward-list-heading"><div><strong>Role reward ladder</strong><span><?=count($levels['reward_roles']??[])?> configured</span></div></div>
<?php if(!empty($levels['reward_roles'])):?>
<?php foreach($levels['reward_roles'] as $lvl=>$rid):?>
<form method="post" class="reward-row">
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="remove_level_reward">
<input type="hidden" name="reward_level" value="<?=htmlspecialchars((string)$lvl)?>">
<span class="reward-level">Level <?=htmlspecialchars((string)$lvl)?></span>
<span class="reward-role"><i class="bi bi-award"></i><?=htmlspecialchars(role_label($roles,$rid))?></span>
<button class="icon-button" type="submit" aria-label="Remove level <?=htmlspecialchars((string)$lvl)?> reward" title="Remove reward"><i class="bi bi-trash"></i></button>
</form>
<?php endforeach;?>
<?php else:?>
<div class="compact-empty reward-empty"><span><i class="bi bi-award"></i></span><strong>No role rewards yet</strong><p>Add a level and role above to start the ladder.</p></div>
<?php endif;?>
</div>
</section>

<section class="panel" data-module-view="autoroles" hidden>
<div class="panel-heading">
<span><i class="bi bi-people"></i></span>
<div>
<h2>Autoroles</h2>
<p>Choose multiple roles to assign on member join.</p>
</div>
</div>
<form method="post" class="settings-stack">
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="save_roles">
<div class="form-field"><span class="field-label">Join roles</span><?=role_picker($roles,$autoroles,'autorole_ids','autoroles-role-picker')?></div>
<button class="button primary" type="submit">Save autoroles</button>
</form>
</section>

<section class="panel" data-module-view="verification" hidden>
<div class="panel-heading">
<span><i class="bi bi-patch-check"></i></span>
<div>
<h2>Verification</h2>
<p>Configure the role and panel wording.</p>
</div>
</div>
<form method="post" class="settings-stack">
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="save_verification">
<label class="toggle-row">
<input type="checkbox" name="verification_enabled" <?=!empty($verification['enabled'])?'checked':''?>>
<span>
<strong>Enable verification</strong>
</span>
</label>
<div class="form-field"><span class="field-label">Panel channel</span><?=channel_picker($channels,$verification['channel_id'],'verification_channel_id','verification-channel-picker')?></div>
<div class="form-field"><span class="field-label">Verified role</span><?=single_role_picker($roles,$verification['role_id'],'verification_role_id','verification-role-picker')?></div>
<div class="form-field"><span class="field-label">Role removed after verification</span><?=single_role_picker($roles,$verification['remove_role_id'],'verification_remove_role_id','verification-remove-role-picker')?></div>
<label>Panel title<input name="verification_title" value="<?=htmlspecialchars((string)$verification['title'])?>">
</label>
<label>Description<textarea name="verification_description" rows="3">
<?=htmlspecialchars((string)$verification['description'])?>
</textarea>
</label>
<label>Button label<input name="verification_button_label" value="<?=htmlspecialchars((string)$verification['button_label'])?>">
</label>
<button class="button primary" type="submit">Save verification</button>
</form>
</section>

<section class="panel ticket-panel" data-module-view="tickets" hidden>
<div class="panel-heading">
<span><i class="bi bi-ticket-perforated"></i></span>
<div>
<h2>Tickets</h2>
<p>Save the defaults, then publish a working panel to any text channel.</p>
</div>
</div>
<div class="ticket-workspace">
<form method="post" class="settings-stack">
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="save_tickets">
<h3>Ticket defaults</h3>
<div class="form-field"><span class="field-label">Default ticket category</span><?=channel_picker($categories,$tickets['default_category_id'],'ticket_category_id','ticket-category-picker',true,true)?></div>
<div class="form-field"><span class="field-label">Transcript log channel</span><?=channel_picker($channels,$tickets['log_channel_id'],'ticket_log_channel_id','ticket-log-channel-picker')?></div>
<div class="form-field"><span class="field-label">Support roles</span><?=role_picker($roles,$tickets['support_role_ids']??[],'ticket_support_role_ids','ticket-support-role-picker')?></div>
<label class="toggle-row">
<input type="checkbox" name="one_ticket_per_member" <?=!empty($tickets['one_ticket_per_member'])?'checked':''?>>
<span>
<strong>One open ticket per member</strong>
</span>
</label>
<div class="field-grid">
<label>Ticket name<input name="ticket_name" value="<?=htmlspecialchars((string)$tickets['ticket_name'])?>">
</label>
<label>Transcript limit<input type="number" name="transcript_limit" min="50" max="2000" value="<?=htmlspecialchars((string)$tickets['transcript_limit'])?>">
</label>
<label>Auto-delete after closing<input type="number" name="auto_delete_minutes" min="0" max="10080" value="<?=htmlspecialchars((string)($tickets['auto_delete_minutes']??0))?>"><small>Minutes before a closed ticket is deleted. Use 0 to disable automatic deletion.</small>
</label>
</div>
<label>Welcome message<textarea name="ticket_welcome_message" rows="3">
<?=htmlspecialchars((string)$tickets['welcome_message'])?>
</textarea>
</label>
<button class="button secondary" type="submit">Save defaults</button>
</form>
<form method="post" class="settings-stack publish-card">
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="live_action">
<input type="hidden" name="action" value="ticket.panel">
<h3>Publish one ticket dropdown</h3>
<p class="form-intro">Create one polished panel with multiple ticket types. Each choice can open in its own category and notify its own support role.</p>
<div class="form-field"><span class="field-label">Send to channel</span><?=channel_picker($channels,null,'channel_id','ticket-publish-channel-picker',true)?></div>
<input type="hidden" name="category_id" value="<?=htmlspecialchars((string)($tickets['default_category_id']??''))?>">
<label>Panel title<input name="title" value="How can we help?" maxlength="256" required>
</label>
<label>Introduction<textarea name="description" rows="4" required>Choose the ticket type that best matches what you need. Your conversation will be private.</textarea>
</label>
<div class="field-grid">
<label>Dropdown placeholder<input name="select_placeholder" value="Select a ticket type…" maxlength="150" required>
</label>
<label>Accent colour<input name="panel_color" value="#7C6CFF" maxlength="7" pattern="#[0-9A-Fa-f]{6}">
</label>
</div>
<div class="ticket-option-builder">
<div class="section-heading"><div><h4>Dropdown options</h4><p>Fill the first option and any additional choices you need. You can add more later with <code>/ticket panel add-option</code>.</p></div></div>
<?php for ($ticketOptionIndex = 0; $ticketOptionIndex < 6; $ticketOptionIndex++): $ticketOptionNumber = $ticketOptionIndex + 1; ?>
<div class="ticket-option-card">
<div class="ticket-option-heading"><strong>Option <?=$ticketOptionNumber?></strong><span><?=$ticketOptionIndex===0?'Required':'Optional'?></span></div>
<div class="field-grid">
<label>Name<input name="ticket_option_name[]" value="<?=$ticketOptionIndex===0?'General Support':''?>" maxlength="100" <?=$ticketOptionIndex===0?'required':''?> placeholder="e.g. Billing Support">
</label>
<label>Custom icon<input name="ticket_option_emoji[]" value="<?=$ticketOptionIndex===0?'🎫':''?>" maxlength="100" placeholder="Unicode or server emoji">
</label>
</div>
<label>Description<input name="ticket_option_description[]" value="<?=$ticketOptionIndex===0?'General questions and assistance':''?>" maxlength="100" placeholder="Shown under the option name">
</label>
<div class="field-grid">
<div class="form-field"><span class="field-label">Ticket category</span><?=channel_picker($categories,$tickets['default_category_id'],"ticket_option_category_id[]","ticket-option-category-{$ticketOptionNumber}",$ticketOptionIndex===0,true)?></div>
<div class="form-field"><span class="field-label">Support role</span><?=single_role_picker($roles,$tickets['support_role_ids'][0]??null,"ticket_option_support_role_id[]","ticket-option-role-{$ticketOptionNumber}")?></div>
</div>
</div>
<?php endfor; ?>
</div>
<div class="ticket-media-builder">
<div class="section-heading"><div><h4>Panel media</h4><p>All media links must be public HTTPS URLs. Header image is displayed as a separate banner above the content; footer image is Discord's small footer icon.</p></div></div>
<label>Header image URL<input type="url" name="panel_header_image_url" placeholder="https://example.com/header.png"></label>
<div class="field-grid">
<label>Thumbnail URL<input type="url" name="panel_thumbnail_url" placeholder="https://example.com/icon.png"></label>
<label>Body image URL<input type="url" name="panel_image_url" placeholder="https://example.com/body.png"></label>
</div>
<div class="field-grid">
<label>Author name<input name="panel_author_name" maxlength="256" placeholder="Defaults to server name • Support centre"></label>
<label>Author icon URL<input type="url" name="panel_author_icon_url" placeholder="https://example.com/author.png"></label>
</div>
<div class="field-grid">
<label>Footer text<input name="panel_footer_text" maxlength="2048" placeholder="Defaults to Rallybit Tickets and panel ID"></label>
<label>Footer icon URL<input type="url" name="panel_footer_icon_url" placeholder="https://example.com/footer.png"></label>
</div>
</div>
<div class="ticket-display-options">
<label class="toggle-row"><input type="checkbox" name="panel_show_author" checked><span><strong>Show author</strong><small>Server branding or the custom author above.</small></span></label>
<label class="toggle-row"><input type="checkbox" name="panel_show_option_details" checked><span><strong>Show option details</strong><small>List each dropdown choice inside the embed.</small></span></label>
<label class="toggle-row"><input type="checkbox" name="panel_show_workload" checked><span><strong>Show workload</strong><small>Display the number of active tickets.</small></span></label>
<label class="toggle-row"><input type="checkbox" name="panel_show_guidance" checked><span><strong>Show guidance</strong><small>Remind members what to include.</small></span></label>
<label class="toggle-row"><input type="checkbox" name="panel_show_timestamp" checked><span><strong>Show timestamp</strong><small>Add the panel's latest refresh time.</small></span></label>
</div>
<button class="button primary full" type="submit">Publish dropdown panel</button>
</form>
</div>
</section>

<section class="panel" data-module-view="reviews" hidden>
<div class="panel-heading">
<span><i class="bi bi-star"></i></span>
<div>
<h2>Review channels</h2>
<p>Route staff and member reviews to separate channels.</p>
</div>
</div>
<form method="post" class="settings-stack">
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="save_reviews">
<div class="form-field"><span class="field-label">Staff reviews channel</span><?=channel_picker($channels,$reviews['staff_channel_id'],'staff_review_channel_id','staff-review-channel-picker',true)?><small>Reviews submitted with <code>/review type:Staff</code> are posted here.</small></div>
<div class="form-field"><span class="field-label">Member reviews channel</span><?=channel_picker($channels,$reviews['member_channel_id'],'member_review_channel_id','member-review-channel-picker',true)?><small>Reviews submitted with <code>/review type:Member</code> are posted here.</small></div>
<div class="permission-note"><i class="bi bi-info-circle"></i><p>Members choose a review type, user, rating from one to five stars, and a reason. Rallybit publishes the review without pinging the reviewer or reviewed user.</p></div>
<button class="button primary" type="submit"><i class="bi bi-check2"></i> Save review channels</button>
</form>
</section>

<section class="panel" data-module-view="moderation" hidden>
<div class="panel-heading">
<span><i class="bi bi-shield-exclamation"></i></span>
<div>
<h2>Moderation panel permissions</h2>
<p>Choose which staff roles can see and use each moderation action.</p>
</div>
</div>
<form method="post" class="settings-stack">
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="save_moderation_permissions">
<div class="form-field"><span class="field-label">Report review channel</span><?=channel_picker($channels,$reports['channel_id']??null,'report_channel_id','report-review-channel-picker')?><small>Member reports submitted with <code>/report user</code> are sent here.</small></div>
<div class="permission-grid">
<div class="permission-field"><span class="permission-label"><i class="bi bi-exclamation-triangle"></i> Warnings and reports</span><?=role_picker($guildRoles,$moderation['warn_role_ids']??[],'moderation_warn_role_ids','moderation-warn-role-picker')?><small>Controls warnings, report management, history, and message clearing.</small></div>
<div class="permission-field"><span class="permission-label"><i class="bi bi-clock"></i> Timeouts</span><?=role_picker($guildRoles,$moderation['timeout_role_ids']??[],'moderation_timeout_role_ids','moderation-timeout-role-picker')?><small>Controls applying and removing member timeouts.</small></div>
<div class="permission-field"><span class="permission-label"><i class="bi bi-person-x"></i> Kicks</span><?=role_picker($guildRoles,$moderation['kick_role_ids']??[],'moderation_kick_role_ids','moderation-kick-role-picker')?><small>Controls removing members from the server.</small></div>
<div class="permission-field"><span class="permission-label"><i class="bi bi-slash-circle"></i> Bans</span><?=role_picker($guildRoles,$moderation['ban_role_ids']??[],'moderation_ban_role_ids','moderation-ban-role-picker')?><small>Controls bans and the <code>/mod unban</code> command.</small></div>
</div>
<div class="permission-note"><i class="bi bi-info-circle"></i><p>A selected role is required alongside the matching Discord permission. Server owners and administrators always retain full access. If an action has no selected roles, Discord's native permission controls it.</p></div>
<button class="button primary" type="submit"><i class="bi bi-check2"></i> Save moderation permissions</button>
</form>
</section>

<section class="panel" data-module-view="security" hidden>
<div class="panel-heading">
<span><i class="bi bi-shield-check"></i></span>
<div>
<h2>Security essentials</h2>
<p>Configure the account age gate and scam trap per server.</p>
</div>
</div>
<form method="post" class="settings-stack">
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="save_security">
<label class="toggle-row">
<input type="checkbox" name="agegate_enabled" <?=!empty($security['agegate']['enabled'])?'checked':''?>>
<span>
<strong>Enable account age gate</strong>
</span>
</label>
<label>Minimum account age in days<input type="number" name="agegate_days" min="0" max="3650" value="<?=htmlspecialchars((string)($security['agegate']['minimum_days']??7))?>">
</label>
<label class="toggle-row">
<input type="checkbox" name="agegate_dm" <?=!empty($security['agegate']['dm_member'])?'checked':''?>>
<span>
<strong>DM rejected members</strong>
</span>
</label>
<hr>
<label class="toggle-row">
<input type="checkbox" name="trap_enabled" <?=!empty($security['trap']['enabled'])?'checked':''?>>
<span>
<strong>Enable do-not-text-here trap</strong>
</span>
</label>
<label>Trap channel name<input name="trap_channel_name" value="<?=htmlspecialchars((string)($security['trap']['channel_name']??'do-not-text-here'))?>">
</label>
<label>Trap action<select name="trap_action">
<option value="ban" <?=selected_value($security['trap']['action']??'ban','ban')?>>Ban</option>
<option value="kick" <?=selected_value($security['trap']['action']??'ban','kick')?>>Kick</option>
</select>
</label>
<button class="button primary" type="submit">Save security settings</button>
</form>
</section>

<section class="panel automation-panel" data-module-view="automation" hidden>
<div class="panel-heading">
<span><i class="bi bi-clock-history"></i></span>
<div>
<h2>Multi-channel automation</h2>
<p>Add as many independent schedules and channels as the server needs.</p>
</div>
</div>
<form method="post" class="settings-stack">
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="add_automation">
<div class="field-grid">
<label>Type<select name="automation_kind">
<option value="activity">Activity check</option>
<option value="quiz">Quiz</option>
<option value="pulse">Pulse</option>
<option value="icebreaker">Icebreaker</option>
<option value="giveaway">Giveaway</option>
</select>
</label>
<div class="form-field"><span class="field-label">Channel</span><?=channel_picker($channels,null,'automation_channel_id','automation-channel-picker',true)?></div>
<label>Interval minutes<input type="number" name="automation_interval" min="5" value="60">
</label>
<div class="form-field"><span class="field-label">Ping role</span><?=single_role_picker($roles,null,'automation_ping_role_id','automation-ping-role-picker')?></div>
</div>
<label>Prompt, prize or custom text<input name="automation_message">
</label>
<div class="field-grid">
<label>Category<input name="automation_category" value="mixed">
</label>
<label>Duration<input type="number" name="automation_duration" value="30">
</label>
<label>Winners<input type="number" name="automation_winners" value="1" min="1" max="20">
</label>
</div>
<button class="button primary" type="submit">Add automation</button>
</form>
<?php if($automations):?>
<div class="automation-list">
<?php foreach($automations as $id=>$auto):?>
<form method="post" class="automation-row">
<input type="hidden" name="csrf_token" value="<?=htmlspecialchars($csrf)?>">
<input type="hidden" name="operation" value="remove_automation">
<input type="hidden" name="automation_id" value="<?=htmlspecialchars((string)$id)?>">
<div>
<strong>
<?=htmlspecialchars(strtoupper((string)$auto['kind']))?>
</strong>
<span>#<?=htmlspecialchars((string)$auto['channel_id'])?> · every <?=htmlspecialchars((string)$auto['interval_minutes'])?>m · ID <?=htmlspecialchars((string)$id)?>
</span>
</div>
<button class="button danger small" type="submit">Remove</button>
</form>
<?php endforeach;?>
</div>
<?php endif;?>
</section>
</div>
</div>
</div>
</main>
</div>
<script>
document.querySelectorAll('[data-action-form]').forEach(function(form){
  var select=form.querySelector('[data-action-select]');
  var fields=form.querySelectorAll('[data-actions]');
  function update(){
    var action=select.value;
    fields.forEach(function(field){
      var show=field.dataset.actions.split(',').includes(action);
      field.hidden=!show;
      field.querySelectorAll('input,select,textarea').forEach(function(input){input.disabled=!show;});
    });
  }
  select.addEventListener('change',update);
  update();
});
var moduleButtons=document.querySelectorAll('[data-module-target]');
var moduleViews=document.querySelectorAll('[data-module-view]');
function openModule(name){
  moduleButtons.forEach(function(button){button.classList.toggle('active',button.dataset.moduleTarget===name);});
  moduleViews.forEach(function(view){view.hidden=view.dataset.moduleView!==name;});
  if(history.replaceState) history.replaceState(null,'','#'+name);
}
moduleButtons.forEach(function(button){button.addEventListener('click',function(){openModule(button.dataset.moduleTarget);});});
var requested=location.hash.slice(1);
if(requested && document.querySelector('[data-module-target="'+requested+'"]')) openModule(requested);
document.querySelectorAll('[data-channel-picker]').forEach(function(picker){
  var trigger=picker.querySelector('.channel-picker-trigger');
  var popover=picker.querySelector('[data-channel-picker-popover]');
  var search=picker.querySelector('[data-channel-picker-search]');
  var options=Array.from(picker.querySelectorAll('[data-channel-option]'));
  var value=picker.querySelector('[data-channel-picker-value]');
  var label=picker.querySelector('[data-channel-picker-label]');
  var noResults=picker.querySelector('[data-channel-picker-no-results]');
  var error=picker.querySelector('[data-channel-picker-error]');
  function filter(){
    var query=search.value.trim().toLowerCase();var visible=0;
    options.forEach(function(option){var show=query===''||option.dataset.search.includes(query);option.hidden=!show;if(show)visible++;});
    noResults.hidden=visible!==0;
  }
  function close(){popover.hidden=true;trigger.setAttribute('aria-expanded','false');search.value='';filter();}
  function open(){
    document.querySelectorAll('[data-channel-picker-popover]:not([hidden])').forEach(function(other){if(other!==popover){other.hidden=true;other.parentElement.querySelector('.channel-picker-trigger').setAttribute('aria-expanded','false');}});
    popover.hidden=false;trigger.setAttribute('aria-expanded','true');search.focus();
  }
  function choose(option){
    value.value=option.dataset.value;label.textContent=option.dataset.label;
    options.forEach(function(item){item.setAttribute('aria-selected',item===option?'true':'false');});
    picker.classList.remove('invalid');error.hidden=true;value.dispatchEvent(new Event('change',{bubbles:true}));close();trigger.focus();
  }
  trigger.addEventListener('click',function(){popover.hidden?open():close();});
  options.forEach(function(option){option.addEventListener('click',function(){choose(option);});});
  search.addEventListener('input',filter);
  picker.addEventListener('keydown',function(event){if(event.key==='Escape'){close();trigger.focus();}});
  document.addEventListener('click',function(event){if(!picker.contains(event.target))close();});
  var form=picker.closest('form');
  if(form)form.addEventListener('submit',function(event){
    if(picker.dataset.required==='true'&&!value.disabled&&!value.value){event.preventDefault();picker.classList.add('invalid');error.hidden=false;open();}
  });
});
document.querySelectorAll('[data-single-role-picker]').forEach(function(picker){
  var trigger=picker.querySelector('.single-role-picker-trigger');
  var popover=picker.querySelector('[data-single-role-picker-popover]');
  var search=picker.querySelector('[data-single-role-picker-search]');
  var options=Array.from(picker.querySelectorAll('[data-single-role-option]'));
  var value=picker.querySelector('[data-single-role-picker-value]');
  var label=picker.querySelector('[data-single-role-picker-label]');
  var colour=picker.querySelector('[data-single-role-picker-colour]');
  var placeholderIcon=picker.querySelector('[data-single-role-picker-placeholder-icon]');
  var noResults=picker.querySelector('[data-single-role-picker-no-results]');
  var error=picker.querySelector('[data-single-role-picker-error]');
  function filter(){
    var query=search.value.trim().toLowerCase();var visible=0;
    options.forEach(function(option){var show=query===''||option.dataset.search.includes(query);option.hidden=!show;if(show)visible++;});
    noResults.hidden=visible!==0;
  }
  function close(){popover.hidden=true;trigger.setAttribute('aria-expanded','false');search.value='';filter();}
  function open(){
    document.querySelectorAll('[data-single-role-picker-popover]:not([hidden])').forEach(function(other){if(other!==popover){other.hidden=true;other.parentElement.querySelector('.single-role-picker-trigger').setAttribute('aria-expanded','false');}});
    popover.hidden=false;trigger.setAttribute('aria-expanded','true');search.focus();
  }
  function choose(option){
    var selectedColour=option.dataset.colour;
    value.value=option.dataset.value;label.textContent=option.dataset.label;
    colour.hidden=!selectedColour;placeholderIcon.hidden=!!selectedColour;
    if(selectedColour)colour.style.setProperty('--role-colour',selectedColour);
    options.forEach(function(item){item.setAttribute('aria-selected',item===option?'true':'false');});
    picker.classList.remove('invalid');error.hidden=true;value.dispatchEvent(new Event('change',{bubbles:true}));close();trigger.focus();
  }
  trigger.addEventListener('click',function(){popover.hidden?open():close();});
  options.forEach(function(option){option.addEventListener('click',function(){choose(option);});});
  search.addEventListener('input',filter);
  picker.addEventListener('keydown',function(event){if(event.key==='Escape'){close();trigger.focus();}});
  document.addEventListener('click',function(event){if(!picker.contains(event.target))close();});
  var form=picker.closest('form');
  if(form)form.addEventListener('submit',function(event){
    if(picker.dataset.required==='true'&&!value.disabled&&!value.value){event.preventDefault();picker.classList.add('invalid');error.hidden=false;open();}
  });
});
document.querySelectorAll('[data-role-picker]').forEach(function(picker){
  var trigger=picker.querySelector('.role-picker-trigger');
  var popover=picker.querySelector('[data-role-picker-popover]');
  var search=picker.querySelector('[data-role-picker-search]');
  var options=Array.from(picker.querySelectorAll('[data-role-option]'));
  var summary=picker.querySelector('[data-role-picker-summary]');
  var count=picker.querySelector('[data-role-picker-count]');
  var done=picker.querySelector('[data-role-picker-done]');
  function update(){
    var selected=options.filter(function(option){return option.querySelector('input').checked;});
    summary.textContent=selected.length===0?'Choose roles':selected.length===1?'1 role selected':selected.length+' roles selected';
    count.textContent=selected.length+' selected';
  }
  function close(){popover.hidden=true;trigger.setAttribute('aria-expanded','false');}
  function open(){
    document.querySelectorAll('[data-role-picker-popover]:not([hidden])').forEach(function(other){if(other!==popover){other.hidden=true;other.parentElement.querySelector('.role-picker-trigger').setAttribute('aria-expanded','false');}});
    popover.hidden=false;trigger.setAttribute('aria-expanded','true');search.focus();
  }
  trigger.addEventListener('click',function(){popover.hidden?open():close();});
  options.forEach(function(option){option.querySelector('input').addEventListener('change',update);});
  search.addEventListener('input',function(){
    var query=search.value.trim().toLowerCase();
    options.forEach(function(option){option.hidden=query!==''&&!option.dataset.search.includes(query);});
  });
  done.addEventListener('click',close);
  picker.addEventListener('keydown',function(event){if(event.key==='Escape'){close();trigger.focus();}});
  document.addEventListener('click',function(event){if(!picker.contains(event.target))close();});
  update();
});
</script>
</body>
</html>
