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

# Create two instances: one fast/unauthenticated, one authenticated with cookies
YDL_OPTIONS_FAST = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch1',
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'no_color': True,
    'cachedir': False,
    'lazy_extractors': True,
}

YDL_OPTIONS_AUTH = {
    **YDL_OPTIONS_FAST,
    'cookiefile': os.path.join(os.path.dirname(__file__), 'cookies.txt'),
}

_ydl_fast = yt_dlp.YoutubeDL(YDL_OPTIONS_FAST)
_ydl_auth = yt_dlp.YoutubeDL(YDL_OPTIONS_AUTH)

async def get_stream_url(query: str) -> dict:
    """Search with fast/unauthenticated instance first, fallback to cookies on failure."""
    loop = asyncio.get_event_loop()
    
    # Try fast way first
    try:
        info = await loop.run_in_executor(None, lambda: _ydl_fast.extract_info(query, download=False))
    except Exception:
        # If fast fails, try with cookies
        print(f"⚠️ Fast search failed for '{query}', retrying with cookies...")
        info = await loop.run_in_executor(None, lambda: _ydl_auth.extract_info(query, download=False))

    if not info:
        raise Exception("Could not extract info.")
        
    if 'entries' in info:
        if not info['entries']:
            raise Exception("No results found.")
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
        self.queue: deque[dict] = deque()  # stores full info dicts
        self.current_title: str | None = None
        self.current_file: str | None = None
        self.volume: float = 0.5
        self.is_loading: bool = False
        self.loop_mode: str = "off"  # "off", "song", "queue"
        self.current_info: dict | None = None


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

    async def _play_track(self, ctx: commands.Context, info: dict):
        """Start playing a track using extracted info."""
        st = self.state(ctx.guild.id)
        st.is_loading = True
        st.current_info = info

        title = info.get('title', 'Unknown')
        stream_url = info.get('url')
        
        # If URL is missing or likely expired (old info), re-extract
        if not stream_url:
            try:
                # Use original_url or webpage_url if available
                query = info.get('original_url') or info.get('webpage_url') or info.get('title')
                info = await get_stream_url(query)
                st.current_info = info
                stream_url = info.get('url')
                title = info.get('title', title)
            except Exception as e:
                await ctx.send(f"❌ Could not re-extract track: {e}")
                st.is_loading = False
                self._advance(ctx)
                return

        if not stream_url:
            await ctx.send(f"❌ Could not extract stream URL for **{title}**")
            st.is_loading = False
            self._advance(ctx)
            return

        # Optimized FFmpeg flags for OCI/network resilience
        user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        ffmpeg_options = {
            'before_options': f'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 32k -analyzeduration 0 -fflags nobuffer -flags low_delay -user_agent "{user_agent}"',
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
                await ctx.send(f"▶️ Now playing: **{title}**" + (f" (Loop: {st.loop_mode})" if st.loop_mode != "off" else ""))
                ctx.voice_client.play(source, after=self._make_after_callback(ctx))
                
                # Start pre-fetching the next track if available
                self.bot.loop.create_task(self._prefetch_next(ctx))
            else:
                st.current_title = None
                st.is_loading = False
        except Exception as exc:
            await ctx.send(f"❌ Playback failed: {exc}")
            st.current_title = None
            st.is_loading = False
            self._advance(ctx)

    async def _prefetch_next(self, ctx: commands.Context):
        """Extract info for the next track in queue while current is playing."""
        st = self.state(ctx.guild.id)
        if not st.queue:
            return
            
        next_track = st.queue[0]
        # If it's just a placeholder or doesn't have a direct URL, extract it
        if not next_track.get('url'):
            try:
                query = next_track.get('original_url') or next_track.get('title')
                info = await get_stream_url(query)
                st.queue[0].update(info)
                print(f"✅ Prefetched: {info.get('title')}")
            except Exception as e:
                print(f"⚠️ Prefetch failed: {e}")

    def _make_after_callback(self, ctx: commands.Context):
        def _after(error):
            if error:
                print(f"❌ Voice playback error: {error}")
            self._advance(ctx)

        return _after

    def _advance(self, ctx: commands.Context):
        """Called when a track ends — pops the next item from the queue."""
        st = self.state(ctx.guild.id)
        
        # Handle Loop Logic
        if st.loop_mode == "song" and st.current_info:
            # Re-play same track
            asyncio.run_coroutine_threadsafe(self._play_track(ctx, st.current_info), self.bot.loop)
            return
        
        if st.loop_mode == "queue" and st.current_info:
            # Add the track that just finished to the back of the queue
            st.queue.append(st.current_info)

        if st.queue:
            info = st.queue.popleft()
            asyncio.run_coroutine_threadsafe(
                self._play_track(ctx, info), self.bot.loop
            )
        else:
            st.current_title = None
            st.current_info = None

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

        async with ctx.typing():
            try:
                info = await get_stream_url(query)
                info['original_url'] = query # Keep original query
            except Exception as e:
                return await ctx.send(f"❌ Could not load track: {e}")

        if vc.is_playing() or vc.is_paused() or st.is_loading:
            st.queue.append(info)
            pos = len(st.queue)
            await ctx.send(f"📋 Added to queue (#{pos}): **{info.get('title')}**")
        else:
            await self._play_track(ctx, info)


    @commands.command()
    async def skip(self, ctx):
        """Skip the current song."""
        st = self.state(ctx.guild.id)
        if not ctx.voice_client:
            return await ctx.send("❌ Not connected to voice.")

        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            # If looping a song, temporarily disable it for manual skip
            original_loop = st.loop_mode
            if st.loop_mode == "song":
                st.loop_mode = "off"
            
            ctx.voice_client.stop()
            await ctx.send("⏭️ Skipped.")
            
            # Restore loop mode after a tiny delay so _advance sees "off" first
            if original_loop == "song":
                await asyncio.sleep(0.5)
                st.loop_mode = "song"
                
        elif st.is_loading:
            await ctx.send("⏳ Currently loading the next song... please wait.")
        elif st.queue:
            self._advance(ctx)
            await ctx.send("⏭️ Skipped (manual advance).")
        else:
            await ctx.send("❌ Nothing is playing.")

    @commands.command()
    async def loop(self, ctx, mode: str = None):
        """Change loop mode: off, song, queue."""
        st = self.state(ctx.guild.id)
        valid_modes = ["off", "song", "queue"]
        
        if mode is None:
            # Cycle through modes
            idx = (valid_modes.index(st.loop_mode) + 1) % len(valid_modes)
            st.loop_mode = valid_modes[idx]
        elif mode.lower() in valid_modes:
            st.loop_mode = mode.lower()
        else:
            return await ctx.send(f"❌ Invalid mode. Use: `!loop <off|song|queue>`")
            
        emoji = {"off": "➡️", "song": "🔂", "queue": "🔁"}
        await ctx.send(f"{emoji[st.loop_mode]} Loop mode set to: **{st.loop_mode}**")

    @commands.command()
    async def shuffle(self, ctx):
        """Shuffle the current queue."""
        st = self.state(ctx.guild.id)
        if len(st.queue) < 2:
            return await ctx.send("❌ Not enough songs in queue to shuffle.")
            
        import random
        # Convert to list, shuffle, then back to deque
        temp_list = list(st.queue)
        random.shuffle(temp_list)
        st.queue = deque(temp_list)
        await ctx.send("🔀 Queue shuffled.")

    @commands.command(aliases=['rm'])
    async def remove(self, ctx, index: int):
        """Remove a song from the queue by its index."""
        st = self.state(ctx.guild.id)
        if index < 1 or index > len(st.queue):
            return await ctx.send(f"❌ Invalid index. Use `!q` to see song numbers.")
            
        # Deque doesn't support direct index removal well, convert to list
        temp_list = list(st.queue)
        removed = temp_list.pop(index - 1)
        st.queue = deque(temp_list)
        
        await ctx.send(f"🗑️ Removed: **{removed.get('title')}**")


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

        for i, info in enumerate(list(st.queue)[:10], 1):
            title = info.get('title', 'Unknown')
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
