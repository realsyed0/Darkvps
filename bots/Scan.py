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
 
