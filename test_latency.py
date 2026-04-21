import asyncio
import time
from discord.ext import commands
import discord
import music
from unittest.mock import MagicMock

async def test():
    bot = MagicMock()
    cog = music.Music(bot)
    
    # Simulate ctx
    ctx = MagicMock()
    ctx.typing = MagicMock()
    ctx.typing.return_value = MagicMock()
    ctx.typing.return_value.__aenter__ = MagicMock(return_value=asyncio.Future())
    ctx.typing.return_value.__aexit__ = MagicMock(return_value=asyncio.Future())
    
    # Time the play logic
    start = time.perf_counter()
    
    # Mimic the play command logic
    # 1. ensure_voice
    await cog._ensure_voice(ctx)
    # 2. get_stream_url
    info = await music.get_stream_url('never gonna give you up')
    # 3. play_track
    await cog._play_track(ctx, info, ensure_voice=False)
    
    print(f'Total time: {time.perf_counter() - start:.2f}s')

asyncio.run(test())
