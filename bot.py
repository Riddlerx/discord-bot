import os
import random
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
import aiohttp
import time
from typing import Optional, Dict, Any, List

# Load environment variables
load_dotenv()

# Configuration
GUILD_CHANNEL_ID = int(os.getenv("GUILD_CHANNEL_ID", 1486759247768191018))
ROLE_NAME = os.getenv("ROLE_NAME", "Demigods")
WELCOME_CHANNEL = os.getenv("WELCOME_CHANNEL", "text")
BLIZZARD_CLIENT_ID = os.getenv("BLIZZARD_CLIENT_ID")
BLIZZARD_CLIENT_SECRET = os.getenv("BLIZZARD_CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

REALMS = {
    "frostmourne": 3725,
    "barthilas": 3721,
    "area52": 3676,
    "illidan": 57
}

WELCOME_MESSAGES = [
    "⚔️ A new hero enters Azeroth! Welcome {user}!",
    "🍺 Another adventurer joins the tavern! Welcome {user}!",
    "🔥 Reinforcements have arrived! Welcome {user}!",
    "🏹 The guild grows stronger today! Welcome {user}!",
    "🛡️ The guild welcomes a new champion! {user}!"
]

import json

# Global Caches & State
raider_cache: Dict[str, tuple] = {}
CACHE_DURATION = 1800  # 30 minutes
GUILD_VAULT_MESSAGE_ID: Optional[int] = None
LAST_CONTENT: Optional[str] = None
blizzard_token: Optional[str] = None
blizzard_token_expiry: float = 0
commodities_cache: Optional[Dict] = None
commodities_cache_time: float = 0

STATE_FILE = "bot_state.json"

def save_state():
    """Save persistent bot state."""
    state = {
        "guild_vault_message_id": GUILD_VAULT_MESSAGE_ID,
        "last_content": LAST_CONTENT
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def load_state():
    """Load persistent bot state."""
    global GUILD_VAULT_MESSAGE_ID, LAST_CONTENT
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                GUILD_VAULT_MESSAGE_ID = state.get("guild_vault_message_id")
                LAST_CONTENT = state.get("last_content")
        except Exception as e:
            print(f"⚠️ Error loading state: {e}")

load_state()

# Intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

async def safe_get(session: aiohttp.ClientSession, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None, retries: int = 3, delay: int = 1) -> Optional[Dict]:
    """Get JSON data safely with retries and error handling."""
    for attempt in range(1, retries + 1):
        try:
            async with session.get(url, params=params, headers=headers, timeout=10) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 404:
                    return None
                print(f"⚠️ Request failed (status {response.status}, attempt {attempt}/{retries}): {url}")
        except Exception as e:
            print(f"⚠️ Request failed (attempt {attempt}/{retries}): {e}")
        
        if attempt < retries:
            await asyncio.sleep(delay)
    return None

async def get_access_token(session: aiohttp.ClientSession) -> Optional[str]:
    """Fetch or refresh Blizzard OAuth token."""
    global blizzard_token, blizzard_token_expiry
    now = time.time()
    
    if blizzard_token and now < blizzard_token_expiry:
        return blizzard_token

    url = "https://oauth.battle.net/token"
    auth = aiohttp.BasicAuth(BLIZZARD_CLIENT_ID, BLIZZARD_CLIENT_SECRET)
    
    try:
        async with session.post(url, data={"grant_type": "client_credentials"}, auth=auth) as response:
            if response.status == 200:
                data = await response.json()
                blizzard_token = data.get("access_token")
                # Expire 1 minute early to be safe
                blizzard_token_expiry = now + data.get("expires_in", 3600) - 60
                return blizzard_token
    except Exception as e:
        print(f"❌ Failed to get Blizzard access token: {e}")
    return None

async def get_item_by_id(session: aiohttp.ClientSession, item_id: int) -> Optional[Dict]:
    """Fetch item details directly from Blizzard API using item ID."""
    token = await get_access_token(session)
    if not token: return None

    url = f"https://us.api.blizzard.com/data/wow/item/{item_id}"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"namespace": "static-us", "locale": "en_US"}
    
    data = await safe_get(session, url, headers=headers, params=params)
    if data:
        # Blizzard API returns item name as 'name' directly for this endpoint
        return {"id": data["id"], "name": data["name"]}
    return None


    """Fetch guild roster from Blizzard API."""
    token = await get_access_token(session)
    if not token:
        return []

    url = f"https://us.api.blizzard.com/data/wow/guild/{realm}/{guild}/roster"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"namespace": "profile-us", "locale": "en_US"}

    data = await safe_get(session, url, params=params, headers=headers)
    if not data:
        return []

    members = []
    for m in data.get("members", []):
        char = m["character"]
        members.append({
            "name": char["name"],
            "realm": char["realm"]["slug"]
        })
    return members

async def get_mplus_data(session: aiohttp.ClientSession, name: str, realm: str) -> Dict[str, Any]:
    """Fetch mythic plus and raid data from Raider.io."""
    key = f"{name}-{realm}".lower()
    now = time.time()

    if key in raider_cache:
        data, timestamp = raider_cache[key]
        if now - timestamp < CACHE_DURATION:
            return data

    url = "https://raider.io/api/v1/characters/profile"
    params = {
        "region": "us",
        "realm": realm,
        "name": name,
        "fields": "mythic_plus_weekly_highest_level_runs,mythic_plus_scores_by_season:current,raid_progression"
    }

    data = await safe_get(session, url, params=params)
    if data is None:
        data = {
            "mythic_plus_weekly_highest_level_runs": [],
            "mythic_plus_scores_by_season": [],
            "raid_progression": {}
        }

    raider_cache[key] = (data, now)
    return data

def get_top_keys(data: Dict) -> List[int]:
    """Return top 3 M+ keys for the week."""
    runs = data.get("mythic_plus_weekly_highest_level_runs", [])
    keys = sorted([r["mythic_level"] for r in runs], reverse=True)
    while len(keys) < 3:
        keys.append(0)
    return keys[:3]

def get_raid_vault(data: Dict) -> List[str]:
    """Determine raid vault difficulties based on cumulative kills (M/H/N)."""
    raid = data.get("raid_progression")
    if not raid or not isinstance(raid, dict):
        return ["-", "-", "-"]

    # Find the raid with any progress. Usually the first one, but let's be safe.
    # We prioritize raids that have at least one kill.
    raid_data = None
    for slug in raid:
        curr = raid[slug]
        if curr.get("normal_bosses_killed", 0) > 0 or \
           curr.get("heroic_bosses_killed", 0) > 0 or \
           curr.get("mythic_bosses_killed", 0) > 0:
            raid_data = curr
            break
    
    if not raid_data:
        return ["-", "-", "-"]

    m = raid_data.get("mythic_bosses_killed", 0)
    h = raid_data.get("heroic_bosses_killed", 0)
    n = raid_data.get("normal_bosses_killed", 0)

    # In WoW, vault slots are at 2, 4, and 6 bosses.
    # A slot's difficulty is the highest difficulty you have at least [count] kills in.
    # High difficulty kills count towards lower difficulty slots.
    
    # We assume 'h' and 'n' from Raider.io might not be cumulative (some APIs differ),
    # so we ensure they are for our check.
    h_plus = max(h, m)
    n_plus = max(n, h, m)

    def get_difficulty(count):
        if m >= count: return "M"
        if h_plus >= count: return "H"
        if n_plus >= count: return "N"
        return "-"

    return [get_difficulty(2), get_difficulty(4), get_difficulty(6)]

def format_row(rank: int, name: str, keys: List[int], raid: List[str], score: int, name_width: int) -> str:
    display_name = name if len(name) <= name_width else name[:name_width-1] + "…"
    key_str = f"{keys[0]}/{keys[1]}/{keys[2]}"
    raid_str = "/".join(raid)
    return f"| #{rank:<2} {display_name:<{name_width}} | {key_str:^9} | {raid_str:^9} | {score:>6} |"

async def fetch_char_stats(session: aiohttp.ClientSession, char: Dict) -> Optional[tuple]:
    """Helper for parallel character fetching."""
    data = await get_mplus_data(session, char["name"], char["realm"])
    keys = get_top_keys(data)
    raid = get_raid_vault(data)
    score = 0
    if data.get("mythic_plus_scores_by_season"):
        try:
            score = int(data["mythic_plus_scores_by_season"][0]["scores"]["all"])
        except (KeyError, IndexError, ValueError):
            score = 0
    
    if sum(keys) > 0 or score > 0 or any(r != "-" for r in raid):
        return (char["name"], keys, raid, score)
    return None

async def build_guild_vault(session: aiohttp.ClientSession) -> str:
    realm = "frostmourne"
    guild_name = "sinful-garden"

    guild = await get_guild_roster(session, realm, guild_name)
    if not guild:
        return "⚠️ Error fetching guild roster."

    # Fetch all character data in parallel with limited concurrency
    tasks = [fetch_char_stats(session, char) for char in guild]
    results = await asyncio.gather(*tasks)
    rows = [r for r in results if r is not None]

    # Sort by keys (sum), then score
    rows.sort(key=lambda x: (sum(x[1]), x[3]), reverse=True)

    # Use a default width if no rows
    max_name_len = max((len(name) for name, _, _, _ in rows[:30]), default=10)
    max_name_len = min(max_name_len, 20) # cap width

    table = ["🔥 WEEKLY VAULT LEADERBOARD 🔥"]
    header = f"| {'Name':<{max_name_len + 3}} | Key Vault | Raid Vault | Score |"
    table.append(header)
    table.append(f"|{'-'*(max_name_len+5)}+-----------+-----------+--------|")

    for i, (name, keys, raid, score) in enumerate(rows[:30], start=1):
        table.append(format_row(i, name, keys, raid, score, max_name_len))

    table.append(f"|{'-'*(max_name_len+5)}+-----------+-----------+--------|")

    token_price = await get_wow_token_price(session)
    if token_price > 0:
        table.append(f"💰 WoW Token Price: {token_price:,.0f}g")

    # Add Last Updated timestamp
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    table.append(f"\nLast Updated: {now} (UTC)")

    return "```" + "\n".join(table) + "```"

async def get_wow_token_price(session: aiohttp.ClientSession) -> float:
    token = await get_access_token(session)
    if not token: return 0
    
    url = "https://us.api.blizzard.com/data/wow/token/index"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"namespace": "dynamic-us", "locale": "en_US"}
    
    data = await safe_get(session, url, headers=headers, params=params)
    if data:
        return data.get("price", 0) / 10000
    return 0

async def get_commodities_cached(session: aiohttp.ClientSession) -> Dict:
    global commodities_cache, commodities_cache_time
    now = time.time()
    
    if commodities_cache and now - commodities_cache_time < 1800:
        return commodities_cache

    token = await get_access_token(session)
    if not token: return {}

    url = "https://us.api.blizzard.com/data/wow/auctions/commodities"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"namespace": "dynamic-us", "locale": "en_US"}

    data = await safe_get(session, url, headers=headers, params=params)
    if data:
        commodities_cache = data
        commodities_cache_time = now
        return data
    return commodities_cache or {}

async def search_items(session: aiohttp.ClientSession, item_name: str) -> Optional[Dict]:
    token = await get_access_token(session)
    if not token: return None

    # First, try to find an exact match using the search endpoint
    url = "https://us.api.blizzard.com/data/wow/search/item"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "namespace": "static-us",
        "locale": "en_US",
        "name.en_US": item_name,
        "orderby": "id"
    }
    
    data = await safe_get(session, url, headers=headers, params=params)
    if data:
        results = data.get("results", [])
        for r in results:
            item = r["data"]
            if item.get("name", {}).get("en_US", "").lower() == item_name.lower():
                return {"id": item["id"], "name": item["name"]["en_US"]}
    
    # If not found by name search, try by ID if input is an integer
    try:
        item_id = int(item_name)
        return await get_item_by_id(session, item_id)
    except ValueError:
        pass # Not an integer, continue

    return None

@bot.event
async def on_ready():
    print(f"Bot connected as {bot.user}")
    bot.loop.create_task(auto_update())

async def auto_update():
    await bot.wait_until_ready()
    global GUILD_VAULT_MESSAGE_ID, LAST_CONTENT

    async with aiohttp.ClientSession() as session:
        while not bot.is_closed():
            if GUILD_VAULT_MESSAGE_ID is None:
                await asyncio.sleep(60)
                continue

            channel = bot.get_channel(GUILD_CHANNEL_ID)
            if not channel:
                print(f"❌ Channel {GUILD_CHANNEL_ID} not found")
                await asyncio.sleep(60)
                continue

            try:
                try:
                    message = await channel.fetch_message(GUILD_VAULT_MESSAGE_ID)
                except discord.NotFound:
                    print("❌ Message deleted, resetting...")
                    GUILD_VAULT_MESSAGE_ID = None
                    continue

                new_content = await build_guild_vault(session)
                if new_content != LAST_CONTENT:
                    await message.edit(content=new_content)
                    LAST_CONTENT = new_content
                    save_state()
                    print("✅ Updated leaderboard")
                else:
                    print("⏸ No changes detected")

            except Exception as e:
                print(f"❌ Update error: {e}")

            await asyncio.sleep(1800)

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

@bot.command()
async def guildvault(ctx):
    global GUILD_VAULT_MESSAGE_ID, LAST_CONTENT
    async with ctx.typing():
        async with aiohttp.ClientSession() as session:
            content = await build_guild_vault(session)
            message = await ctx.send(content)
            GUILD_VAULT_MESSAGE_ID = message.id
            LAST_CONTENT = content
            save_state()

@bot.command()
async def price(ctx, item_name: str, realm: str = None):
    async with ctx.typing():
        async with aiohttp.ClientSession() as session:
            item_data = await search_items(session, item_name)
            if not item_data:
                await ctx.send(f"❌ Item **{item_name}** not found.")
                return

            item_id = item_data["id"]
            prices = []

            # Check commodities
            commodities = await get_commodities_cached(session)
            for auction in commodities.get("auctions", []):
                if auction["item"]["id"] == item_id:
                    prices.append(auction["unit_price"])

            # Check realm if provided
            if realm:
                realm_id = REALMS.get(realm.lower().replace(" ", ""))
                if realm_id:
                    token = await get_access_token(session)
                    url = f"https://us.api.blizzard.com/data/wow/connected-realm/{realm_id}/auctions"
                    headers = {"Authorization": f"Bearer {token}"}
                    params = {"namespace": "dynamic-us", "locale": "en_US"}
                    data = await safe_get(session, url, headers=headers, params=params)
                    if data:
                        for auction in data.get("auctions", []):
                            if auction["item"]["id"] == item_id:
                                if "unit_price" in auction:
                                    prices.append(auction["unit_price"])
                                elif "buyout" in auction:
                                    prices.append(auction["buyout"])

            if not prices:
                await ctx.send(f"❌ No auctions found for **{item_data['name']}**.")
                return

            prices_gold = [p / 10000 for p in prices]
            lowest = min(prices_gold)
            avg = sum(prices_gold) / len(prices_gold)

            embed = discord.Embed(
                title=f"💰 {item_data['name']}",
                color=discord.Color.gold()
            )
            embed.add_field(name="📉 Lowest", value=f"{lowest:,.2f}g", inline=True)
            embed.add_field(name="📊 Average", value=f"{avg:,.2f}g", inline=True)
            embed.add_field(name="📦 Listings", value=f"{len(prices):,}", inline=True)
            embed.set_footer(text=f"Realm: {realm.title() if realm else 'Global Commodities'}")
            
            await ctx.send(embed=embed)

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKEN not found in environment.")
    else:
        bot.run(DISCORD_BOT_TOKEN)
