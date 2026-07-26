<?php
/**
 * Application-Level Security Measures
 * Rallybit Dashboard
 */

/**
 * Secure Session Management
 * Enforces secure cookie settings to prevent session hijacking.
 */
function secure_session_init(): void {
    if (session_status() === PHP_SESSION_NONE) {
        $configuredDays = (int)(getenv('DASHBOARD_SESSION_DAYS') ?: 30);
        $sessionDays = max(1, min(90, $configuredDays));
        $lifetime = $sessionDays * 86400;
        $isHttps = (isset($_SERVER['HTTPS']) && $_SERVER['HTTPS'] === 'on') ||
            (isset($_SERVER['HTTP_X_FORWARDED_PROTO']) && strtolower((string)$_SERVER['HTTP_X_FORWARDED_PROTO']) === 'https');

        ini_set('session.cookie_httponly', '1');
        ini_set('session.use_only_cookies', '1');
        ini_set('session.use_strict_mode', '1');
        ini_set('session.gc_maxlifetime', (string)$lifetime);
        ini_set('session.cookie_lifetime', (string)$lifetime);
        session_set_cookie_params([
            'lifetime' => $lifetime,
            'path' => '/',
            'secure' => $isHttps,
            'httponly' => true,
            'samesite' => 'Lax',
        ]);
        session_start();

        $now = time();
        if (empty($_SESSION['_created_at']) || $now - (int)$_SESSION['_created_at'] > 43200) {
            session_regenerate_id(true);
            $_SESSION['_created_at'] = $now;
        }
        $_SESSION['_last_seen_at'] = $now;

        // Refresh the browser cookie on every request so active dashboard
        // sessions remain signed in for the full configured period.
        setcookie(session_name(), session_id(), [
            'expires' => $now + $lifetime,
            'path' => '/',
            'secure' => $isHttps,
            'httponly' => true,
            'samesite' => 'Lax',
        ]);
    }
}

// Initialize secure session
secure_session_init();

/**
 * Simple Rate Limiter
 * Blocks users who exceed $limit requests within $window seconds.
 */
function check_rate_limit($limit = 60, $window = 60) {
    $ip = $_SERVER['REMOTE_ADDR'];
    $key = 'rate_limit_' . md5($ip);
    
    $current_time = time();
    
    if (!isset($_SESSION[$key])) {
        $_SESSION[$key] = [
            'count' => 1,
            'start_time' => $current_time
        ];
    } else {
        $data = $_SESSION[$key];
        
        // Reset window if expired
        if (($current_time - $data['start_time']) > $window) {
            $_SESSION[$key] = [
                'count' => 1,
                'start_time' => $current_time
            ];
        } else {
            // Increment count
            $_SESSION[$key]['count']++;
            
            // Check limit
            if ($_SESSION[$key]['count'] > $limit) {
                header("HTTP/1.1 429 Too Many Requests");
                die("<h1>429 Too Many Requests</h1><p>You are sending too many requests. Please wait a moment.</p>");
            }
        }
    }
}

/**
 * CSRF Protection
 * Generates and validates CSRF tokens.
 */
function get_csrf_token() {
    if (empty($_SESSION['csrf_token'])) {
        $_SESSION['csrf_token'] = bin2hex(random_bytes(32));
    }
    return $_SESSION['csrf_token'];
}

function validate_csrf_token($token) {
    if (!isset($_SESSION['csrf_token']) || $token !== $_SESSION['csrf_token']) {
        header("HTTP/1.1 403 Forbidden");
        die("<h1>403 Forbidden</h1><p>CSRF verification failed. Please refresh and try again.</p>");
    }
    return true;
}

/**
 * Basic Input Sanitization
 * Recursive function to sanitize arrays and strings.
 */
function sanitize_output($data) {
    if (is_array($data)) {
        foreach ($data as $key => $value) {
            $data[$key] = sanitize_output($value);
        }
    } else {
        $data = htmlspecialchars($data, ENT_QUOTES, 'UTF-8');
    }
    return $data;
}
?>
