import os
import json
import logging
import sqlite3
import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, CallbackContext
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

# ========== CONFIGURATION ==========
BOT_TOKEN = "8235353438:AAERZ4JtgBaOn0qcGHn0TXt3RwdyMgRg-Z4"
ADMIN_IDS = [5464634575, 7457553175, 7836805892]

# Required channels for force join
REQUIRED_CHANNELS = [
    {
        'name': 'DARK LEGACY', 
        'id': -1003863829311,
        'url': 'https://t.me/+P8G7AqQFn1ZiNjk1
    },
    {
        'name': 'DEV CHNL', 
        'id': -1003626977108,
        'url': 'https://t.me/G4OSTPY'
    },
]

# Database setup
DB_NAME = "osint_bot.db"

DEFAULT_DAILY_CREDITS = 10
CREDIT_COSTS = {
    "phone": 1,
    "ip": 1,
    "vehicle": 2,
    "aadhaar": 3,
    "ifsc": 1,
    "pak": 1,
    "imei": 2,
    "pan": 2,
    "email": 1,
    "ff_ban": 1,
    "ff_info": 1
}

# ========== DATABASE FUNCTIONS ==========
def init_database():
    """Initialize SQLite database with required tables"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            join_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_banned BOOLEAN DEFAULT 0,
            total_credits INTEGER DEFAULT 0,
            daily_reset DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_activity DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Credit usage table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS credit_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            service_type TEXT,
            credits_used INTEGER,
            query TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Redeem codes table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS redeem_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            credits INTEGER,
            max_uses INTEGER DEFAULT 1,
            used_count INTEGER DEFAULT 0,
            created_by INTEGER,
            created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    # Redeem code usage tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS redeem_code_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_id INTEGER,
            user_id INTEGER,
            used_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (code_id) REFERENCES redeem_codes(id)
        )
    ''')
    
    # Bot settings table for lock state persistence
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Initialize bot lock state if not exists
    cursor.execute('''
        INSERT OR IGNORE INTO bot_settings (key, value) 
        VALUES ('bot_locked', '0')
    ''')
    
    conn.commit()
    conn.close()

def get_user_credits(user_id: int) -> Tuple[int, datetime]:
    """Get user's available credits and last reset time"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute(
        'SELECT total_credits, daily_reset FROM users WHERE user_id = ?',
        (user_id,)
    )
    result = cursor.fetchone()
    
    if not result:
        # Auto-register new user
        cursor.execute(
            '''INSERT INTO users (user_id, total_credits) 
               VALUES (?, ?)''',
            (user_id, DEFAULT_DAILY_CREDITS)
        )
        conn.commit()
        conn.close()
        return DEFAULT_DAILY_CREDITS, datetime.now()
    
    credits, last_reset = result
    last_reset = datetime.fromisoformat(last_reset)
    
    # Reset daily credits if needed
    if datetime.now() - last_reset >= timedelta(days=1):
        credits = DEFAULT_DAILY_CREDITS
        cursor.execute(
            '''UPDATE users SET total_credits = ?, daily_reset = ? 
               WHERE user_id = ?''',
            (credits, datetime.now().isoformat(), user_id)
        )
        conn.commit()
    
    conn.close()
    return credits, last_reset

def use_credits(user_id: int, service_type: str, query: str = "") -> bool:
    """Deduct credits for a service"""
    cost = CREDIT_COSTS.get(service_type, 1)
    credits, _ = get_user_credits(user_id)
    
    if credits < cost:
        return False
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Deduct credits
    cursor.execute(
        '''UPDATE users SET total_credits = total_credits - ? 
           WHERE user_id = ?''',
        (cost, user_id)
    )
    
    # Log usage
    cursor.execute(
        '''INSERT INTO credit_usage (user_id, service_type, credits_used, query)
           VALUES (?, ?, ?, ?)''',
        (user_id, service_type, cost, query)
    )
    
    # Update last activity
    cursor.execute(
        '''UPDATE users SET last_activity = ? WHERE user_id = ?''',
        (datetime.now().isoformat(), user_id)
    )
    
    conn.commit()
    conn.close()
    return True

# ========== BOT LOCK FUNCTIONALITY ==========
def get_bot_lock_state():
    """Get bot lock state from database"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_settings WHERE key = 'bot_locked'")
    result = cursor.fetchone()
    conn.close()
    return result[0] == '1' if result else False

def set_bot_lock_state(locked: bool):
    """Set bot lock state in database"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE bot_settings SET value = ?, updated_date = CURRENT_TIMESTAMP WHERE key = 'bot_locked'",
        ('1' if locked else '0',)
    )
    conn.commit()
    conn.close()
    return locked

async def lock_bot():
    """Lock the bot"""
    set_bot_lock_state(True)
    return True

async def unlock_bot():
    """Unlock the bot"""
    set_bot_lock_state(False)
    return True

def check_bot_lock():
    """Check if bot is locked"""
    return get_bot_lock_state()

# ========== API CALL FUNCTIONS ==========
async def call_api_with_timeout(url: str, timeout: int = 15) -> Optional[dict]:
    """Generic API call with timeout"""
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            async with session.get(url, headers=headers, timeout=timeout) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logging.error(f"API request failed with status {response.status}")
                    return None
    except asyncio.TimeoutError:
        logging.error(f"API timeout for URL: {url}")
        return None
    except Exception as e:
        logging.error(f"API error: {e}")
        return None

# Phone Lookup API
async def phone_lookup_api(phone_number: str) -> Optional[dict]:
    """Phone number lookup API"""
    url = f"https://anon-num-info.vercel.app/num?key=temp203&num=6205923286"
    return await call_api_with_timeout(url)

def format_phone_result(data: dict) -> str:
    """Format phone lookup results"""
    if not data or not data.get('success'):
        return "❌ *Phone Lookup Failed*\n\nNo results found or API error."
    
    results = data.get('result', [])
    if not results:
        return "📱 *Phone Number Lookup*\n\n❌ No results found"
    
    result = results[0]
    message = "📱 *Phone Number Lookup Results*\n\n"
    message += f"🔢 *Number:* `{result.get('mobile', 'N/A')}`\n"
    message += f"👤 *Name:* {result.get('name', 'N/A')}\n"
    message += f"👨 *Father:* {result.get('father_name', 'N/A')}\n"
    message += f"📞 *Alt Mobile:* {result.get('alt_mobile', 'N/A')}\n"
    message += f"📍 *Circle:* {result.get('circle', 'N/A')}\n"
    message += f"🆔 *ID Number:* {result.get('id_number', 'N/A')}\n"
    
    # Parse address
    address = result.get('address', '')
    if address:
        parts = address.split('!!!')
        if len(parts) >= 2:
            relationship = parts[0] if parts[0] != 'NA' else ''
            address_parts = parts[1].split('!') if len(parts) > 1 else []
            
            if relationship:
                message += f"👥 *Relationship:* {relationship}\n"
            
            if address_parts:
                street = address_parts[2] if len(address_parts) > 2 and address_parts[2] != 'NA' else ''
                area = address_parts[3] if len(address_parts) > 3 and address_parts[3] != 'NA' else ''
                city = address_parts[4] if len(address_parts) > 4 and address_parts[4] != 'NA' else ''
                state = address_parts[5] if len(address_parts) > 5 and address_parts[5] != 'NA' else ''
                pincode = address_parts[6] if len(address_parts) > 6 and address_parts[6] != 'NA' else ''
                
                address_lines = []
                if street: address_lines.append(street)
                if area: address_lines.append(area)
                if city: address_lines.append(city)
                if state: address_lines.append(state)
                if pincode: address_lines.append(pincode)
                
                if address_lines:
                    formatted_address = ', '.join(filter(None, address_lines))
                    message += f"🏠 *Address:* {formatted_address}\n"
    
    return message

# Aadhaar Lookup API
async def aadhaar_lookup_api(aadhaar_number: str) -> Optional[dict]:
    """Aadhaar number lookup API"""
    url = f"https://anon-num-info.vercel.app/aadhar?key=temp153&id={aadhaar_number}"
    return await call_api_with_timeout(url, timeout=20)

def format_aadhaar_result(data: dict) -> str:
    """Format Aadhaar lookup results"""
    if not data or not data.get('success'):
        return "❌ *Aadhaar Lookup Failed*\n\nAPI error or invalid response."
    
    data_section = data.get('data', {})
    if not data_section.get('success'):
        return "🆔 *Aadhaar Lookup*\n\n❌ No family details found for this Aadhaar number"
    
    results = data_section.get('results', [])
    if not results:
        return "🆔 *Aadhaar Lookup*\n\n❌ No family details found"
    
    result = results[0]
    message = "🆔 *Aadhaar Card - Family Details*\n\n"
    
    query_info = data.get('query', {})
    aadhaar_number = query_info.get('aadhaar_number', 'N/A')
    message += f"🔢 *Aadhaar Number:* `{aadhaar_number}`\n\n"
    
    # Ration Card Details
    ration_card = result.get('ration_card_details', {})
    if ration_card:
        message += "📋 *Ration Card Information*\n"
        message += f"🏛️ *State:* {ration_card.get('state_name', 'N/A')}\n"
        message += f"🗺️ *District:* {ration_card.get('district_name', 'N/A')}\n"
        message += f"💳 *Ration Card No:* `{ration_card.get('ration_card_no', 'N/A')}`\n"
        message += f"📝 *Scheme:* {ration_card.get('scheme_name', 'N/A')}\n\n"
    
    # Family Members
    members = result.get('members', [])
    if members:
        message += f"👨‍👩‍👧‍👦 *Family Members ({len(members)}):*\n\n"
        for member in members:
            s_no = member.get('s_no', 'N/A')
            member_name = member.get('member_name', 'N/A')
            member_id = member.get('member_id', 'N/A')
            remark = member.get('remark', '')
            
            message += f"{s_no}. *{member_name}*\n"
            message += f"   🆔 Member ID: `{member_id}`\n"
            if remark and remark != 'null' and remark != 'NA':
                message += f"   📝 Remark: {remark}\n"
            message += "\n"
    
    # Additional Information
    additional_info = result.get('additional_info', {})
    if additional_info:
        message += "ℹ️ *Additional Information*\n"
        fps_category = additional_info.get('fps_category', 'N/A')
        if fps_category:
            message += f"🏪 *FPS Category:* {fps_category}\n"
        
        impds_allowed = additional_info.get('impds_transaction_allowed', False)
        message += f"💳 *IM-PDS Allowed:* {'✅ Yes' if impds_allowed else '❌ No'}\n"
        
        central_repo = additional_info.get('exists_in_central_repository', False)
        message += f"🗄️ *In Central Repository:* {'✅ Yes' if central_repo else '❌ No'}\n"
        
        duplicate_aadhaar = additional_info.get('duplicate_aadhaar_beneficiary', False)
        message += f"⚠️ *Duplicate Aadhaar:* {'❌ Yes' if duplicate_aadhaar else '✅ No'}\n"
    
    message += f"\n📊 *Total Records Found:* {data_section.get('count', 0)}"
    return message

# IP Lookup API
async def ip_lookup_api(ip_address: str) -> Optional[dict]:
    """IP address lookup API"""
    url = f"https://anon-multi-info.vercel.app/ipinfo?key=temp053&ip=status,message,continent,continentCode,country,countryCode,region,regionName,city,district,zip,lat,lon,timezone,offset,currency,isp,org,as,asname,reverse,mobile,proxy,hosting,query"
    return await call_api_with_timeout(url)

def format_ip_result(data: dict) -> str:
    """Format IP lookup results"""
    if not data or data.get('status') != 'success':
        error_msg = data.get('message', 'No information found for this IP')
        error_msg = error_msg.replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
        return f"❌ Error\n\n{error_msg}"
    
    message = "🌐 *IP Address Information*\n\n"
    
    # Escape all text fields
    query = data.get('query', 'N/A').replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
    country = str(data.get('country', 'N/A')).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
    countryCode = str(data.get('countryCode', 'N/A')).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
    region = str(data.get('regionName', 'N/A')).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
    city = str(data.get('city', 'N/A')).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
    isp = str(data.get('isp', 'N/A')).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
    org = str(data.get('org', 'N/A')).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
    lat = str(data.get('lat', 'N/A')).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
    lon = str(data.get('lon', 'N/A')).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
    timezone = str(data.get('timezone', 'N/A')).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
    
    message += f"🔢 *IP:* `{query}`\n"
    message += f"🌍 *Country:* {country} ({countryCode})\n"
    message += f"🗺️ *Region:* {region}\n"
    message += f"🏙️ *City:* {city}\n"
    message += f"📡 *ISP:* {isp}\n"
    message += f"🏢 *Organization:* {org}\n"
    message += f"📍 *Lat/Lon:* {lat}, {lon}\n"
    message += f"⏱️ *Timezone:* {timezone}\n"
    message += f"📱 *Mobile:* {'Yes' if data.get('mobile') else 'No'}\n"
    message += f"🛡️ *Proxy:* {'Yes' if data.get('proxy') else 'No'}\n"
    message += f"🏢 *Hosting:* {'Yes' if data.get('hosting') else 'No'}"
    
    return message

# PAN Lookup API
async def pan_lookup_api(pan_number: str) -> Optional[dict]:
    """PAN number lookup API"""
    url = f"https://anon-gst-info.vercel.app/advanced/pan?key=anon404&pan={pan_number}"
    return await call_api_with_timeout(url)

def format_pan_result(data: dict) -> str:
    """Format PAN lookup results"""
    if not data or not data.get('success'):
        error_msg = data.get('error', 'No data found')
        error_msg = error_msg.replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
        return f"❌ Error\n\n{error_msg}"
    
    message = "📄 *PAN Card Details*\n\n"
    
    pan_number = data.get('query', data.get('pan', 'N/A'))
    pan_number = str(pan_number).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
    message += f"🔢 *PAN:* `{pan_number}`\n"
    
    full_name = data.get('fullName', 'N/A')
    if full_name and full_name != 'N/A':
        full_name = str(full_name).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
        message += f"👤 *Full Name:* {full_name}\n"
    else:
        first_name = data.get('firstName', 'N/A')
        last_name = data.get('lastName', 'N/A')
        if first_name != 'N/A' or last_name != 'N/A':
            first_name = str(first_name).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
            last_name = str(last_name).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
            message += f"👤 *Name:* {first_name} {last_name}\n"
    
    pan_status = data.get('panStatus', 'N/A')
    if pan_status and pan_status != 'N/A':
        pan_status = str(pan_status).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
        message += f"📊 *Status:* {pan_status}\n"
    else:
        status = data.get('status', 'N/A')
        if status and status != 'N/A':
            status = str(status).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
            message += f"📊 *Status:* {status}\n"
    
    dob = data.get('dob', 'N/A')
    if dob and dob != 'N/A':
        dob = str(dob).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
        message += f"📅 *DOB:* {dob}\n"
    
    return message

# Pakistan Lookup API
async def pak_lookup_api(pak_number: str) -> Optional[dict]:
    """Pakistan number lookup API"""
    url = f"https://suryansh.site/pakinfo.php?pak_num={pak_number}"
    return await call_api_with_timeout(url)

def format_pak_result(data: dict) -> str:
    """Format Pakistan number lookup results"""
    if not data or not data.get('success'):
        return f"❌ *Error*\n\n`{data.get('error', 'Unknown error')}`"
    
    results = data.get('results', [])
    if not results:
        return "🇵🇰 *Pakistan Number Lookup*\n\n❌ No results found"
    
    message = "🇵🇰 *Pakistan Number Lookup*\n\n"
    message += f"🔢 *Number:* `{results[0].get('n', 'N/A')}`\n"
    message += f"👤 *Name:* {results[0].get('name', 'N/A')}\n"
    message += f"🆔 *CNIC:* {results[0].get('cnic', 'N/A')}\n"
    message += f"🏠 *Address:* {results[0].get('address', 'N/A')}\n\n"
    
    message += f"📊 *Found {len(results)} records:*\n"
    for i, result in enumerate(results[:3], 1):
        message += f"{i}. *Address {i}:* {result.get('address', 'N/A')}\n"
    
    return message

# IMEI Lookup API
async def imei_lookup_api(imei_number: str) -> Optional[dict]:
    """IMEI number lookup API"""
    url = f"https://anon-phone-specs.vercel.app/imei?key=tempx678&imei=3{imei_number}"
    return await call_api_with_timeout(url)

def format_imei_result(data: dict) -> str:
    """Format IMEI lookup results"""
    if not data or not data.get('success', True):
        return f"❌ *Error*\n\n`{data.get('error', 'Unknown error')}`"
    
    result = data.get('result', {})
    header = result.get('header', {})
    items = result.get('items', [])
    
    message = "📱 *IMEI Device Information*\n\n"
    message += f"🏷️ *Brand:* {header.get('brand', 'N/A')}\n"
    message += f"📱 *Model:* {header.get('model', 'N/A')}\n"
    message += f"🔢 *IMEI:* `{header.get('imei', 'N/A')}`\n\n"
    
    categories = {}
    current_category = "General"
    
    for item in items:
        if item.get('role') == 'header':
            current_category = item.get('title', 'General')
            categories[current_category] = []
        elif item.get('role') == 'item':
            categories.setdefault(current_category, []).append(item)
    
    for category, items_list in categories.items():
        message += f"📋 *{category}:*\n"
        for item in items_list:
            title = item.get('title', '')
            content = item.get('content', 'N/A')
            message += f"  • *{title}:* {content}\n"
        message += "\n"
    
    return message

# Email Lookup API
async def email_lookup_api(email: str) -> Optional[dict]:
    """Email lookup API"""
    url = f"https://zx-osint-ghostxshaurya.vercel.app/api?key=zxop&type=mailinfo&term={email}"
    return await call_api_with_timeout(url)

def format_email_result(data: dict) -> str:
    """Format email lookup results"""
    if not data or not data.get('success'):
        return f"❌ *Error*\n\n`{data.get('error', 'Unknown error')}`"
    
    result = data.get('result', {})
    message = "📧 *Email Lookup Results*\n\n"
    message += f"📧 *Email:* `{result.get('Email', 'N/A')}`\n"
    message += f"🌐 *Domain:* {result.get('Domain', 'N/A')}\n"
    message += f"🏢 *Provider:* {result.get('Provider', 'N/A')}\n"
    message += f"📅 *Created:* {result.get('Creation Date', 'N/A')}\n"
    message += f"📅 *Expires:* {result.get('Expiration Date', 'N/A')}\n"
    message += f"🛡️ *Disposable:* {result.get('Disposable', 'N/A')}\n"
    message += f"📍 *Server Location:* {result.get('Server Location', 'N/A')}\n"
    message += f"🔒 *SSL Issuer:* {result.get('SSL Issuer', 'N/A')}\n"
    message += f"🌐 *Domain IP:* {result.get('Domain IP', 'N/A')}\n"
    message += f"🛡️ *ISP:* {result.get('ISP', 'N/A')}\n"
    message += f"📋 *Registrar:* {result.get('Registrar', 'N/A')}\n\n"
    
    breaches = result.get('Breaches Found', [])
    if breaches and breaches[0] != "Error fetching data":
        message += "⚠️ *Breaches Found:*\n"
        for breach in breaches:
            message += f"  • {breach}\n"
        message += "\n"
    
    message += "📡 *MX Records:*\n"
    mx_records = result.get('MX Records', [])
    for mx in mx_records[:3]:
        message += f"  • `{mx}`\n"
    
    return message

# IFSC Lookup API
async def ifsc_lookup_api(ifsc_code: str) -> Optional[dict]:
    """IFSC code lookup API"""
    url = f"https://anon-multi-info.vercel.app/ifsc?key=temp053&code={ifsc_code}"
    return await call_api_with_timeout(url)

def format_ifsc_result(data: dict) -> str:
    """Format IFSC lookup results"""
    if not data or not data.get('success'):
        return f"❌ *Error*\n\n`{data.get('error', 'Unknown error')}`"
    
    result = data.get('result', {})
    message = "🏦 *IFSC Code Details*\n\n"
    message += f"🏦 *Bank:* {result.get('BANK', 'N/A')}\n"
    message += f"🏷️ *IFSC:* `{result.get('IFSC', 'N/A')}`\n"
    message += f"📍 *Branch:* {result.get('BRANCH', 'N/A')}\n"
    message += f"🏙️ *Centre:* {result.get('CENTRE', 'N/A')}\n"
    message += f"🗺️ *Address:* {result.get('ADDRESS', 'N/A')}\n"
    message += f"🏙️ *City:* {result.get('CITY', 'N/A')}\n"
    message += f"🗺️ *District:* {result.get('DISTRICT', 'N/A')}\n"
    message += f"🗺️ *State:* {result.get('STATE', 'N/A')}\n"
    message += f"📞 *Contact:* {result.get('CONTACT', 'N/A')}\n"
    message += f"🔢 *MICR:* {result.get('MICR', 'N/A')}\n"
    message += f"🌍 *ISO Code:* {result.get('ISO3166', 'N/A')}\n"
    message += f"💳 *SWIFT:* {result.get('SWIFT', 'N/A')}\n\n"
    
    services = []
    if result.get('NEFT'): services.append("💳 NEFT")
    if result.get('RTGS'): services.append("💰 RTGS")
    if result.get('IMPS'): services.append("⚡ IMPS")
    if result.get('UPI'): services.append("📱 UPI")
    
    if services:
        message += "✅ *Services Available:*\n"
        for service in services:
            message += f"  {service}\n"
    
    return message

# Vehicle Lookup API
async def vehicle_lookup_api(vehicle_number: str) -> Optional[dict]:
    """Vehicle number lookup API"""
    url = f"https://anon-vehicle-info.vercel.app/rc?key=tempx183&rc={vehicle_number}"
    return await call_api_with_timeout(url)

def format_vehicle_result(data: dict) -> str:
    """Format vehicle lookup results"""
    if not data or not data.get('success'):
        return f"❌ *Error*\n\n`{data.get('error', 'Unknown error')}`"
    
    result = data.get('result', {})
    message = "🚗 *Vehicle Registration Details*\n\n"
    
    source1 = result.get('source1', {}).get('details', {})
    message += "📋 *Source 1 Information:*\n"
    message += f"👤 *Owner:* {source1.get('Owner Name', 'N/A')}\n"
    message += f"🏭 *Maker:* {source1.get('Maker Model', 'N/A')}\n"
    message += f"🚘 *Model:* {source1.get('Model Name', 'N/A')}\n"
    message += f"⛽ *Fuel Type:* {source1.get('Fuel Type', 'N/A')}\n"
    message += f"📅 *Reg Date:* {source1.get('Registration Date', 'N/A')}\n"
    message += f"📅 *Fitness Upto:* {source1.get('Fitness Upto', 'N/A')}\n"
    message += f"📍 *RTO:* {source1.get('Registered RTO', 'N/A')}\n"
    message += f"📞 *Phone:* {source1.get('Phone', 'N/A')}\n"
    message += f"🏠 *Address:* {source1.get('Address', 'N/A')}\n"
    message += f"🏙️ *City:* {source1.get('City Name', 'N/A')}\n\n"
    
    source2 = result.get('source2', {}).get('data', {})
    if source2:
        nexus2 = source2.get('Nexus2', {})
        father_name = nexus2.get("Father's Name", 'N/A')
        message += f"👤 *Owner:* {nexus2.get('Owner Name', 'N/A')}\n"
        message += f"👨 *Father:* {father_name}\n"
        message += f"📅 *Insurance:* {nexus2.get('Insurance Expiry', 'N/A')}\n"
    
    return message

# Free Fire Ban Check API
async def ff_ban_lookup_api(uid: str) -> Optional[dict]:
    """Free Fire ban check API"""
    url = f"https://zx-osint-ghostxshaurya.vercel.app/api?key=zxop&type=ffbancheck&term={uid}"
    return await call_api_with_timeout(url)

def format_ff_ban_result(data: dict) -> str:
    """Format FF ban check results"""
    if not data or not data.get('success'):
        return f"❌ *Error*\n\n`{data.get('error', 'Unknown error')}`"
    
    result = data.get('result', {})
    message = "🎮 *Free Fire Ban Check*\n\n"
    message += f"👤 *Nickname:* {result.get('nickname', 'N/A')}\n"
    message += f"🆔 *UID:* `{result.get('uid', 'N/A')}`\n"
    
    ban_status = result.get('ban_status', 'UNKNOWN')
    if ban_status == "NOT BANNED":
        message += f"✅ *Status:* NOT BANNED\n"
        message += f"⏰ *Ban Period:* 0 days\n"
    else:
        message += f"❌ *Status:* {ban_status}\n"
        message += f"⏰ *Ban Period:* {result.get('ban_period', 0)} days\n"
    
    message += f"🌍 *Region:* {result.get('region', 'Unknown')}\n"
    return message

# Free Fire Info API
async def ff_info_lookup_api(uid: str) -> Optional[dict]:
    """Free Fire account info API"""
    url = f"https://ffinfoapibysaksham.vercel.app/accinfo?uid={uid}&region=IND"
    return await call_api_with_timeout(url)

def format_ff_info_result(data: dict) -> str:
    """Format Free Fire Account Info results"""
    if not data or 'basicInfo' not in data:
        return f"❌ *Error*\n\n`Account not found or API error`"
    
    basic = data.get('basicInfo', {})
    message = "🎮 *Free Fire Account Information*\n\n"
    message += f"👤 *Nickname:* {basic.get('nickname', 'N/A')}\n"
    message += f"🆔 *UID:* `{basic.get('accountId', 'N/A')}`\n"
    
    try:
        create_at = basic.get('createAt')
        if create_at:
            create_date = datetime.fromtimestamp(int(create_at)).strftime('%Y-%m-%d')
            message += f"📅 *Created:* {create_date}\n"
        else:
            message += f"📅 *Created:* N/A\n"
    except:
        message += f"📅 *Created:* N/A\n"
    
    try:
        last_login = basic.get('lastLoginAt')
        if last_login:
            login_date = datetime.fromtimestamp(int(last_login)).strftime('%Y-%m-%d')
            message += f"📅 *Last Login:* {login_date}\n"
        else:
            message += f"📅 *Last Login:* N/A\n"
    except:
        message += f"📅 *Last Login:* N/A\n"
    
    message += f"⭐ *Level:* {basic.get('level', 'N/A')}\n"
    message += f"🏆 *Rank:* {basic.get('rank', 'N/A')}\n"
    message += f"📊 *Max Rank:* {basic.get('maxRank', 'N/A')}\n"
    message += f"🌍 *Region:* {basic.get('region', 'N/A')}\n"
    message += f"💎 *Exp:* {basic.get('exp', 'N/A')}\n"
    message += f"👍 *Likes:* {basic.get('liked', 'N/A')}\n"
    
    credit = data.get('creditScoreInfo', {})
    if credit:
        message += f"📈 *Credit Score:* {credit.get('score', 'N/A')}\n"
    
    pet = data.get('petInfo', {})
    if pet:
        message += f"🐾 *Pet Level:* {pet.get('level', 'N/A')}\n"
    
    return message

# ========== CHANNEL VERIFICATION ==========
async def check_channel_membership(user_id: int, bot) -> Tuple[bool, List[Dict]]:
    """Check if user is member of all required channels"""
    missing_channels = []
    
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel['id'], user_id=user_id)
            if member.status in ['left', 'kicked']:
                missing_channels.append(channel)
        except BadRequest as e:
            if "user not found" in str(e).lower() or "chat not found" in str(e).lower():
                missing_channels.append(channel)
            else:
                logging.error(f"Error checking channel membership: {e}")
        except Exception as e:
            logging.error(f"Unexpected error checking channel: {e}")
            missing_channels.append(channel)
    
    return len(missing_channels) == 0, missing_channels

def create_channel_buttons(missing_channels: List[Dict]) -> InlineKeyboardMarkup:
    """Create inline buttons for missing channels"""
    keyboard = []
    row = []
    for i, channel in enumerate(missing_channels, 1):
        row.append(
            InlineKeyboardButton(
                f"📢 {channel['name']}", 
                url=channel['url']
            )
        )
        if i % 2 == 0:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    keyboard.append([
        InlineKeyboardButton(
            "✅ Verify Membership", 
            callback_data='check_channels'
        )
    ])
    
    return InlineKeyboardMarkup(keyboard)

def create_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Create the main 2x2 grid inline keyboard"""
    keyboard = [
        [
            InlineKeyboardButton("📱 Phone", callback_data='lookup_phone'),
            InlineKeyboardButton("📧 Email", callback_data='lookup_email')
        ],
        [
            InlineKeyboardButton("🚗 Vehicle", callback_data='lookup_vehicle'),
            InlineKeyboardButton("🏦 IFSC", callback_data='lookup_ifsc')
        ],
        [
            InlineKeyboardButton("🔢 IMEI", callback_data='lookup_imei'),
            InlineKeyboardButton("🌐 IP", callback_data='lookup_ip')
        ],
        [
            InlineKeyboardButton("🎮 FF Ban", callback_data='lookup_ff_ban'),
            InlineKeyboardButton("🎮 FF Info", callback_data='lookup_ff_info')
        ],
        [
            InlineKeyboardButton("📄 PAN", callback_data='lookup_pan'),
            InlineKeyboardButton("🇵🇰 Pakistan", callback_data='lookup_pak')
        ],
        [
            InlineKeyboardButton("🆔 Aadhaar", callback_data='lookup_aadhaar'),
            InlineKeyboardButton("💳 Redeem Code", callback_data='redeem_code')
        ],
        [
            InlineKeyboardButton("💎 My Credits", callback_data='my_credits'),
            InlineKeyboardButton("ℹ️ Help", callback_data='help')
        ]
    ]
    
    if user_id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("⚙️ Admin", callback_data='admin_panel')])
    
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    
    # Check if bot is locked
    if check_bot_lock() and user.id not in ADMIN_IDS:
        await update.message.reply_text(
            "🔒 *Bot is Temporarily Locked*\n\n"
            "The bot is currently under maintenance.\n"
            "Please try again later.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Clear any previous context
    if 'awaiting_input' in context.user_data:
        del context.user_data['awaiting_input']
    
    # Check channel membership first
    is_member, missing_channels = await check_channel_membership(user.id, context.bot)
    
    if not is_member:
        channel_message = f"👋 Welcome {user.first_name}!\n\n"
        channel_message += "📢 Channel Membership Required\n\n"
        channel_message += "To use DARKOSINT Bot, you must join these channels:\n\n"
        
        for i, channel in enumerate(missing_channels, 1):
            channel_message += f"{i}. {channel['name']}\n"
        
        channel_message += "\n👇 Join all channels then click Verify Membership"
        
        keyboard = create_channel_buttons(missing_channels)
        
        if update.callback_query:
            try:
                await update.callback_query.message.edit_text(
                    channel_message,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=keyboard
                )
            except:
                await update.callback_query.message.reply_text(
                    channel_message,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=keyboard
                )
        else:
            await update.message.reply_text(
                channel_message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard
            )
        return
    
    # User has joined all channels, show main menu
    await show_main_menu(update, context, is_from_start=True)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, is_from_start=False):
    """Show main menu with user info and options"""
    user = update.effective_user
    
    # Clear any previous context
    if 'awaiting_input' in context.user_data:
        del context.user_data['awaiting_input']
    
    # Get user credits
    credits, reset_time = get_user_credits(user.id)
    
    # Check if user is admin
    is_admin = user.id in ADMIN_IDS
    
    if is_from_start:
        if is_admin:
            welcome_msg = f"""👋 Hey *{user.first_name}* 👑

*ADMIN MODE* 🛡️

🆔 Your ID: `{user.id}`
💎 Your Credits: {credits}

Choose a lookup option below or use Admin Panel 👇"""
        else:
            welcome_msg = f"""👋 Hey {user.first_name}!

Welcome To DARKOSINT Bot 🥳

🆔 Your ID: `{user.id}`
💎 Your Credits: {credits}

Choose a lookup option below to get started 👇"""
    else:
        if is_admin:
            welcome_msg = f"""👋 Welcome back *{user.first_name}* 👑

*ADMIN MODE* 🛡️

🆔 Your ID: `{user.id}`
💎 Your Credits: {credits}

Choose a lookup option below 👇"""
        else:
            welcome_msg = f"""👋 Welcome back {user.first_name}!

🆔 Your ID: `{user.id}`
💎 Your Credits: {credits}

Choose a lookup option below 👇"""
    
    keyboard = create_main_menu_keyboard(user.id)
    
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(
                welcome_msg,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard
            )
        except Exception as e:
            logging.error(f"Error editing message: {e}")
            try:
                await update.callback_query.message.reply_text(
                    welcome_msg,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=keyboard
                )
            except:
                await update.callback_query.message.reply_text(
                    "Welcome to DARKOSINT Bot! Use the buttons below:",
                    reply_markup=keyboard
                )
    else:
        try:
            await update.message.reply_text(
                welcome_msg,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard
            )
        except:
            await update.message.reply_text(
                "Welcome to DARKOSINT Bot! Use the buttons below:",
                reply_markup=keyboard
            )

async def handle_inline_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Check if bot is locked (except for admin and specific actions)
    allowed_actions = ['dashboard', 'check_channels', 'my_credits', 'help', 'admin_panel']
    if check_bot_lock() and user_id not in ADMIN_IDS and query.data not in allowed_actions:
        try:
            await query.answer("⚠️ Bot is under maintenance", show_alert=True)
        except:
            pass
        
        lock_message = await query.message.reply_text(
            "🔒 *Bot is Temporarily Locked*\n\n"
            "The bot is currently under maintenance.\n"
            "Please try again later.\n\n"
            "Contact admin if this persists.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data='dashboard')]
            ])
        )
        
        try:
            await query.message.edit_text(
                "🔒 *Bot Locked for Maintenance*\n\n"
                "This feature is temporarily unavailable.\n"
                "Please check back later.",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
            
        return
    
    try:
        await query.answer()
    except Exception as e:
        logging.warning(f"Callback query error: {e}")
    
    button_data = query.data
    
    # Clear context when any button is pressed
    if 'awaiting_input' in context.user_data:
        del context.user_data['awaiting_input']
    
    # Handle dashboard button first
    if button_data == 'dashboard':
        await show_main_menu(update, context, is_from_start=False)
        return
    
    if button_data == 'check_channels':
        user = update.effective_user
        is_member, missing_channels = await check_channel_membership(user.id, context.bot)
        
        if not is_member:
            channel_message = "❌ Still Missing Channels\n\n"
            channel_message += "You haven't joined all required channels:\n\n"
            
            for i, channel in enumerate(missing_channels, 1):
                channel_message += f"{i}. {channel['name']}\n"
            
            channel_message += "\nPlease join all channels and try again."
            
            keyboard = create_channel_buttons(missing_channels)
            
            await query.message.edit_text(
                channel_message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard
            )
            return
        
        await show_main_menu(update, context, is_from_start=True)
        return
    
    elif button_data.startswith('lookup_'):
        if check_bot_lock() and user_id not in ADMIN_IDS:
            try:
                await query.answer("⚠️ Bot is under maintenance", show_alert=True)
            except:
                pass
            
            await query.message.edit_text(
                "🔒 *Service Temporarily Unavailable*\n\n"
                "Lookup services are currently under maintenance.\n"
                "Please try again later.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Dashboard", callback_data='dashboard')]
                ])
            )
            return
        
        lookup_type = button_data.replace('lookup_', '')
        
        prompts = {
            'phone': "📱 Send me a phone number to lookup:",
            'email': "📧 Send me an email address to lookup:",
            'imei': "🔢 Send me a 15-digit IMEI number:",
            'ifsc': "🏦 Send me an IFSC code:",
            'vehicle': "🚗 Send me a vehicle number:",
            'ff_ban': "🎮 Send me a Free Fire UID for ban check:",
            'ff_info': "🎮 Send me a Free Fire UID for account info:",
            'ip': "🌐 Send me an IP address:",
            'pan': "📄 Send me a PAN number:",
            'pak': "🇵🇰 Send me a Pakistan number:",
            'aadhaar': "🆔 Send me an Aadhaar number:"
        }
        
        if lookup_type in prompts:
            context.user_data['awaiting_input'] = lookup_type
            await query.message.reply_text(
                prompts[lookup_type],
                parse_mode=ParseMode.MARKDOWN
            )
        return
    
    elif button_data == 'redeem_code':
        if check_bot_lock() and user_id not in ADMIN_IDS:
            try:
                await query.answer("⚠️ Bot is under maintenance", show_alert=True)
            except:
                pass
            
            await query.message.edit_text(
                "🔒 *Redemption Service Unavailable*\n\n"
                "Code redemption is currently under maintenance.\n"
                "Please try again later.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Dashboard", callback_data='dashboard')]
                ])
            )
            return
        
        await query.message.reply_text(
            "💳 Send me your redeem code:",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['awaiting_input'] = 'redeem'
        return
    
    elif button_data == 'my_credits':
        user_id = update.effective_user.id
        credits, reset_time = get_user_credits(user_id)
        
        time_until_reset = reset_time + timedelta(days=1) - datetime.now()
        hours, remainder = divmod(int(time_until_reset.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        
        credits_msg = f"""💎 *Your Credits Status*

📊 *Available:* {credits}
⏰ *Reset In:* {hours}h {minutes}m
📅 *Daily Credits:* {DEFAULT_DAILY_CREDITS}"""
        
        await query.message.reply_text(credits_msg, parse_mode=ParseMode.MARKDOWN)
        return
    
    elif button_data == 'help':
        await show_help(update, context)
        return
    
    elif button_data == 'admin_panel':
        if user_id not in ADMIN_IDS:
            await query.message.reply_text(
                "❌ Access Denied!",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        await show_admin_panel(update, context)
        return
    
    elif button_data.startswith('admin_'):
        await handle_admin_command(update, context, button_data)
        return

async def handle_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str):
    """Handle admin panel commands"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await query.message.reply_text(
            "❌ Access Denied!",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if command == 'admin_broadcast':
        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm", callback_data='admin_broadcast_confirm'),
                InlineKeyboardButton("❌ Cancel", callback_data='admin_panel')
            ]
        ])
        
        await query.message.edit_text(
            "📢 *Broadcast Confirmation*\n\n"
            "Are you sure you want to send a broadcast?\n\n"
            "*Note:* This will send a message to ALL users in the database.\n"
            "You will be asked for the message content next.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=confirm_keyboard
        )
        return
    
    elif command == 'admin_broadcast_confirm':
        await query.message.reply_text(
            "📢 *Broadcast Message*\n\n"
            "Send the message you want to broadcast to all users.\n"
            "You can use markdown formatting.\n\n"
            "*Available commands:*\n"
            "• `/cancel` - Cancel broadcast\n"
            "• `/preview` - Preview message before sending",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['admin_action'] = 'broadcast'
        return
    
    elif command == 'admin_broadcast_send':
        broadcast_message = context.user_data.get('last_broadcast_message')
        
        if not broadcast_message:
            await query.message.reply_text(
                "❌ No broadcast message found!\n"
                "Please start the broadcast process again.",
                parse_mode=ParseMode.MARKDOWN
            )
            await show_admin_panel(update, context)
            return
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users')
        all_users = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        total_users = len(all_users)
        
        progress_msg = await query.message.reply_text(
            f"""
📢 *Broadcast Starting*

◇ Total Users: {total_users}
◇ Successful: 0
◇ Blocked Users: 0
◇ Deleted Accounts: 0
◇ Errors: 0
◇ Remaining: {total_users}

Progress: 0/{total_users}
*Please wait...*
            """,
            parse_mode=ParseMode.MARKDOWN
        )
        
        stats = {
            'success': 0,
            'blocked': 0,
            'deleted': 0,
            'error': 0,
            'processed': 0,
            'remaining': total_users
        }
        
        async def update_progress():
            try:
                text = f"""
📢 *Broadcast in Progress*

◇ Total Users: {total_users}
◇ Successful: {stats['success']}
◇ Blocked Users: {stats['blocked']}
◇ Deleted Accounts: {stats['deleted']}
◇ Errors: {stats['error']}
◇ Remaining: {stats['remaining']}

Progress: {stats['processed']}/{total_users}
                """
                
                await progress_msg.edit_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        
        for user_id in all_users:
            try:
                if user_id in ADMIN_IDS:
                    stats['processed'] += 1
                    stats['remaining'] -= 1
                    continue
                
                await context.bot.send_message(
                    chat_id=user_id,
                    text=broadcast_message,
                    parse_mode=ParseMode.MARKDOWN
                )
                stats['success'] += 1
                
            except BadRequest as e:
                if "Forbidden" in str(e):
                    stats['blocked'] += 1
                elif "user not found" in str(e):
                    stats['deleted'] += 1
                else:
                    stats['error'] += 1
            except Exception as e:
                stats['error'] += 1
            
            stats['processed'] += 1
            stats['remaining'] -= 1
            
            if stats['processed'] % 5 == 0 or stats['remaining'] == 0:
                await update_progress()
            
            await asyncio.sleep(0.2)
        
        await update_progress()
        
        success_rate = (stats['success'] / total_users * 100) if total_users > 0 else 0
        
        final_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📢 New Broadcast", callback_data='admin_broadcast'),
                InlineKeyboardButton("📊 Stats", callback_data='admin_stats')
            ],
            [
                InlineKeyboardButton("🏠 Admin Panel", callback_data='admin_panel')
            ]
        ])
        
        final_text = f"""
✅ *Broadcast Completed!*

📊 *Summary:*
• Total Users: {total_users}
• ✅ Successful: {stats['success']}
• 🚫 Blocked: {stats['blocked']}
• 🗑️ Deleted: {stats['deleted']}
• ❌ Errors: {stats['error']}

📤 Success Rate: {success_rate:.1f}%
        """
        
        try:
            await query.message.edit_text(
                final_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=final_keyboard
            )
        except:
            await query.message.reply_text(
                final_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=final_keyboard
            )
        
        if 'last_broadcast_message' in context.user_data:
            del context.user_data['last_broadcast_message']
        
        return
    
    elif command == 'admin_broadcast_edit':
        await query.message.reply_text(
            "📝 *Edit Broadcast Message*\n\n"
            "Send the updated message:",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['admin_action'] = 'broadcast'
        return
    
    elif command == 'admin_lock':
        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Lock Bot", callback_data='admin_lock_confirm'),
                InlineKeyboardButton("❌ No, Cancel", callback_data='admin_panel')
            ]
        ])
        
        await query.message.edit_text(
            "🔒 *Confirm Bot Lock*\n\n"
            "Are you sure you want to lock the bot?\n\n"
            "*Effects:*\n"
            "• Users will see 'Bot is under maintenance' message\n"
            "• Lookup services will be unavailable\n"
            "• Only admins can use the bot\n\n"
            "Bot will remain locked until you unlock it.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=confirm_keyboard
        )
        return
    
    elif command == 'admin_lock_confirm':
        await lock_bot()
        
        test_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🧪 Test Lock", callback_data='admin_test_lock'),
                InlineKeyboardButton("🔓 Unlock Now", callback_data='admin_unlock')
            ],
            [
                InlineKeyboardButton("📊 Stats", callback_data='admin_stats'),
                InlineKeyboardButton("🏠 Admin Panel", callback_data='admin_panel')
            ]
        ])
        
        await query.message.edit_text(
            "✅ *Bot Successfully Locked!*\n\n"
            "🔒 *Status:* **LOCKED**\n\n"
            "*What happens now:*\n"
            "• Non-admin users will see maintenance message\n"
            "• Lookup services are disabled for users\n"
            "• Admin functions remain available\n\n"
            "You can test the lock or unlock the bot when ready.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=test_keyboard
        )
        return
    
    elif command == 'admin_test_lock':
        test_message = await query.message.reply_text(
            "🧪 *Testing Bot Lock...*\n\n"
            "This is what users will see when bot is locked:",
            parse_mode=ParseMode.MARKDOWN
        )
        
        await asyncio.sleep(1)
        
        await test_message.edit_text(
            "🔒 *Bot is Temporarily Locked*\n\n"
            "The bot is currently under maintenance.\n"
            "Please try again later.\n\n"
            "Contact admin if this persists.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data='dashboard')]
            ])
        )
        
        await asyncio.sleep(2)
        await query.message.reply_text(
            "✅ *Lock Test Complete*\n\n"
            "The lock is working correctly. Users will see the maintenance message.\n\n"
            "You can unlock the bot when maintenance is complete.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔓 Unlock Bot", callback_data='admin_unlock')],
                [InlineKeyboardButton("🏠 Admin Panel", callback_data='admin_panel')]
            ])
        )
        return
    
    elif command == 'admin_unlock':
        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Unlock Bot", callback_data='admin_unlock_confirm'),
                InlineKeyboardButton("❌ No, Keep Locked", callback_data='admin_panel')
            ]
        ])
        
        await query.message.edit_text(
            "🔓 *Confirm Bot Unlock*\n\n"
            "Are you sure you want to unlock the bot?\n\n"
            "*Effects:*\n"
            "• All services will be available to users\n"
            "• Users can perform lookups again\n"
            "• Normal bot operation resumes\n\n"
            "Proceed with unlock?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=confirm_keyboard
        )
        return
    
    elif command == 'admin_unlock_confirm':
        await unlock_bot()
        
        await query.message.edit_text(
            "✅ *Bot Successfully Unlocked!*\n\n"
            "🔓 *Status:* **UNLOCKED**\n\n"
            "*What happens now:*\n"
            "• All services are available to users\n"
            "• Users can perform lookups\n"
            "• Normal operation has resumed\n\n"
            "The bot is now ready for use.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 Stats", callback_data='admin_stats'),
                    InlineKeyboardButton("🏠 Admin Panel", callback_data='admin_panel')
                ]
            ])
        )
        return
    
    elif command == 'admin_add_code':
        options_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➕ Single Use", callback_data='admin_add_code_single'),
                InlineKeyboardButton("➕ Multi Use", callback_data='admin_add_code_multi')
            ],
            [
                InlineKeyboardButton("➕ Unlimited Use", callback_data='admin_add_code_unlimited'),
                InlineKeyboardButton("❌ Cancel", callback_data='admin_panel')
            ]
        ])
        
        await query.message.edit_text(
            "➕ *Add Redeem Code*\n\n"
            "Choose the type of code to create:\n\n"
            "🔹 *Single Use* - One user can redeem\n"
            "🔹 *Multi Use* - Multiple users can redeem\n"
            "🔹 *Unlimited* - No usage limit\n\n"
            "Select an option:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=options_keyboard
        )
        return
    
    elif command == 'admin_add_code_single':
        await query.message.reply_text(
            "➕ *Create Single-Use Code*\n\n"
            "Send code details in format:\n"
            "`CODE:CREDITS`\n\n"
            "*Example:*\n"
            "`PREMIUM100:100` - Gives 100 credits, single use\n\n"
            "*Commands:*\n"
            "• `/cancel` - Cancel creation\n"
            "• `/list` - List existing codes",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['admin_action'] = 'add_code_single'
        return
    
    elif command == 'admin_add_code_multi':
        await query.message.reply_text(
            "➕ *Create Multi-Use Code*\n\n"
            "Send code details in format:\n"
            "`CODE:CREDITS:MAX_USES`\n\n"
            "*Examples:*\n"
            "`WELCOME10:10:50` - 50 users, 10 credits each\n"
            "`SPECIAL20:20:100` - 100 users, 20 credits each\n\n"
            "*Commands:*\n"
            "• `/cancel` - Cancel creation\n"
            "• `/list` - List existing codes",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['admin_action'] = 'add_code_multi'
        return
    
    elif command == 'admin_add_code_unlimited':
        await query.message.reply_text(
            "➕ *Create Unlimited-Use Code*\n\n"
            "Send code details in format:\n"
            "`CODE:CREDITS`\n\n"
            "*Examples:*\n"
            "`BONUS5:5` - Unlimited users, 5 credits each\n"
            "`FREECREDITS:1` - Unlimited users, 1 credit each\n\n"
            "*Commands:*\n"
            "• `/cancel` - Cancel creation\n"
            "• `/list` - List existing codes",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['admin_action'] = 'add_code_unlimited'
        return
    
    elif command == 'admin_stats':
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM credit_usage')
        total_lookups = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM credit_usage WHERE date(timestamp) = date("now")')
        today_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT SUM(credits_used) FROM credit_usage')
        total_credits_used = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM credit_usage WHERE timestamp > datetime("now", "-7 days")')
        active_7days = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM redeem_codes')
        total_codes = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM redeem_codes WHERE is_active = 1')
        active_codes = cursor.fetchone()[0]
        
        cursor.execute('SELECT SUM(used_count) FROM redeem_codes')
        total_code_uses = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT SUM(credits * used_count) FROM redeem_codes')
        total_code_credits = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT COUNT(*) FROM credit_usage WHERE date(timestamp) = date("now")')
        today_lookups = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT service_type, COUNT(*) as count 
            FROM credit_usage 
            GROUP BY service_type 
            ORDER BY count DESC 
            LIMIT 1
        ''')
        popular_service = cursor.fetchone()
        
        conn.close()
        
        is_locked = check_bot_lock()
        lock_status = "🔒 LOCKED" if is_locked else "🔓 UNLOCKED"
        lock_emoji = "🔒" if is_locked else "🔓"
        
        stats_msg = f"""
{lock_emoji} *Bot Statistics* {lock_emoji}

*Status:* {lock_status}

👥 *Users:*
• Total Users: `{total_users}`
• Active (7 days): `{active_7days}`
• Today's Active: `{today_users}`

🔍 *Usage:*
• Total Lookups: `{total_lookups}`
• Today's Lookups: `{today_lookups}`
• Total Credits Used: `{total_credits_used}`
• Daily Credits: `{DEFAULT_DAILY_CREDITS}`

💳 *Redeem Codes:*
• Total Codes: `{total_codes}`
• Active Codes: `{active_codes}`
• Total Redemptions: `{total_code_uses}`
• Credits Given: `{total_code_credits}`
"""
        
        if popular_service:
            service_name, service_count = popular_service
            stats_msg += f"\n📈 *Most Popular Service:*\n• `{service_name}` - {service_count} lookups"
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Refresh Stats", callback_data='admin_stats'),
                InlineKeyboardButton("👥 View Users", callback_data='admin_users')
            ],
            [
                InlineKeyboardButton("💳 View Codes", callback_data='admin_view_codes'),
                InlineKeyboardButton("🏠 Admin Panel", callback_data='admin_panel')
            ]
        ])
        
        await query.message.edit_text(
            stats_msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )
        return
    
    elif command == 'admin_view_codes':
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT code, credits, max_uses, used_count, is_active, created_date 
            FROM redeem_codes 
            ORDER BY created_date DESC 
            LIMIT 10
        ''')
        codes = cursor.fetchall()
        
        conn.close()
        
        if not codes:
            codes_msg = "📋 *Redeem Codes*\n\nNo codes found."
        else:
            codes_msg = "📋 *Recent Redeem Codes*\n\n"
            for code_data in codes:
                code, credits, max_uses, used_count, is_active, created_date = code_data
                
                try:
                    created = datetime.fromisoformat(created_date)
                    created_str = created.strftime("%d/%m/%Y")
                except:
                    created_str = "Unknown"
                
                if max_uses == 0:
                    uses_text = "Unlimited"
                    remaining = "∞"
                else:
                    uses_text = f"{used_count}/{max_uses}"
                    remaining = max_uses - used_count
                
                status = "✅ Active" if is_active else "❌ Inactive"
                
                codes_msg += f"🔑 *Code:* `{code}`\n"
                codes_msg += f"💎 Credits: {credits}\n"
                codes_msg += f"📊 Uses: {uses_text} ({remaining} remaining)\n"
                codes_msg += f"📅 Created: {created_str}\n"
                codes_msg += f"📈 Status: {status}\n"
                codes_msg += f"━━━━━━━━━━━━━━━━\n"
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➕ Add Code", callback_data='admin_add_code'),
                InlineKeyboardButton("🔄 Refresh", callback_data='admin_view_codes')
            ],
            [
                InlineKeyboardButton("🏠 Admin Panel", callback_data='admin_panel')
            ]
        ])
        
        await query.message.edit_text(
            codes_msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )
        return
    
    elif command == 'admin_users':
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT u.user_id, u.username, u.total_credits, 
                   MAX(cu.timestamp) as last_activity,
                   COUNT(cu.id) as total_lookups
            FROM users u
            LEFT JOIN credit_usage cu ON u.user_id = cu.user_id
            GROUP BY u.user_id
            ORDER BY last_activity DESC NULLS LAST, u.join_date DESC
            LIMIT 10
        ''')
        recent_users = cursor.fetchall()
        
        conn.close()
        
        if not recent_users:
            users_msg = "👥 *Recent Users*\n\nNo users found."
        else:
            users_msg = "👥 *Recent Users (Top 10)*\n\n"
            for user in recent_users:
                user_id, username, credits, last_activity, total_lookups = user
                username = username or "No Username"
                
                if last_activity:
                    try:
                        last_activity_dt = datetime.fromisoformat(last_activity)
                        time_diff = datetime.now() - last_activity_dt
                        days = time_diff.days
                        hours = time_diff.seconds // 3600
                        
                        if days > 0:
                            last_seen = f"{days}d ago"
                        elif hours > 0:
                            last_seen = f"{hours}h ago"
                        else:
                            last_seen = "Recently"
                    except:
                        last_seen = "Unknown"
                else:
                    last_seen = "Never"
                
                users_msg += f"🆔 `{user_id}`\n"
                users_msg += f"👤 {username}\n"
                users_msg += f"💎 Credits: {credits}\n"
                users_msg += f"🔍 Lookups: {total_lookups}\n"
                users_msg += f"🕒 Last Seen: {last_seen}\n"
                users_msg += f"━━━━━━━━━━━━━━━━\n"
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 Stats", callback_data='admin_stats'),
                InlineKeyboardButton("🔄 Refresh", callback_data='admin_users')
            ],
            [
                InlineKeyboardButton("🏠 Admin Panel", callback_data='admin_panel')
            ]
        ])
        
        await query.message.edit_text(
            users_msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )
        return

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str):
    """Handle admin text commands"""
    admin_action = context.user_data.get('admin_action')
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Access Denied!")
        context.user_data.pop('admin_action', None)
        return
    
    # Handle commands
    if message_text.lower() == '/cancel':
        await update.message.reply_text(
            "❌ Operation cancelled.",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data.pop('admin_action', None)
        return
    
    elif message_text.lower() == '/list':
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT code, credits, max_uses, used_count, is_active 
            FROM redeem_codes 
            ORDER BY created_date DESC 
            LIMIT 5
        ''')
        codes = cursor.fetchall()
        
        conn.close()
        
        if not codes:
            list_msg = "📋 *Existing Codes*\n\nNo codes found."
        else:
            list_msg = "📋 *Recent Codes (Last 5)*\n\n"
            for code_data in codes:
                code, credits, max_uses, used_count, is_active = code_data
                
                if max_uses == 0:
                    uses_text = "Unlimited"
                    remaining = "∞"
                else:
                    uses_text = f"{used_count}/{max_uses}"
                    remaining = max_uses - used_count
                
                status = "✅" if is_active else "❌"
                
                list_msg += f"{status} `{code}`\n"
                list_msg += f"   Credits: {credits}\n"
                list_msg += f"   Uses: {uses_text} ({remaining} left)\n"
                list_msg += f"   ─────\n"
        
        await update.message.reply_text(
            list_msg,
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    elif message_text.lower() == '/preview' and admin_action == 'broadcast':
        preview_msg = context.user_data.get('last_broadcast_message', 'No message to preview')
        await update.message.reply_text(
            f"📝 *Broadcast Preview*\n\n{preview_msg}",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if admin_action == 'broadcast':
        context.user_data['last_broadcast_message'] = message_text
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        conn.close()
        
        preview_text = message_text[:300] + "..." if len(message_text) > 300 else message_text
        
        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Send Now", callback_data='admin_broadcast_send'),
                InlineKeyboardButton("✏️ Edit", callback_data='admin_broadcast_edit')
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data='admin_panel')
            ]
        ])
        
        await update.message.reply_text(
            f"📢 *Broadcast Ready*\n\n"
            f"*Message:*\n{preview_text}\n\n"
            f"*Recipients:* {total_users} users\n\n"
            f"*Options:*\n"
            f"• Send Now - Start broadcasting immediately\n"
            f"• Edit - Modify the message\n"
            f"• Cancel - Go back to admin panel",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=confirm_keyboard
        )
        
        return
    
    elif admin_action in ['add_code_single', 'add_code_multi', 'add_code_unlimited']:
        try:
            if admin_action == 'add_code_single':
                parts = message_text.split(':')
                if len(parts) != 2:
                    raise ValueError("Use format: CODE:CREDITS")
                
                code = parts[0].strip().upper()
                credits = int(parts[1].strip())
                max_uses = 1
                
            elif admin_action == 'add_code_multi':
                parts = message_text.split(':')
                if len(parts) != 3:
                    raise ValueError("Use format: CODE:CREDITS:MAX_USES")
                
                code = parts[0].strip().upper()
                credits = int(parts[1].strip())
                max_uses = int(parts[2].strip())
                
                if max_uses < 2:
                    raise ValueError("Multi-use code must have at least 2 uses")
                
            elif admin_action == 'add_code_unlimited':
                parts = message_text.split(':')
                if len(parts) != 2:
                    raise ValueError("Use format: CODE:CREDITS")
                
                code = parts[0].strip().upper()
                credits = int(parts[1].strip())
                max_uses = 0
            
            if not code.isalnum():
                raise ValueError("Code must contain only letters and numbers")
            if len(code) < 3:
                raise ValueError("Code must be at least 3 characters")
            if len(code) > 20:
                raise ValueError("Code must be 20 characters or less")
            
            if credits < 1:
                raise ValueError("Credits must be at least 1")
            if credits > 1000:
                raise ValueError("Credits cannot exceed 1000")
            
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            
            cursor.execute('SELECT id FROM redeem_codes WHERE code = ?', (code,))
            if cursor.fetchone():
                conn.close()
                await update.message.reply_text(
                    f"❌ Code `{code}` already exists!",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            cursor.execute(
                '''INSERT INTO redeem_codes (code, credits, max_uses, created_by) 
                   VALUES (?, ?, ?, ?)''',
                (code, credits, max_uses, user_id)
            )
            
            conn.commit()
            conn.close()
            
            if max_uses == 0:
                uses_text = "Unlimited uses"
            elif max_uses == 1:
                uses_text = "Single use"
            else:
                uses_text = f"{max_uses} uses"
            
            await update.message.reply_text(
                f"✅ *Redeem Code Created Successfully!*\n\n"
                f"🔑 *Code:* `{code}`\n"
                f"💎 *Credits:* {credits}\n"
                f"👥 *Type:* {uses_text}\n\n"
                f"*Code is now active!*\n"
                f"Users can redeem it using the Redeem Code button.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("➕ Add Another", callback_data='admin_add_code'),
                        InlineKeyboardButton("📋 View Codes", callback_data='admin_view_codes')
                    ],
                    [
                        InlineKeyboardButton("🏠 Admin Panel", callback_data='admin_panel')
                    ]
                ])
            )
            
        except ValueError as e:
            await update.message.reply_text(
                f"❌ *Invalid Input!*\n\n"
                f"Error: {str(e)}\n\n"
                f"Please check the format and try again.",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logging.error(f"Error creating code: {e}")
            await update.message.reply_text(
                f"❌ *Error creating code:*\n\n`{str(e)}`",
                parse_mode=ParseMode.MARKDOWN
            )
        
        context.user_data.pop('admin_action', None)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages for lookups"""
    user_id = update.effective_user.id
    
    # Check if bot is locked
    if check_bot_lock() and user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "🔒 *Bot is Temporarily Locked*\n\n"
            "The bot is currently under maintenance.\n"
            "Please try again later.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    message_text = update.message.text.strip()
    awaiting = context.user_data.get('awaiting_input')
    
    # Check for admin actions
    if 'admin_action' in context.user_data:
        await handle_admin_message(update, context, message_text)
        return
    
    # If no context and message is /start, handle it
    if not awaiting and message_text.startswith('/'):
        return
    
    # If no context, show main menu
    if not awaiting:
        await show_main_menu(update, context, is_from_start=False)
        return
    
    # Check if this is a redeem code
    if awaiting == 'redeem':
        await handle_redeem_code(update, context, message_text)
        await asyncio.sleep(2)
        await show_main_menu(update, context, is_from_start=False)
        return
    
    # Check if user is still in required channels
    is_member, _ = await check_channel_membership(user_id, context.bot)
    if not is_member:
        await update.message.reply_text(
            "❌ Access Denied!\n\n"
            "You have left required channels.\n"
            "Use /start to rejoin.",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data.pop('awaiting_input', None)
        return
    
    # Validate input format
    is_valid, error_msg = validate_input(awaiting, message_text)
    if not is_valid:
        await update.message.reply_text(
            error_msg,
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data.pop('awaiting_input', None)
        return
    
    # Check credits for paid services
    if awaiting in CREDIT_COSTS:
        credits, _ = get_user_credits(user_id)
        if credits < CREDIT_COSTS[awaiting]:
            await update.message.reply_text(
                f"❌ Insufficient Credits!\n\n"
                f"Required: {CREDIT_COSTS[awaiting]} credits\n"
                f"Available: {credits} credits\n\n"
                f"Wait for reset or redeem code.",
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data.pop('awaiting_input', None)
            return
    
    # Show processing message
    processing_msg = await update.message.reply_text(
        "🔄 Processing your request...\n"
        "Please wait...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    try:
        # Call appropriate API based on lookup type
        api_data = None
        
        if awaiting == 'phone':
            api_data = await phone_lookup_api(message_text)
        elif awaiting == 'aadhaar':
            api_data = await aadhaar_lookup_api(message_text)
        elif awaiting == 'ip':
            api_data = await ip_lookup_api(message_text)
        elif awaiting == 'pan':
            api_data = await pan_lookup_api(message_text)
        elif awaiting == 'pak':
            api_data = await pak_lookup_api(message_text)
        elif awaiting == 'imei':
            api_data = await imei_lookup_api(message_text)
        elif awaiting == 'email':
            api_data = await email_lookup_api(message_text)
        elif awaiting == 'ifsc':
            api_data = await ifsc_lookup_api(message_text)
        elif awaiting == 'vehicle':
            api_data = await vehicle_lookup_api(message_text)
        elif awaiting == 'ff_ban':
            api_data = await ff_ban_lookup_api(message_text)
        elif awaiting == 'ff_info':
            api_data = await ff_info_lookup_api(message_text)
        
        if not api_data:
            await processing_msg.edit_text(
                "❌ API Error!\n\nNo response received from the API.",
                parse_mode=ParseMode.MARKDOWN
            )
            await asyncio.sleep(1)
            await show_main_menu(update, context, is_from_start=False)
            return
        
        # Check for API success based on API type
        success = False
        error_detail = "Unknown error"
        
        if awaiting == 'ip':
            success = api_data.get('status') == 'success'
            if not success:
                error_detail = api_data.get('message', 'IP lookup failed')
        
        elif awaiting == 'ff_info':
            success = 'basicInfo' in api_data
            if not success:
                error_detail = "Account not found"
        
        elif awaiting == 'phone':
            success = api_data.get('success', False) and 'result' in api_data
            if success:
                results = api_data.get('result', [])
                success = len(results) > 0
                if not success:
                    error_detail = "Phone number not found"
            else:
                error_detail = api_data.get('error', 'Phone lookup failed')
        
        elif awaiting == 'aadhaar':
            success = api_data.get('success', False) and 'data' in api_data
            if success:
                data_section = api_data.get('data', {})
                success = data_section.get('success', False) and 'results' in data_section
                if success:
                    results = data_section.get('results', [])
                    success = len(results) > 0
                    if not success:
                        error_detail = "No family details found"
                else:
                    error_detail = "Aadhaar lookup failed"
            else:
                error_detail = "Aadhaar API error"
        
        elif awaiting == 'pan':
            success = api_data.get('success', False) or 'fullName' in api_data or 'firstName' in api_data
            if not success:
                error_detail = api_data.get('error', 'PAN not found or invalid')
        
        elif awaiting == 'pak':
            success = api_data.get('success', False) and 'results' in api_data
            if success:
                results = api_data.get('results', [])
                success = len(results) > 0
                if not success:
                    error_detail = "Pakistan number not found"
            else:
                error_detail = "Pakistan lookup failed"
        
        else:
            success = api_data.get('success', False)
            if not success:
                if 'error' in api_data:
                    error_detail = api_data['error']
                elif 'message' in api_data:
                    error_detail = api_data['message']
                else:
                    error_detail = "API request failed"
        
        if not success:
            error_detail = error_detail.replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
            await processing_msg.edit_text(
                f"❌ Error!\n\n{error_detail}",
                parse_mode=ParseMode.MARKDOWN
            )
            await asyncio.sleep(1)
            await show_main_menu(update, context, is_from_start=False)
            return
        
        # Deduct credits
        if not use_credits(user_id, awaiting, message_text):
            await processing_msg.edit_text(
                "❌ Credit Deduction Failed!",
                parse_mode=ParseMode.MARKDOWN
            )
            await asyncio.sleep(1)
            await show_main_menu(update, context, is_from_start=False)
            return
        
        # Format response based on API type
        formatted_response = ""
        
        formatting_functions = {
            'phone': format_phone_result,
            'email': format_email_result,
            'imei': format_imei_result,
            'ifsc': format_ifsc_result,
            'vehicle': format_vehicle_result,
            'ff_ban': format_ff_ban_result,
            'ff_info': format_ff_info_result,
            'ip': format_ip_result,
            'pan': format_pan_result,
            'pak': format_pak_result,
            'aadhaar': format_aadhaar_result
        }
        
        if awaiting in formatting_functions:
            formatted_response = formatting_functions[awaiting](api_data)
        else:
            formatted_response = f"📊 Results for: `{message_text}`\n\n"
            formatted_response += "```json\n" + json.dumps(api_data, indent=2) + "\n```"
        
        # Add footer with credits info
        remaining_credits, _ = get_user_credits(user_id)
        formatted_response += f"\n💎 Credits used: {CREDIT_COSTS.get(awaiting, 1)}\n"
        formatted_response += f"💎 Remaining: {remaining_credits}"
        
        await processing_msg.edit_text(
            formatted_response,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Clear the awaiting input
        context.user_data.pop('awaiting_input', None)
        
        # Show dashboard button after results
        await asyncio.sleep(1)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Back to Dashboard", callback_data='dashboard')]
        ])
        await update.message.reply_text(
            "Return to dashboard:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logging.error(f"Error processing {awaiting} lookup: {e}")
        await processing_msg.edit_text(
            f"❌ Error!\n\nTry again or contact admin.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Clear context and show dashboard
        context.user_data.pop('awaiting_input', None)
        await asyncio.sleep(1)
        await show_main_menu(update, context, is_from_start=False)

def validate_input(api_type: str, input_text: str) -> Tuple[bool, str]:
    """Validate user input format and return (is_valid, error_message)"""
    input_text = input_text.strip()
    
    if api_type == 'phone':
        if input_text.startswith('+91'):
            return False, "❌ Please send only 10-digit number without +91"
        elif input_text.startswith('91') and len(input_text) == 12:
            return False, "❌ Please send only 10-digit number without 91"
        elif input_text.isdigit() and len(input_text) == 10:
            return True, ""
        else:
            return False, "❌ Please send a valid 10-digit Indian phone number"
    
    elif api_type == 'pak':
        if input_text.startswith('+92'):
            return False, "❌ Please send only 10-digit number without +92"
        elif input_text.startswith('92') and len(input_text) == 12:
            return False, "❌ Please send only 10-digit number without 92"
        elif input_text.isdigit() and len(input_text) == 10:
            return True, ""
        else:
            return False, "❌ Please send a valid 10-digit Pakistan number"
    
    elif api_type == 'email':
        if '@' in input_text and '.' in input_text:
            return True, ""
        else:
            return False, "❌ Please send a valid email address"
    
    elif api_type == 'imei':
        if input_text.isdigit() and len(input_text) == 15:
            return True, ""
        else:
            return False, "❌ Please send a valid 15-digit IMEI number"
    
    elif api_type == 'ifsc':
        if len(input_text) == 11 and input_text[:4].isalpha():
            return True, ""
        else:
            return False, "❌ Please send a valid IFSC code (11 characters)"
    
    elif api_type == 'vehicle':
        if len(input_text) >= 10:
            return True, ""
        else:
            return False, "❌ Please send a valid vehicle number"
    
    elif api_type in ['ff_ban', 'ff_info']:
        if input_text.isdigit():
            return True, ""
        else:
            return False, "❌ Please send a valid numeric UID"
    
    elif api_type == 'pan':
        if len(input_text) == 10:
            return True, ""
        else:
            return False, "❌ Please send a valid 10-character PAN number"
    
    elif api_type == 'aadhaar':
        if input_text.isdigit() and len(input_text) == 12:
            return True, ""
        else:
            return False, "❌ Please send a valid 12-digit Aadhaar number"
    
    elif api_type == 'ip':
        parts = input_text.split('.')
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return True, ""
        else:
            return False, "❌ Please send a valid IP address (e.g., 8.8.8.8)"
    
    return True, ""

async def handle_redeem_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    """Handle redeem code input with multi-use support"""
    user_id = update.effective_user.id
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute(
        '''SELECT id, code, credits, max_uses, used_count 
           FROM redeem_codes 
           WHERE code = ? AND is_active = 1''',
        (code.upper(),)
    )
    
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        await update.message.reply_text(
            f"❌ Invalid Code!\n\n"
            f"`{code}` is not valid or inactive.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    code_id, code_text, credits_to_add, max_uses, used_count = result
    
    if max_uses > 0 and used_count >= max_uses:
        conn.close()
        await update.message.reply_text(
            f"❌ Code Limit Reached!\n\n"
            f"`{code_text}` has been used {used_count}/{max_uses} times.\n"
            f"No more redemptions available.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    cursor.execute(
        '''SELECT id FROM redeem_code_usage 
           WHERE code_id = ? AND user_id = ?''',
        (code_id, user_id)
    )
    
    if cursor.fetchone():
        conn.close()
        await update.message.reply_text(
            f"❌ Already Redeemed!\n\n"
            f"You have already used code `{code_text}`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        cursor.execute(
            '''UPDATE redeem_codes SET used_count = used_count + 1 
               WHERE id = ?''',
            (code_id,)
        )
        
        cursor.execute(
            '''INSERT INTO redeem_code_usage (code_id, user_id) 
               VALUES (?, ?)''',
            (code_id, user_id)
        )
        
        cursor.execute(
            '''UPDATE users SET total_credits = total_credits + ? 
               WHERE user_id = ?''',
            (credits_to_add, user_id)
        )
        
        conn.commit()
        
        cursor.execute(
            '''SELECT used_count, max_uses FROM redeem_codes WHERE id = ?''',
            (code_id,)
        )
        new_used_count, max_uses = cursor.fetchone()
        
        cursor.execute(
            'SELECT total_credits FROM users WHERE user_id = ?',
            (user_id,)
        )
        new_credits = cursor.fetchone()[0]
        
        conn.close()
        
        if max_uses == 0:
            uses_info = "Unlimited uses remaining"
        else:
            remaining = max_uses - new_used_count
            uses_info = f"{remaining}/{max_uses} uses remaining"
        
        await update.message.reply_text(
            f"🎉 *Code Redeemed Successfully!*\n\n"
            f"✅ *Code:* `{code_text}`\n"
            f"💎 *Added:* +{credits_to_add} credits\n"
            f"💰 *Total:* {new_credits} credits\n"
            f"📊 *Usage:* {uses_info}",
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        conn.rollback()
        conn.close()
        logging.error(f"Error redeeming code: {e}")
        await update.message.reply_text(
            f"❌ Error redeeming code!\n\n"
            f"Please try again or contact admin.",
            parse_mode=ParseMode.MARKDOWN
        )

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help information"""
    help_text = """
🤖 *DARKOSINT Bot Help*

📋 *Commands:*
/start - Start bot
/help - Show help
/credits - Check credits

🔍 *Lookup Types:*
• Phone - Indian numbers (10 digits)
• Email - Address lookup
• Vehicle - Registration
• IFSC - Bank codes
• IMEI - Device info
• IP - Address info
• FF Ban - Game status
• FF Info - Account details
• PAN - Card details
• Pakistan - Numbers (10 digits)
• Aadhaar - Validation

💎 *Credit System:*
- Daily: 10 free credits
- Resets every 24h
- Each lookup costs credits
- Redeem codes for more

📝 *Important:*
• Send only 10-digit numbers for phone/Pakistan
• Don't include +91 or 91 for Indian numbers
• Don't include +92 or 92 for Pakistan numbers

🆘 *Support:*
Contact admin for help.
"""
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Back to Dashboard", callback_data='dashboard')]
    ])
    
    if update.callback_query:
        await update.callback_query.message.reply_text(
            help_text, 
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            help_text, 
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin panel for authorized users"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await query.message.reply_text(
            "❌ Access Denied!",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    is_locked = check_bot_lock()
    lock_status = "🔒 LOCKED" if is_locked else "🔓 UNLOCKED"
    
    lock_button_text = "🔓 Unlock Bot" if is_locked else "🔒 Lock Bot"
    lock_button_callback = 'admin_unlock' if is_locked else 'admin_lock'
    
    keyboard = [
        [
            InlineKeyboardButton("📢 Broadcast", callback_data='admin_broadcast'),
            InlineKeyboardButton(lock_button_text, callback_data=lock_button_callback)
        ],
        [
            InlineKeyboardButton("➕ Add Code", callback_data='admin_add_code'),
            InlineKeyboardButton("📊 Stats", callback_data='admin_stats')
        ],
        [
            InlineKeyboardButton("👥 Users", callback_data='admin_users'),
            InlineKeyboardButton("🏠 Dashboard", callback_data='dashboard')
        ]
    ]
    
    admin_text = f"""
⚙️ *Admin Panel*

📋 *Actions:*
• Broadcast - Send message to all users
• Lock/Unlock - Control bot access
• Add Code - Create redeem codes
• Stats - View bot statistics
• Users - View recent users

*Current Status:* {lock_status}
    """
    
    await query.message.edit_text(
        admin_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user credits"""
    user_id = update.effective_user.id
    credits, reset_time = get_user_credits(user_id)
    
    time_until_reset = reset_time + timedelta(days=1) - datetime.now()
    hours, remainder = divmod(int(time_until_reset.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    
    credits_msg = f"""
💎 *Your Credits Status*

📊 *Available:* {credits}
⏰ *Reset In:* {hours}h {minutes}m
📅 *Daily:* {DEFAULT_DAILY_CREDITS}
"""
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Back to Dashboard", callback_data='dashboard')]
    ])
    
    await update.message.reply_text(
        credits_msg, 
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logging.error(f"Update {update} caused error {context.error}")
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again.",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass

# ========== MAIN FUNCTION ==========
def main():
    """Start the bot"""
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    
    init_database()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_error_handler(error_handler)
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", show_help))
    application.add_handler(CommandHandler("credits", credits_command))
    
    application.add_handler(CallbackQueryHandler(handle_inline_button))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print(f"🤖 OSINT Bot Starting...")
    print(f"👑 Admin IDs: {ADMIN_IDS}")
    print(f"🔗 Channels: {len(REQUIRED_CHANNELS)}")
    print("🚀 Bot running. Ctrl+C to stop.")
    
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == '__main__':
    main()