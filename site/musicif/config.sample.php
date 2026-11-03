<?php
// Copy this file to config.php and fill in your values.
// config.php is never served as text (PHP executes it), so secrets stay private.
return [
    // From https://developer.spotify.com/dashboard → your app → Settings
    'spotify_client_id'     => 'PASTE_CLIENT_ID',
    'spotify_client_secret' => 'PASTE_CLIENT_SECRET',

    // Must match EXACTLY the Redirect URI added in the Spotify app settings
    'redirect_uri' => 'https://kasparsleitis.com/musicif/callback.php',

    // Password for the admin page (remote control)
    'admin_password' => 'change-me-please',

    // Secret key for the player PC. Open the player as:
    //   https://kasparsleitis.com/musicif/player.html?key=THIS_VALUE
    // Generate something long and random, e.g. 32+ letters/numbers.
    'player_key' => 'change-me-to-a-long-random-string',

    // Schedule times are interpreted in this time zone
    'timezone' => 'Europe/Riga',

    // Name shown in Spotify's device list
    'device_name' => 'Sound Machine',
];
