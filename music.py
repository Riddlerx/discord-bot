import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import random
import tempfile
import glob
import time
from concurrent.futures import ThreadPoolExecutor
from collections import deque

TEMP_DIR = os.path.join(tempfile.gettempdir(), 'discord_music')
os.makedirs(TEMP_DIR, exist_ok=True)

# ── yt-dlp options ─────────────────────────────────────────────────────────────

YDL_OPTIONS_FAST = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch1',
    'quiet': True,
    'no_warnings': True,
    'no_color': True,
    'js_runtimes': {'node': {}},
    'force_ipv4': True,
    'retries': 5,
    'fragment_retries': 5,
    'concurrent_fragment_downloads': 5,
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'cookiefile': os.getenv("YTDLP_COOKIES") or os.getenv("YOUTUBE_COOKIES_PATH") or 'cookies.txt',
    'proxy': os.getenv("YTDLP_PROXY"),
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'web'],
            'player_skip': ['webpage', 'configs'],
        }
    },
    'noprogress': True,
    'no_part': True,  # Write directly to destination to save disk I/O
    'buffersize': 16384, # 16K buffer to keep RAM usage low
    'outtmpl': os.path.join(TEMP_DIR, '%(id)s.%(ext)s'),
}

YDL_OPTIONS_FALLBACK = {
    **YDL_OPTIONS_FAST,
    'format': 'bestaudio/best',
}


def _get_yt_dlp_auth_config() -> dict:
    """Return yt-dlp auth-related options from environment variables."""
    cookies_path = os.getenv("YTDLP_COOKIES") or os.getenv("YOUTUBE_COOKIES_PATH")
    cookies_from_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER")
    auth_options: dict = {}

    if cookies_path:
        if not os.path.exists(cookies_path):
            print(f"\u26a0\ufe0f yt-dlp cookie file not found: {cookies_path}")
        else:
            auth_options["cookiefile"] = cookies_path

    if cookies_from_browser:
        auth_options["cookiesfrombrowser"] = (cookies_from_browser,)

    return auth_options


def _build_ydl_options(base_options: dict) -> dict:
    """Clone base yt-dlp options and apply auth and environment configuration."""
    auth_cfg = _get_yt_dlp_auth_config()
    options = {
        **base_options,
        **auth_cfg,
    }

    force_ipv4 = os.getenv("YTDLP_FORCE_IPV4")
    if force_ipv4 is not None:
        options["force_ipv4"] = force_ipv4.lower() in ("1", "true", "yes", "on")

    js_runtime = os.getenv("YTDLP_JS_RUNTIME")
    if js_runtime:
        options["js_runtimes"] = {js_runtime: {}}

    if auth_cfg.get("cookiefile"):
        print(f"\u2705 Using cookies from: {auth_cfg['cookiefile']}")
    elif base_options.get("cookiefile"):
        print(f"\u2705 Using default cookies from: {base_options['cookiefile']}")

    return options


_ydl_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="yt-dlp")
_extract_semaphore = asyncio.Semaphore(1)
_info_cache: dict[str, tuple[float, dict]] = {}
_info_cache_lock = asyncio.Lock()
_inflight_queries: dict[str, asyncio.Future] = {}
_inflight_queries_lock = asyncio.Lock()
_INFO_CACHE_TTL = 3600
_STARTUP_WARMUP_DELAY = 5
_STARTUP_WARMUP_YOUTUBE = os.getenv("MUSIC_WARMUP_YOUTUBE", "").strip().lower() in ("1", "true", "yes", "on")


def _normalize_query(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().lower()


def _clone_info(info: dict) -> dict:
    return dict(info)


async def _read_cached_info(keys: list[str]) -> dict | None:
    now = time.monotonic()
    async with _info_cache_lock:
        for key in keys:
            cached = _info_cache.get(key)
            if cached and now - cached[0] < _INFO_CACHE_TTL:
                return _clone_info(cached[1])
    return None


async def _store_cached_info(info: dict, *keys: str | None):
    now = time.monotonic()
    cached_info = _clone_info(info)
    cache_keys = {_normalize_query(key) for key in keys}
    cache_keys.update(
        {
            _normalize_query(info.get("id")),
            _normalize_query(info.get("webpage_url")),
            _normalize_query(info.get("original_url")),
            _normalize_query(info.get("title")),
        }
    )
    async with _info_cache_lock:
        expired = [key for key, (ts, _) in _info_cache.items() if now - ts >= _INFO_CACHE_TTL]
        for key in expired:
            _info_cache.pop(key, None)
        for key in cache_keys:
            if key:
                _info_cache[key] = (now, cached_info)


def get_audio_path(video_id: str) -> str | None:
    """Find the downloaded audio file for a video ID."""
    patterns = [
        os.path.join(TEMP_DIR, f'{video_id}.opus'),
        os.path.join(TEMP_DIR, f'{video_id}.m4a'),
        os.path.join(TEMP_DIR, f'{video_id}.webm'),
        os.path.join(TEMP_DIR, f'{video_id}.mp4'),
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


# ── Core: single-call search + download ───────────────────────────────────────

async def search_and_download(query: str, *, refresh: bool = False) -> tuple[dict, str]:
    """Search YouTube, extract info, and download audio in ONE yt-dlp call.

    Returns (info_dict, audio_filepath).
    Uses caching and dedup to avoid redundant work.
    """
    normalized = _normalize_query(query)

    # 1. Check cache — if we already have info + file on disk, return immediately
    if not refresh:
        cached = await _read_cached_info([normalized] if normalized else [])
        if cached and cached.get('id'):
            existing_path = get_audio_path(cached['id'])
            if existing_path:
                return cached, existing_path

    # 2. Dedup in-flight requests for the same query
    inflight_key = f"refresh:{normalized}" if refresh else (normalized or query)
    future: asyncio.Future | None = None
    is_owner = False
    async with _inflight_queries_lock:
        future = _inflight_queries.get(inflight_key)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            _inflight_queries[inflight_key] = future
            is_owner = True

    if not is_owner:
        result = await asyncio.shield(future)
        info = _clone_info(result[0])
        return info, result[1]

    # 3. Single yt-dlp call: search + extract + download
    try:
        loop = asyncio.get_running_loop()

        def _do_search_and_download():
            opts = _build_ydl_options(YDL_OPTIONS_FAST)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=True)

            if info and 'entries' in info:
                if not info['entries']:
                    raise Exception("No results found.")
                info = info['entries'][0]

            if not info or not info.get('id'):
                raise Exception("Could not extract video info.")

            path = get_audio_path(info['id'])
            if not path:
                raise Exception(f"Download finished but file not found for {info.get('id')}")

            return info, path

        async with _extract_semaphore:
            info, path = await loop.run_in_executor(_ydl_executor, _do_search_and_download)

        await _store_cached_info(info, query)
        future.set_result((_clone_info(info), path))
        return info, path

    except Exception as exc:
        # On failure, retry once with fallback options
        error_text = str(exc).lower()
        if "requested format" in error_text:
            try:
                def _do_fallback():
                    opts = _build_ydl_options(YDL_OPTIONS_FALLBACK)
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(query, download=True)
                    if info and 'entries' in info:
                        if not info['entries']:
                            raise Exception("No results found.")
                        info = info['entries'][0]
                    if not info or not info.get('id'):
                        raise Exception("Could not extract video info.")
                    path = get_audio_path(info['id'])
                    if not path:
                        raise Exception(f"Download finished but file not found")
                    return info, path

                async with _extract_semaphore:
                    info, path = await loop.run_in_executor(_ydl_executor, _do_fallback)

                await _store_cached_info(info, query)
                future.set_result((_clone_info(info), path))
                return info, path
            except Exception as fallback_exc:
                future.set_exception(fallback_exc)
                future.exception()
                raise fallback_exc

        future.set_exception(exc)
        future.exception()
        raise
    finally:
        async with _inflight_queries_lock:
            current = _inflight_queries.get(inflight_key)
            if current is future:
                _inflight_queries.pop(inflight_key, None)


# ── Per-guild state ────────────────────────────────────────────────────────────

class GuildState:
    def __init__(self):
        self.queue: deque[dict] = deque()
        self.current_title: str | None = None
        self.current_file: str | None = None
        self.volume: float = 0.5
        self.is_loading: bool = False
        self.loop_mode: str = "off"
        self.current_info: dict | None = None
        self.advance_lock = asyncio.Lock()
        self.prefetch_task: asyncio.Task | None = None


# ── Cog ───────────────────────────────────────────────────────────────────────

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._states: dict[int, GuildState] = {}
        self._warmup_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None

    def state(self, guild_id: int) -> GuildState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildState()
        return self._states[guild_id]

    async def cog_load(self):
        cleanup_all()
        self._warmup_task = asyncio.create_task(self._warmup_extractors())
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

    def cog_unload(self):
        if self._warmup_task and not self._warmup_task.done():
            self._warmup_task.cancel()
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
        for st in self._states.values():
            if st.prefetch_task and not st.prefetch_task.done():
                st.prefetch_task.cancel()

    async def _periodic_cleanup(self):
        """Periodically remove old audio files to save disk space."""
        try:
            while True:
                await asyncio.sleep(3600)  # Every hour
                now = time.time()
                for f in glob.glob(os.path.join(TEMP_DIR, '*')):
                    try:
                        # If file is older than 2 hours, remove it
                        if os.path.isfile(f) and now - os.path.getmtime(f) > 7200:
                            os.remove(f)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass

    async def _warmup_extractors(self):
        try:
            await asyncio.sleep(_STARTUP_WARMUP_DELAY)
            loop = asyncio.get_running_loop()
            start = time.perf_counter()
            # Warm up by triggering lazy extractor loading
            await loop.run_in_executor(
                _ydl_executor,
                lambda: yt_dlp.YoutubeDL(_build_ydl_options(YDL_OPTIONS_FAST))._ies,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            print(f"\u2705 Music extractors warmed in {elapsed_ms:.0f}ms")

            if _STARTUP_WARMUP_YOUTUBE:
                start = time.perf_counter()
                await loop.run_in_executor(
                    _ydl_executor,
                    lambda: yt_dlp.YoutubeDL(_build_ydl_options(YDL_OPTIONS_FAST)).extract_info(
                        "ytsearch1:youtube", download=False
                    ),
                )
                elapsed_ms = (time.perf_counter() - start) * 1000
                print(f"\u2705 Music YouTube warmup finished in {elapsed_ms:.0f}ms")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"\u26a0\ufe0f Music warmup failed: {exc}")

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Restrict music commands to one text channel per guild."""
        if ctx.guild is None:
            await ctx.send("\u274c Music commands can only be used in a server.")
            return False

        # Default to 'music-bot' if not specified in environment
        music_channel_name = os.getenv("MUSIC_TEXT_CHANNEL", "music-bot")

        if isinstance(ctx.channel, discord.TextChannel) and ctx.channel.name == music_channel_name:
            return True

        await ctx.send(f"\u274c Music commands can only be used in the #{music_channel_name} channel.")
        return False

    # ── internal playback ────────────────────────────────────────────────────

    async def _ensure_voice(self, ctx: commands.Context) -> bool:
        """Connect/move to the author's voice channel. Returns False on failure."""
        try:
            if ctx.voice_client:
                if ctx.voice_client.is_connected():
                    # Move if author is in a different channel
                    if ctx.author.voice and ctx.voice_client.channel != ctx.author.voice.channel:
                        await ctx.voice_client.move_to(ctx.author.voice.channel)
                    return True
                else:
                    # Clean up "ghost" connection
                    await ctx.voice_client.disconnect(force=True)

            if not ctx.author.voice:
                await ctx.send("\u274c Join a voice channel first.")
                return False

            await ctx.author.voice.channel.connect(timeout=60.0, reconnect=True)
            return True
        except discord.ClientException as exc:
            await ctx.send(f"\u274c Could not join voice: {exc}")
            return False
        except Exception as exc:
            await ctx.send(f"\u274c Voice connection failed: {exc}")
            return False
        return True

    async def _play_track(self, ctx: commands.Context, info: dict, *, ensure_voice: bool = True):
        """Start playing a track from a downloaded file."""
        st = self.state(ctx.guild.id)

        if ensure_voice and not await self._ensure_voice(ctx):
            return

        st.is_loading = True
        st.current_info = info
        title = info.get('title', 'Unknown')

        # Get audio path — may already be downloaded by prefetch or play command
        audio_path = info.get('_audio_path')
        if not audio_path or not os.path.exists(audio_path):
            try:
                query = info.get('original_url') or info.get('webpage_url') or info.get('title')
                info, audio_path = await search_and_download(query, refresh=True)
                st.current_info = info
                title = info.get('title', title)
            except Exception as e:
                await ctx.send(f"\u274c Could not download track: {e}")
                st.is_loading = False
                self._advance(ctx)
                return

        st.current_file = audio_path

        # Using FFmpeg filters for volume control to allow using FFmpegOpusAudio
        # This is much more CPU efficient on e2-micro instances
        volume_filter = f'volume={st.volume}'
        
        try:
            source = discord.FFmpegOpusAudio(
                audio_path,
                before_options='-nostdin -thread_queue_size 4096',
                options=f'-vn -loglevel warning -af {volume_filter}'
            )
        except Exception as e:
            print(f"DEBUG: FFmpegOpusAudio failed, falling back to PCMAudio: {e}")
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(
                    audio_path,
                    before_options='-nostdin -thread_queue_size 4096',
                    options=f'-vn -loglevel warning -af {volume_filter}'
                ),
                volume=st.volume
            )

        try:
            if ctx.voice_client:
                st.current_title = title
                st.is_loading = False
                await ctx.send(f"\u25b6\ufe0f Now playing: **{title}**" + (f" (Loop: {st.loop_mode})" if st.loop_mode != "off" else ""))
                ctx.voice_client.play(source, after=self._make_after_callback(ctx))
                self._schedule_prefetch(ctx)
            else:
                st.current_title = None
                st.is_loading = False
        except Exception as exc:
            await ctx.send(f"\u274c Playback failed: {exc}")
            st.current_title = None
            st.is_loading = False
            self._advance(ctx)

    def _schedule_prefetch(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)
        if st.prefetch_task and not st.prefetch_task.done():
            return
        st.prefetch_task = self.bot.loop.create_task(self._prefetch_next(ctx))

    async def _prefetch_next(self, ctx: commands.Context):
        """Pre-download audio for the next track while current plays."""
        st = self.state(ctx.guild.id)
        current_task = asyncio.current_task()
        try:
            # Small delay to let the current playback stabilize
            await asyncio.sleep(5)
            
            if not st.queue:
                return
            next_track = st.queue[0]
            try:
                query = next_track.get('original_url') or next_track.get('webpage_url') or next_track.get('title')
                if query:
                    info, path = await search_and_download(query)
                    if st.queue and st.queue[0] is next_track:
                        st.queue[0].update(info)
                        st.queue[0]['_audio_path'] = path
                        print(f"\u2705 Prefetched: {info.get('title')}")
            except Exception as e:
                print(f"\u26a0\ufe0f Prefetch failed: {e}")
        finally:
            if st.prefetch_task is current_task:
                st.prefetch_task = None

    def _make_after_callback(self, ctx: commands.Context):
        def _after(error):
            if error:
                print(f"\u274c Voice playback error: {error}")
            self._advance(ctx)
        return _after

    def _advance(self, ctx: commands.Context):
        """Called when a track ends \u2014 pops the next item from the queue."""
        asyncio.run_coroutine_threadsafe(self._advance_async(ctx), self.bot.loop)

    async def _advance_async(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)
        async with st.advance_lock:
            next_info = None
            if st.loop_mode == "song" and st.current_info:
                next_info = _clone_info(st.current_info)
                # Preserve audio path for looped song
                if st.current_file:
                    next_info['_audio_path'] = st.current_file
            else:
                if st.loop_mode == "queue" and st.current_info:
                    queued = _clone_info(st.current_info)
                    if st.current_file:
                        queued['_audio_path'] = st.current_file
                    st.queue.append(queued)
                if st.queue:
                    next_info = st.queue.popleft()
                else:
                    st.current_title = None
                    st.current_info = None
                    st.is_loading = False

        if next_info:
            await self._play_track(ctx, next_info)

    # ── commands ─────────────────────────────────────────────────────────────

    @commands.command()
    async def join(self, ctx):
        """Join your voice channel."""
        await self._ensure_voice(ctx)

    @commands.command(aliases=['p'])
    async def play(self, ctx, *, query: str):
        """Play a song or add it to the queue."""
        if ctx.voice_client is None and not ctx.author.voice:
            await ctx.send("\u274c Join a voice channel first.")
            return

        st = self.state(ctx.guild.id)
        print(f"DEBUG: !play command start for '{query}'")

        async with ctx.typing():
            try:
                s_start = time.perf_counter()
                voice_ok = await self._ensure_voice(ctx)
                print(f"DEBUG: Voice took {time.perf_counter() - s_start:.2f}s")

                s_dl = time.perf_counter()
                info, audio_path = await search_and_download(query)
                elapsed = time.perf_counter() - s_dl
                print(f"DEBUG: Search+download took {elapsed:.2f}s -> {audio_path}")

                info['original_url'] = query
                info['_audio_path'] = audio_path
            except Exception as e:
                print(f"DEBUG: Error loading track: {e}")
                return await ctx.send(f"\u274c Could not load track: {e}")

        if not voice_ok:
            return

        vc = ctx.voice_client
        if vc.is_playing() or vc.is_paused() or st.is_loading:
            st.queue.append(info)
            self._schedule_prefetch(ctx)
            pos = len(st.queue)
            await ctx.send(f"\U0001f4cb Added to queue (#{pos}): **{info.get('title')}**")
        else:
            await self._play_track(ctx, info, ensure_voice=False)

    @commands.command()
    async def skip(self, ctx):
        """Skip the current song."""
        st = self.state(ctx.guild.id)
        if not ctx.voice_client:
            return await ctx.send("\u274c Not connected to voice.")

        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            original_loop = st.loop_mode
            if st.loop_mode == "song":
                st.loop_mode = "off"
            ctx.voice_client.stop()
            await ctx.send("\u23ed\ufe0f Skipped.")
            if original_loop == "song":
                await asyncio.sleep(0.5)
                st.loop_mode = "song"
        elif st.is_loading:
            await ctx.send("\u23f3 Currently loading the next song... please wait.")
        elif st.queue:
            self._advance(ctx)
            await ctx.send("\u23ed\ufe0f Skipped (manual advance).")
        else:
            await ctx.send("\u274c Nothing is playing.")

    @commands.command()
    async def loop(self, ctx, mode: str = None):
        """Change loop mode: off, song, queue."""
        st = self.state(ctx.guild.id)
        valid_modes = ["off", "song", "queue"]
        if mode is None:
            idx = (valid_modes.index(st.loop_mode) + 1) % len(valid_modes)
            st.loop_mode = valid_modes[idx]
        elif mode.lower() in valid_modes:
            st.loop_mode = mode.lower()
        else:
            return await ctx.send(f"\u274c Invalid mode. Use: `!loop <off|song|queue>`")
        emoji = {"off": "\u27a1\ufe0f", "song": "\U0001f502", "queue": "\U0001f501"}
        await ctx.send(f"{emoji[st.loop_mode]} Loop mode set to: **{st.loop_mode}**")

    @commands.command()
    async def shuffle(self, ctx):
        """Shuffle the current queue."""
        st = self.state(ctx.guild.id)
        if len(st.queue) < 2:
            return await ctx.send("\u274c Not enough songs in queue to shuffle.")
        temp_list = list(st.queue)
        random.shuffle(temp_list)
        st.queue = deque(temp_list)
        await ctx.send("\U0001f500 Queue shuffled.")

    @commands.command(aliases=['rm'])
    async def remove(self, ctx, index: int):
        """Remove a song from the queue by its index."""
        st = self.state(ctx.guild.id)
        if index < 1 or index > len(st.queue):
            return await ctx.send(f"\u274c Invalid index. Use `!q` to see song numbers.")
        temp_list = list(st.queue)
        removed = temp_list.pop(index - 1)
        st.queue = deque(temp_list)
        await ctx.send(f"\U0001f5d1\ufe0f Removed: **{removed.get('title')}**")

    @commands.command()
    async def pause(self, ctx):
        """Pause playback."""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("\u23f8\ufe0f Paused.")
        else:
            await ctx.send("\u274c Nothing is playing.")

    @commands.command()
    async def resume(self, ctx):
        """Resume paused playback."""
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("\u25b6\ufe0f Resumed.")
        else:
            await ctx.send("\u274c Not paused.")

    @commands.command()
    async def stop(self, ctx):
        """Stop playback, clear the queue, and leave."""
        st = self.state(ctx.guild.id)
        st.queue.clear()
        st.current_title = None
        st.current_info = None
        st.current_file = None
        st.is_loading = False
        if st.prefetch_task and not st.prefetch_task.done():
            st.prefetch_task.cancel()
            st.prefetch_task = None
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
        cleanup_all()
        await ctx.send("\u23f9\ufe0f Stopped and left the channel.")

    @commands.command(aliases=['q'])
    async def queue(self, ctx):
        """Show the current queue."""
        st = self.state(ctx.guild.id)
        if not st.current_title and not st.queue and not st.is_loading:
            return await ctx.send("\U0001f4cb Queue is empty.")

        lines = []
        if st.is_loading:
            lines.append("\u23f3 **Loading next song...**")
        elif st.current_title:
            lines.append(f"\u25b6\ufe0f **Now playing:** {st.current_title}")

        for i, info in enumerate(list(st.queue)[:10], 1):
            title = info.get('title', 'Unknown')
            lines.append(f"`{i}.` {title}")

        if len(st.queue) > 10:
            lines.append(f"\u2026 and {len(st.queue) - 10} more")

        await ctx.send("\n".join(lines))

    @commands.command(aliases=['vol'])
    async def volume(self, ctx, level: int):
        """Set volume from 1 to 100."""
        if not 1 <= level <= 100:
            return await ctx.send("\u274c Volume must be between 1 and 100.")
        st = self.state(ctx.guild.id)
        st.volume = level / 100
        
        if ctx.voice_client and ctx.voice_client.is_playing():
             await ctx.send(f"\u2705 Volume set to **{level}%** (will apply to the next song).")
        else:
             await ctx.send(f"\u2705 Volume set to **{level}%**")

    @commands.command(aliases=['np'])
    async def nowplaying(self, ctx):
        """Show the currently playing song."""
        st = self.state(ctx.guild.id)
        if st.is_loading:
            await ctx.send("\u23f3 Loading next song...")
        elif st.current_title:
            await ctx.send(f"\u25b6\ufe0f Now playing: **{st.current_title}**")
        else:
            await ctx.send("\u274c Nothing is playing.")

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
                st.current_info = None
                st.current_file = None
                st.is_loading = False
                if st.prefetch_task and not st.prefetch_task.done():
                    st.prefetch_task.cancel()
                    st.prefetch_task = None
                await vc.disconnect()
                cleanup_all()


async def setup(bot):
    await bot.add_cog(Music(bot))
