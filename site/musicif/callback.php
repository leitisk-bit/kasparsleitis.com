<?php
declare(strict_types=1);
require __DIR__ . '/lib.php';

function done(string $msg, bool $ok): void
{
    header('Location: admin.html#setup?' . http_build_query([$ok ? 'connected' : 'error' => $msg]));
    exit;
}

if (!is_admin()) done('Log in to the admin page first', false);
if (!empty($_GET['error'])) done('Spotify: ' . $_GET['error'], false);

$state = (string)($_GET['state'] ?? '');
if (!preg_match('/^(\d+)\.([a-f0-9]{64})$/', $state, $m)
    || !hash_equals(sign('oauth|' . $m[1]), $m[2])
    || (int)$m[1] < time() - 600) {
    done('Login expired or invalid — try again', false);
}

$r = spotify_token_request([
    'grant_type' => 'authorization_code',
    'code' => (string)($_GET['code'] ?? ''),
    'redirect_uri' => cfg('redirect_uri'),
]);
if ($r['status'] !== 200 || empty($r['json']['refresh_token'])) {
    done('Token exchange failed: ' . ($r['json']['error_description'] ?? $r['json']['error'] ?? ('HTTP ' . $r['status'])), false);
}

$t = [
    'access_token' => $r['json']['access_token'],
    'refresh_token' => $r['json']['refresh_token'],
    'expires_at' => time() + (int)($r['json']['expires_in'] ?? 3600),
    'scope' => $r['json']['scope'] ?? '',
    'connected_at' => time(),
];
save_json('tokens', $t);

$me = spotify_api('GET', '/me');
if ($me['status'] === 200) {
    update_json('tokens', function ($t) use ($me) {
        $t['user'] = $me['json']['display_name'] ?? ($me['json']['id'] ?? 'Spotify user');
        return $t;
    });
}
done('Spotify connected', true);
