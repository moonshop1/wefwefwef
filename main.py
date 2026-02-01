import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import random
import datetime
import asyncio
from pymongo import MongoClient
import keep_alive  

# --- ⚙️ CONFIGURATION & DATABASE ⚙️ ---
keep_alive.keep_alive()

# This uses os.getenv which is safer for Render
TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URI = os.getenv("MONGO_URI")

# 🚨 EMERGENCY LOGGING (Check your Render logs for these messages!)
if TOKEN is None:
    print("❌ ERROR: The bot cannot find 'BOT_TOKEN'. Check Render -> Environment -> Secret Files/Variables.")
else:
    print(f"✅ Token detected (Length: {len(TOKEN)})")

if MONGO_URI is None:
    print("❌ ERROR: The bot cannot find 'MONGO_URI'.")
else:
    print("✅ MongoDB URI detected.")

# Connect to MongoDB
try:
    cluster = MongoClient(MONGO_URI)
    db = cluster["MoonShop"]
    collection = db["Data"]
    print("✅ MongoDB Connected Successfully!")
except Exception as e:
    print(f"❌ DATABASE CONNECTION ERROR: {e}")

# --- SETUP ---
OWNER_IDS = [1447404658053091388, 1142465701043503125]
CURRENCY = "Moon Coins"
SYMBOL = "🪙"
PAYMENT_LOG_CHANNEL_ID = 1467351916076990627 
MOD_RECEIPT_CHANNEL_ID = 1467351881251684477 

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class MoonBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.synced = False

    async def on_ready(self):
        await self.wait_until_ready()
        if not self.synced:
            await self.tree.sync()
            self.synced = True
        print(f"✅ Logged in as {self.user}")
        print(f"🌕 Moon's Shop Database Connected.")
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Moon's Shop | /help"))

bot = MoonBot()

# --- 💾 DATABASE FUNCTIONS (MongoDB) ---

def load_data():
    # Try to find the data in the database
    data = collection.find_one({"_id": "server_data"})
    
    # If it doesn't exist (first run), create it
    if data is None:
        new_data = {
            "_id": "server_data",
            "users": {},
            "auctions": {},
            "cooldowns": {},
            "shifts": {}
        }
        collection.insert_one(new_data)
        return new_data
    
    return data

def save_data(data):
    # Update the database with new info
    collection.replace_one({"_id": "server_data"}, data)

def get_balance(user_id):
    data = load_data()
    return data["users"].get(str(user_id), 0)

def update_balance(user_id, amount):
    data = load_data()
    uid = str(user_id)
    if uid not in data["users"]:
        data["users"][uid] = 0
    data["users"][uid] += amount
    save_data(data)
    return data["users"][uid]

# --- 🪙 ADMIN MONEY COMMANDS ---

@bot.tree.command(name="add_coins", description="ADMIN: Add Moon Coins to a user's wallet")
@app_commands.describe(user="The customer", amount="Amount of coins to add")
async def add_coins(interaction: discord.Interaction, user: discord.Member, amount: int):
    if interaction.user.id not in OWNER_IDS and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Only Moon can print money.", ephemeral=True)
        return

    new_bal = update_balance(user.id, amount)
    
    embed = discord.Embed(title="✅ Deposit Successful", color=0x00ff00)
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="Added", value=f"+{amount} {SYMBOL}", inline=True)
    embed.add_field(name="New Balance", value=f"{new_bal} {SYMBOL}", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remove_coins", description="ADMIN: Remove Moon Coins from a user")
async def remove_coins(interaction: discord.Interaction, user: discord.Member, amount: int):
    if interaction.user.id not in OWNER_IDS and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
        return

    update_balance(user.id, -amount)
    await interaction.response.send_message(f"📉 Removed **{amount} {SYMBOL}** from {user.mention}.")

# --- 👛 USER ECONOMY COMMANDS ---

@bot.tree.command(name="balance", description="Check your Moon Coin wallet")
async def balance(interaction: discord.Interaction, user: discord.Member = None):
    if user is None:
        user = interaction.user
    bal = get_balance(user.id)
    
    embed = discord.Embed(title=f"👛 {user.display_name}'s Wallet", color=0xF1C40F)
    embed.add_field(name="Balance", value=f"**{bal}** {SYMBOL}", inline=False)
    embed.set_footer(text="Buy more coins in #tickets!")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="pay", description="Pay coins to another user")
async def pay(interaction: discord.Interaction, user: discord.Member, amount: int):
    sender_bal = get_balance(interaction.user.id)
    
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
        return
    if sender_bal < amount:
        await interaction.response.send_message(f"❌ You're broke! You only have {sender_bal} {SYMBOL}.", ephemeral=True)
        return

    update_balance(interaction.user.id, -amount)
    update_balance(user.id, amount)
    
    await interaction.response.send_message(f"💸 **{interaction.user.mention}** sent **{amount} {SYMBOL}** to **{user.mention}**!")

@bot.tree.command(name="leaderboard", description="See the richest people in the server")
async def leaderboard(interaction: discord.Interaction):
    data = load_data()
    # Sort users by balance (highest first)
    sorted_users = sorted(data["users"].items(), key=lambda x: x[1], reverse=True)[:10]
    
    desc = ""
    for i, (uid, bal) in enumerate(sorted_users, 1):
        desc += f"**#{i}** <@{uid}> — {bal} {SYMBOL}\n"
        
    embed = discord.Embed(title="🏆 Moon's Richest List", description=desc, color=0xFFD700)
    await interaction.response.send_message(embed=embed)

# --- 🔨 AUCTION SYSTEM (Pay-on-Win) ---

@bot.tree.command(name="start_auction", description="ADMIN: Start a bidding war")
async def start_auction(interaction: discord.Interaction, item: str, starting_price: int, duration_hours: int):
    if interaction.user.id not in OWNER_IDS:
        return

    end_time = datetime.datetime.now() + datetime.timedelta(hours=duration_hours)
    timestamp = int(end_time.timestamp())

    embed = discord.Embed(title="🔨 NEW AUCTION STARTED!", description=f"**Item:** {item}\n**Starting Bid:** {starting_price} {SYMBOL}", color=0xE67E22)
    embed.add_field(name="Ends In", value=f"<t:{timestamp}:R>", inline=False)
    embed.add_field(name="How to Bid", value=f"`/bid amount:[price] paywith:[method]`", inline=False)
    embed.set_footer(text="Bids are binding. Payment is collected upon winning.")

    await interaction.response.send_message(content="@everyone", embed=embed)
    
    data = load_data()
    data["auctions"] = {
        "item": item, 
        "top_bid": starting_price, 
        "top_bidder": None, 
        "bid_method": None, 
        "end": timestamp
    }
    save_data(data)

@bot.tree.command(name="bid", description="Place a bid (No money taken until you win)")
@app_commands.describe(amount="Amount of Moon Coins (Value)", paywith="How will you pay?")
@app_commands.choices(paywith=[
    app_commands.Choice(name="Moon Coins (Wallet Balance)", value="Balance"),
    app_commands.Choice(name="PayPal (USD Equivalent)", value="PayPal"),
    app_commands.Choice(name="Crypto (LTC/SOL Equivalent)", value="Crypto")
])
async def bid(interaction: discord.Interaction, amount: int, paywith: app_commands.Choice[str]):
    data = load_data()
    auction = data.get("auctions", {})
    
    # 1. Validation
    if not auction or datetime.datetime.now().timestamp() > auction["end"]:
        await interaction.response.send_message("❌ No active auction right now.", ephemeral=True)
        return
    
    if amount <= auction["top_bid"]:
        await interaction.response.send_message(f"❌ Bid too low! Current highest is **{auction['top_bid']} {SYMBOL}**.", ephemeral=True)
        return

    # 2. Balance Check (But NO deduction yet)
    if paywith.value == "Balance":
        bal = get_balance(interaction.user.id)
        if bal < amount:
            await interaction.response.send_message(f"❌ You cannot afford this bid. Your balance: {bal} {SYMBOL}", ephemeral=True)
            return

    # 3. Update Auction Data
    auction["top_bid"] = amount
    auction["top_bidder"] = interaction.user.id
    auction["bid_method"] = paywith.value
    data["auctions"] = auction
    save_data(data)
    
    await interaction.response.send_message(f"✅ **New Top Bid!** {interaction.user.mention} bid **{amount}** using **{paywith.name}**!")

@bot.tree.command(name="end_auction", description="ADMIN: End auction & Create Ticket")
async def end_auction(interaction: discord.Interaction):
    if interaction.user.id not in OWNER_IDS:
        return

    data = load_data()
    auction = data.get("auctions", {})
    
    if not auction:
        await interaction.response.send_message("❌ No auction found.", ephemeral=True)
        return

    winner_id = auction["top_bidder"]
    price = auction["top_bid"]
    item = auction["item"]
    method = auction.get("bid_method", "Unknown")

    if not winner_id:
        await interaction.response.send_message("❌ Auction ended with 0 bids.")
        data["auctions"] = {}
        save_data(data)
        return

    winner = interaction.guild.get_member(winner_id)
    if not winner:
        await interaction.response.send_message("⚠️ Winner has left the server.")
        return

    # --- PAYMENT LOGIC ---
    payment_status = "❌ Payment Pending (Manual)"
    if method == "Balance":
        bal = get_balance(winner_id)
        if bal >= price:
            update_balance(winner_id, -price) # Deduct NOW
            payment_status = "✅ Paid Automatically (Moon Coins)"
        else:
            payment_status = "⚠️ FAILED (Insufficient Funds)"

    # --- CREATE TICKET CHANNEL ---
    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False), 
        interaction.guild.me: discord.PermissionOverwrite(read_messages=True), 
        winner: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    for oid in OWNER_IDS:
        owner_member = interaction.guild.get_member(oid)
        if owner_member:
            overwrites[owner_member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    channel_name = f"auction-winner-{winner.display_name}"
    try:
        ticket_channel = await interaction.guild.create_text_channel(channel_name, overwrites=overwrites)
        ticket_embed = discord.Embed(title="🏆 CONGRATULATIONS!", description=f"{winner.mention} won the auction for **{item}**!", color=0xFFD700)
        ticket_embed.add_field(name="Winning Bid", value=f"{price} {SYMBOL}", inline=True)
        ticket_embed.add_field(name="Method", value=method, inline=True)
        ticket_embed.add_field(name="Payment Status", value=payment_status, inline=False)
        ticket_embed.set_footer(text="Owners will process your delivery here.")
        await ticket_channel.send(content=f"{winner.mention} <@&{OWNER_IDS[0]}>", embed=ticket_embed)
    except:
        await interaction.response.send_message("❌ Could not create ticket channel. Check permissions.")
        return

    public_embed = discord.Embed(title="🏁 AUCTION ENDED", description=f"Winner: {winner.mention}\nItem: **{item}**\nPrice: **{price} {SYMBOL}**", color=0x9B59B6)
    await interaction.response.send_message(embed=public_embed)

    data["auctions"] = {}
    save_data(data)

# --- 🤖 SMART PAYMENT SYSTEM ---
import string

def generate_code(length=6):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))

class AdminVerifyView(discord.ui.View):
    def __init__(self, user_id, amount, proof_text):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.amount = amount
        self.proof_text = proof_text

    @discord.ui.button(label="✅ Approve & Add Coins", style=discord.ButtonStyle.green, custom_id="approve_payment")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        new_bal = update_balance(self.user_id, self.amount)
        receipt_id = "RCPT-" + generate_code(8)
        
        for child in self.children:
            child.disabled = True
        
        embed = interaction.message.embeds[0]
        embed.color = 0x00FF00
        embed.add_field(name="Status", value=f"✅ **APPROVED** by {interaction.user.mention}", inline=False)
        embed.add_field(name="Receipt ID", value=f"`{receipt_id}`", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)
        
        try:
            user = await bot.fetch_user(self.user_id)
            dm_embed = discord.Embed(title="✅ Payment Successful!", color=0x2ECC71)
            dm_embed.add_field(name="Amount Added", value=f"+{self.amount} Moon Coins", inline=False)
            dm_embed.add_field(name="New Balance", value=f"{new_bal} MC", inline=False)
            dm_embed.add_field(name="Receipt ID", value=f"`{receipt_id}`", inline=False)
            await user.send(embed=dm_embed)
        except:
            pass

        mod_channel = bot.get_channel(MOD_RECEIPT_CHANNEL_ID)
        if mod_channel:
            log_embed = discord.Embed(title="🧾 New Receipt Generated", color=0x3498db)
            log_embed.add_field(name="User", value=f"<@{self.user_id}>", inline=True)
            log_embed.add_field(name="Proof/Note", value=f"`{self.proof_text}`", inline=True)
            log_embed.add_field(name="Receipt ID", value=f"`{receipt_id}`", inline=True)
            await mod_channel.send(embed=log_embed)

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.red, custom_id="deny_payment")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        embed = interaction.message.embeds[0]
        embed.color = 0xFF0000
        embed.add_field(name="Status", value=f"❌ **DENIED** by {interaction.user.mention}", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)
        try:
            user = await bot.fetch_user(self.user_id)
            await user.send(f"❌ **Payment Failed.** The admin denied your verification. Please open a ticket.")
        except:
            pass

class CryptoTxidModal(discord.ui.Modal, title="Verify Crypto Payment"):
    txid = discord.ui.TextInput(label="Transaction Hash / ID", placeholder="Paste the long string of letters/numbers...", required=True)

    def __init__(self, amount, method):
        super().__init__()
        self.amount = amount
        self.method = method

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"⏳ **Verifying...** Admin notified.", ephemeral=True)
        
        log_channel = bot.get_channel(PAYMENT_LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(title="💸 NEW CRYPTO CLAIM", color=0x9B59B6)
            embed.add_field(name="User", value=f"{interaction.user.mention}", inline=True)
            embed.add_field(name="Amount", value=f"{self.amount} Coins", inline=True)
            embed.add_field(name="Method", value=self.method, inline=True)
            embed.add_field(name="🧾 PROVIDED TXID", value=f"`{self.txid.value}`", inline=False)
            
            view = AdminVerifyView(interaction.user.id, self.amount, self.txid.value)
            await log_channel.send(content="@here", embed=embed, view=view)

class PayConfirmView(discord.ui.View):
    def __init__(self, amount, method, pay_total, note=None):
        super().__init__(timeout=None)
        self.amount = amount
        self.method = method
        self.pay_total = pay_total
        self.note = note

    @discord.ui.button(label="✅ I Have Sent the Money", style=discord.ButtonStyle.blurple)
    async def paid_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.method in ["LTC", "SOL"]:
            await interaction.response.send_modal(CryptoTxidModal(self.amount, self.method))
            return

        button.disabled = True
        button.label = "Processing..."
        await interaction.response.edit_message(view=self)
        
        await interaction.followup.send(f"⏳ **Verifying...** Admin checking for note `{self.note}`.", ephemeral=True)
        
        log_channel = bot.get_channel(PAYMENT_LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(title="💸 NEW PAYPAL CLAIM", color=0x3498db)
            embed.add_field(name="User", value=f"{interaction.user.mention}", inline=True)
            embed.add_field(name="Amount", value=f"{self.amount} Coins", inline=True)
            embed.add_field(name="🛑 LOOK FOR NOTE:", value=f"`{self.note}`", inline=False)
            view = AdminVerifyView(interaction.user.id, self.amount, self.note)
            await log_channel.send(content="@here", embed=embed, view=view)

@bot.tree.command(name="buycoins", description="Buy Moon Coins (PayPal/LTC/SOL)")
@app_commands.describe(amount="How many coins you want", method="Payment Method")
@app_commands.choices(method=[
    app_commands.Choice(name="PayPal 💳", value="PayPal"),
    app_commands.Choice(name="Litecoin (LTC) Ł", value="LTC"),
    app_commands.Choice(name="Solana (SOL) ◎", value="SOL")
])
async def buycoins(interaction: discord.Interaction, amount: int, method: app_commands.Choice[str]):
    if amount < 1:
        await interaction.response.send_message("❌ You must buy at least 1 coin.", ephemeral=True)
        return

    COIN_PRICE = 3.00
    FEE = 0.99
    subtotal = amount * COIN_PRICE
    total = subtotal + FEE

    addresses = {
        "PayPal": "YOUR_PAYPAL@gmail.com", 
        "LTC": "YOUR_LTC_ADDRESS",
        "SOL": "YOUR_SOL_ADDRESS"
    }
    pay_address = addresses.get(method.value, "Ask Admin")
    
    embed = discord.Embed(title=f"💳 Order: {amount} Moon Coins", color=0x2ECC71)
    embed.add_field(name="💰 Amount Due", value=f"**${total:.2f} USD**", inline=False)
    embed.add_field(name="📍 Send To", value=f"`{pay_address}`", inline=False)

    note = None
    if method.value == "PayPal":
        note = "MOON-" + generate_code(4)
        embed.add_field(name="📝 REQUIRED NOTE", value=f"**You MUST include this note:**\n# `{note}`", inline=False)
    else:
        embed.add_field(name="📝 INSTRUCTIONS", value="**After sending, click the button below to paste your TXID.**", inline=False)
    
    view = PayConfirmView(amount, method.value, total, note)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# --- 🎰 GAMES & UTILITY ---

@bot.tree.command(name="roulette", description="Bet your coins! Red/Black/Green")
@app_commands.choices(color=[
    app_commands.Choice(name="Red (2x)", value="red"),
    app_commands.Choice(name="Black (2x)", value="black"),
    app_commands.Choice(name="Green (15x)", value="green")
])
async def roulette(interaction: discord.Interaction, amount: int, color: app_commands.Choice[str]):
    bal = get_balance(interaction.user.id)
    if bal < amount:
        await interaction.response.send_message("❌ Not enough coins!", ephemeral=True)
        return
    if amount < 1:
        await interaction.response.send_message("❌ You must bet at least 1 coin.", ephemeral=True)
        return

    roll = random.randint(0, 36)
    outcome = "green" if roll == 0 else ("red" if roll % 2 == 0 else "black")
    
    update_balance(interaction.user.id, -amount)
    
    embed = discord.Embed(title="🎰 Moon Roulette", description=f"Spinning... You bet on **{color.name}**", color=0x3498db)
    await interaction.response.send_message(embed=embed)
    await asyncio.sleep(2)
    
    winnings = 0
    if outcome == color.value:
        if outcome == "green":
            winnings = amount * 15
        else:
            winnings = amount * 2
        update_balance(interaction.user.id, winnings)
        result_msg = f"✅ BALL LANDED ON **{outcome.upper()}**! You won **{winnings} {SYMBOL}**!"
        color_hex = 0x00ff00
    else:
        result_msg = f"❌ Ball landed on **{outcome.upper()}**. You lost **{amount} {SYMBOL}**."
        color_hex = 0xff0000

    result_embed = discord.Embed(title="🎰 Result", description=result_msg, color=color_hex)
    await interaction.edit_original_response(embed=result_embed)

@bot.tree.command(name="work", description="Work a shift (Once every 12h)")
async def work(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    data = load_data()
    
    if "cooldowns" not in data: data["cooldowns"] = {}
    if user_id not in data["cooldowns"]: data["cooldowns"][user_id] = {}
        
    last_work = data["cooldowns"][user_id].get("last_work", 0)
    current_time = datetime.datetime.now().timestamp()
    
    if current_time - last_work < 43200:
        time_left = 43200 - (current_time - last_work)
        hours = int(time_left // 3600)
        minutes = int((time_left % 3600) // 60)
        await interaction.response.send_message(f"⏳ **You are too tired!** Come back in **{hours}h {minutes}m**.", ephemeral=True)
        return

    earnings = 0.000007
    update_balance(interaction.user.id, earnings)
    
    data = load_data() 
    if "cooldowns" not in data: data["cooldowns"] = {}
    if user_id not in data["cooldowns"]: data["cooldowns"][user_id] = {}
    
    data["cooldowns"][user_id]["last_work"] = current_time
    save_data(data)
    
    jobs = ["You swept the floor.", "You organized receipts.", "You dusted shelves.", "You held the door open."]
    job_text = random.choice(jobs)
    
    embed = discord.Embed(title="🔨 Shift Complete", description=f"{job_text}\n\n**You earned:** {earnings:.6f} {SYMBOL}", color=0x3498db)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="giveaway", description="ADMIN: Start a giveaway")
@app_commands.describe(prize="What are we giving away?", duration="Duration (e.g. 60s, 5m, 1h)", winners="How many winners?")
async def giveaway(interaction: discord.Interaction, prize: str, duration: str, winners: int):
    if interaction.user.id not in OWNER_IDS:
        await interaction.response.send_message("❌ Only Moon can host giveaways.", ephemeral=True)
        return

    seconds = 0
    if duration.endswith("s"):
        seconds = int(duration[:-1])
    elif duration.endswith("m"):
        seconds = int(duration[:-1]) * 60
    elif duration.endswith("h"):
        seconds = int(duration[:-1]) * 3600
    else:
        await interaction.response.send_message("❌ Invalid format! Use 30s, 5m, or 1h.", ephemeral=True)
        return

    end_time = int((datetime.datetime.now() + datetime.timedelta(seconds=seconds)).timestamp())
    embed = discord.Embed(title="🎉 GIVEAWAY TIME! 🎉", description=f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** <t:{end_time}:R>", color=0xFF00FF)
    embed.set_footer(text="React with 🎉 to enter!")
    
    await interaction.response.send_message(embed=embed)
    message = await interaction.original_response()
    await message.add_reaction("🎉")

    await asyncio.sleep(seconds)

    try:
        message = await interaction.channel.fetch_message(message.id)
    except:
        await interaction.followup.send(f"⚠️ Error: Giveaway message for **{prize}** was deleted.")
        return

    users = []
    reaction = discord.utils.get(message.reactions, emoji="🎉")
    if reaction:
        async for user in reaction.users():
            if not user.bot:
                users.append(user)

    if len(users) < winners:
        await interaction.followup.send(f"⚠️ Not enough entrants for **{prize}**.")
        return

    chosen = random.sample(users, winners)
    winner_text = ", ".join([u.mention for u in chosen])
    await interaction.followup.send(f"🎉 **CONGRATULATIONS** {winner_text}! You won **{prize}**!")

@bot.tree.command(name="close", description="Close and delete the current ticket")
async def close(interaction: discord.Interaction):
    allowed_names = ["ticket", "auction", "winner", "support", "order"]
    if not any(name in interaction.channel.name.lower() for name in allowed_names):
        await interaction.response.send_message("❌ **Safety Block:** You can only use this in Ticket or Auction channels.", ephemeral=True)
        return

    await interaction.response.send_message("🔒 **Ticket Closing...**\nDeleting channel in 5 seconds.", ephemeral=False)
    await asyncio.sleep(5)
    await interaction.channel.delete()

# --- 👮 SHIFT MANAGEMENT SYSTEM (Updated) ---
# ⚠️ CONFIGURATION
CH_ON_DUTY  = 1467278649265885297
CH_OFF_DUTY = 1467278717611937976
CH_BREAK    = 1467278775753506987
ROLE_ON_DUTY = 1467284055593717966
ROLE_BREAK   = 1467284393499295890

@bot.tree.command(name="workhours", description="Check how many hours a staff member has worked")
async def workhours(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    user_id = str(user.id)
    
    total_seconds = 0
    if "shifts" in data and user_id in data["shifts"]:
        total_seconds = data["shifts"][user_id].get("total_seconds", 0)
    
    current_status = "🔴 Off Duty"
    if "shifts" in data and user_id in data["shifts"] and data["shifts"][user_id].get("start_time"):
        start_time = data["shifts"][user_id]["start_time"]
        current_duration = int(datetime.datetime.now().timestamp() - start_time)
        current_status = f"🟢 Currently Active ({current_duration // 60}m)"

    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)

    embed = discord.Embed(title=f"👮 Staff Report: {user.display_name}", color=0x3498db)
    embed.add_field(name="Total Time Worked", value=f"**{hours} hours, {minutes} minutes**", inline=False)
    embed.add_field(name="Current Status", value=current_status, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    user = message.author
    user_id = str(user.id)
    
    # 🟢 ON DUTY LOGIC
    if message.channel.id == CH_ON_DUTY:
        role_on = message.guild.get_role(ROLE_ON_DUTY)
        role_break = message.guild.get_role(ROLE_BREAK)
        if role_on and role_break:
            await user.add_roles(role_on)
            await user.remove_roles(role_break)
            
            data = load_data()
            if "shifts" not in data: data["shifts"] = {}
            if user_id not in data["shifts"]: data["shifts"][user_id] = {"total_seconds": 0, "start_time": None}
            
            if data["shifts"][user_id]["start_time"] is None:
                data["shifts"][user_id]["start_time"] = datetime.datetime.now().timestamp()
                save_data(data)
                
            await message.add_reaction("✅")
            await message.add_reaction("👮")

    # 🔴 OFF DUTY LOGIC
    elif message.channel.id == CH_OFF_DUTY:
        role_on = message.guild.get_role(ROLE_ON_DUTY)
        role_break = message.guild.get_role(ROLE_BREAK)
        if role_on and role_break:
            await user.remove_roles(role_on)
            await user.remove_roles(role_break)
            
            data = load_data()
            if "shifts" not in data: data["shifts"] = {}
            start_time = data["shifts"].get(user_id, {}).get("start_time")
            
            if start_time:
                end_time = datetime.datetime.now().timestamp()
                duration_seconds = end_time - start_time
                
                data["shifts"][user_id]["total_seconds"] += duration_seconds
                data["shifts"][user_id]["start_time"] = None
                save_data(data)
                
                hours = int(duration_seconds // 3600)
                minutes = int((duration_seconds % 3600) // 60)
                total_hours = int(data['shifts'][user_id]['total_seconds'] // 3600)
                
                embed = discord.Embed(title="👋 Shift Ended", description=f"{user.mention} has clocked out.", color=0xFF0000)
                embed.add_field(name="Session Duration", value=f"{hours}h {minutes}m", inline=True)
                embed.add_field(name="Total Hours", value=f"{total_hours}h", inline=True)
                await message.channel.send(embed=embed)
            else:
                embed = discord.Embed(title="👋 Shift Ended", description=f"{user.mention} clocked out manually.\n*(No active timer found)*", color=0xFF0000)
                await message.channel.send(embed=embed)

            await message.add_reaction("😴")

    # 🥪 BREAK LOGIC
    elif message.channel.id == CH_BREAK:
        role_on = message.guild.get_role(ROLE_ON_DUTY)
        role_break = message.guild.get_role(ROLE_BREAK)
        if role_on and role_break:
            await user.remove_roles(role_on)
            await user.add_roles(role_break)
            
            data = load_data()
            if "shifts" not in data: data["shifts"] = {}
            start_time = data["shifts"].get(user_id, {}).get("start_time")
            
            if start_time:
                end_time = datetime.datetime.now().timestamp()
                duration_seconds = end_time - start_time
                
                data["shifts"][user_id]["total_seconds"] += duration_seconds
                data["shifts"][user_id]["start_time"] = None
                save_data(data)
                
                hours = int(duration_seconds // 3600)
                minutes = int((duration_seconds % 3600) // 60)
                await message.channel.send(f"🥪 **Shift Paused.** You worked **{hours}h {minutes}m** before this break.")
            await message.add_reaction("🥪")

# --- 🎰 SLOTS & WEEKLY ---

@bot.tree.command(name="slots", description="Spin the slot machine! Win big!")
@app_commands.describe(bet="Amount to bet")
async def slots(interaction: discord.Interaction, bet: int):
    bal = get_balance(interaction.user.id)
    if bal < bet:
        await interaction.response.send_message("❌ You are too poor for this bet!", ephemeral=True)
        return
    if bet < 5:
        await interaction.response.send_message("❌ Minimum bet is 5 Moon Coins.", ephemeral=True)
        return

    update_balance(interaction.user.id, -bet)

    emojis = ["🍒", "🍋", "🍇", "💎", "7️⃣", "🍊", "💩", "👻", "🤖", "🌙"]
    a = random.choice(emojis)
    b = random.choice(emojis)
    c = random.choice(emojis)

    embed = discord.Embed(title="🎰 SLOTS", description="... spinning ...", color=0x9B59B6)
    await interaction.response.send_message(embed=embed)
    await asyncio.sleep(1.5)

    winnings = 0
    if a == b == c:
        winnings = bet * 6.7
        result = "JACKPOT!! 🚨 WOOHOO!"
        color = 0xFFD700
    elif a == b or b == c or a == c:
        winnings = bet * 1.2
        result = "Nice! Small win."
        color = 0x00FF00
    else:
        result = "You lost, try again!"
        color = 0xFF0000

    if winnings > 0:
        update_balance(interaction.user.id, winnings)

    embed = discord.Embed(title="🎰 SLOTS RESULT", description=f"**| {a} | {b} | {c} |**\n\n{result}", color=color)
    if winnings > 0:
        embed.add_field(name="Winnings", value=f"{winnings} {SYMBOL}", inline=False)
    
    await interaction.edit_original_response(embed=embed)

@bot.tree.command(name="weekly", description="Claim your weekly reward (Once every 7 Days)")
async def weekly(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    data = load_data()
    
    if "cooldowns" not in data: data["cooldowns"] = {}
    if user_id not in data["cooldowns"]: data["cooldowns"][user_id] = {}
        
    last_weekly = data["cooldowns"][user_id].get("last_weekly", 0)
    current_time = datetime.datetime.now().timestamp()
    
    if current_time - last_weekly < 604800:
        time_left = 604800 - (current_time - last_weekly)
        days = int(time_left // 86400)
        hours = int((time_left % 86400) // 3600)
        minutes = int((time_left % 3600) // 60)
        await interaction.response.send_message(f"⏳ **Already claimed!** Come back in **{days}d {hours}h {minutes}m**.", ephemeral=True)
        return

    earnings = 0.00000981
    update_balance(interaction.user.id, earnings)

    data = load_data()
    if "cooldowns" not in data: data["cooldowns"] = {}
    if user_id not in data["cooldowns"]: data["cooldowns"][user_id] = {}
    
    data["cooldowns"][user_id]["last_weekly"] = current_time
    save_data(data)

    embed = discord.Embed(title="📅 Weekly Reward", description=f"Here is your allowance.\n\n**Received:** {earnings:.8f} {SYMBOL}", color=0x2ECC71)
    await interaction.response.send_message(embed=embed)

# --- 🌙 ADMIN EXTRAS ---

@bot.tree.command(name="night_market", description="ADMIN: Open/Close the Night Market")
async def night_market(interaction: discord.Interaction, status: str):
    if interaction.user.id not in OWNER_IDS: return
    if status.lower() == "open":
        embed = discord.Embed(title="🌙 THE NIGHT MARKET IS OPEN 🌙", description="Prices have dropped by 30%! Buy now before the sun rises.", color=0x9B59B6)
        await interaction.response.send_message(content="@everyone", embed=embed)
    else:
        await interaction.response.send_message("🔒 **Night Market is now CLOSED.** Prices returned to normal.")

@bot.tree.command(name="drop_steal", description="ADMIN: Drop a Daily Steal")
async def drop_steal(interaction: discord.Interaction, item: str, price: int, stock: int):
    if interaction.user.id not in OWNER_IDS: return
    embed = discord.Embed(title="⚡ DAILY STEAL ALERT ⚡", color=0xFF0000)
    embed.add_field(name="Item", value=item, inline=False)
    embed.add_field(name="INSANE PRICE", value=f"~~{price*2}~~ ⮕ **{price} {SYMBOL}**", inline=False)
    embed.add_field(name="Stock", value=f"{stock} Left!", inline=False)
    embed.set_footer(text="First to open a ticket gets it!")
    await interaction.response.send_message(content="@here", embed=embed)

@bot.tree.command(name="stock_rain", description="ADMIN: Simulate a stock drop event")
async def stock_rain(interaction: discord.Interaction):
    if interaction.user.id not in OWNER_IDS: return
    await interaction.response.send_message("🌧️ **INITIATING STOCK RAIN...**")
    channel = interaction.channel
    messages = ["💧 Dropping 1x Netflix Account...", "💧 Dropping 1x Disney+ Code...", "💧 Dropping 500 Moon Coins...", "⛈️ **HEAVY RAIN INCOMING!** Check #free-drops NOW!"]
    for msg in messages:
        await channel.send(msg)
        await asyncio.sleep(2)

@bot.tree.command(name="owner_spin", description="ADMIN: Pick a random winner from a list")
async def owner_spin(interaction: discord.Interaction, names: str):
    if interaction.user.id not in OWNER_IDS: return
    participant_list = names.replace(",", " ").split()
    winner = random.choice(participant_list)
    await interaction.response.send_message(f"🎲 **Spinning the wheel...**")
    await asyncio.sleep(2)
    await interaction.followup.send(f"🎉 The winner is: **{winner}**!")

@bot.tree.command(name="admincmds", description="ADMIN: View secret commands")
async def admincmds(interaction: discord.Interaction):
    if interaction.user.id not in OWNER_IDS and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You are not an admin.", ephemeral=True)
        return
    embed = discord.Embed(title="🔒 Admin Control Panel", color=0xFF0000)
    embed.add_field(name="💰 Money Printer", value="`/add_coins @user [amount]` - Give money\n`/remove_coins @user [amount]` - Take money", inline=False)
    embed.add_field(name="📢 Events", value="`/start_auction` - Start auction\n`/giveaway` - Start giveaway\n`/night_market` - Toggle sale", inline=False)
    embed.add_field(name="⚡ Drops", value="`/drop_steal` - Flash sale\n`/stock_rain` - Fake drop event", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="cmds", description="View all available commands")
async def cmds(interaction: discord.Interaction):
    embed = discord.Embed(title="📜 Moon's Command List", color=0x9B59B6)
    embed.add_field(name="💸 Economy", value="`/balance`, `/pay`, `/buycoins`, `/leaderboard`", inline=False)
    embed.add_field(name="🎰 Gambling", value="`/slots`, `/roulette`, `/bid`", inline=False)
    embed.add_field(name="🛠️ Grinding", value="`/work`, `/weekly`", inline=False)
    embed.add_field(name="👮 Staff", value="`/workhours`", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="say", description="ADMIN: Make the bot say something")
async def say(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
    if interaction.user.id not in OWNER_IDS: return
    if channel is None: channel = interaction.channel
    await channel.send(message)
    await interaction.response.send_message("✅ Message sent!", ephemeral=True)


bot.run(TOKEN)
