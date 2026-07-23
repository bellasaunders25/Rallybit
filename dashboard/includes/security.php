<?php
/**
 * Application-Level Security Measures
 * Rallybit Dashboard
 */

/**
 * Secure Session Management
 * Enforces secure cookie settings to prevent session hijacking.
 */
function secure_session_init() {
    if (session_status() === PHP_SESSION_NONE) {
        // Enforce cookie security settings
        ini_set('session.cookie_httponly', 1);
        ini_set('session.use_only_cookies', 1);
        
        $is_https = (isset($_SERVER['HTTPS']) && $_SERVER['HTTPS'] === 'on') || 
                    (isset($_SERVER['HTTP_X_FORWARDED_PROTO']) && $_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https');
        
        if ($is_https) {
            ini_set('session.cookie_secure', 1);
        }
        
        ini_set('session.cookie_samesite', 'Lax');
        
        session_start();
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
