import logging
import json
import sqlite3
import requests
import random
import string
import re
import traceback
import os
from datetime import datetime, timedelta
import asyncio
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

# ===== CONFIGURATION =====
BOT_TOKEN = "8649625047:AAEBQTSHmcFdIJR61TtFbmEPrfHg3JEBVvY"
ADMIN_ID = 5464634575
ERROR_CHANNEL_ID = -1003863829311
HISTORY_CHANNEL_ID = -1003863829311

# Updated API Configuration
APIS = [
    {
        "name": "Primary API",
        "url": "https://demon.taitanx.workers.dev/?mobile={number}",
        "method": "GET"
    },
    {
        "name": "Backup API 1", 
        "url": "https://mynkapi.amit1100941.workers.dev/%20?mobile={number}&key=mynk01",
        "method": "GET"
    },
    {
        "name": "Backup API 2",
        "url": "https://seller-ki-mkc.taitanx.workers.dev/?mobile={number}",
        "method": "GET"
    },
    {
        "name": "Backup API 3",
        "url": "https://veerulookup.onrender.com/search_phone?number={number}",
        "method": "GET"
    }
]

# Credit System Constants
LOOKUP_COST = 1
REFERRAL_BONUS = 4
DEFAULT_CREDITS = 4

# Channel Configuration - ONLY REQUIRED CHANNELS
REQUIRED_CHANNELS = [
    {'name': 'Darkeyy💸', 'username': '@Darkeyy0', 'url': 'https://t.me/+P8G7AqQFn1ZiNjk1'}    
]

# Use only required channels for verification
ALL_CHANNELS = REQUIRED_CHANNELS

# Redeem Code Configuration
REDEEM_CODE_LENGTH = 8
REDEEM_CODE_VALIDITY_DAYS = 30

# Personality traits for random generation
PERSONALITY_TRAITS = [
    "Friendly and outgoing personality",
    "Reserved and introverted nature",
    "Ambitious and goal-oriented mindset",
    "Creative and artistic temperament",
    "Analytical and logical thinker",
    "Adventurous and risk-taking spirit",
    "Caring and empathetic individual",
    "Professional and career-focused",
    "Social media enthusiast",
    "Tech-savvy and gadget lover",
    "Family-oriented person",
    "Travel enthusiast and explorer"
]

# Disable httpx logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

def init_db():
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    # Users table with referral system
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            credits INTEGER DEFAULT 4,
            joined_channels INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT 0,
            referral_count INTEGER DEFAULT 0,
            referral_bonus_earned INTEGER DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Lookup history table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lookups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            number TEXT,
            result TEXT,
            lookup_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Redeem codes table with max_uses
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS redeem_codes (
            code TEXT PRIMARY KEY,
            credits INTEGER,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            used_by INTEGER DEFAULT NULL,
            used_at TIMESTAMP DEFAULT NULL,
            max_uses INTEGER DEFAULT 1,
            use_count INTEGER DEFAULT 0
        )
    ''')
    
    # Credit history table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS credit_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            reason TEXT,
            admin_id INTEGER DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# Initialize database
init_db()

# Enable logging but suppress httpx logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ===== COMPLETE DATABASE FUNCTIONS =====
def get_user(user_id):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def add_user(user_id, username, first_name, referred_by=0):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    existing_user = cursor.fetchone()
    
    if not existing_user:
        # FIXED: Give DEFAULT_CREDITS to new users - PROPERLY
        cursor.execute('''
            INSERT INTO users (user_id, username, first_name, credits, referred_by) 
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, DEFAULT_CREDITS, referred_by))
        
        # Add credit history entry for initial credits
        cursor.execute('''
            INSERT INTO credit_history (user_id, amount, reason, admin_id)
            VALUES (?, ?, ?, ?)
        ''', (user_id, DEFAULT_CREDITS, "Initial signup credits", None))
        
        # Handle referral bonus if applicable
        if referred_by and referred_by != user_id:
            cursor.execute('''
                UPDATE users 
                SET referral_count = referral_count + 1,
                    referral_bonus_earned = referral_bonus_earned + ?,
                    credits = credits + ?
                WHERE user_id = ?
            ''', (REFERRAL_BONUS, REFERRAL_BONUS, referred_by))
            
            cursor.execute('''
                INSERT INTO credit_history (user_id, amount, reason, admin_id)
                VALUES (?, ?, ?, ?)
            ''', (referred_by, REFERRAL_BONUS, f"Referral bonus from user {user_id}", None))
        
        logging.info(f"✅ NEW USER ADDED: {user_id} with {DEFAULT_CREDITS} credits")
    else:
        # For existing users, just update username and name if needed
        cursor.execute('''
            UPDATE users SET username = ?, first_name = ? WHERE user_id = ?
        ''', (username, first_name, user_id))
        logging.info(f"🔄 EXISTING USER UPDATED: {user_id}")
    
    conn.commit()
    conn.close()

def ensure_user(user_id, username=None, first_name=None, referred_by=0):
    """MAIN FIX: Ensure user exists with proper credits like main.py"""
    user = get_user(user_id)
    
    if not user:
        # User doesn't exist, create with default credits
        add_user(user_id, username or "Unknown", first_name or "User", referred_by)
        logging.info(f"🔧 ENSURED USER: Created new user {user_id} with {DEFAULT_CREDITS} credits")
        return True
    else:
        # User exists, check if credits need to be set to default
        current_credits = user[3]  # credits column
        
        # If user has 0 credits or credits are None, set to default
        if current_credits == 0 or current_credits is None:
            conn = sqlite3.connect('data.db')
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET credits = ? WHERE user_id = ?', (DEFAULT_CREDITS, user_id))
            
            # Add to credit history
            cursor.execute('''
                INSERT INTO credit_history (user_id, amount, reason, admin_id)
                VALUES (?, ?, ?, ?)
            ''', (user_id, DEFAULT_CREDITS, "Credit reset to default", None))
            
            conn.commit()
            conn.close()
            logging.info(f"🔧 ENSURED USER: Reset credits for {user_id} to {DEFAULT_CREDITS}")
        
        # Update username and first_name if provided
        if username or first_name:
            conn = sqlite3.connect('data.db')
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET username = COALESCE(?, username), first_name = COALESCE(?, first_name) 
                WHERE user_id = ?
            ''', (username, first_name, user_id))
            conn.commit()
            conn.close()
        
        return True

def update_credits(user_id, amount, reason="System", admin_id=None):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    # Ensure user exists first
    ensure_user(user_id)
    
    cursor.execute('UPDATE users SET credits = credits + ? WHERE user_id = ?', (amount, user_id))
    
    cursor.execute('''
        INSERT INTO credit_history (user_id, amount, reason, admin_id)
        VALUES (?, ?, ?, ?)
    ''', (user_id, amount, reason, admin_id))
    
    conn.commit()
    conn.close()

def set_credits(user_id, amount, reason="System", admin_id=None):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    # Ensure user exists first
    ensure_user(user_id)
    
    cursor.execute('SELECT credits FROM users WHERE user_id = ?', (user_id,))
    current = cursor.fetchone()
    current_credits = current[0] if current else 0
    difference = amount - current_credits
    
    cursor.execute('UPDATE users SET credits = ? WHERE user_id = ?', (amount, user_id))
    
    if difference != 0:
        cursor.execute('''
            INSERT INTO credit_history (user_id, amount, reason, admin_id)
            VALUES (?, ?, ?, ?)
        ''', (user_id, difference, reason, admin_id))
    
    conn.commit()
    conn.close()

def set_joined_channels(user_id, status):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET joined_channels = ? WHERE user_id = ?', (status, user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users')
    users = cursor.fetchall()
    conn.close()
    return users

def ban_user(user_id):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET is_banned = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def unban_user(user_id):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET is_banned = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_referral_stats(user_id):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT referral_count, referral_bonus_earned FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result if result else (0, 0)

def get_referred_by(user_id):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT referred_by FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def get_credit_history(user_id, limit=10):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT amount, reason, created_at FROM credit_history 
        WHERE user_id = ? ORDER BY created_at DESC LIMIT ?
    ''', (user_id, limit))
    history = cursor.fetchall()
    conn.close()
    return history

# Channel verification functions
def check_user_joined_channels(user_id):
    """Check if user has joined all required channels"""
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT joined_channels FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return result[0] == 1
    return False

def update_channel_status(user_id, status):
    """Update user's channel join status"""
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET joined_channels = ? WHERE user_id = ?', (status, user_id))
    conn.commit()
    conn.close()

# Redeem Code Functions
def generate_redeem_code(length=REDEEM_CODE_LENGTH):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def create_redeem_code(credits, created_by, max_uses=1, validity_days=REDEEM_CODE_VALIDITY_DAYS):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    code = generate_redeem_code()
    expires_at = datetime.now() + timedelta(days=validity_days)
    
    cursor.execute('''
        INSERT INTO redeem_codes (code, credits, created_by, expires_at, max_uses)
        VALUES (?, ?, ?, ?, ?)
    ''', (code, credits, created_by, expires_at, max_uses))
    
    conn.commit()
    conn.close()
    return code

def create_custom_redeem_code(code, credits, max_uses=1, validity_days=REDEEM_CODE_VALIDITY_DAYS):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    expires_at = datetime.now() + timedelta(days=validity_days)
    
    cursor.execute('''
        INSERT INTO redeem_codes (code, credits, created_by, expires_at, max_uses)
        VALUES (?, ?, ?, ?, ?)
    ''', (code.upper(), credits, ADMIN_ID, expires_at, max_uses))
    
    conn.commit()
    conn.close()
    return code.upper()

def revoke_redeem_code(code):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM redeem_codes WHERE code = ?', (code.upper(),))
    affected = cursor.rowcount
    
    conn.commit()
    conn.close()
    return affected > 0

def use_redeem_code(code, user_id):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT credits, max_uses, use_count FROM redeem_codes 
        WHERE code = ? AND (used_by IS NULL OR used_by != ?) 
        AND expires_at > datetime('now') AND use_count < max_uses
    ''', (code, user_id))
    
    result = cursor.fetchone()
    
    if result:
        credits, max_uses, use_count = result
        
        cursor.execute('''
            UPDATE redeem_codes 
            SET use_count = use_count + 1,
                used_by = ?, 
                used_at = datetime('now') 
            WHERE code = ?
        ''', (user_id, code))
        
        # FIXED: Use ensure_user before updating credits
        ensure_user(user_id)
        cursor.execute('UPDATE users SET credits = credits + ? WHERE user_id = ?', (credits, user_id))
        
        cursor.execute('''
            INSERT INTO credit_history (user_id, amount, reason, admin_id)
            VALUES (?, ?, ?, ?)
        ''', (user_id, credits, f"Redeem code: {code}", None))
        
        conn.commit()
        conn.close()
        return credits
    else:
        conn.close()
        return None

def get_redeem_codes(created_by=None):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    if created_by:
        cursor.execute('''
            SELECT code, credits, created_at, expires_at, used_by, max_uses, use_count 
            FROM redeem_codes WHERE created_by = ? ORDER BY created_at DESC
        ''', (created_by,))
    else:
        cursor.execute('''
            SELECT code, credits, created_by, created_at, expires_at, used_by, max_uses, use_count 
            FROM redeem_codes ORDER BY created_at DESC
        ''')
    
    codes = cursor.fetchall()
    conn.close()
    return codes

def reset_all_credits():
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    users = get_all_users()
    reset_count = 0
    
    for user in users:
        user_id = user[0]
        current_credits = user[3]
        
        # Only reset if credits are different from default
        if current_credits != DEFAULT_CREDITS:
            difference = DEFAULT_CREDITS - current_credits
            
            cursor.execute('UPDATE users SET credits = ? WHERE user_id = ?', (DEFAULT_CREDITS, user_id))
            
            cursor.execute('''
                INSERT INTO credit_history (user_id, amount, reason, admin_id)
                VALUES (?, ?, ?, ?)
            ''', (user_id, difference, "Reset all credits", ADMIN_ID))
            
            reset_count += 1
    
    conn.commit()
    conn.close()
    return reset_count

def reset_user_credits(user_id):
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    # Ensure user exists first
    ensure_user(user_id)
    
    cursor.execute('SELECT credits FROM users WHERE user_id = ?', (user_id,))
    current = cursor.fetchone()
    current_credits = current[0] if current else 0
    difference = DEFAULT_CREDITS - current_credits
    
    cursor.execute('UPDATE users SET credits = ? WHERE user_id = ?', (DEFAULT_CREDITS, user_id))
    
    if difference != 0:
        cursor.execute('''
            INSERT INTO credit_history (user_id, amount, reason, admin_id)
            VALUES (?, ?, ?, ?)
        ''', (user_id, difference, "User credit reset", ADMIN_ID))
    
    conn.commit()
    conn.close()
    return difference

async def check_channel_membership(user_id, bot):
    """Check if user is member of all required channels"""
    try:
        for channel in ALL_CHANNELS:
            try:
                # Handle different channel formats properly
                if 'id' in channel:
                    chat_id = channel['id']
                    logging.info(f"Checking channel {channel['name']} (ID: {chat_id}) for user {user_id}")
                elif 'username' in channel:
                    username = channel['username'].replace('@', '')  # Remove @ if present
                    chat_id = f"@{username}"  # Add @ back for proper format
                    logging.info(f"Checking channel {channel['name']} (Username: {username}) for user {user_id}")
                else:
                    logging.error(f"Channel {channel['name']} has no id or username")
                    return False
                
                # Get chat member with proper error handling
                try:
                    member = await bot.get_chat_member(chat_id, user_id)
                    logging.info(f"User {user_id} status in {channel['name']}: {member.status}")
                    
                    if member.status in ['left', 'kicked', 'banned']:
                        logging.info(f"User {user_id} is NOT member of {channel['name']} (status: {member.status})")
                        return False
                        
                except Exception as e:
                    logging.error(f"Error getting chat member for {channel['name']}: {e}")
                    # For private channels, if we can't check, assume user needs to join
                    return False
                    
            except Exception as e:
                logging.error(f"Error checking channel {channel['name']} for user {user_id}: {e}")
                return False
                
        logging.info(f"✅ User {user_id} is member of all channels")
        return True
        
    except Exception as e:
        logging.error(f"Error in check_channel_membership: {e}")
        return False

async def verify_user_channels(update, context, user_id):
    """Central function to verify user has joined channels"""
    try:
        # First check database cache
        has_joined_db = check_user_joined_channels(user_id)
        
        if has_joined_db:
            logging.info(f"User {user_id} already verified in database")
            return True
        
        # If not in database cache, do actual check
        logging.info(f"Checking channel membership for user {user_id}")
        is_member = await check_channel_membership(user_id, context.bot)
        
        if is_member:
            # Update database cache
            update_channel_status(user_id, 1)
            logging.info(f"User {user_id} verified and database updated")
            return True
        else:
            # Send verification message
            logging.info(f"User {user_id} needs to join channels, sending verification message")
            await send_channel_verification_message(update, context, user_id)
            return False
    except Exception as e:
            logging.error(f"Error in verify_user_channels: {e}")
        return False

async def send_channel_verification_message(update, context, user_id):
    """Send channel join verification message"""
    try:
        channel_text = "🔰 *CHANNEL MEMBERSHIP REQUIRED*\n\n"
        channel_text += "To access all features of this bot, you need to join our official channels.\n\n"
        
        channel_text += "*📢 Required Channels:*\n"
        for i, channel in enumerate(REQUIRED_CHANNELS, 1):
            channel_text += f"{i}. {channel['name']}\n"
        
        channel_text += "\n📋 *Instructions:*\n"
        channel_text += "1. Click each channel button below to join\n"
        channel_text += "2. Join ALL channels\n"
        channel_text += "3. Return here and click verify\n"
        channel_text += "4. Get instant access to bot features\n\n"
        channel_text += "🔒 *Privacy Note:* We only verify membership, no personal data is stored."
        
        # Create inline keyboard with channel buttons
        keyboard = []
        
        # Add channel buttons
        for channel in REQUIRED_CHANNELS:
            keyboard.append([InlineKeyboardButton(f"🔗 Join {channel['name']}", url=channel['url'])])
        
        # Add verify button
        keyboard.append([InlineKeyboardButton("✅ VERIFY MEMBERSHIP", callback_data="verify_channels")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.message:
            await update.message.reply_text(channel_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await context.bot.send_message(chat_id=user_id, text=channel_text, reply_markup=reply_markup, parse_mode='Markdown')
        
        logging.info(f"Sent channel verification message to user {user_id}")
    except Exception as e:
        logging.error(f"Error sending verification message: {e}")

async def send_main_menu(update, context, user_id):
    """Send the main menu/dashboard"""
    # FIXED: Use ensure_user to make sure user exists with proper credits
    ensure_user(user_id)
    user_data = get_user(user_id)
    credits = user_data[3] if user_data else 0
    
    menu_text = f"""
🤖 Welcome to Ghost OSINT Bot

💰 Your Credits: {credits}

🔍 Features:
• Phone number lookup with detailed OSINT
• Multiple data sources for accurate results  
• Downloadable reports in TXT format
• Referral system to earn more credits

📊 What you get in reports:
• Personal details & Aadhaar data
• Network & carrier information
• Location tracking data
• Device identifiers
• Personality analysis

💡 Each lookup costs {LOOKUP_COST} credit
    """
    
    keyboard = [
        [{"text": "📱 Phone Info"}, {"text": "💰 My Credits"}],
        [{"text": "🔗 My Referral"}, {"text": "🎁 Redeem Code"}],
        [{"text": "❓ Help"}, {"text": "🧑‍💻 Support"}]
    ]
    reply_markup = {"keyboard": keyboard, "resize_keyboard": True}
    
    if update.message:
        await update.message.reply_text(menu_text, reply_markup=reply_markup)
    else:
        await context.bot.send_message(chat_id=user_id, text=menu_text, reply_markup=reply_markup)

# ===== ENHANCED PHONE LOOKUP WITH UPDATED APIS =====
def phone_lookup(number: str):
    number = re.sub(r'\D', '', number)
    
    if len(number) < 10:
        return {"error": "❌ Invalid phone number. Please enter a valid 10-digit phone number."}
    
    if not number.isdigit():
        return {"error": "❌ Phone number should contain only digits."}
    
    if number.startswith('+91'):
        number = number[3:]
    elif number.startswith('91') and len(number) == 12:
        number = number[2:]
    
    if len(number) != 10:
        return {"error": "❌ Please enter a valid 10-digit phone number."}
    
    if not number.startswith(('6', '7', '8', '9')):
        return {"error": "❌ Invalid mobile number format. Indian numbers usually start with 6,7,8,9."}
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    for api in APIS:
        try:
            api_name = api["name"]
            url = api["url"].format(number=number)
            
            logging.info(f"[PHONE] Trying {api_name} for {number}")
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            logging.info(f"[PHONE] {api_name} raw response: {data}")
            
            processed_data = process_api_response(api_name, data, number)
            
            if processed_data and processed_data.get("name") not in ["N/A", "Unknown", None, ""]:
                logging.info(f"[PHONE] Success with {api_name} for {number}: {processed_data}")
                return processed_data
            else:
                logging.warning(f"[PHONE] {api_name} returned no valid data for {number}")
                continue
                
        except requests.exceptions.Timeout:
            logging.warning(f"[PHONE] {api_name} timeout for {number}")
            continue
        except requests.exceptions.RequestException as e:
            logging.warning(f"[PHONE] {api_name} error: {e}")
            continue
        except Exception as e:
            logging.error(f"[PHONE] {api_name} unexpected error: {e}")
            continue
    
    return {"error": "⚠️ No data found for this number across all sources"}

def process_api_response(api_name, data, number):
    """Process different API response formats with exact field mapping"""
    try:
        # Common format for all new APIs
        if data.get("data") and isinstance(data["data"], list) and len(data["data"]) > 0:
            result = data["data"][0]
            return {
                "name": result.get("name", "N/A"),
                "father_name": result.get("fname", "N/A"),
                "address": result.get("address", "N/A"),
                "circle": result.get("circle", "Unknown"),
                "aadhaar_id": result.get("id", "N/A"),
                "mobile": number,
                "alternate_mobile": result.get("alt", "N/A"),
                "status": "success"
            }
        
        return None
        
    except Exception as e:
        logging.error(f"Error processing {api_name} response: {e}")
        return None

# ===== REPORT GENERATION FUNCTIONS =====
def generate_imei():
    base_imei = ''.join([str(random.randint(0, 9)) for _ in range(15)])
    positions = random.sample(range(15), 4)
    imei_list = list(base_imei)
    for pos in positions:
        imei_list[pos] = '*'
    return ''.join(imei_list)

def generate_ip():
    return f"192.168.{random.randint(1, 255)}.{random.randint(1, 255)}"

def generate_mac():
    return ":".join([f"{random.randint(0x00, 0xff):02x}" for _ in range(6)])

def extract_location_info(address):
    if address == "N/A" or address == "***" or not address:
        return "N/A", "N/A", "N/A", "N/A", "N/A"
    
    try:
        address = address.strip()
        parts = re.split(r'[!,\n]', address)
        parts = [part.strip() for part in parts if part.strip()]
        parts = [part for part in parts if part]
        
        if len(parts) >= 3:
            state = "N/A"
            for part in reversed(parts):
                if part.upper() in ['BIHAR', 'UP', 'UTTAR PRADESH', 'DELHI', 'MAHARASHTRA', 'WEST BENGAL', 'TAMIL NADU', 'KARNATAKA', 'KERALA']:
                    state = part
                    break
            if state == "N/A" and len(parts) > 1:
                state = parts[-1]
            
            city = "N/A"
            for part in parts:
                if any(keyword in part.upper() for keyword in ['MADHUBANI', 'PATNA', 'DELHI', 'MUMBAI', 'KOLKATA', 'CHENNAI', 'BANGALORE']):
                    city = part
                    break
            if city == "N/A" and len(parts) > 2:
                city = parts[-2]
            
            hometown = parts[0] if parts else "N/A"
            
            mobile_location = hometown if hometown != "N/A" else "Unknown"
            tower_location = city if city != "N/A" else hometown if hometown != "N/A" else "Unknown"
            
            state = state.replace('!', '').strip()
            city = city.replace('!', '').strip()
            hometown = hometown.replace('!', '').strip()
     return state, city, hometown, f"Near {mobile_location}", f"Tower near {tower_location}"
        
        else:
            state = parts[-1] if parts else "N/A"
            city = parts[-2] if len(parts) >= 2 else "N/A"
            hometown = parts[0] if parts else "N/A"
            mobile_location = f"Near {hometown}" if hometown != "N/A" else "N/A"
            tower_location = f"Tower near {city}" if city != "N/A" else "N/A"
            
            return state, city, hometown, mobile_location, tower_location
            
    except Exception as e:
        logging.error(f"Error extracting location info: {e}")
        return "N/A", "N/A", "N/A", "N/A", "N/A"

def generate_personality(name):
    if not name or name == 'N/A':
        return random.choice(PERSONALITY_TRAITS)
    
    female_indicators = ['priya', 'sita', 'rani', 'laxmi', 'kumari', 'devi', 'shanti', 'anjali']
    male_indicators = ['kumar', 'singh', 'raj', 'ram', 'lal', 'prasad', 'khan']
    
    name_lower = name.lower()
    gender = 'she' if any(indicator in name_lower for indicator in female_indicators) else 'he'
    
    trait = random.choice(PERSONALITY_TRAITS)
    return f"{gender.capitalize()} has {trait.lower()}"

def create_report_text(number, api_data):
    current_date = datetime.now().strftime("%d-%b-%Y | %H:%M:%S IST")
    
    name = api_data.get('name', '')
    personality = generate_personality(name)
    
    address = api_data.get('address', 'N/A')
    state, city, hometown, mobile_location, tower_location = extract_location_info(address)
    
    circle = api_data.get('circle', 'Unknown')
    carriers = ["Jio", "Airtel", "Vi", "BSNL"]
    carrier = random.choice(carriers)
    
    report = f"""
────────────────────────────
🔍 GHOST NUMBER OSINT REPORT
────────────────────────────

📱 Number: {number}
🕵️ Scanned On: {current_date}

────────────────────────────
🧾 AADHAAR OSINT DATA
────────────────────────────

👤 Name: {api_data.get('name', 'N/A')}
👨‍👦 Father/Guardian: {api_data.get('father_name', 'N/A')}
🏠 Address: {address}
🌐 Circle: {circle}
🆔 ID Number: {api_data.get('aadhaar_id', 'N/A')}

────────────────────────────
📞 NUMBER LOOKUP DATA
────────────────────────────
👤 Owner Name: {api_data.get('name', 'N/A')}
🏠 Owner Address: {address}
📱 Carrier: {carrier}
📶 Connection: {carrier} SIM
📍 State: {state}
🏙️ Hometown: {hometown}
🌐 Reference City: {city}
📡 Mobile Locations: {mobile_location}
📡 Tower Locations: {tower_location}
📱 IMEI: {generate_imei()}
🌐 IP: {generate_ip()}
🔗 MAC: {generate_mac()}
📊 Tracking History: Traced by {random.randint(1,50)} people in 24 hrs
🧠 Personality: {personality}

────────────────────────────
👨‍💻 Developer: @Darkeyy0
────────────────────────────
"""
    return report

def create_report_file(number, api_data):
    """Create a TXT file with the report"""
    report_text = create_report_text(number, api_data)
    
    if not os.path.exists('reports'):
        os.makedirs('reports')
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"reports/lookup_{number}_{timestamp}.txt"
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    return filename

# ===== BOT HANDLERS =====
async def start(update, context):
    if not update.message:
        return
    
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    username = update.effective_user.username
    
    referred_by = 0
    if context.args:
        try:
            referred_by = int(context.args[0])
        except ValueError:
            referred_by = 0
    
    user_data = get_user(user_id)
    if user_data and user_data[5] == 1:
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return
    
    # FIXED: Use ensure_user instead of add_user to handle credit initialization properly
    ensure_user(user_id, username, first_name, referred_by)
    
    # Get updated user data to show credits
    user_data = get_user(user_id)
    credits = user_data[3] if user_data else 0
    
    logging.info(f"User {user_id} credits after ensure: {credits}")
    
    # Use central verification function
    has_access = await verify_user_channels(update, context, user_id)
    if has_access:
        await send_main_menu(update, context, user_id)

async def handle_button(update, context):
    if not update.message or not update.message.text:
        return
    
    text = update.message.text
    user_id = update.effective_user.id
    
    # Check if user has joined channels using central verification
    has_access = await verify_user_channels(update, context, user_id)
    if not has_access:
        return
    
    # FIXED: Ensure user exists with proper credits before any operation
    ensure_user(user_id)
    user_data = get_user(user_id)
    
    if text == "📱 Phone Info":
        if not user_data or user_data[3] < LOOKUP_COST:
            await update.message.reply_text(f"❌ You don't have enough credits! (Cost: {LOOKUP_COST} credit)")
            return
        
        await update.message.reply_text(
            "🔢 Send any 10-digit mobile number to get detailed OSINT report\n"
            "📱 Example: 9876543210\n\n"
            "🕵️ What you'll get:\n"
            "• Personal details (Aadhaar linked)\n"
            "• Network & carrier info\n"
            "• Location data\n"
            "• Device identifiers\n"
            "• Full downloadable report\n\n"
            f"⚠️ Note: This will deduct {LOOKUP_COST} credit from your balance.\n\n"
            "💡 Just type the 10-digit number now..."
        )
        
        context.user_data['waiting_for_number'] = True
        
    elif text == "💰 My Credits":
        # FIXED: Use ensure_user to make sure credits are properly set
        ensure_user(user_id)
        user_data = get_user(user_id)
        credits = user_data[3] if user_data else 0
        referral_count, bonus_earned = get_referral_stats(user_id)
        referred_by = get_referred_by(user_id)
        
        credit_text = f"""💰 Credits: {credits}

🆔 Referral Code: {user_id}
👥 Referred By: {'None' if referred_by == 0 else referred_by}
📈 Referrals: {referral_count}
🎁 Referral Bonus Earned: {bonus_earned} credits"""
        
        await update.message.reply_text(credit_text)
        
    elif text == "🔗 My Referral":
        referral_count, bonus_earned = get_referral_stats(user_id)
        
        referral_text = f"""📣 Your Referral Info:

🆔 Referral Code: {user_id}
🔗 Referral Link: https://t.me/{(await context.bot.get_me()).username}?start={user_id}
👥 Total Referrals: {referral_count}

💰 Earn {REFERRAL_BONUS} credits per new user who joins using your link!"""
        
        await update.message.reply_text(referral_text)
        
    elif text == "🎁 Redeem Code":
        await update.message.reply_text(
            "🎁 Redeem Code\n\n"
            "To redeem a code, use the command:\n"
            "/redeem <code>\n\n"
            "Example: /redeem ABC123XY\n\n"
            "💡 Get redeem codes from:\n"
            "• Admin giveaways\n"
            "• Special events\n"
            "• Promotional campaigns\n\n"
            "🔑 Note: Codes are case-insensitive"
        )
        
    elif text == "❓ Help":
        help_text = """
🤖 Ghost Number OSINT Bot Help

🔍 How to use:
1. Tap 'Phone Info'
2. Send any 10-digit Indian number
3. Get detailed OSINT report

💰 Credit System:
- Each lookup costs 1 credit
- Get 4 credits per referral
- Start with 4 free credits

📊 What's in the report:
• Personal details & Aadhaar data
• Network & carrier information  
• Location tracking data
• Device identifiers
• Personality analysis

🔗 Referral Program:
Share your referral link to earn credits!

🎁 Redeem Codes:
Use /redeem <code> to redeem credits

Need more help? Contact support.
"""
        await update.message.reply_text(help_text)
        
    elif text == "🧑‍💻 Support":
        await update.message.reply_text(
            "🧑🏻‍💻 Need help?\nContact: @Ghostxkingg"
        )

async def handle_callback_query(update, context):
    """Handle inline keyboard callbacks"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "verify_channels":
        logging.info(f"User {user_id} clicked verify_channels button")
        
        # Show checking message
        await query.edit_message_text("🔄 Checking channel membership...\n\nPlease wait while we verify your subscriptions.")
        
        # Check if user has joined all channels
        is_member = await check_channel_membership(user_id, context.bot)
        
        if is_member:
            # Update database and send main menu
            update_channel_status(user_id, 1)
            await query.edit_message_text("✅ *Verification Successful!*\n\n🎉 Welcome to Ghost OSINT Bot! You now have full access to all features.", parse_mode='Markdown')
            logging.info(f"User {user_id} successfully verified channels")
            await send_main_menu(update, context, user_id)
        else:
            await query.edit_message_text("❌ *Verification Failed*\n\nYou haven't joined all required channels yet. Please make sure you've joined ALL channels below and try again.", parse_mode='Markdown')
            logging.info(f"User {user_id} failed channel verification")
            
            # Resend the channel list with verify button
            await send_channel_verification_message(update, context, user_id)

async def process_number_lookup(update, context):
    user_id = update.effective_user.id
    
    # Check if user has joined channels using central verification
    has_access = await verify_user_channels(update, context, user_id)
    if not has_access:
        return
    
    if context.user_data.get('waiting_for_number'):
        number = update.message.text.strip()
        
        # FIXED: Ensure user exists with proper credits before lookup
        ensure_user(user_id)
        user_data = get_user(user_id)
        
        if not user_data or user_data[3] < LOOKUP_COST:
            await update.message.reply_text(f"❌ You don't have enough credits! (Cost: {LOOKUP_COST} credit)")
            context.user_data['waiting_for_number'] = False
            return

     
        if not re.match(r'^[6-9]\d{9}$', number):
            await update.message.reply_text("❌ Please enter a valid 10-digit Indian mobile number starting with 6,7,8, or 9.")
            return
        
        update_credits(user_id, -LOOKUP_COST, "Phone lookup")
        
        processing_msg = await update.message.reply_text(
            f"🔄 Processing lookup for {number}\n\n"
            "🔍 Checking multiple data sources...\n"
            "⌛ This may take 10-15 seconds"
        )
        
        try:
            api_data = phone_lookup(number)
            
            if 'error' not in api_data:
                filename = create_report_file(number, api_data)
                
                with open(filename, 'rb') as file:
                    caption = f"🔍 OSINT Report for {number}\n\n"
                    caption += f"👤 Name: {api_data.get('name', 'N/A')}\n"
                    caption += f"🏠 Address: {api_data.get('address', 'N/A')[:100]}...\n"
                    caption += f"👨‍👦 Father: {api_data.get('father_name', 'N/A')}\n"
                    caption += f"🌐 Circle: {api_data.get('circle', 'Unknown')}\n\n"
                    caption += f"💰 Credits left: {user_data[3] - 1}"
                    
                    await update.message.reply_document(
                        document=file,
                        filename=f"OSINT_Report_{number}.txt",
                        caption=caption
                    )
                
                try:
                    os.remove(filename)
                except:
                    pass
                
            else:
                update_credits(user_id, LOOKUP_COST, "Lookup refund")
                await update.message.reply_text(f"❌ {api_data['error']}\n\n💰 Your credit has been refunded.")
                
        except Exception as e:
            logging.error(f"API Error: {e}")
            update_credits(user_id, LOOKUP_COST, "Lookup error refund")
            await update.message.reply_text("❌ Error processing your request. Your credit has been refunded.")
        
        finally:
            await processing_msg.delete()
            context.user_data['waiting_for_number'] = False
    
    else:
        number = update.message.text.strip()
        if re.match(r'^[6-9]\d{9}$', number):
            await update.message.reply_text("⚠️ Please first click '📱 Phone Info' button to start a lookup.")

# ===== MISSING COMMAND HANDLERS =====
async def redeem_cmd(update, context):
    user_id = update.effective_user.id
    
    # Check if user has joined channels using central verification
    has_access = await verify_user_channels(update, context, user_id)
    if not has_access:
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /redeem <code>")
        return
    
    code = context.args[0].upper()
    credits_added = use_redeem_code(code, user_id)
    
    if credits_added:
        user_data = get_user(user_id)
        new_credits = user_data[3] if user_data else 0
        await update.message.reply_text(f"✅ Code redeemed successfully! +{credits_added} credits\n💰 Total credits: {new_credits}")
    else:
        await update.message.reply_text("❌ Invalid or expired redeem code!")

async def admin_commands(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    commands_text = """
🔧 ADMIN COMMANDS:

👥 User Management:
/addcredits <user_id> <amount> - Add credits to user
/removecredits <user_id> <amount> - Remove credits from user  
/setcredits <user_id> <amount> - Set user credits to specific amount
/resetcredits <user_id> - Reset user credits to 4
/resetuser <user_id> - Reset specific user
/usercredits <user_id> - Check user credits
/usercreditsdetailed <user_id> - Detailed user credit info
/credithistory <user_id> - View user credit history
/ban <user_id> - Ban user
/unban <user_id> - Unban user
/userstats - All user statistics

🎁 Code Management:
/addcode <credits> [max_uses] - Generate redeem code
/revokeredeem <code> - Revoke redeem code
/debugcodes - Debug redeem codes
/listcodes - List all redeem codes

📊 System:
/broadcast <message> - Broadcast to all users
/resetallcredits - Reset ALL users credits to 4
/debugphone <number> - Debug phone lookup
/testcodes - Test redeem code system

❓ Help:
/adminhelp - Show this help message
"""
    await update.message.reply_text(commands_text)

async def add_credits_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if len(context.args) != 2:
        await update.message.reply_text("❌ Usage: /addcredits <user_id> <amount>")
        return
    
    try:
        target_user = int(context.args[0])
        amount = int(context.args[1])
        
        # FIXED: Use ensure_user before adding credits
        ensure_user(target_user)
        update_credits(target_user, amount, f"Admin added by {user_id}", user_id)
        user_data = get_user(target_user)
        new_credits = user_data[3] if user_data else 0
        
        await update.message.reply_text(f"✅ Added {amount} credits to user {target_user}\n💰 New balance: {new_credits}")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID or amount")

async def remove_credits_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if len(context.args) != 2:
        await update.message.reply_text("❌ Usage: /removecredits <user_id> <amount>")
        return
    
    try:
        target_user = int(context.args[0])
        amount = int(context.args[1])
        
        # FIXED: Use ensure_user before removing credits
        ensure_user(target_user)
        update_credits(target_user, -amount, f"Admin removed by {user_id}", user_id)
        user_data = get_user(target_user)
        new_credits = user_data[3] if user_data else 0
        
        await update.message.reply_text(f"✅ Removed {amount} credits from user {target_user}\n💰 New balance: {new_credits}")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID or amount")

async def set_credits_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if len(context.args) != 2:
        await update.message.reply_text("❌ Usage: /setcredits <user_id> <amount>")
        return
    
    try:
        target_user = int(context.args[0])
        amount = int(context.args[1])
        
        # FIXED: Use ensure_user before setting credits
        ensure_user(target_user)
        set_credits(target_user, amount, f"Admin set by {user_id}", user_id)
        await update.message.reply_text(f"✅ Set credits for user {target_user} to {amount}")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID or amount")

async def reset_credits_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /resetcredits <user_id>")
        return
    
    try:
        target_user = int(context.args[0])
        difference = reset_user_credits(target_user)
        
        await update.message.reply_text(f"✅ Reset credits for user {target_user} to {DEFAULT_CREDITS} (adjusted by {difference})")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")

async def reset_user_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /resetuser <user_id>")
        return
    
    try:
        target_user = int(context.args[0])
        reset_user_credits(target_user)
        await update.message.reply_text(f"✅ User {target_user} has been reset to default credits ({DEFAULT_CREDITS})")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")

async def user_credits_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /usercredits <user_id>")
        return
    
    try:
        target_user = int(context.args[0])
        user_data = get_user(target_user)
        
        if user_data:
            credits = user_data[3]
            username = user_data[1] or "No username"
            first_name = user_data[2] or "No name"
            
            await update.message.reply_text(f"👤 User: {first_name} (@{username})\n🆔 ID: {target_user}\n💰 Credits: {credits}")
        else:
            await update.message.reply_text("❌ User not found")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")

async def user_credits_detailed_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /usercreditsdetailed <user_id>")
        return
    
    try:
        target_user = int(context.args[0])
        user_data = get_user(target_user)
        
        if user_data:
            credits = user_data[3]
            username = user_data[1] or "No username"
            first_name = user_data[2] or "No name"
            joined_channels = "✅" if user_data[4] == 1 else "❌"
            is_banned = "✅" if user_data[5] == 1 else "❌"
            referred_by = user_data[6]
            referral_count = user_data[7]
            referral_bonus = user_data[8]
            
            response = f"""👤 USER DETAILS:

🆔 ID: {target_user}
👤 Name: {first_name}
📱 Username: @{username}
💰 Credits: {credits}
📢 Channels Joined: {joined_channels}
🚫 Banned: {is_banned}
🔗 Referred By: {referred_by if referred_by else 'None'}
📈 Referrals: {referral_count}
🎁 Referral Bonus: {referral_bonus} credits"""
            
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("❌ User not found")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")

async def credit_history_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /credithistory <user_id>")
        return
    
    try:
        target_user = int(context.args[0])
        history = get_credit_history(target_user, 10)
        
        if history:
            response = f"📊 Credit History for {target_user}:\n\n"
            for amount, reason, date in history:
                sign = "+" if amount > 0 else ""
                response += f"• {sign}{amount} - {reason}\n  📅 {date}\n\n"
            
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("❌ No credit history found for this user")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")

async def add_code_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("❌ Usage: /addcode <credits> [max_uses=1]")
        return
    
    try:
        credits = int(context.args[0])
        max_uses = int(context.args[1]) if len(context.args) > 1 else 1
        
        code = create_redeem_code(credits, user_id, max_uses)
        await update.message.reply_text(f"✅ Redeem code created!\n\n🔑 Code: `{code}`\n💰 Credits: {credits}\n🔢 Max Uses: {max_uses}", parse_mode='Markdown')
    except ValueError:
        await update.message.reply_text("❌ Invalid credits or max_uses value")

async def revoke_redeem_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /revokeredeem <code>")
        return
    
    code = context.args[0].upper()
    if revoke_redeem_code(code):
        await update.message.reply_text(f"✅ Redeem code `{code}` has been revoked.", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Code not found or already revoked")

async def debug_codes_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    codes = get_redeem_codes()
    
    if not codes:
        await update.message.reply_text("❌ No redeem codes found")
        return
            
    response = "🔍 REDEEM CODES DEBUG:\n\n"
    for code in codes[:10]:  # Show first 10 codes
        code_text, credits, created_by, created_at, expires_at, used_by, max_uses, use_count = code
        
        status = "✅ ACTIVE" if use_count < max_uses and (not expires_at or expires_at > datetime.now()) else "❌ EXPIRED/USED"
        used_info = f"Used {use_count}/{max_uses} times"
        if used_by:
            used_info += f" by {used_by}"
        
        response += f"🔑 {code_text}\n💰 {credits} credits\n{used_info}\n📅 Created: {created_at}\n⏰ Expires: {expires_at}\n{status}\n\n"
    
    await update.message.reply_text(response)

async def ban_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /ban <user_id>")
        return
    
    try:
        target_user = int(context.args[0])
        ban_user(target_user)
        await update.message.reply_text(f"✅ User {target_user} has been banned")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")

async def unban_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /unban <user_id>")
        return
    
    try:
        target_user = int(context.args[0])
        unban_user(target_user)
        await update.message.reply_text(f"✅ User {target_user} has been unbanned")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")

async def user_stats_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    users = get_all_users()
    total_users = len(users)
    total_credits = sum(user[3] for user in users)
    banned_users = sum(1 for user in users if user[5] == 1)
    active_users = total_users - banned_users
    
    response = f"""📊 USER STATISTICS:

👥 Total Users: {total_users}
✅ Active Users: {active_users}
🚫 Banned Users: {banned_users}
💰 Total Credits: {total_credits}

📈 User Distribution:"""
    
    # Credit distribution
    credit_ranges = {
        "0-2": 0,
        "3-10": 0,
        "11-20": 0,
        "21+": 0
    }
    
    for user in users:
        credits = user[3]
        if credits <= 2:
            credit_ranges["0-2"] += 1
        elif credits <= 10:
            credit_ranges["3-10"] += 1
        elif credits <= 20:
            credit_ranges["11-20"] += 1
        else:
            credit_ranges["21+"] += 1
    
    for range_name, count in credit_ranges.items():
        percentage = (count / total_users) * 100 if total_users > 0 else 0
        response += f"\n• {range_name} credits: {count} users ({percentage:.1f}%)"
    
    await update.message.reply_text(response)

async def broadcast_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /broadcast <message>")
        return
    
    message = ' '.join(context.args)
    users = get_all_users()
    successful = 0
    failed = 0
    
    status_msg = await update.message.reply_text(f"📢 Broadcasting to {len(users)} users...")
    
    for user in users:
        try:
            if user[5] == 0:  # Not banned
                await context.bot.send_message(chat_id=user[0], text=message)
                successful += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
        
        # Small delay to avoid rate limiting
        await asyncio.sleep(0.1)
    
    await status_msg.edit_text(f"✅ Broadcast completed!\n\n📤 Successful: {successful}\n❌ Failed: {failed}")

async def debug_phone_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /debugphone <number>")
        return
    
    number = context.args[0]
    await update.message.reply_text(f"🔍 Debugging phone lookup for: {number}")
    
    try:
        result = phone_lookup(number)
        await update.message.reply_text(f"📊 Result: {json.dumps(result, indent=2)}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def test_codes_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    # Create a test code
    test_code = create_redeem_code(5, user_id, 2)
    await update.message.reply_text(f"🧪 TEST CODE CREATED:\n\n🔑 Code: `{test_code}`\n💰 Credits: 5\n🔢 Max Uses: 2", parse_mode='Markdown')

async def list_codes_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    codes = get_redeem_codes()
    
    if not codes:
        await update.message.reply_text("❌ No redeem codes found")
        return
    
    response = "🔑 ACTIVE REDEEM CODES:\n\n"
    active_codes = 0
    
    for code in codes:
        code_text, credits, created_by, created_at, expires_at, used_by, max_uses, use_count = code
        
        if use_count < max_uses and (not expires_at or datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S') > datetime.now()):
            active_codes += 1
            remaining_uses = max_uses - use_count
            response += f"🔑 `{code_text}`\n💰 {credits} credits\n📊 {remaining_uses}/{max_uses} uses left\n⏰ Expires: {expires_at}\n\n"
    
    if active_codes == 0:
        response = "❌ No active redeem codes found"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def admin_help_cmd(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    await admin_commands(update, context)

async def error_handler(update, context):
    """Log errors and send them to the error channel"""
    logging.error(f"Exception while handling an update: {context.error}")
    
    try:
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = ''.join(tb_list)
        
        error_message = (
            f"🛑 An exception was raised while handling an update\n"
            f"💬 Update: {update}\n"
            f"❌ Error: {context.error}\n"
            f"🔍 Traceback:\n{tb_string}"
        )
        
        # Send to error channel (truncate if too long)
        if len(error_message) > 4000:
            error_message = error_message[:4000] + "\n... (truncated)"
        
        await context.bot.send_message(chat_id=ERROR_CHANNEL_ID, text=error_message)
    except Exception as e:
        logging.error(f"Error in error handler: {e}")

# Add the missing reset_all_credits command handler
async def reset_all_credits_cmd(update, context):
    """Fix credits for all existing users"""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    reset_count = reset_all_credits()
    await update.message.reply_text(f"✅ Reset credits for {reset_count} users to default ({DEFAULT_CREDITS} credits)")

# ===== MAIN FUNCTION =====
def main():
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add ALL command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("redeem", redeem_cmd))
    application.add_handler(CommandHandler("admincommands", admin_commands))
 application.add_handler(CommandHandler("addcredits", add_credits_cmd))
    application.add_handler(CommandHandler("removecredits", remove_credits_cmd))
    application.add_handler(CommandHandler("setcredits", set_credits_cmd))
    application.add_handler(CommandHandler("resetcredits", reset_credits_cmd))
    application.add_handler(CommandHandler("resetuser", reset_user_cmd))
    application.add_handler(CommandHandler("usercredits", user_credits_cmd))
    application.add_handler(CommandHandler("usercreditsdetailed", user_credits_detailed_cmd))
    application.add_handler(CommandHandler("credithistory", credit_history_cmd))
    application.add_handler(CommandHandler("addcode", add_code_cmd))
    application.add_handler(CommandHandler("revokeredeem", revoke_redeem_cmd))
    application.add_handler(CommandHandler("debugcodes", debug_codes_cmd))
    application.add_handler(CommandHandler("ban", ban_cmd))
    application.add_handler(CommandHandler("unban", unban_cmd))
    application.add_handler(CommandHandler("userstats", user_stats_cmd))
    application.add_handler(CommandHandler("broadcast", broadcast_cmd))
    application.add_handler(CommandHandler("debugphone", debug_phone_cmd))
    application.add_handler(CommandHandler("testcodes", test_codes_cmd))
    application.add_handler(CommandHandler("listcodes", list_codes_cmd))
    application.add_handler(CommandHandler("adminhelp", admin_help_cmd))
    application.add_handler(CommandHandler("resetallcredits", reset_all_credits_cmd))
    
    # Handle callback queries (for channel verification) - THIS MUST BE ADDED
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    
    # Handle button messages
    button_texts = [
        "📱 Phone Info", "💰 My Credits", "🔗 My Referral", 
        "🎁 Redeem Code", "❓ Help", "🧑‍💻 Support"
    ]
    application.add_handler(MessageHandler(filters.Text(button_texts), handle_button))
    
    # Handle number input for lookup
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_number_lookup))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    print("🤖 Ghost OSINT Bot is running...")
    print("🚀 Enhanced with multiple API sources!")
    print("📄 Reports are sent as downloadable TXT files!")
    print("💾 All database functions are active!")
    print("🔧 Admin commands loaded: 21 commands")
    print("📢 Channel verification system: ACTIVE")
    print("🔗 Channels configured:")
    for channel in REQUIRED_CHANNELS:
        print(f"   - {channel['name']}: {channel['url']}")
    application.run_polling()

if __name__ == '__main__':
    main()
