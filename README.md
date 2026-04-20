# discordbot

## YouTube auth for music playback

Some YouTube videos may require signed-in access to extract audio. If you see an error like "Sign in to confirm you’re not a bot," set one of these environment variables:

- `YTDLP_COOKIES` – path to a yt-dlp-compatible cookie file
- `YTDLP_COOKIES_FROM_BROWSER` – browser name for yt-dlp's `--cookies-from-browser` support (for example, `chrome`)

Example:

```bash
export YTDLP_COOKIES=/path/to/youtube_cookies.txt
```
