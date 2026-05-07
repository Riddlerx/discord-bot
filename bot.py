import os
import random
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
import time
import logging

# Load environment variables
load_dotenv(override=True)
STARTUP_MONOTONIC = time.perf_counter()

# Configuration
ROLE_NAME = os.getenv("ROLE_NAME", "Demigods")
WELCOME_CHANNEL = os.getenv("WELCOME_CHANNEL", "text")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

WELCOME_MESSAGES = [
    "⚔️ A new hero enters Azeroth! Welcome {user}!",
    "🍺 Another adventurer joins the tavern! Welcome {user}!",
    "🔥 Reinforcements have arrived! Welcome {user}!",
    "🏹 The guild grows stronger today! Welcome {user}!",
    "🛡️ The guild welcomes a new champion! {user}!"
]

def setup_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("discord").setLevel(logging.INFO)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

setup_logging()
logger = logging.getLogger("discordbot")

# Intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    case_insensitive=True,
    help_command=None
)

@bot.command(name="help")
async def help_command(ctx):
    """Display the list of available commands."""
    msg = (
        "**Commands List**\n\n"
        "🎵 **Music**\n"
        "`!play url/search` - Play a song\n"
        "`!skip` - Skip current track\n"
        "`!pause`/`!resume` - Toggle playback\n"
        "`!loop` - Cycle loop: off/song/queue\n"
        "`!volume <1-100>` - Set audio level\n"
        "`!q` - Show the queue\n"
        "`!np` - Show current song\n"
        "`!remove <index>` - Remove from queue\n"
        "`!stop` - Stop & leave channel\n\n"
        "🧹 **Management**\n"
        "`!clear` - Empty the queue\n"
        "`!roll <max>` - Roll 1-100 (or max)\n"
        "`!coin` - Flip a coin\n\n"
        "💰 **Economy & WoW**\n"
        "`!price item[:realm]` - Check WoW AH\n"
        "`!lookup name[-realm]` - WoW character stats\n"
        "`!guildvault` - Show guild leaderboard"
    )
    await ctx.send(msg)

def ensure_voice_dependencies() -> None:
    """Check for Opus and Davey libraries required for Discord voice."""
    if not discord.opus.is_loaded():
        for candidate in ("libopus.so.0", "libopus.so"):
            try:
                discord.opus.load_opus(candidate)
                logger.info("Loaded Opus library: %s", candidate)
                break
            except OSError:
                continue
        else:
            logger.warning("Opus library could not be loaded. Voice playback may fail.")

@bot.event
async def on_ready():
    startup_elapsed = time.perf_counter() - STARTUP_MONOTONIC
    logger.info("Bot connected as %s in %.2fs", bot.user, startup_elapsed)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="!help for commands"))

@bot.event
async def on_disconnect():
    logger.warning("Discord gateway disconnected")

@bot.event
async def on_resumed():
    logger.info("Discord gateway session resumed")

@bot.event
async def on_member_join(member):
    role = discord.utils.get(member.guild.roles, name=ROLE_NAME)
    if role:
        await member.add_roles(role)

    channel = discord.utils.get(member.guild.text_channels, name=WELCOME_CHANNEL)
    if channel:
        message = random.choice(WELCOME_MESSAGES)
        await channel.send(message.format(user=member.mention))

@bot.command()
async def roll(ctx, max_num: int = 100):
    number = random.randint(1, max_num)
    await ctx.send(f"🎲 {ctx.author.mention} rolled **{number}** (1-{max_num})")

@bot.command()
async def coin(ctx):
    result = random.choice(["Heads", "Tails"])
    await ctx.send(f"🪙 {ctx.author.mention} flipped **{result}**!")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    logger.exception("Command error: %s", error)
    await ctx.send(f"⚠️ Error: {error}")

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not found in environment.")
    else:
        async def main():
            async with bot:
                ensure_voice_dependencies()
                
                # Load WoW Cog
                try:
                    await bot.load_extension('wow')
                    logger.info("WoW extension loaded")
                except Exception as e:
                    logger.exception("Failed to load WoW extension: %s", e)

                # Load Music Cog
                enable_music = os.getenv("ENABLE_MUSIC_FEATURES", "true").lower() in ("true", "1", "yes", "on")
                if enable_music:
                    try:
                        await bot.load_extension('music')
                        logger.info("Music extension loaded")
                    except Exception as e:
                        logger.exception("Failed to load music extension: %s", e)
                
                await bot.start(DISCORD_BOT_TOKEN)
        
        asyncio.run(main())
