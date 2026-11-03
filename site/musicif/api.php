<?php
declare(strict_types=1);
require __DIR__ . '/lib.php';

$action = (string)($_GET['action'] ?? '');
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$body = [];
if ($method === 'POST') {
    // Custom header blocks cross-site form posts (simple CSRF protection)
    if (($_SERVER['HTTP_X_SM'] ?? '') !== '1') json_out(['error' => 'Bad request'], 400);
    $body = json_decode((string)file_get_contents('php://input'), true) ?: [];
}

function require_admin(): void { if (!is_admin()) json_out(['error' => 'Not logged in'], 401); }
function require_player(): void { if (!is_player()) json_out(['error' => 'Bad player key'], 403); }
function player_device(): ?string
{
    $p = load_json('player');
    return (!empty($p['deviceId']) && ($p['lastSeen'] ?? 0) > time() - 60) ? $p['deviceId'] : null;
}
function sp_ok(array $r): bool { return $r['status'] >= 200 && $r['status'] < 300; }
function sp_err(array $r): string
{
    $msg = $r['json']['error']['message'] ?? ($r['error'] ?: ('Spotify HTTP ' . $r['status']));
    if (($r['json']['error']['reason'] ?? '') === 'NO_ACTIVE_DEVICE' || $r['status'] === 404)
        $msg .= ' — is the player page open on the PC?';
    if ($r['status'] === 403) $msg .= ' (Spotify Premium is required for playback control)';
    return $msg;
}
function control_state(): array { return load_json('control', ['override' => null, 'nonce' => 1]); }
function public_override(?array $o): ?array { return ($o && ($o['until'] ?? 0) > time()) ? $o : null; }

switch ($action) {

/* ======================= admin auth ======================= */
case 'login':
    if ($method !== 'POST') json_out(['error' => 'POST only'], 405);
    if (!hash_equals((string)cfg('admin_password'), (string)($body['password'] ?? ''))) {
        sleep(1);
        json_out(['error' => 'Wrong password'], 401);
    }
    set_admin_cookie();
    json_out(['ok' => true]);

case 'logout':
    set_admin_cookie(true);
    json_out(['ok' => true]);

/* ======================= admin: status ======================= */
case 'status':
    require_admin();
    $tokens = load_json('tokens');
    $connected = !empty($tokens['refresh_token']);
    $sched = load_json('schedule', ['slots' => []]);
    $player = load_json('player');
    $ctrl = control_state();
    $playback = null;
    $spotifyError = $tokens['last_error'] ?? null;
    if ($connected && empty($_GET['lite'])) {
        $r = spotify_api('GET', '/me/player', ['additional_types' => 'track,episode']);
        if ($r['status'] === 200 && is_array($r['json'])) {
            $j = $r['json'];
            $it = $j['item'] ?? null;
            $playback = [
                'device' => $j['device']['name'] ?? null,
                'deviceId' => $j['device']['id'] ?? null,
                'volume' => $j['device']['volume_percent'] ?? null,
                'isPlaying' => (bool)($j['is_playing'] ?? false),
                'shuffle' => (bool)($j['shuffle_state'] ?? false),
                'progress' => $j['progress_ms'] ?? 0,
                'duration' => $it['duration_ms'] ?? 0,
                'track' => $it['name'] ?? null,
                'artists' => isset($it['artists']) ? implode(', ', array_column($it['artists'], 'name')) : ($it['show']['name'] ?? ''),
                'image' => $it['album']['images'][0]['url'] ?? ($it['images'][0]['url'] ?? null),
                'context' => $j['context']['uri'] ?? null,
            ];
        } elseif ($r['status'] !== 204) {
            $spotifyError = sp_err($r);
        }
    }
    $nc = next_change($sched['slots']);
    json_out([
        'connected' => $connected,
        'spotifyUser' => $tokens['user'] ?? null,
        'connectedAt' => $tokens['connected_at'] ?? null,
        'spotifyError' => $connected ? $spotifyError : null,
        'player' => [
            'online' => ($player['lastSeen'] ?? 0) > time() - 45,
            'lastSeen' => $player['lastSeen'] ?? null,
            'deviceId' => $player['deviceId'] ?? null,
            'mode' => $player['mode'] ?? null,
            'error' => $player['error'] ?? null,
        ],
        'playback' => $playback,
        'activeSlot' => active_slot($sched['slots']),
        'nextChange' => $nc,
        'override' => public_override($ctrl['override'] ?? null),
        'now' => time(),
        'timezone' => cfg('timezone'),
        'playerUrl' => (is_https() ? 'https://' : 'http://') . ($_SERVER['HTTP_HOST'] ?? 'localhost') . base_path() . 'player.html?key=' . rawurlencode((string)cfg('player_key')),
    ]);

/* ======================= admin: playlists ======================= */
case 'playlists':
    require_admin();
    $out = [];
    $offset = 0;
    do {
        $r = spotify_api('GET', '/me/playlists', ['limit' => 50, 'offset' => $offset]);
        if (!sp_ok($r)) json_out(['error' => sp_err($r)], 502);
        foreach ($r['json']['items'] ?? [] as $p) {
            if (!$p) continue;
            $out[] = [
                'uri' => $p['uri'],
                'name' => $p['name'],
                'image' => $p['images'][0]['url'] ?? '',
                'tracks' => $p['items']['total'] ?? ($p['tracks']['total'] ?? 0),
                'owner' => $p['owner']['display_name'] ?? '',
            ];
        }
        $offset += 50;
    } while (!empty($r['json']['next']) && $offset < 500);
    json_out(['playlists' => $out]);

case 'resolve': // look up a pasted playlist link
    require_admin();
    $uri = normalize_spotify_uri((string)($_GET['q'] ?? ''));
    if (!$uri) json_out(['error' => 'Paste a Spotify playlist/album link'], 400);
    [, $type, $id] = explode(':', $uri);
    $r = spotify_api('GET', "/{$type}s/$id");
    if (!sp_ok($r)) json_out(['uri' => $uri, 'name' => $uri, 'image' => '', 'tracks' => 0, 'warning' => sp_err($r)]);
    $j = $r['json'];
    json_out([
        'uri' => $uri, 'name' => $j['name'] ?? $uri,
        'image' => $j['images'][0]['url'] ?? '',
        'tracks' => $j['items']['total'] ?? ($j['tracks']['total'] ?? 0),
    ]);

/* ======================= admin: schedule ======================= */
case 'schedule':
    require_admin();
    if ($method === 'POST') {
        try {
            $slots = validate_slots($body['slots'] ?? null);
        } catch (InvalidArgumentException $e) {
            json_out(['error' => $e->getMessage()], 400);
        }
        save_json('schedule', ['slots' => $slots, 'updatedAt' => time()]);
    }
    json_out(load_json('schedule', ['slots' => []]));

/* ======================= admin: transport controls ======================= */
case 'control':
    require_admin();
    if ($method !== 'POST') json_out(['error' => 'POST only'], 405);
    $dev = player_device();
    $q = $dev ? ['device_id' => $dev] : [];
    switch ($body['cmd'] ?? '') {
        case 'play':     $r = spotify_api('PUT', '/me/player/play', $q); break;
        case 'pause':    $r = spotify_api('PUT', '/me/player/pause', $q); break;
        case 'next':     $r = spotify_api('POST', '/me/player/next', $q); break;
        case 'previous': $r = spotify_api('POST', '/me/player/previous', $q); break;
        case 'volume':   $r = spotify_api('PUT', '/me/player/volume', $q + ['volume_percent' => max(0, min(100, (int)($body['value'] ?? 50)))]); break;
        case 'shuffle':  $r = spotify_api('PUT', '/me/player/shuffle', $q + ['state' => !empty($body['value']) ? 'true' : 'false']); break;
        case 'transfer':
            if (!$dev) json_out(['error' => 'Player page is offline'], 409);
            $r = spotify_api('PUT', '/me/player', [], ['device_ids' => [$dev], 'play' => true]);
            break;
        default: json_out(['error' => 'Unknown command'], 400);
    }
    if (!sp_ok($r)) json_out(['error' => sp_err($r)], 502);
    json_out(['ok' => true]);

/* ======================= admin: manual override ("play now" / "stop") ======================= */
case 'override':
    require_admin();
    if ($method !== 'POST') json_out(['error' => 'POST only'], 405);
    $sched = load_json('schedule', ['slots' => []]);
    $mode = (string)($body['mode'] ?? 'clear');

    if ($mode === 'clear') { // back to schedule
        update_json('control', function ($c) {
            $c['override'] = null;
            $c['nonce'] = ($c['nonce'] ?? 1) + 1;
            return $c;
        }, ['override' => null, 'nonce' => 1]);
        json_out(['ok' => true]);
    }

    $dur = (string)($body['duration'] ?? 'next');
    $now = time();
    switch ($dur) {
        case '30m': $until = $now + 1800; break;
        case '1h':  $until = $now + 3600; break;
        case '2h':  $until = $now + 7200; break;
        case 'day': $until = (new DateTimeImmutable('tomorrow'))->getTimestamp() - 1; break;
        default:    $until = next_change($sched['slots'])['at'] ?? ($now + 3600);
    }
    $o = ['id' => bin2hex(random_bytes(5)), 'action' => $mode === 'stop' ? 'stop' : 'play', 'until' => $until, 'createdAt' => $now];
    if ($o['action'] === 'play') {
        $uri = normalize_spotify_uri((string)($body['uri'] ?? ''));
        if (!$uri) json_out(['error' => 'Choose a playlist'], 400);
        $o += ['uri' => $uri, 'name' => mb_substr((string)($body['name'] ?? 'Manual'), 0, 120),
               'image' => (string)($body['image'] ?? ''), 'tracks' => (int)($body['tracks'] ?? 0),
               'shuffle' => (bool)($body['shuffle'] ?? true), 'volume' => null];
    }
    update_json('control', function ($c) use ($o) { $c['override'] = $o; return $c; }, ['override' => null, 'nonce' => 1]);
    // The player page applies it within ~10 s (with a smooth fade).
    json_out(['ok' => true, 'override' => $o]);

/* ======================= player endpoints (need ?key= or X-Player-Key) ======================= */
case 'token':
    require_player();
    $tok = spotify_access_token();
    if (!$tok) json_out(['error' => 'Spotify not connected — open the admin page and connect Spotify'], 503);
    $t = load_json('tokens');
    json_out(['access_token' => $tok, 'expires_at' => $t['expires_at'] ?? time() + 3000]);

case 'config':
    require_player();
    $ctrl = control_state();
    json_out([
        'slots' => load_json('schedule', ['slots' => []])['slots'],
        'override' => public_override($ctrl['override'] ?? null),
        'nonce' => $ctrl['nonce'] ?? 1,
        'timezone' => cfg('timezone'),
        'deviceName' => cfg('device_name', 'Sound Machine'),
        'now' => time(),
        'connected' => !empty(load_json('tokens')['refresh_token']),
    ]);

case 'heartbeat':
    require_player();
    if ($method !== 'POST') json_out(['error' => 'POST only'], 405);
    update_json('player', function ($p) use ($body) {
        return [
            'deviceId' => preg_match('/^[a-zA-Z0-9]{10,80}$/', (string)($body['deviceId'] ?? '')) ? $body['deviceId'] : ($p['deviceId'] ?? null),
            'lastSeen' => time(),
            'mode' => mb_substr((string)($body['mode'] ?? ''), 0, 120),
            'error' => mb_substr((string)($body['error'] ?? ''), 0, 300),
        ];
    });
    json_out(['ok' => true]);

default:
    json_out(['error' => 'Unknown action'], 404);
}
