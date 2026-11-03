<?php
declare(strict_types=1);

/* ---------- config ---------- */
function cfg(string $key, $default = null)
{
    static $c = null;
    if ($c === null) {
        $f = __DIR__ . '/config.php';
        if (!is_file($f)) {
            http_response_code(500);
            header('Content-Type: text/plain; charset=utf-8');
            exit("Missing config.php — copy config.sample.php to config.php and fill it in.");
        }
        $c = require $f;
    }
    return $c[$key] ?? $default;
}
date_default_timezone_set((string)cfg('timezone', 'Europe/Riga'));

/* ---------- JSON storage (data/*.json) ---------- */
function data_path(string $name): string
{
    $d = __DIR__ . '/data';
    if (!is_dir($d)) mkdir($d, 0750, true);
    return "$d/$name.json";
}
function load_json(string $name, array $default = []): array
{
    $p = data_path($name);
    if (!is_file($p)) return $default;
    $v = json_decode((string)file_get_contents($p), true);
    return is_array($v) ? $v : $default;
}
function save_json(string $name, array $val): void
{
    $p = data_path($name);
    $tmp = $p . '.' . bin2hex(random_bytes(4)) . '.tmp';
    file_put_contents($tmp, json_encode($val, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE));
    rename($tmp, $p); // atomic
}
/** Read-modify-write under an exclusive lock. */
function update_json(string $name, callable $fn, array $default = []): array
{
    $lf = fopen(data_path($name) . '.lock', 'c');
    flock($lf, LOCK_EX);
    try {
        $v = $fn(load_json($name, $default));
        save_json($name, $v);
        return $v;
    } finally {
        flock($lf, LOCK_UN);
        fclose($lf);
    }
}

/* ---------- auth (stateless signed cookies, works on any shared host) ---------- */
function secret(): string
{
    return hash('sha256', 'sm|' . cfg('admin_password') . '|' . cfg('player_key') . '|' . cfg('spotify_client_secret'));
}
function sign(string $s): string { return hash_hmac('sha256', $s, secret()); }
function is_https(): bool
{
    return (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
        || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
}
function base_path(): string
{
    return rtrim(str_replace('\\', '/', dirname($_SERVER['SCRIPT_NAME'] ?? '/')), '/') . '/';
}
function is_admin(): bool
{
    $c = $_COOKIE['sm_admin'] ?? '';
    if (!preg_match('/^(\d+)\.([a-f0-9]{64})$/', $c, $m)) return false;
    if ((int)$m[1] < time()) return false;
    return hash_equals(sign('admin|' . $m[1]), $m[2]);
}
function set_admin_cookie(bool $clear = false): void
{
    $exp = $clear ? time() - 3600 : time() + 86400 * 30;
    setcookie('sm_admin', $clear ? '' : $exp . '.' . sign('admin|' . $exp), [
        'expires' => $exp, 'path' => base_path(), 'httponly' => true,
        'samesite' => 'Lax', 'secure' => is_https(),
    ]);
}
function is_player(): bool
{
    $k = $_SERVER['HTTP_X_PLAYER_KEY'] ?? ($_GET['key'] ?? '');
    return is_string($k) && $k !== '' && hash_equals((string)cfg('player_key'), $k);
}

/* ---------- HTTP ---------- */
function http_req(string $method, string $url, array $headers = [], ?string $body = null): array
{
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_CUSTOMREQUEST => $method,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_TIMEOUT => 15,
        CURLOPT_CONNECTTIMEOUT => 8,
    ]);
    if ($body !== null) curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
    $resp = curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    $resp = $resp === false ? '' : (string)$resp;
    return ['status' => $status, 'body' => $resp, 'json' => json_decode($resp, true), 'error' => $err];
}
function json_out($data, int $status = 200): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}

/* ---------- Spotify ---------- */
const SPOTIFY_SCOPES = 'streaming user-read-email user-read-private user-read-playback-state '
    . 'user-modify-playback-state user-read-currently-playing playlist-read-private playlist-read-collaborative';

function spotify_token_request(array $form): array
{
    return http_req('POST', 'https://accounts.spotify.com/api/token', [
        'Authorization: Basic ' . base64_encode(cfg('spotify_client_id') . ':' . cfg('spotify_client_secret')),
        'Content-Type: application/x-www-form-urlencoded',
    ], http_build_query($form));
}

/** Returns a valid access token (refreshing if needed) or null if not connected. */
function spotify_access_token(bool $force = false): ?string
{
    $fresh = fn($t) => !$force && !empty($t['access_token']) && ($t['expires_at'] ?? 0) > time() + 90;
    $t = load_json('tokens');
    if (empty($t['refresh_token'])) return null;
    if ($fresh($t)) return $t['access_token'];

    $lf = fopen(data_path('tokens') . '.lock', 'c');
    flock($lf, LOCK_EX);
    try {
        $t = load_json('tokens');           // someone may have refreshed meanwhile
        if ($fresh($t)) return $t['access_token'];
        $r = spotify_token_request(['grant_type' => 'refresh_token', 'refresh_token' => $t['refresh_token']]);
        if ($r['status'] !== 200 || empty($r['json']['access_token'])) {
            $t['last_error'] = ($r['json']['error_description'] ?? $r['json']['error'] ?? $r['error']) ?: ('HTTP ' . $r['status']);
            $t['last_error_at'] = time();
            save_json('tokens', $t);
            return null;
        }
        $t['access_token'] = $r['json']['access_token'];
        $t['expires_at'] = time() + (int)($r['json']['expires_in'] ?? 3600);
        if (!empty($r['json']['refresh_token'])) $t['refresh_token'] = $r['json']['refresh_token'];
        unset($t['last_error'], $t['last_error_at']);
        save_json('tokens', $t);
        return $t['access_token'];
    } finally {
        flock($lf, LOCK_UN);
        fclose($lf);
    }
}

function spotify_api(string $method, string $path, array $query = [], ?array $body = null): array
{
    for ($attempt = 0; $attempt < 2; $attempt++) {
        $tok = spotify_access_token($attempt > 0);
        if (!$tok) return ['status' => 401, 'json' => ['error' => ['message' => 'Spotify is not connected']], 'body' => ''];
        $url = 'https://api.spotify.com/v1' . $path . ($query ? '?' . http_build_query($query) : '');
        $h = ['Authorization: Bearer ' . $tok];
        $b = null;
        if ($body !== null) { $h[] = 'Content-Type: application/json'; $b = json_encode($body); }
        elseif ($method === 'PUT' || $method === 'POST') { $h[] = 'Content-Length: 0'; $b = ''; }
        $r = http_req($method, $url, $h, $b);
        if ($r['status'] !== 401) return $r;
    }
    return $r;
}

/** "https://open.spotify.com/playlist/ID?si=…" or "spotify:playlist:ID" → spotify URI */
function normalize_spotify_uri(string $s): ?string
{
    $s = trim($s);
    if (preg_match('~^spotify:(playlist|album|artist):([A-Za-z0-9]{10,})$~', $s, $m)) return "spotify:$m[1]:$m[2]";
    if (preg_match('~open\.spotify\.com/(?:intl-[a-z-]+/)?(playlist|album|artist)/([A-Za-z0-9]{10,})~', $s, $m)) return "spotify:$m[1]:$m[2]";
    return null;
}

/* ---------- schedule ---------- */
function hm(string $s): int
{
    [$h, $m] = array_map('intval', explode(':', $s) + [0, 0]);
    return $h * 60 + $m;
}
function slot_applies(array $s, int $dow, int $min): bool
{
    if (($s['enabled'] ?? true) === false) return false;
    return in_array($dow, $s['days'] ?? [], true) && hm($s['start']) <= $min && $min < hm($s['end']);
}
/** First matching slot wins (list order = priority). */
function active_slot(array $slots, ?DateTimeInterface $at = null): ?array
{
    $d = $at ?? new DateTimeImmutable('now');
    $dow = (int)$d->format('N');
    $min = (int)$d->format('G') * 60 + (int)$d->format('i');
    foreach ($slots as $s) if (slot_applies($s, $dow, $min)) return $s;
    return null;
}
/** Next moment (unix ts) when the schedule changes state, and the slot that starts then (if any). */
function next_change(array $slots): array
{
    $now = new DateTimeImmutable('now');
    $cur = active_slot($slots, $now);
    $t = $now->setTime((int)$now->format('G'), (int)$now->format('i'));
    for ($i = 1; $i <= 7 * 24 * 60; $i++) {
        $t = $t->modify('+1 minute');
        $s = active_slot($slots, $t);
        if (($s['id'] ?? null) !== ($cur['id'] ?? null)) return ['at' => $t->getTimestamp(), 'slot' => $s];
    }
    return ['at' => null, 'slot' => null];
}
function validate_slots($in): array
{
    if (!is_array($in)) throw new InvalidArgumentException('slots must be a list');
    $out = [];
    foreach ($in as $i => $s) {
        $n = $i + 1;
        $name = trim((string)($s['name'] ?? ''));
        if ($name === '') throw new InvalidArgumentException("Mode #$n needs a name");
        foreach (['start', 'end'] as $k)
            if (!preg_match('/^([01]\d|2[0-3]):[0-5]\d$/', (string)($s[$k] ?? '')) && ($k !== 'end' || ($s[$k] ?? '') !== '24:00'))
                throw new InvalidArgumentException("Mode \"$name\": invalid $k time");
        if (hm($s['start']) >= hm($s['end'])) throw new InvalidArgumentException("Mode \"$name\": end must be after start");
        $days = array_values(array_unique(array_filter(array_map('intval', (array)($s['days'] ?? [])), fn($d) => $d >= 1 && $d <= 7)));
        sort($days);
        if (!$days) throw new InvalidArgumentException("Mode \"$name\": pick at least one day");
        $uri = normalize_spotify_uri((string)($s['uri'] ?? ''));
        if (!$uri) throw new InvalidArgumentException("Mode \"$name\": choose a playlist");
        $vol = isset($s['volume']) && $s['volume'] !== '' && $s['volume'] !== null ? max(0, min(100, (int)$s['volume'])) : null;
        $out[] = [
            'id' => preg_match('/^[a-z0-9]{6,20}$/', (string)($s['id'] ?? '')) ? $s['id'] : bin2hex(random_bytes(5)),
            'name' => mb_substr($name, 0, 60),
            'days' => $days,
            'start' => $s['start'],
            'end' => $s['end'],
            'uri' => $uri,
            'playlistName' => mb_substr((string)($s['playlistName'] ?? ''), 0, 120),
            'image' => filter_var($s['image'] ?? '', FILTER_VALIDATE_URL) ?: '',
            'tracks' => max(0, (int)($s['tracks'] ?? 0)),
            'volume' => $vol,
            'shuffle' => (bool)($s['shuffle'] ?? true),
            'color' => preg_match('/^#[0-9a-fA-F]{6}$/', (string)($s['color'] ?? '')) ? $s['color'] : '#e8a54b',
            'enabled' => ($s['enabled'] ?? true) !== false,
        ];
    }
    return $out;
}
