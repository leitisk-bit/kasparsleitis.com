<?php
declare(strict_types=1);
require __DIR__ . '/lib.php';

// Admin-only: start the Spotify login (Authorization Code flow)
if (!is_admin()) { header('Location: admin.html'); exit; }

$ts = (string)time();
$state = $ts . '.' . sign('oauth|' . $ts);
header('Location: https://accounts.spotify.com/authorize?' . http_build_query([
    'response_type' => 'code',
    'client_id' => cfg('spotify_client_id'),
    'scope' => SPOTIFY_SCOPES,
    'redirect_uri' => cfg('redirect_uri'),
    'state' => $state,
    'show_dialog' => 'true',
]));
