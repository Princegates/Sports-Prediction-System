# Desktop member-count widget (Windows, Rainmeter)

A small always-on-top card on your desktop showing total members, new today and
members with access, refreshed every minute. No need to open the platform.

## One-time setup

1. **Make a key.** In PowerShell, run:
   `-join ((48..57)+(97..122) | Get-Random -Count 40 | % {[char]$_})`
   and copy the 40-character result.
2. **Save it on the server.** Sign in as Super Admin, open **Settings ->
   Automatic refresh -> Desktop widget key**, paste it, and save. (The feed is
   off until a key of at least 24 characters is set. Keys are write-only: the
   page never shows it back.)
3. **Install Rainmeter** (free): https://www.rainmeter.net
4. **Add the skin.** Copy the `SoccaUsers` folder into
   `Documents\Rainmeter\Skins\`, open `SoccaUsers\SoccaUsers.ini` in Notepad and
   replace `PASTE_YOUR_KEY_HERE` with your key. Save.
5. In Rainmeter's manager, **Refresh all**, pick **SoccaUsers -> SoccaUsers.ini**
   and **Load**. Drag the card where you want it; right-click -> *Settings* ->
   *Draggable / Keep on top* as you prefer. Click the card to refresh now.

## What it exposes

`GET https://api.soccaintel.com/api/public/widget/users?key=...` returns only
`total_users`, `new_today`, `active_access`, `unverified` and a timestamp. No
names or emails. Anyone with the key can read those four numbers, so keep it
out of screenshots; to rotate it, set a new key in Settings (the old one stops
working at once). A wrong key gets 403 and the feed is rate-limited.

## Phone instead?

The same URL works from any widget app that can fetch JSON (for example
Scriptable on iPhone).
