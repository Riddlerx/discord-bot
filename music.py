import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
from collections import deque

YDL_OPTIONS = {
    'format': 'bestaudio[ext=m4a]/bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',

    # ✅ Helps avoid bot detection
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    },

    # ✅ Stability improvements
    'geo_bypass': True,
    'retries': 10,
    'fragment_retries': 10,

    # ✅ Avoid incomplete extraction issues
    'extract_flat': False,

    # ✅ Fixed signature solving (required for 2026 YouTube changes)
    'js_runtimes': {'node': {}},
    'remote_components': ['ejs:github'],
    'cookiefile': 'cookies.txt', 
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_at_eof 1',
    'options': '-vn -loglevel warning',
}

MUSIC_TEXT_CHANNEL = os.getenv("MUSIC_TEXT_CHANNEL", "music-bot")

def fetch_info(query: str) -> dict:
    """Blocking yt-dlp call — run in executor so it doesn't freeze the bot."""
    import time
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                info = ydl.extract_info(query, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            return info
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            time.sleep(1)
    return {}

# ── Per-guild state ────────────────────────────────────────────────────────────

class GuildState:
    def __init__(self):
        self.queue: deque[str] = deque()
        self.current_title: str | None = None
        self.volume: float = 0.5

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

    async def _play_query(self, ctx: commands.Context, query: str):
        """Fetch audio info and start playing."""
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, lambda: fetch_info(query))
        except Exception as e:
            await ctx.send(f"❌ Could not load track: {e}")
            self._advance(ctx)
            return

        url = info['url']
        title = info.get('title', 'Unknown')
        self.state(ctx.guild.id).current_title = title

        ffmpeg_opts = FFMPEG_OPTIONS.copy()
        ffmpeg_opts['before_options'] += (
            ' -user_agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" '
            '-referer "https://www.youtube.com/"'
        )

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(url, **ffmpeg_opts),
            volume=self.state(ctx.guild.id).volume,
        )
        try:
            ctx.voice_client.play(source, after=self._make_after_callback(ctx))
        except Exception as exc:
            await ctx.send(f"❌ Playback failed: {exc}")
            self.state(ctx.guild.id).current_title = None
            return
        await ctx.send(f"▶️ Now playing: **{title}**")

    def _make_after_callback(self, ctx: commands.Context):
        def _after(error):
            if error:
                print(f"❌ Voice playback error: {error}")
            self._advance(ctx)

        return _after

    def _advance(self, ctx: commands.Context):
        """Called when a track ends — pops the next item from the queue."""
        st = self.state(ctx.guild.id)
        if st.queue:
            query = st.queue.popleft()
            asyncio.run_coroutine_threadsafe(
                self._play_query(ctx, query), self.bot.loop
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

        vc = ctx.voice_client

        if vc.is_playing() or vc.is_paused():
            async with ctx.typing():
                loop = asyncio.get_event_loop()
                try:
                    info = await loop.run_in_executor(None, lambda: fetch_info(query))
                    title = info.get('title', query)
                except Exception:
                    title = query

            self.state(ctx.guild.id).queue.append(query)
            pos = len(self.state(ctx.guild.id).queue)
            await ctx.send(f"📋 Added to queue (#{pos}): **{title}**")
        else:
            async with ctx.typing():
                await self._play_query(ctx, query)

    @commands.command()
    async def skip(self, ctx):
        """Skip the current song."""
        if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
            ctx.voice_client.stop()
            await ctx.send("⏭️ Skipped.")
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
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
        await ctx.send("⏹️ Stopped and left the channel.")

    @commands.command(aliases=['q'])
    async def queue(self, ctx):
        """Show the current queue."""
        st = self.state(ctx.guild.id)
        if not st.current_title and not st.queue:
            return await ctx.send("📋 Queue is empty.")

        lines = []
        if st.current_title:
            lines.append(f"▶️ **Now playing:** {st.current_title}")
        for i, query in enumerate(list(st.queue)[:10], 1):
            lines.append(f"`{i}.` {query}")
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
        title = self.state(ctx.guild.id).current_title
        if title:
            await ctx.send(f"▶️ Now playing: **{title}**")
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
