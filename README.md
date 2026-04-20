# discordbot

## YouTube auth for music playback

Some YouTube videos may require signed-in access to extract audio. If you see an error like "Sign in to confirm you’re not a bot," set one of these environment variables:

- `YTDLP_COOKIES` – path to a yt-dlp-compatible cookie file
- `YTDLP_COOKIES_FROM_BROWSER` – browser name for yt-dlp's `--cookies-from-browser` support (for example, `chrome`)

Example:

```bash
export YTDLP_COOKIES=/path/to/youtube_cookies.txt
```

## Oracle Cloud note

This happens much more often on Oracle Cloud public IPs than on a local machine. In practice:

- `YTDLP_COOKIES` is the reliable server-side option
- `YTDLP_COOKIES_FROM_BROWSER` is mostly useful on a desktop/laptop that already has your browser profile

Recommended flow for OCI:

1. On your local computer, export YouTube cookies in `cookies.txt` format.
2. Copy that file to the server, for example to `/home/ubuntu/discordbot/cookies.txt`.
3. Set `YTDLP_COOKIES=/home/ubuntu/discordbot/cookies.txt` in the bot environment.
4. Restart the bot process.

If the error returns later, re-export the cookies. YouTube cookies can expire or become invalid after account/security changes.
