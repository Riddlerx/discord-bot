import os
import random
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
import aiohttp
import time
from typing import Optional, Dict, Any, List
import urllib.parse

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
raider_semaphore = asyncio.Semaphore(5) # Lower concurrency to be gentler

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

# ... (rest of global state)

async def safe_get(session: aiohttp.ClientSession, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None, retries: int = 3, delay: int = 1) -> Optional[Dict]:
    """Get JSON data safely with retries and error handling."""
    for attempt in range(1, retries + 1):
        try:
            async with session.get(url, params=params, headers=headers, timeout=15) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status in [400, 404]:
                    # 400/404 are common for missing characters on Raider.io, handle silently
                    return None
                elif response.status == 429:
                    # Rate limited, wait longer
                    await asyncio.sleep(delay * 3)
                
                if response.status != 200:
                     print(f"⚠️ Request failed (status {response.status}, attempt {attempt}/{retries}): {url}")
        except Exception as e:
            # Only print actual connection/timeout errors
            if attempt == retries:
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

async def get_guild_roster(session: aiohttp.ClientSession, realm: str, guild: str) -> List[Dict]:
    """Fetch guild roster from Blizzard API."""
    token = await get_access_token(session)
    if not token:
        print("DEBUG: No Blizzard token available")
        return []

    url = f"https://us.api.blizzard.com/data/wow/guild/{realm}/{guild}/roster"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"namespace": "profile-us", "locale": "en_US"}
    
    # print(f"DEBUG: Guild roster request - URL: {url}")

    data = await safe_get(session, url, params=params, headers=headers)
    if not data:
        print(f"DEBUG: No data returned for guild {guild} on {realm}")
        return []

    members = []
    for m in data.get("members", []):
        char = m["character"]
        members.append({
            "name": char["name"],
            "realm": char["realm"]["slug"]
        })
    print(f"DEBUG: Found {len(members)} members in guild {guild}")
    return members

blizzard_semaphore = asyncio.Semaphore(10) # Limit concurrent Blizzard profile requests

async def get_vault_data(session: aiohttp.ClientSession, name: str, realm: str) -> tuple:
    """Fetch M+ and Raid vault data from Raider.io and Blizzard API."""
    token = await get_access_token(session)
    if not token:
        return [0, 0, 0], ["-", "-", "-"], 0

    headers = {"Authorization": f"Bearer {token}"}
    params = {"namespace": "profile-us", "locale": "en_US"}
    base_url = f"https://us.api.blizzard.com/profile/wow/character/{realm}/{urllib.parse.quote(name.lower())}"
    
    # Try Raider.io for M+ data (faster updates, avoids hardcoded period/season IDs)
    rio_url = f"https://raider.io/api/v1/characters/profile?region=us&realm={urllib.parse.quote(realm.lower())}&name={urllib.parse.quote(name.lower())}&fields=mythic_plus_weekly_highest_level_runs,mythic_plus_scores_by_season:current"
    
    # Check Raider.io Cache
    cache_key = f"{name}-{realm}".lower()
    cached_rio = None
    if cache_key in raider_cache:
        ts, data = raider_cache[cache_key]
        if time.time() - ts < CACHE_DURATION:
            cached_rio = data

    # Fetch all data in parallel
    async with blizzard_semaphore:
        tasks = [
            safe_get(session, f"{base_url}/encounters/raids", params=params, headers=headers),
            safe_get(session, f"{base_url}/mythic-keystone-profile", params=params, headers=headers)
        ]
        if not cached_rio:
            tasks.append(safe_get(session, rio_url))
        
        # We use a gather but handle the fact that Raider.io task might not be there
        responses = await asyncio.gather(*tasks)
        raid_data = responses[0]
        mplus_data = responses[1]
        rio_data = cached_rio if cached_rio else (responses[2] if len(responses) > 2 else None)
        
        if rio_data and not cached_rio:
            raider_cache[cache_key] = (time.time(), rio_data)

    # 1. Process M+ Data & Score
    keys = [0, 0, 0]
    score = 0
    
    # Prefer Raider.io for M+ because it handles current week better
    if rio_data:
        runs = rio_data.get("mythic_plus_weekly_highest_level_runs", [])
        # Raider.io uses 'mythic_level'
        levels = sorted([r.get("mythic_level", 0) for r in runs if isinstance(r, dict)], reverse=True)
        if levels:
            keys[0] = levels[0]
            keys[1] = levels[3] if len(levels) >= 4 else 0
            keys[2] = levels[7] if len(levels) >= 8 else 0
        
        seasons = rio_data.get("mythic_plus_scores_by_season", [])
        if seasons and isinstance(seasons, list):
            score = int(seasons[0].get("scores", {}).get("all", 0))
    elif mplus_data:
        # Fallback to Blizzard M+ profile
        curr_period = mplus_data.get("current_period", {})
        runs = curr_period.get("best_runs", [])
        # Blizzard uses 'keystone_level'
        levels = sorted([r.get("keystone_level", 0) for r in runs if isinstance(r, dict)], reverse=True)
        if levels:
            keys[0] = levels[0]
            keys[1] = levels[3] if len(levels) >= 4 else 0
            keys[2] = levels[7] if len(levels) >= 8 else 0

    # 2. Process Raid Data
    raid = ["-", "-", "-"]
    if raid_data:
        # Determine last Tuesday (US Reset) - Tuesday is 1 in tm_wday
        now = time.time()
        dt_utc = time.gmtime(now)
        days_since_tue = (dt_utc.tm_wday - 1) % 7

        import calendar
        # Construct the last reset point as Tuesday 15:00 UTC
        reset_day = time.gmtime(now - days_since_tue * 86400)
        reset_time_str = f"{reset_day.tm_year}-{reset_day.tm_mon}-{reset_day.tm_mday} 15:00:00"
        last_reset_ts = calendar.timegm(time.strptime(reset_time_str, "%Y-%m-%d %H:%M:%S"))

        if now < last_reset_ts:
            last_reset_ts -= 7 * 86400

        # Midnight Expansion Identifiers
        # We check both name and common Journal IDs for the Profile API
        CURRENT_EXPANSION_NAMES = ["Midnight", "The Midnight Expansion"]
        CURRENT_EXPANSION_IDS = [501, 17, 506]

        weekly_bosses = {"mythic": set(), "heroic": set(), "normal": set()}

        for exp in raid_data.get("expansions", []):
            expansion_info = exp.get("expansion", {})
            exp_name = expansion_info.get("name")
            exp_id = expansion_info.get("id")
            
            # ONLY count kills from the current expansion (Midnight)
            is_midnight = (exp_name in CURRENT_EXPANSION_NAMES) or (exp_id in CURRENT_EXPANSION_IDS)
            
            if is_midnight:
                for instance in exp.get("instances", []):
                    for mode in instance.get("modes", []):
                        diff = mode["difficulty"]["type"].lower()
                        if diff in weekly_bosses:
                            for encounter in mode.get("progress", {}).get("encounters", []):
                                last_kill = encounter.get("last_kill_timestamp", 0) / 1000
                                if last_kill >= last_reset_ts:
                                    weekly_bosses[diff].add(encounter["encounter"]["name"])

        m = len(weekly_bosses["mythic"])
        h = len(weekly_bosses["heroic"])
        n = len(weekly_bosses["normal"])
        
        h_plus = h + m
        n_plus = n + h + m

        def get_diff(count):
            if m >= count: return "M"
            if h_plus >= count: return "H"
            if n_plus >= count: return "N"
            return "-"
        raid = [get_diff(2), get_diff(4), get_diff(6)]

    return keys, raid, score

def format_row(rank: int, name: str, keys: List[int], raid: List[str], score: int, name_width: int) -> str:
    display_name = name if len(name) <= name_width else name[:name_width-1] + "…"
    key_str = f"{keys[0]}/{keys[1]}/{keys[2]}"
    raid_str = "/".join(raid)
    return f"| #{rank:<2} {display_name:<{name_width}} | {key_str:^9} | {raid_str:^9} | {score:>6} |"

async def fetch_char_stats(session: aiohttp.ClientSession, char: Dict) -> Optional[tuple]:
    """Helper for parallel character fetching."""
    keys, raid, score = await get_vault_data(session, char["name"], char["realm"])
    
    # ONLY show characters who have actually done something THIS WEEK for the vault.
    # If they only have a score from previous weeks/seasons, they shouldn't be on the vault board.
    if sum(keys) > 0 or any(r != "-" for r in raid):
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

    unix_now = int(time.time())
    return "```" + "\n".join(table) + "```" + f"Last Updated: <t:{unix_now}:R>"

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

async def search_items(session: aiohttp.ClientSession, item_name: str) -> List[Dict]:
    token = await get_access_token(session)
    if not token: return []

    url = "https://us.api.blizzard.com/data/wow/search/item"
    headers = {"Authorization": f"Bearer {token}"}
    
    # Try multiple variations of the name (original, spaces instead of dashes, etc.)
    clean_name = item_name.strip().rstrip(".,")
    variations = [clean_name]
    if "-" in clean_name:
        variations.append(clean_name.replace("-", " "))
        variations.append(clean_name.replace("-", ""))

    for name_variant in variations:
        # Strategy 1: Relevance (no sort) for exact variant - best for exact matches
        # Strategy 2: Modern items first (id:desc) for wildcard - best for finding newest items
        search_strategies = [
            ({"name.en_US": name_variant}, {}),
            ({"name.en_US": f"*{name_variant}*"}, {"orderby": "id:desc"}),
        ]

        for q_params, extra_params in search_strategies:
            params = {
                "namespace": "static-us",
                "locale": "en_US",
                **q_params,
                **extra_params
            }
            data = await safe_get(session, url, headers=headers, params=params)
            if data and data.get("results"):
                results = data.get("results")
                
                # 1. Look for EXACT case-insensitive match
                exact_matches = []
                for r in results:
                    name = r["data"].get("name", {}).get("en_US", "")
                    if name.lower() == name_variant.lower() or name.lower() == clean_name.lower():
                        exact_matches.append({"id": r["data"]["id"], "name": name})
                
                if exact_matches:
                    return sorted(exact_matches, key=lambda x: x["id"])
                
                # 2. Look for "STARTS WITH" match
                starts_with = []
                for r in results:
                    name = r["data"].get("name", {}).get("en_US", "")
                    if name.lower().startswith(name_variant.lower()) or name.lower().startswith(clean_name.lower()):
                        starts_with.append({"id": r["data"]["id"], "name": name})
                if starts_with:
                    # Return all starts_with matches, limited to a reasonable number of unique names
                    unique_names = []
                    final_results = []
                    for m in sorted(starts_with, key=lambda x: x["id"]):
                        if m["name"] not in unique_names:
                            if len(unique_names) >= 5: continue
                            unique_names.append(m["name"])
                        final_results.append(m)
                    return final_results

                # 3. Look for "CONTAINS" match (fallback for items with prefixes like "Pattern: ")
                contains = []
                for r in results:
                    name = r["data"].get("name", {}).get("en_US", "")
                    if name_variant.lower() in name.lower() or clean_name.lower() in name.lower():
                        contains.append({"id": r["data"]["id"], "name": name})
                if contains:
                    # Sort by name length to get the most specific match first
                    contains.sort(key=lambda x: len(x["name"]))
                    unique_names = []
                    final_results = []
                    for m in contains:
                        if m["name"] not in unique_names:
                            if len(unique_names) >= 5: continue
                            unique_names.append(m["name"])
                        final_results.append(m)
                    return final_results

    # Look for common prefixes and typos
    prefixes = [
        "pattern", "patern", "recipe", "receipe", "design", "plans", "schematic", 
        "formula", "technique", "contract", "plans", "blueprint"
    ]
    
    clean_lower = clean_name.lower()
    for p in prefixes:
        # Check if it starts with prefix + space or prefix + colon
        if clean_lower.startswith(p + " ") or clean_lower.startswith(p + ":"):
            # Strip prefix and any following colon/space
            shorter_name = clean_name[len(p):].lstrip(": ").strip()
            if shorter_name:
                shorter_results = await search_items(session, shorter_name)
                if shorter_results:
                    return shorter_results

    # Final Fallback: If it's "Infused X", try searching just "X"
    if clean_lower.startswith("infused "):
        shorter_name = clean_name[8:]
        return await search_items(session, shorter_name)

    return []

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
        try:
            async with aiohttp.ClientSession() as session:
                content = await build_guild_vault(session)
                if len(content) > 2000:
                    # Truncate or handle over-limit message
                    # For now, let's just send a warning if it's too long
                    # but we'll try to send the first 2000 chars or just fewer rows.
                    # A better way is to reduce row count in build_guild_vault.
                    print(f"⚠️ Warning: Leaderboard content too long ({len(content)} chars).")
                    if content.endswith("```"):
                        content = content[:1990] + "\n...```"
                    else:
                        content = content[:2000]

                message = await ctx.send(content)
                GUILD_VAULT_MESSAGE_ID = message.id
                LAST_CONTENT = content
                save_state()
        except Exception as e:
            print(f"❌ Error in guildvault command: {e}")
            await ctx.send(f"⚠️ An error occurred while building the vault: {e}")

@bot.command()
async def price(ctx, *, search: str):
    async with ctx.typing():
        item_name = search
        realm = None
        
        if ":" in search:
            # Check if the part after the LAST colon is a known realm
            parts = search.rsplit(":", 1)
            potential_realm = parts[1].strip().lower().replace(" ", "").replace("'", "")
            
            is_realm = potential_realm in REALMS
            if not is_realm:
                # Also check partial matches in REALMS
                for k in REALMS.keys():
                    if potential_realm in k:
                        is_realm = True
                        break
            
            if is_realm:
                item_name = parts[0].strip()
                realm = parts[1].strip()
            else:
                # Colon is likely part of the item name (e.g. "Pattern: ...")
                item_name = search
                realm = None

        # Fallback to default realm if not a commodity and no realm specified
        if not realm:
            realm = "frostmourne"

        async with aiohttp.ClientSession() as session:
            item_results = await search_items(session, item_name)
            if not item_results:
                await ctx.send(f"❌ Item **{item_name}** not found. (v2.2)")
                return

            # Group qualities if they have the same name
            display_name = item_results[0]["name"]
            unique_names = list(set(r["name"] for r in item_results))
            
            embed = discord.Embed(
                title=f"💰 {display_name}" if len(unique_names) == 1 else "💰 Search Results",
                color=discord.Color.gold()
            )

            # Get commodities for all IDs
            commodities = await get_commodities_cached(session)
            
            # Get realm data
            realm_data = None
            if realm:
                realm_key = realm.lower().replace(" ", "").replace("'", "")
                realm_id = REALMS.get(realm_key)
                if not realm_id:
                    for k, v in REALMS.items():
                        if realm_key in k:
                            realm_id = v
                            realm = k
                            break
                if realm_id:
                    token = await get_access_token(session)
                    url = f"https://us.api.blizzard.com/data/wow/connected-realm/{realm_id}/auctions"
                    headers = {"Authorization": f"Bearer {token}"}
                    params = {"namespace": "dynamic-us", "locale": "en_US"}
                    realm_data = await safe_get(session, url, headers=headers, params=params)

            for i, item in enumerate(item_results):
                item_id = item["id"]
                current_item_name = item["name"]
                prices = []
                
                # Check commodities
                for auction in commodities.get("auctions", []):
                    if auction["item"]["id"] == item_id:
                        prices.append(auction["unit_price"])
                
                # Check realm (if not a commodity or to find realm-specific versions)
                if realm_data:
                    for auction in realm_data.get("auctions", []):
                        if auction["item"]["id"] == item_id:
                            if "unit_price" in auction:
                                prices.append(auction["unit_price"])
                            elif "buyout" in auction:
                                prices.append(auction["buyout"])

                if prices:
                    prices_gold = [p / 10000 for p in prices]
                    lowest = min(prices_gold)
                    avg = sum(prices_gold) / len(prices_gold)
                    
                    # Label based on name and quality
                    label = current_item_name
                    if len(item_results) > 1:
                        # If there are multiple items with the same name, they are likely qualities
                        same_name_count = sum(1 for r in item_results if r["name"] == current_item_name)
                        if same_name_count > 1:
                            # Figure out which quality this is
                            quality_idx = [r["id"] for r in item_results if r["name"] == current_item_name].index(item_id)
                            label += f" (Q{quality_idx+1})"
                    
                    val = f"**Lowest:** {lowest:,.2f}g\n**Avg:** {avg:,.2f}g\n**Listings:** {len(prices):,}"
                    embed.add_field(name=label, value=val, inline=True)

            if not embed.fields:
                found_names = ", ".join(unique_names)
                await ctx.send(f"❌ No auctions found for **{found_names}** on {realm.title() if realm else 'Global'}.")
                return

            footer_text = f"Realm: {realm.title()}" if realm else "Global Commodities"
            embed.set_footer(text=footer_text)
            await ctx.send(embed=embed)

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKEN not found in environment.")
    else:
        bot.run(DISCORD_BOT_TOKEN)
