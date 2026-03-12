import os
import random
import discord
from discord.ext import commands

# intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

ROLE_NAME = "Demigods"
WELCOME_CHANNEL = "text"

WELCOME_MESSAGES = [
    "⚔️ A new hero enters Azeroth! Welcome {user}!",
    "🍺 Another adventurer joins the tavern! Welcome {user}!",
    "🔥 Reinforcements have arrived! Welcome {user}!",
    "🏹 The guild grows stronger today! Welcome {user}!",
    "🛡️ The guild welcomes a new champion! {user}!"
]

@bot.event
async def on_ready():
    print(f"Bot connected as {bot.user}")

@bot.event
async def on_member_join(member):

    # give role
    role = discord.utils.get(member.guild.roles, name=ROLE_NAME)
    if role:
        await member.add_roles(role)

    # welcome message
    channel = discord.utils.get(member.guild.text_channels, name=WELCOME_CHANNEL)
    if channel:
        message = random.choice(WELCOME_MESSAGES)
        await channel.send(message.format(user=member.mention))

# 🎲 roll command
@bot.command()
async def roll(ctx, max_num: int = 100):
    number = random.randint(1, max_num)
    await ctx.send(f"🎲 {ctx.author.mention} rolled **{number}** (1-{max_num})")

# 🪙 coin flip
@bot.command()
async def coin(ctx):
    result = random.choice(["Heads", "Tails"])
    await ctx.send(f"🪙 {ctx.author.mention} flipped **{result}**!")

token = os.getenv("DISCORD_BOT_TOKEN")

if not token:
    raise Exception("DISCORD_BOT_TOKEN not set!")

bot.run(token)
