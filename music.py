import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import tempfile
import glob
from collections import deque

TEMP_DIR = os.path.join(tempfile.gettempdir(), 'discord_music')
os.makedirs(TEMP_DIR, exist_ok=True)

YDL_OPTIONS = {
    'format': 'bestaudio[ext=webm]/bestaudio/best',  # prefer webm (smaller)
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',

    'cookiefile': os.path.join(os.path.dirname(__file__), 'cookies.txt'),

    'http_headers': {
        'User-Agent': 'Mozilla/5.0',
    },

    'js_runtimes': {'node': {}},
    'remote_components': ['ejs:github'],

    'outtmpl': os.path.join(TEMP_DIR, '%(id)s.%(ext)s'),
    # Removed postprocessors — no conversion needed, FFmpeg reads any format
}

MUSIC_TEXT_CHANNEL = os.getenv("MUSIC_TEXT_CHANNEL", "music-bot")


def get_stream_url(query: str) -> dict:
    """Search and return the direct stream URL and info."""
    opts = {**YDL_OPTIONS, 'format': 'bestaudio/best', 'skip_download': True}
    if 'outtmpl' in opts: del opts['outtmpl']
    if 'postprocessors' in opts: del opts['postprocessors']
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
        if 'entries' in info:
            info = info['entries'][0]
    return info


def get_audio_path(video_id: str) -> str | None:
    """Find the downloaded audio file for a video ID."""
    patterns = [
        os.path.join(TEMP_DIR, f'{video_id}.opus'),
        os.path.join(TEMP_DIR, f'{video_id}.m4a'),
        os.path.join(TEMP_DIR, f'{video_id}.webm'),
        os.path.join(TEMP_DIR, f'{video_id}.*'),
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


def cleanup_file(filepath: str):
    """Remove a temp audio file."""
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
    except Exception:
        pass


def cleanup_all():
    """Remove all temp audio files."""
    for f in glob.glob(os.path.join(TEMP_DIR, '*')):
        try:
            os.remove(f)
        except Exception:
            pass


# ── Per-guild state ────────────────────────────────────────────────────────────

class GuildState:
    def __init__(self):
        self.queue: deque[tuple[str, str]] = deque()
        self.current_title: str | None = None
        self.current_file: str | None = None
        self.volume: float = 0.5
        self.is_loading: bool = False


# ── Cog ───────────────────────────────────────────────────────────────────────

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._states: dict[int, GuildState] = {}

    def state(self, guild_id: int) -> GuildState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildState()
        return self._states[guild_id]

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Restrict music commands to one text channel per guild."""
        if ctx.guild is None:
            await ctx.send("❌ Music commands can only be used in a server.")
            return False

        if isinstance(ctx.channel, discord.TextChannel) and ctx.channel.name == MUSIC_TEXT_CHANNEL:
            return True

        await ctx.send(f"❌ Use music commands in #{MUSIC_TEXT_CHANNEL}.")
        return False

    # ── internal playback ────────────────────────────────────────────────────

    async def _ensure_voice(self, ctx: commands.Context) -> bool:
        """Connect/move to the author's voice channel. Returns False on failure."""
        if not ctx.author.voice:
            await ctx.send("❌ Join a voice channel first.")
            return False
        channel = ctx.author.voice.channel
        try:
            if ctx.voice_client is None:
                await channel.connect()
            elif ctx.voice_client.channel != channel:
                await ctx.voice_client.move_to(channel)
        except discord.ClientException as exc:
            await ctx.send(f"❌ Could not join voice: {exc}")
            return False
        except Exception as exc:
            await ctx.send(f"❌ Voice connection failed: {exc}")
            return False
        return True

    async def _play_query(self, ctx: commands.Context, query: str, title: str | None = None):
        """Get stream URL and start playing using FFmpeg with optimized flags."""
        st = self.state(ctx.guild.id)
        st.is_loading = True

        # NOTE: The 5s delay here is caused by YouTube URL extraction (yt-dlp). 
        # Future optimization: Implement a pre-fetcher that extracts this URL
        # while the current song is still playing.
        
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, lambda: get_stream_url(query))
            stream_url = info.get('url')
            if not title:
                title = info.get('title', 'Unknown')
            if not stream_url:
                raise Exception("Could not extract stream URL")
        except Exception as e:
            await ctx.send(f"❌ Could not load track: {e}")
            st.is_loading = False
            self._advance(ctx)
            return

        # Optimized FFmpeg flags for OCI/network resilience
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 100M -analyzeduration 0 -fflags nobuffer -flags low_delay',
            'options': '-vn'
        }

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(stream_url, **ffmpeg_options),
            volume=st.volume,
        )
        
        try:
            if ctx.voice_client:
                st.current_title = title
                st.is_loading = False
                await ctx.send(f"▶️ Now playing: **{title}**")
                ctx.voice_client.play(source, after=self._make_after_callback(ctx))
            else:
                st.current_title = None
                st.is_loading = False
        except Exception as exc:
            await ctx.send(f"❌ Playback failed: {exc}")
            st.current_title = None
            st.is_loading = False
            self._advance(ctx)

    def _make_after_callback(self, ctx: commands.Context):
        def _after(error):
            if error:
                print(f"❌ Voice playback error: {error}")
            st = self.state(ctx.guild.id)
            self._advance(ctx)

        return _after

    def _advance(self, ctx: commands.Context):
        """Called when a track ends — pops the next item from the queue."""
        st = self.state(ctx.guild.id)
        if st.queue:
            query, title = st.queue.popleft()
            asyncio.run_coroutine_threadsafe(
                self._play_query(ctx, query, title), self.bot.loop
            )
        else:
            st.current_title = None

    # ── commands ─────────────────────────────────────────────────────────────

    @commands.command()
    async def join(self, ctx):
        """Join your voice channel."""
        await self._ensure_voice(ctx)

    @commands.command(aliases=['p'])
    async def play(self, ctx, *, query: str):
        """Play a song or add it to the queue."""
        if not await self._ensure_voice(ctx):
            return

        st = self.state(ctx.guild.id)
        vc = ctx.voice_client

        if vc.is_playing() or vc.is_paused() or st.is_loading:
            async with ctx.typing():
                loop = asyncio.get_event_loop()
                try:
                    # Just search for title, don't download yet
                    def _search(q):
                        opts = {**YDL_OPTIONS, 'skip_download': True}
                        del opts['outtmpl']
                        del opts['postprocessors']
                        with yt_dlp.YoutubeDL(opts) as ydl:
                            info = ydl.extract_info(q, download=False)
                        if 'entries' in info:
                            info = info['entries'][0]
                        return info
                    info = await loop.run_in_executor(None, lambda: _search(query))
                    title = info.get('title', query)
                except Exception:
                    title = query

            st.queue.append((query, title))
            pos = len(st.queue)
            await ctx.send(f"📋 Added to queue (#{pos}): **{title}**")
        else:
            async with ctx.typing():
                await self._play_query(ctx, query)

    @commands.command()
    async def skip(self, ctx):
        """Skip the current song."""
        st = self.state(ctx.guild.id)
        if not ctx.voice_client:
            return await ctx.send("❌ Not connected to voice.")

        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            ctx.voice_client.stop()  # triggers _after callback which cleans up
            await ctx.send("⏭️ Skipped.")
        elif st.is_loading:
            await ctx.send("⏳ Currently loading the next song... please wait.")
        elif st.queue:
            self._advance(ctx)
            await ctx.send("⏭️ Skipped (manual advance).")
        else:
            await ctx.send("❌ Nothing is playing.")

    @commands.command()
    async def pause(self, ctx):
        """Pause playback."""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("⏸️ Paused.")
        else:
            await ctx.send("❌ Nothing is playing.")

    @commands.command()
    async def resume(self, ctx):
        """Resume paused playback."""
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("▶️ Resumed.")
        else:
            await ctx.send("❌ Not paused.")

    @commands.command()
    async def stop(self, ctx):
        """Stop playback, clear the queue, and leave."""
        st = self.state(ctx.guild.id)
        st.queue.clear()
        st.current_title = None
        st.is_loading = False
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
        await ctx.send("⏹️ Stopped and left the channel.")

    @commands.command(aliases=['q'])
    async def queue(self, ctx):
        """Show the current queue."""
        st = self.state(ctx.guild.id)
        if not st.current_title and not st.queue and not st.is_loading:
            return await ctx.send("📋 Queue is empty.")

        lines = []
        if st.is_loading:
            lines.append("⏳ **Loading next song...**")
        elif st.current_title:
            lines.append(f"▶️ **Now playing:** {st.current_title}")

        for i, (query, title) in enumerate(list(st.queue)[:10], 1):
            lines.append(f"`{i}.` {title}")

        if len(st.queue) > 10:
            lines.append(f"… and {len(st.queue) - 10} more")

        await ctx.send("\n".join(lines))

    @commands.command(aliases=['vol'])
    async def volume(self, ctx, level: int):
        """Set volume from 1 to 100."""
        if not 1 <= level <= 100:
            return await ctx.send("❌ Volume must be between 1 and 100.")
        st = self.state(ctx.guild.id)
        st.volume = level / 100
        if ctx.voice_client and ctx.voice_client.source:
            ctx.voice_client.source.volume = st.volume
        await ctx.send(f"🔊 Volume set to **{level}%**")

    @commands.command(aliases=['np'])
    async def nowplaying(self, ctx):
        """Show the currently playing song."""
        st = self.state(ctx.guild.id)
        if st.is_loading:
            await ctx.send("⏳ Loading next song...")
        elif st.current_title:
            await ctx.send(f"▶️ Now playing: **{st.current_title}**")
        else:
            await ctx.send("❌ Nothing is playing.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member == self.bot.user:
            return
        vc = member.guild.voice_client
        if not vc or not vc.is_connected():
            return
        if len([m for m in vc.channel.members if not m.bot]) == 0:
            await asyncio.sleep(60)
            if vc.is_connected() and len([m for m in vc.channel.members if not m.bot]) == 0:
                st = self.state(member.guild.id)
                st.queue.clear()
                st.current_title = None
                await vc.disconnect()


async def setup(bot):
    await bot.add_cog(Music(bot))
