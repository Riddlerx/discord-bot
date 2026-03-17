import os
import requests
import random
import discord
from discord.ext import commands
from dotenv import load_dotenv
load_dotenv()
import time

cache = {}
cache_time = 0

# intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

REALMS = {
    "frostmourne": 3725,
    "barthilas": 3721,
    "area52": 3676,
    "illidan": 57
}

def normalize_realm(name):
    return name.lower().replace(" ", "")
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

def get_commodities(token):
    url = "https://us.api.blizzard.com/data/wow/auctions/commodities"

    headers = {
        "Authorization": f"Bearer {token}"
    }

    params = {
        "namespace": "dynamic-us",
        "locale": "en_US"
    }

    response = requests.get(url, headers=headers, params=params)

    return response.json()

def get_access_token():
    url = "https://oauth.battle.net/token"

    response = requests.post(
        url,
        data={"grant_type": "client_credentials"},
        auth=(
            os.getenv("BLIZZARD_CLIENT_ID"),
            os.getenv("BLIZZARD_CLIENT_SECRET")
        )
    )

    data = response.json()

    return data.get("access_token")

def search_items(token, item_name):
    """Search items by name and auto-select main item (ignore recipes, teas, etc.)"""
    url = "https://us.api.blizzard.com/data/wow/search/item"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "namespace": "static-us",
        "locale": "en_US",
        "name.en_US": item_name
    }
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    results = data.get("results", [])

    if not results:
        return None  # no items found

    # Filter: only include items with exact name match, ignore recipes and teas
    filtered = []
    for r in results:
        item = r["data"]
        name = item["name"]["en_US"].lower()
        if name == item_name.lower():
            filtered.append({
                "id": item["id"],
                "name": item["name"]["en_US"],
                "level": item.get("level", "N/A")
            })

    if not filtered:
        # fallback: pick the first item anyway
        item = results[0]["data"]
        return {"id": item["id"], "name": item["name"]["en_US"], "level": item.get("level", "N/A")}

    # return the first exact match
    return filtered[0]

@bot.command()
async def price(ctx, item: str, realm: str = None):
    await ctx.send(f"🔎 Searching for **{item}**...")

    try:
        token = get_access_token()
        if not token:
            await ctx.send("⚠️ Failed to get API token.")
            return

        # 🔹 Step 1: Search for item (auto-select main item)
        item_data = search_items(token, item)
        if not item_data:
            await ctx.send("❌ Item not found.")
            return

        item_id = item_data["id"]

        # 🔹 Step 2: Get prices
        prices = []

        # 🌿 Try global commodities first
        data = get_commodities_cached(token)
        auctions = data.get("auctions", [])
        for auction in auctions:
            if auction["item"]["id"] == item_id:
                prices.append(auction["unit_price"])

        # 🧾 If user specified a realm, try realm AH
        if realm:
            realm_key = normalize_realm(realm)
            realm_id = REALMS.get(realm_key)
            if not realm_id:
                await ctx.send("❌ Supported realms: Frostmourne, Barthilas, Area 52, Illidan")
                return

            url = f"https://us.api.blizzard.com/data/wow/connected-realm/{realm_id}/auctions"
            headers = {"Authorization": f"Bearer {token}"}
            params = {"namespace": "dynamic-us", "locale": "en_US"}
            response = requests.get(url, headers=headers, params=params)
            data = response.json()
            auctions = data.get("auctions", [])

            for auction in auctions:
                if auction["item"]["id"] == item_id and "buyout" in auction:
                    prices.append(auction["buyout"])

        if not prices:
            await ctx.send("❌ No auctions found for this item.")
            return

        # 🔹 Step 3: Price calculation
        prices_gold = [p / 10000 for p in prices]
        lowest = min(prices_gold)
        filtered = [p for p in prices_gold if p <= lowest * 5]
        if not filtered:
            filtered = prices_gold
        avg = sum(filtered) / len(filtered)

        # 🔹 Step 4: Embed output
        embed = discord.Embed(
            title=f"💰 {item_data['name']}",
            description="Auction House Data",
            color=discord.Color.green()
        )
        embed.add_field(name="📉 Lowest", value=f"{lowest:.2f}g", inline=True)
        embed.add_field(name="📊 Average", value=f"{avg:.2f}g", inline=True)
        embed.add_field(name="📦 Listings", value=f"{len(prices)}", inline=True)

        if realm:
            embed.set_footer(text=f"Realm: {realm.title()}")
        else:
            embed.set_footer(text="Global Commodities")

        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send("⚠️ Error fetching data.")
        print(e)

def get_commodities_cached(token):
    global cache, cache_time

    if time.time() - cache_time < 1800:  # 30 min cache
        return cache

    data = get_commodities(token)
    cache = data
    cache_time = time.time()

    return data

token = os.getenv("DISCORD_BOT_TOKEN")

if not token:
    raise Exception("DISCORD_BOT_TOKEN not set! Please set the environment variable.")

bot.run(token)
