# --- Auto-install python-telegram-bot if missing ---
try:
    import telegram
except ImportError:
    import sys, subprocess
    print("\n[INFO] python-telegram-bot not found. Installing...\n")
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-U', 'python-telegram-bot'], check=False)
    print("\n[INFO] Installation attempted. Please restart the bot if you still see errors.\n")
import logging
import sqlite3
import threading


def write_deploy_debug(exc: Exception = None):
    """Write a small debug file to help with remote deploy issues (paths, python, packages).
    This is intentionally lightweight and avoids printing secrets like BOT_TOKEN.
    """
    try:
        import sys, time, importlib, shutil, subprocess

        lines = []
        lines.append(f"timestamp: {time.asctime()}")
        lines.append(f"python: {sys.version}")
        lines.append(f"executable: {sys.executable}")
        # show whether telegram is importable
        try:
            spec = importlib.util.find_spec('telegram')
            lines.append(f"telegram_spec: {spec is not None}")
        except Exception:
            lines.append("telegram_spec: error")

        # ffmpeg/yt-dlp availability
        try:
            lines.append(f"ffmpeg: {shutil.which('ffmpeg') or shutil.which('ffmpeg.exe')}")
            lines.append(f"yt-dlp: {shutil.which('yt-dlp') or shutil.which('yt-dlp.exe')}")
        except Exception:
            pass

        # pip list (short)
        try:
            proc = subprocess.run([sys.executable, '-m', 'pip', 'list', '--format=freeze'], capture_output=True, text=True, timeout=20)
            pip_list = proc.stdout.strip().splitlines()
            # include only top-level packages we care about
            interesting = [p for p in pip_list if p.lower().startswith(('python-telegram-bot','requests','yt-dlp'))]
            lines.append('pip_list_lines:')
            lines.extend(interesting[:50])
        except Exception:
            lines.append('pip_list: failed')

        if exc:
            lines.append(f"import_exception: {repr(exc)}")

        with open('deploy_debug.txt', 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    except Exception:
        # avoid raising during startup debug
        pass
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
except Exception as e:
    # Give a clearer runtime hint when the dependency is missing.
    import sys
    # write deployment debug info so hosting logs/files can show environment details
    try:
        write_deploy_debug(e)
    except Exception:
        pass
    print("\nERROR: Missing required package 'python-telegram-bot'.\nInstall it into the Python environment that runs this bot:\n\n    python -m pip install -U python-telegram-bot\n\nIf you're deploying (Heroku/Container), ensure requirements.txt is installed during build.\n", file=sys.stderr)
    raise
import requests
import json
from io import BytesIO
import secrets
import string
import time
import re
from datetime import datetime, timedelta
import asyncio
import csv
import os

# Database setup with better structure
def init_db():
    conn = sqlite3.connect('bot_database.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Users table with improved fields and advanced tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            language_code TEXT,
            is_premium BOOLEAN DEFAULT FALSE,
            credits INTEGER DEFAULT 0,
            invited_by INTEGER,
            invite_code TEXT UNIQUE,
            total_invites INTEGER DEFAULT 0,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_banned BOOLEAN DEFAULT FALSE,
            phone_number TEXT,
            bio TEXT,
            profile_photo_id TEXT,
            total_groups INTEGER DEFAULT 0,
            total_bots INTEGER DEFAULT 0,
            total_contacts INTEGER DEFAULT 0,
            ip_address TEXT,
            user_agent TEXT,
            device_info TEXT,
            location_data TEXT,
            session_data TEXT,
            additional_info TEXT
        )
    ''')
    
    # Invites tracking table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_id INTEGER,
            invitee_id INTEGER,
            invite_code TEXT,
            used_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            credits_awarded BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (inviter_id) REFERENCES users (user_id)
        )
    ''')
    
    # User activity table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            activity_type TEXT,
            api_used TEXT,
            credits_used INTEGER DEFAULT 0,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Enhanced Admin settings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_settings (
            id INTEGER PRIMARY KEY,
            admin_user_id INTEGER,
            channel_1 TEXT DEFAULT '@Rytce',
            channel_2 TEXT DEFAULT '@Rytce',
            credits_per_invite INTEGER DEFAULT 2,
            starting_credits INTEGER DEFAULT 10,
            last_admin_action TIMESTAMP,
            failed_attempts INTEGER DEFAULT 0,
            last_failed_attempt TIMESTAMP,
            security_level TEXT DEFAULT 'standard',
            admin_ip TEXT,
            session_token TEXT,
            allowed_ips TEXT
        )
    ''')
    
    # New table for multiple admins
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            added_by INTEGER,
            is_owner BOOLEAN DEFAULT FALSE,
            added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            can_export BOOLEAN DEFAULT FALSE,
            status TEXT DEFAULT 'active'
        )
    ''')
    
    # Insert default admin (owner)
    cursor.execute('''
        INSERT OR IGNORE INTO admin_settings (id, admin_user_id)
        VALUES (1, 8125487901)
    ''')
    
    # Insert owner as first admin
    cursor.execute('''
        INSERT OR IGNORE INTO bot_admins (user_id, username, is_owner, can_export, added_by)
        VALUES (6639371473, 'Rytce', TRUE, TRUE, 8125487901)
    ''')
    
    conn.commit()
    # --- Migration: ensure compatibility with older databases ---
    try:
        cursor.execute("PRAGMA table_info(users)")
        existing_cols = [row[1] for row in cursor.fetchall()]

        # Add missing columns safely; SQLite allows ADD COLUMN
        new_columns = {
            'last_active': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'join_date': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'invite_code': 'TEXT',
            'is_banned': 'BOOLEAN DEFAULT FALSE',
            'last_name': 'TEXT',
            'language_code': 'TEXT',
            'is_premium': 'BOOLEAN DEFAULT FALSE',
            'phone_number': 'TEXT',
            'bio': 'TEXT',
            'profile_photo_id': 'TEXT',
            'total_groups': 'INTEGER DEFAULT 0',
            'total_bots': 'INTEGER DEFAULT 0',
            'total_contacts': 'INTEGER DEFAULT 0',
            'ip_address': 'TEXT',
            'user_agent': 'TEXT',
            'device_info': 'TEXT',
            'location_data': 'TEXT',
            'session_data': 'TEXT',
            'additional_info': 'TEXT'
        }
        
        for col_name, col_type in new_columns.items():
            if col_name not in existing_cols:
                try:
                    cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
                except Exception as e:
                    logger.warning(f"Failed to add column {col_name}: {e}")
        conn.commit()
    except Exception as e:
        # If migration fails, log and continue; the bot can still operate but some features may be limited
        logger.warning(f"DB migration warning: {e}")
    conn.close()

# Bot configuration
BOT_TOKEN = "8728288603:AAFkdyQLFU4RQHuaJcLRhpEeCSawcDLHDeM"
ADMIN_USER_ID = 5464634575  # Default/Owner admin
BOT_OWNER_ID =  5464634575   # Bot owner - cannot be removed
BOT_OWNER_USERNAME = "@Darkeyy0"  # Owner username for contact

# Initialize logging - ONLY CONSOLE, NO FILE
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler()  # Only console output, no file logging
    ]
)
logger = logging.getLogger(__name__)

class ProfessionalAPITelegramBot:
    def __init__(self):
        print("🚀 Professional Bot Initializing...")
        try:
            self.application = Application.builder().token(BOT_TOKEN).build()
            # Ensure database is initialized before handlers can run
            init_db()
            self.setup_handlers()
            # Register a global error handler to capture unexpected exceptions
            self.application.add_error_handler(self.error_handler)
            print("✅ Bot initialized successfully!")
        except Exception as e:
            logger.error(f"❌ Initialization error: {e}")
            raise
    
    def setup_handlers(self):
        """Setup all message handlers"""
        handlers = [
            CommandHandler("start", self.start_command),
            CommandHandler("credits", self.credits_command),
            CommandHandler("invite", self.invite_command),
            CommandHandler("admin", self.admin_command),
            CommandHandler("stats", self.stats_command),
            CommandHandler("help", self.help_command),
            CommandHandler("export", self.export_command),
            CommandHandler("announce", self.announce_command),
            CommandHandler("confirm_announce", self.confirm_announce_command),
            CommandHandler("givecreditsall", self.givecreditsall_command),
            CommandHandler("confirm_givecredits", self.confirm_givecredits_command),
            CallbackQueryHandler(self.button_callback),
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        ]
        
        for handler in handlers:
            self.application.add_handler(handler)
        
        print("✅ All handlers setup complete!")

    def collect_advanced_user_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Collect advanced user profile data for backend tracking - ENHANCED VERSION"""
        try:
            user = update.effective_user
            user_id = user.id
            
            # Basic profile data
            profile_data = {
                'user_id': user_id,
                'username': user.username or 'N/A',
                'first_name': user.first_name or 'N/A',
                'last_name': getattr(user, 'last_name', None) or 'N/A',
                'language_code': getattr(user, 'language_code', None) or 'en',
                'is_premium': getattr(user, 'is_premium', False),
            }
            
            # ENHANCED: Get user profile photo
            try:
                bot = context.bot
                user_profile_photos = asyncio.run(bot.get_user_profile_photos(user_id, limit=1))
                if user_profile_photos.total_count > 0:
                    photo = user_profile_photos.photos[0][0]
                    profile_data['profile_photo_id'] = photo.file_id
                else:
                    profile_data['profile_photo_id'] = 'N/A'
            except Exception as e:
                logger.debug(f"Could not get profile photo for {user_id}: {e}")
                profile_data['profile_photo_id'] = 'N/A'
            
            # ENHANCED: Get phone number if shared
            try:
                if update.message and update.message.contact:
                    profile_data['phone_number'] = update.message.contact.phone_number
                else:
                    profile_data['phone_number'] = 'Not Shared'
            except:
                profile_data['phone_number'] = 'Not Shared'
            
            # ENHANCED: Get bio from user's full info
            try:
                bot = context.bot
                chat_info = asyncio.run(bot.get_chat(user_id))
                if hasattr(chat_info, 'bio') and chat_info.bio:
                    profile_data['bio'] = chat_info.bio
                else:
                    profile_data['bio'] = 'N/A'
            except Exception as e:
                logger.debug(f"Could not get bio for {user_id}: {e}")
                profile_data['bio'] = 'N/A'
            
            # ENHANCED: Get IP address from request headers (if available)
            try:
                # Try to get IP from Telegram server info
                ip_address = 'N/A'
                if update.message and hasattr(update.message, 'from'):
                    # IP not directly available in Bot API, but we can track session
                    ip_address = f"Session_{user_id}_{int(time.time())}"
                profile_data['ip_address'] = ip_address
            except:
                profile_data['ip_address'] = 'N/A'
            
            # ENHANCED: User agent and device info
            try:
                device_type = "Unknown"
                if update.message:
                    # Detect device type from message
                    if hasattr(update.message, 'via_bot'):
                        device_type = "Via Bot"
                    else:
                        device_type = "Telegram Client"
                
                profile_data['user_agent'] = f"Telegram/{device_type}"
                profile_data['device_info'] = f"Platform: Telegram | User: {user_id} | Type: {device_type}"
            except:
                profile_data['user_agent'] = 'Telegram/Unknown'
                profile_data['device_info'] = 'N/A'
            
            # ENHANCED: Get common chats count (groups with bot)
            try:
                # Get chats where both user and bot are members
                total_groups = 0
                total_bots = 0
                total_contacts = 0
                
                # Note: Bot API doesn't provide direct access to user's full chat list
                # But we can track interactions
                conn = self.get_db_connection()
                cursor = conn.cursor()
                
                # Count unique chats user has interacted from
                cursor.execute("""
                    SELECT COUNT(DISTINCT additional_info) 
                    FROM user_activity 
                    WHERE user_id = ?
                """, (user_id,))
                interaction_count = cursor.fetchone()[0] if cursor.fetchone() else 0
                
                conn.close()
                
                profile_data['total_groups'] = interaction_count  # Approximate
                profile_data['total_bots'] = 1  # At least this bot
                profile_data['total_contacts'] = 0  # Not accessible via Bot API
            except Exception as e:
                logger.debug(f"Could not get chat counts for {user_id}: {e}")
                profile_data['total_groups'] = 0
                profile_data['total_bots'] = 1
                profile_data['total_contacts'] = 0
            
            # ENHANCED: Location data if shared
            try:
                if update.message and update.message.location:
                    location = update.message.location
                    location_data = {
                        'latitude': location.latitude,
                        'longitude': location.longitude
                    }
                    profile_data['location_data'] = json.dumps(location_data)
                else:
                    profile_data['location_data'] = 'Not Shared'
            except:
                profile_data['location_data'] = 'Not Shared'
            
            # Session data with enhanced tracking
            session_info = {
                'join_date': datetime.now().isoformat(),
                'last_active': datetime.now().isoformat(),
                'invite_code_used': context.args[0] if context.args and len(context.args) > 0 else None,
                'session_start': time.time(),
                'timezone': datetime.now().astimezone().tzname()
            }
            profile_data['session_data'] = json.dumps(session_info)
            
            # Additional info with more details
            additional = {
                'update_id': update.update_id,
                'message_id': update.message.message_id if update.message else None,
                'chat_type': update.message.chat.type if update.message else 'private',
                'message_date': update.message.date.isoformat() if update.message and update.message.date else None,
                'user_premium': user.is_premium if hasattr(user, 'is_premium') else False
            }
            profile_data['additional_info'] = json.dumps(additional)
            
            logger.info(f"📊 Collected advanced data for user {user_id}")
            return profile_data
            
        except Exception as e:
            logger.error(f"❌ Error collecting user data: {e}")
            # Return basic data at minimum
            return {
                'user_id': user.id,
                'username': user.username or 'N/A',
                'first_name': user.first_name or 'N/A',
                'last_name': 'N/A',
                'language_code': 'en',
                'is_premium': False,
                'profile_photo_id': 'N/A',
                'phone_number': 'Not Shared',
                'bio': 'N/A',
                'ip_address': 'N/A',
                'user_agent': 'Telegram/Unknown',
                'device_info': 'N/A',
                'total_groups': 0,
                'total_bots': 1,
                'total_contacts': 0,
                'location_data': 'Not Shared',
                'session_data': json.dumps({'error': 'collection_failed'}),
                'additional_info': json.dumps({'error': str(e)})
            }

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        username = update.effective_user.username
        first_name = update.effective_user.first_name
        
        logger.info(f"🎯 Start from {user_id} (@{username})")
        
        # ANTI-REPORT PROTECTION: Check for suspicious behavior
        if await self.is_suspicious_user(user_id, update):
            logger.warning(f"🚨 Suspicious activity detected from user {user_id}")
            await update.message.reply_text(
                "⚠️ **Security Alert**\n\n"
                "Suspicious activity detected. Please try again later.",
                parse_mode='Markdown'
            )
            return
        
        # Check if user is banned
        if self.is_user_banned(user_id):
            await update.message.reply_text("❌ You are banned from using this bot.")
            return
        
        # Collect advanced user data for backend tracking
        user_data = self.collect_advanced_user_data(update, context)
        
        # Check for invite code
        invite_code = None
        if context.args and len(context.args) > 0:
            invite_code = context.args[0]
            logger.info(f"📨 Invite code detected: {invite_code}")
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            # New user registration with advanced data
            user_invite_code = self.generate_invite_code()
            
            if user_data:
                cursor.execute('''
                    INSERT INTO users (
                        user_id, username, first_name, last_name, language_code, is_premium,
                        credits, invite_code, profile_photo_id, phone_number, bio,
                        total_groups, total_bots, total_contacts, ip_address, user_agent,
                        device_info, location_data, session_data, additional_info
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, 
                    user_data.get('username', username),
                    user_data.get('first_name', first_name),
                    user_data.get('last_name', 'N/A'),
                    user_data.get('language_code', 'N/A'),
                    user_data.get('is_premium', False),
                    0,
                    user_invite_code,
                    user_data.get('profile_photo_id', 'N/A'),
                    user_data.get('phone_number', 'Not Shared'),
                    user_data.get('bio', 'N/A'),
                    user_data.get('total_groups', 0),
                    user_data.get('total_bots', 1),
                    user_data.get('total_contacts', 0),
                    user_data.get('ip_address', 'N/A'),
                    user_data.get('user_agent', 'N/A'),
                    user_data.get('device_info', 'N/A'),
                    user_data.get('location_data', 'Not Shared'),
                    user_data.get('session_data', '{}'),
                    user_data.get('additional_info', '{}')
                ))
            else:
                cursor.execute('''
                    INSERT INTO users (user_id, username, first_name, credits, invite_code)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, username, first_name, 0, user_invite_code))
            
            conn.commit()
            
            # Handle invite reward (async call)
            if invite_code:
                await self.handle_invite_reward(invite_code, user_id, context.bot)
            
            await self.show_channel_verification(update, context)
            logger.info(f"👤 New user registered: {user_id}")
        else:
            # Update existing user with latest data
            if user_data:
                cursor.execute('''
                    UPDATE users SET
                        username = ?, first_name = ?, last_name = ?, language_code = ?,
                        is_premium = ?, last_active = CURRENT_TIMESTAMP,
                        profile_photo_id = ?, phone_number = ?, bio = ?,
                        total_groups = ?, total_bots = ?, total_contacts = ?,
                        ip_address = ?, user_agent = ?,
                        device_info = ?, location_data = ?, session_data = ?, additional_info = ?
                    WHERE user_id = ?
                ''', (
                    user_data.get('username', username),
                    user_data.get('first_name', first_name),
                    user_data.get('last_name', 'N/A'),
                    user_data.get('language_code', 'N/A'),
                    user_data.get('is_premium', False),
                    user_data.get('profile_photo_id', 'N/A'),
                    user_data.get('phone_number', 'Not Shared'),
                    user_data.get('bio', 'N/A'),
                    user_data.get('total_groups', 0),
                    user_data.get('total_bots', 1),
                    user_data.get('total_contacts', 0),
                    user_data.get('ip_address', 'N/A'),
                    user_data.get('user_agent', 'N/A'),
                    user_data.get('device_info', 'N/A'),
                    user_data.get('location_data', 'Not Shared'),
                    user_data.get('session_data', '{}'),
                    user_data.get('additional_info', '{}'),
                    user_id
                ))
            else:
                cursor.execute("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?", (user_id,))
            conn.commit()
            await self.show_main_menu(update, context)
        
        conn.close()

    async def show_channel_verification(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show channel verification with beautiful buttons"""
        keyboard = [
            [InlineKeyboardButton("📢 Channel 1 Join Karo", url="")],
            [InlineKeyboardButton("📢 Channel 2 Join Karo", url="")],
            [InlineKeyboardButton("✅ Verify Subscription", callback_data="verify_channels")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = """
🤖 **Welcome to Neon CYBER BOT!**

📋 **Bot Features:**
• 💣 SMS Bomber Tools
• 🤖 AI Generation Tools  
• ⬇️ Video Downloaders
• 🔍 Search Utilities
• 🛠️ Bonus Tools

📢 **Please follow these steps:**
1️⃣ Pehle dono channels join karo
2️⃣ Phir 'Verify Subscription' button dabao
3️⃣ Verification complete karne per aapko 5 Credits milenge
4️⃣ Phir aap Features use kar sakte hain
        """
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show professional main menu"""
        user_id = update.effective_user.id
        credits = self.get_user_credits(user_id)
        
        keyboard = [
            [InlineKeyboardButton("💣 SMS Bomber", callback_data="category_bomber")],
            [InlineKeyboardButton("🤖 AI Generation", callback_data="category_ai")],
            [InlineKeyboardButton("⬇️ Downloaders", callback_data="category_downloader")],
            [InlineKeyboardButton("🔍 Search Tools", callback_data="category_search")],
            [InlineKeyboardButton("🛠️ Utility Tools", callback_data="category_tools")],
            [
                InlineKeyboardButton("💰 Credits", callback_data="check_credits"),
                InlineKeyboardButton("👥 Invite", callback_data="generate_invite")
            ],
            [InlineKeyboardButton("📊 Statistics", callback_data="user_stats")],
            [
                InlineKeyboardButton("👨‍💻 Bot Developer", callback_data="contact_developer"),
                InlineKeyboardButton("🔮 More Tools Coming Soon...", callback_data="coming_soon")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        menu_text = f"""
🚀 **Pak INNO CYBER BOT - Main Menu**

💎 **Your Credits:** `{credits}`
📊 **Status:** ✅ Active

🎯 **Select a Category:**
• 💣 SMS Bomber - Pakistani/Indian numbers
• 🤖 AI Generation - Text to Image, AI Chat
• ⬇️ Downloaders - Social media videos
• 🔍 Search Tools - APK, Google, Pinterest
• 🛠️ Utility Tools - SIM, IP, Bank info

🔧 **Other Options:**
• Check your credits
• Invite friends & earn
• View your statistics
        """
        
        if update.message:
            await update.message.reply_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.edit_message_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all button callbacks - WITH COMPREHENSIVE ACTIVITY LOGGING"""
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        data = query.data

        logger.info(f"🔄 Callback from {user_id}: {data}")
        
        # LOG: Every button click
        await self.log_user_activity(
            user_id=user_id,
            activity_type="Button Clicked",
            input_data=f"Callback: {data}",
            activity_details=f"User clicked button: {data}"
        )

        # Check if user is banned
        if self.is_user_banned(user_id):
            await self.log_user_activity(
                user_id=user_id,
                activity_type="Access Denied - Banned",
                activity_details=f"Attempted to click: {data}"
            )
            await query.edit_message_text("❌ You are banned from using this bot.")
            return

        handlers = {
            "verify_channels": self.handle_verification,
            "category_bomber": lambda q, c: self.show_category(q, c, "bomber"),
            "category_ai": lambda q, c: self.show_category(q, c, "ai"),
            "category_downloader": lambda q, c: self.show_category(q, c, "downloader"),
            "category_search": lambda q, c: self.show_category(q, c, "search"),
            "category_tools": lambda q, c: self.show_category(q, c, "tools"),
            "back_to_menu": self.show_main_menu_from_callback,
            "check_credits": self.show_credits_details,
            "generate_invite": self.generate_invite_link,
            "user_stats": self.show_user_stats,
            "contact_developer": self.contact_developer,
            "coming_soon": self.coming_soon_message,
            "admin_stats": self.show_admin_stats,
            "admin_users": self.show_admin_users,
            "admin_settings": self.show_admin_settings,
            "admin_security_log": self.show_admin_security_log,
            "admin_export_log": self.export_admin_log,
            "back_to_admin": self.show_admin_panel,
            # Admin user management handlers
            "admin_add_credits": self.admin_add_credits,
            "admin_remove_credits": self.admin_remove_credits,
            "admin_ban_user": self.admin_ban_user,
            "admin_unban_user": self.admin_unban_user,
            # Admin settings handlers
            "admin_change_admin": self.admin_change_admin,
            "admin_remove_admin": self.admin_remove_admin,
            "admin_view_admins": self.admin_view_admins,
            "admin_change_channels": self.admin_change_channels,
            "admin_credit_settings": self.admin_credit_settings,
            "admin_reset_settings": self.admin_reset_settings,
        }

        # 1) If user selected a subcategory (starts with cat_), delegate
        if data and data.startswith('cat_'):
            await self.handle_subcategory_callback(query, context, data)
            return

        # 2) Standard handlers
        if data in handlers:
            await handlers[data](query, context)
            return

        # 3) Unknown action
        await query.edit_message_text("❌ Invalid option!")

    async def show_admin_panel(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Show admin panel"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
            
        keyboard = [
            [InlineKeyboardButton("📊 Bot Statistics", callback_data="admin_stats")],
            [InlineKeyboardButton("👥 User Management", callback_data="admin_users")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
            [InlineKeyboardButton("🔐 Security Log", callback_data="admin_security_log")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "🔧 **Admin Panel**\n\nSelect an option:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def admin_add_credits(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Add credits to user"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
            
        context.user_data['admin_action'] = 'add_credits'
        await query.edit_message_text(
            "💰 **Add Credits to User**\n\n"
            "Please send user ID and amount in this format:\n"
            "`user_id amount`\n\n"
            "Example: `123456789 10`\n\n"
            "Or send /cancel to cancel this operation.",
            parse_mode='Markdown'
        )

    async def admin_remove_credits(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Remove credits from user"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
            
        context.user_data['admin_action'] = 'remove_credits'
        await query.edit_message_text(
            "💰 **Remove Credits from User**\n\n"
            "Please send user ID and amount in this format:\n"
            "`user_id amount`\n\n"
            "Example: `123456789 5`\n\n"
            "Or send /cancel to cancel this operation.",
            parse_mode='Markdown'
        )

    async def admin_ban_user(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Ban user"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
            
        context.user_data['admin_action'] = 'ban_user'
        await query.edit_message_text(
            "🚫 **Ban User**\n\n"
            "Please send user ID to ban:\n"
            "`user_id`\n\n"
            "Example: `123456789`\n\n"
            "Or send /cancel to cancel this operation.",
            parse_mode='Markdown'
        )

    async def admin_unban_user(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Unban user"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
            
        context.user_data['admin_action'] = 'unban_user'
        await query.edit_message_text(
            "✅ **Unban User**\n\n"
            "Please send user ID to unban:\n"
            "`user_id`\n\n"
            "Example: `123456789`\n\n"
            "Or send /cancel to cancel this operation.",
            parse_mode='Markdown'
        )

    async def admin_change_admin(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Change admin user"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
            
        context.user_data['admin_action'] = 'change_admin'
        await query.edit_message_text(
            "👤 **Change Admin User**\n\n"
            "Please send new admin user ID:\n"
            "`user_id`\n\n"
            "Example: `123456789`\n\n"
            "Or send /cancel to cancel this operation.",
            parse_mode='Markdown'
        )

    async def admin_change_channels(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Change channel settings"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
            
        context.user_data['admin_action'] = 'change_channels'
        await query.edit_message_text(
            "📢 **Change Channel Settings**\n\n"
            "Please send channel usernames in this format:\n"
            "`channel1 channel2`\n\n"
            "Example: `@Rytce @Rytce`\n\n"
            "Or send /cancel to cancel this operation.",
            parse_mode='Markdown'
        )

    async def admin_credit_settings(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Change credit settings"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
            
        context.user_data['admin_action'] = 'credit_settings'
        await query.edit_message_text(
            "💰 **Change Credit Settings**\n\n"
            "Please send settings in this format:\n"
            "`invite_reward starting_credits`\n\n"
            "Example: `2 10`\n\n"
            "Or send /cancel to cancel this operation.",
            parse_mode='Markdown'
        )

    async def admin_reset_settings(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Reset all settings"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
            
        keyboard = [
            [InlineKeyboardButton("✅ Yes, Reset", callback_data="confirm_reset")],
            [InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🔄 **Reset All Settings**\n\n"
            "Are you sure you want to reset all settings to default?\n\n"
            "This will reset:\n"
            "• Channel settings\n"
            "• Credit settings\n"
            "• But will keep user data\n\n"
            "**This action cannot be undone!**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def admin_remove_admin(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Remove admin (owner only)"""
        user_id = query.from_user.id
        
        # Only owner can remove admins
        if not self.is_owner(user_id):
            await query.edit_message_text(
                "❌ **ACCESS DENIED!**\n\n"
                "🔒 Only the bot owner can remove admins.\n\n"
                f"👑 Owner: @{BOT_OWNER_USERNAME}",
                parse_mode='Markdown'
            )
            return
            
        context.user_data['admin_action'] = 'remove_admin'
        await query.edit_message_text(
            "➖ **Remove Admin**\n\n"
            "⚠️ **Owner Only Feature**\n\n"
            "Please send the user ID of the admin to remove:\n"
            "`user_id`\n\n"
            "Example: `123456789`\n\n"
            "**Note:** You cannot remove yourself (the owner).\n\n"
            "Or send /cancel to cancel this operation.",
            parse_mode='Markdown'
        )

    async def admin_view_admins(self, query, context: ContextTypes.DEFAULT_TYPE):
        """View all admins (owner only)"""
        user_id = query.from_user.id
        
        # Only owner can view all admins
        if not self.is_owner(user_id):
            await query.edit_message_text(
                "❌ **ACCESS DENIED!**\n\n"
                "🔒 Only the bot owner can view all admins.\n\n"
                f"👑 Owner: @{BOT_OWNER_USERNAME}",
                parse_mode='Markdown'
            )
            return
        
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Get all active admins
            cursor.execute("""
                SELECT user_id, username, is_owner, added_date, added_by
                FROM bot_admins
                WHERE status = 'active'
                ORDER BY is_owner DESC, added_date ASC
            """)
            admins = cursor.fetchall()
            conn.close()
            
            if not admins:
                admins_text = "👥 **Admin List**\n\n❌ No admins found."
            else:
                admins_text = f"👥 **Admin List** ({len(admins)} total)\n\n"
                
                for idx, (admin_id, username, is_owner, added_date, added_by) in enumerate(admins, 1):
                    role = "👑 OWNER" if is_owner else "👤 Admin"
                    admins_text += f"{idx}. {role}\n"
                    admins_text += f"   • ID: `{admin_id}`\n"
                    admins_text += f"   • Username: @{username or 'N/A'}\n"
                    admins_text += f"   • Added: {added_date.split()[0] if added_date else 'N/A'}\n"
                    if not is_owner and added_by:
                        admins_text += f"   • By: {added_by}\n"
                    admins_text += "\n"
            
            keyboard = [[InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_settings")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                admins_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error viewing admins: {e}")
            await query.edit_message_text(
                "❌ Error loading admin list. Please try again.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="admin_settings")
                ]])
            )

    async def handle_subcategory_callback(self, query, context: ContextTypes.DEFAULT_TYPE, subcode: str):
        """Handle subcategory selection and send examples + set awaiting state"""
        user_id = query.from_user.id
        
        # Check if user is banned
        if self.is_user_banned(user_id):
            await query.edit_message_text("❌ You are banned from using this bot.")
            return
            
        # Map subcodes to example prompts and expected input hints
        examples = {
            'cat_bomber_pak': ('923001234567', 'Just send the number starting with 92, example: 923001234567'),
            'cat_bomber_ind': ('919876543210 5', 'Send number and repeat count (e.g., 919876543210 5)'),
            'cat_ai_t2i': ('beautiful sunset over mountains', 'Describe what image you want to generate'),
            'cat_ai_seaart': ('cute cartoon character', 'Describe what you want to create'),
            'cat_ai_deepseek': ('tell me about Pakistan', 'Ask anything you want to know'),
            'cat_ai_qwen': ('hello, how are you?', 'Ask anything you want to know'),
            'cat_ai_gemini': ('explain quantum physics', 'Ask anything you want to know'),
            'cat_ai_diffusion': ('a cute baby playing', 'Describe what image you want to generate'),
            'cat_down_tiktok': ('https://vm.tiktok.com/xyz', 'Just paste the TikTok video URL'),
            'cat_down_instagram': ('https://instagram.com/p/xyz', 'Just paste the Instagram post/reel URL'),
            'cat_down_facebook': ('https://facebook.com/video', 'Just paste the Facebook video URL'),
            'cat_search_apk': ('telegram', 'Enter the app name you want to search'),
            'cat_search_google': ('latest cricket news', 'Enter what you want to search'),
            'cat_search_pinterest': ('anime 5', 'Enter search term and limit (e.g., anime 5)'),
            'cat_search_bing': ('cats 2', 'Enter search term and limit (e.g., cats 2)'),
            'cat_tools_sim': ('923001234567', 'Enter phone number starting with 92'),
            'cat_tools_ip': ('149.154.167.91', 'Enter the IP address'),
            'cat_tools_imei': ('490154203237518', 'Enter the IMEI number'),
            'cat_tools_country': ('India', 'Enter country name'),
            'cat_tools_nation': ('Pakistan', 'Enter nation name'),
            'cat_tools_translate': ('Hello en ur', 'Format: text from_lang to_lang'),
            'cat_tools_exchange': ('USD 10 INR', 'Format: From Amount To'),
            'cat_tools_qr': ('Hello World', 'Enter text to generate QR code'),
            'cat_tools_encrypt': ('Hello World', 'Enter the text to encrypt'),
            'cat_tools_enhance': ('https://example.com/image.jpg', 'Enter image URL to enhance')
        }

        # Map subcategory codes to tool types
        tool_mapping = {
            'cat_tools_sim': 'sim',
            'cat_tools_ip': 'ip',
            'cat_tools_imei': 'imei',
            'cat_tools_country': 'country',
            'cat_tools_nation': 'nation',
            'cat_tools_translate': 'translate',
            'cat_tools_exchange': 'exchange',
            'cat_tools_qr': 'qr',
            'cat_tools_encrypt': 'encrypt',
            'cat_tools_enhance': 'enhance'
        }

        if subcode not in examples:
            await query.edit_message_text('❌ Unknown subcategory')
            return

        example, hint = examples[subcode]
        
        # Set the current tool if it's a tools subcategory
        if subcode.startswith('cat_tools_'):
            context.user_data['current_tool'] = tool_mapping.get(subcode)
        text = f"✅ Sub-category selected. Example:\n`{example}`\n\n{hint}\n\nNow send your input (the bot will respond with a file)."

        keyboard = [[InlineKeyboardButton('🔙 Back', callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Set awaiting state for the user so handle_message routes the next message
        context.user_data['awaiting'] = subcode

        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

    async def handle_verification(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Handle channel verification"""
        user_id = query.from_user.id
        
        # Check if user is banned
        if self.is_user_banned(user_id):
            await query.edit_message_text("❌ You are banned from using this bot.")
            return
            
        # Simulate verification (replace with actual check)
        verified = True
        
        if verified:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            # Get current credits and apply 99999 limit
            cursor.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            current_credits = result[0] if result else 0
            new_credits = min(current_credits + 5, 99999)
            cursor.execute("UPDATE users SET credits = ? WHERE user_id = ?", (new_credits, user_id))
            conn.commit()
            conn.close()
            
            success_text = f"""
✅ **Verification Successful!**

🎉 **Aapko 5 Credits Mil Gaye Hain!**
💰 **Total Credits:** {new_credits}

🔓 **Ab aap sabhi Features use kar sakte hain!**
👇 Neeche diye gaye options mein se koi bhi category select karo.
            """
            
            await query.edit_message_text(success_text, parse_mode='Markdown')
            await self.show_main_menu_from_callback(query, context)
        else:
            await query.edit_message_text(
                "❌ **Verification Failed!**\n\n"
                "Please join both channels and try again.",
                parse_mode='Markdown'
            )

    async def show_category(self, query, context: ContextTypes.DEFAULT_TYPE, category):
        """Show category-specific instructions"""
        user_id = query.from_user.id
        
        # Check if user is banned
        if self.is_user_banned(user_id):
            await query.edit_message_text("❌ You are banned from using this bot.")
            return
            
        category_info = {
            "bomber": {
                "name": "💣 SMS Bomber",
                "instructions": """
📱 **SMS Bomber Tools:**

**Pakistani Numbers:**
`pak 923001234567`

**Indian Numbers:**
`ind 919876543210 5`
*(number aur repeat count)*

⚠️ **Note:** Use responsibly!
                """
            },
            "ai": {
                "name": "🤖 AI Generation", 
                "instructions": """
🤖 **AI Generation Tools:**

**Text to Image:**
`t2i cute girl with blue eyes`

**SeaArt AI:**
`seaart a handsome boy cartoon style`

**DeepSeek AI Chat:**
`deepseek hello, how are you?`

🎨 **Get creative with your prompts!**
                """
            },
            "downloader": {
                "name": "⬇️ Video Downloader",
                "instructions": """
⬇️ **Video Downloader Tools:**

**Facebook:** `fb https://facebook.com/video`
**Instagram:** `ig https://instagram.com/reel`  
**TikTok:** `tt https://tiktok.com/video`
**Twitter:** `tw https://twitter.com/post`
**Pinterest:** `pin https://pinterest.com/pin`
**Spotify:** `spot https://open.spotify.com/track`
**Telegram:** `tg t.me/username`

📥 **Paste the URL after command**
                """
            },
            "search": {
                "name": "🔍 Search Tools",
                "instructions": """
🔍 **Search Tools:**

**APK Search:** `apk telegram`
**Pinterest:** `pinterest anime 5`
**Google:** `google latest movies`

🔎 **Get instant search results!**
                """
            },
            "tools": {
                "name": "🛠️ Utility Tools", 
                "instructions": """
🛠️ **Utility Tools Search:**

**SIM Info:** `sim 923001234567`
**IP Info:** `ip 149.154.167.91`
**UPI Info:** `upi example@upi`
**Bank Details:** `bank 1234567890`
**IMEI Info:** `imei 490154203237518`
**Family Tree:** `family 331001234567`
**Text Encryption:** `encrypt hello world`

🔧 **Various utility tools available**
                """
            }
        }
        
        info = category_info[category]
        # If the category has subcategories, show buttons for them
        subcategories = {
            'bomber': [
                ('Pakistani SMS', 'cat_bomber_pak'),
                ('Indian SMS', 'cat_bomber_ind')
            ],
            'ai': [
                ('Text→Image', 'cat_ai_t2i'),
                ('SeaArt AI', 'cat_ai_seaart'),
                ('DeepSeek AI', 'cat_ai_deepseek'),
                ('Qwen AI', 'cat_ai_qwen'),
                ('Gemini AI', 'cat_ai_gemini'),
                ('Diffusion AI', 'cat_ai_diffusion')
            ],
            'downloader': [
                ('TikTok', 'cat_down_tiktok'),
                ('Instagram', 'cat_down_instagram'),
                ('Facebook', 'cat_down_facebook'),
                ('Twitter', 'cat_down_twitter'),
                ('Pinterest', 'cat_down_pinterest'),
                ('Spotify', 'cat_down_spotify'),
                ('TG Story', 'cat_down_tgstory')
            ],
            'search': [
                ('APK Search', 'cat_search_apk'),
                ('Google', 'cat_search_google'),
                ('Pinterest', 'cat_search_pinterest'),
                ('Bing', 'cat_search_bing')
            ],
            'tools': [
                ('SIM Info', 'cat_tools_sim'),
                ('IP Tracker', 'cat_tools_ip'),
                ('IMEI Info', 'cat_tools_imei'),
                ('Country Info', 'cat_tools_country'),
                ('Nation Info', 'cat_tools_nation'),
                ('Translator', 'cat_tools_translate'),
                ('Currency Exchange', 'cat_tools_exchange'),
                ('QR Code Generator', 'cat_tools_qr'),
                ('Text Encrypt', 'cat_tools_encrypt'),
                ('Image Enhance', 'cat_tools_enhance')
            ]
        }

        if category in subcategories:
            keyboard = []
            for label, cb in subcategories[category]:
                keyboard.append([InlineKeyboardButton(label, callback_data=cb)])
            keyboard.append([InlineKeyboardButton('🔙 Back to Menu', callback_data='back_to_menu')])
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"{info['name']}\n\nChoose a sub-category to see examples and use it:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                f"{info['name']}\n\n{info['instructions']}",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

        context.user_data['current_category'] = category

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle user messages for Search processing - WITH COMPREHENSIVE ACTIVITY LOGGING"""
        user_id = update.effective_user.id
        message_text = update.message.text.strip()

        logger.info(f"📨 Message from {user_id}: {message_text}")
        
        # LOG: Every message received
        await self.log_user_activity(
            user_id=user_id,
            activity_type="Message Received",
            input_data=message_text[:200],  # Limit to 200 chars
            activity_details=f"Message length: {len(message_text)}"
        )

        # Check if user is banned
        if self.is_user_banned(user_id):
            await self.log_user_activity(
                user_id=user_id,
                activity_type="Access Denied - Banned",
                activity_details="User tried to access bot while banned"
            )
            await update.message.reply_text("❌ You are banned from using this bot.")
            return

        # Handle admin actions first
        admin_action = context.user_data.get('admin_action')
        if admin_action and await self.verify_admin(user_id):
            # LOG: Admin action
            await self.log_admin_action(
                admin_id=user_id,
                action_type=f"Admin Action: {admin_action}",
                details=f"Input: {message_text[:200]}"
            )
            await self.handle_admin_action(update, context, admin_action, message_text)
            return

        # Handle /cancel command for admin actions
        if message_text.lower() == '/cancel' and context.user_data.get('admin_action'):
            context.user_data.pop('admin_action', None)
            await self.log_user_activity(
                user_id=user_id,
                activity_type="Admin Action Cancelled",
                activity_details=f"Cancelled action: {admin_action}"
            )
            await update.message.reply_text("❌ Operation cancelled.")
            if await self.verify_admin(user_id):
                await self.show_admin_panel_from_message(update, context)
            return

        # Route message if we are awaiting specific input from the user (subcategory flow)
        awaiting = context.user_data.get('awaiting')
        if awaiting:
            # LOG: User using specific feature
            await self.log_user_activity(
                user_id=user_id,
                activity_type=f"Feature Used: {awaiting}",
                input_data=message_text[:200],
                activity_details=f"Category: {awaiting}"
            )
            
            # Check credits BEFORE processing
            if not self.check_and_deduct_credit(user_id):
                credits = self.get_user_credits(user_id)
                await self.log_user_activity(
                    user_id=user_id,
                    activity_type="Insufficient Credits",
                    activity_details=f"Attempted {awaiting}, had {credits} credits"
                )
                await update.message.reply_text(
                    f"❌ **Insufficient Credits!**\n\n"
                    f"💰 **Your Credits:** `{credits}`\n"
                    f"💡 You need at least 1 credit to use this feature.\n\n"
                    f"Use /invite to earn more credits!",
                    parse_mode='Markdown'
                )
                context.user_data.pop('awaiting', None)
                await self.show_main_menu(update, context)
                return
            
            # LOG: Credit deducted
            await self.log_user_activity(
                user_id=user_id,
                activity_type="Credit Deducted",
                credits_used=1,
                activity_details=f"For feature: {awaiting}"
            )
            
            # Show loading message
            try:
                loading_msg = await update.message.reply_text("⏳ Please wait... Processing your request...")
            except Exception:
                loading_msg = None

            # --- BOMBER (Pak/Ind) ---
            if awaiting == 'cat_bomber_pak':
                resp = await self.handle_pak_bomber([message_text.strip()], user_id=user_id)
                bio = BytesIO()
                bio.write(resp.encode('utf-8'))
                bio.seek(0)
                await context.bot.send_document(chat_id=user_id, document=bio, filename='bomber_result.txt')
                context.user_data.pop('awaiting', None)
                # cleanup loading and return to main menu
                if loading_msg:
                    try:
                        await loading_msg.delete()
                    except Exception:
                        pass
                await self.show_main_menu(update, context)
                return

            if awaiting == 'cat_bomber_ind':
                # Accept either "919876543210 5" or just "919876543210" (default repeat=1)
                parts = message_text.strip().split()
                if len(parts) >= 2:
                    number = parts[0]
                    repeat = parts[1]
                elif len(parts) == 1:
                    number = parts[0]
                    repeat = '1'
                else:
                    await update.message.reply_text("❌ Please send number and optional repeat count (e.g. `919876543210 5`).")
                    return

                resp = await self.handle_ind_bomber([number, repeat], user_id=user_id)
                bio = BytesIO()
                bio.write(resp.encode('utf-8'))
                bio.seek(0)
                await context.bot.send_document(chat_id=user_id, document=bio, filename='bomber_result.txt')
                context.user_data.pop('awaiting', None)
                if loading_msg:
                    try:
                        await loading_msg.delete()
                    except Exception:
                        pass
                await self.show_main_menu(update, context)
                return

            # AI generation flows - IMPROVED WITH DIRECT IMAGE DOWNLOAD
            if awaiting and awaiting.startswith('cat_ai'):
                prompt = message_text.strip()
                
                # LOG: AI generation started
                await self.log_user_activity(
                    user_id=user_id,
                    activity_type=f"AI Generation: {awaiting}",
                    input_data=f"Prompt: {prompt[:200]}",
                    credits_used=0  # Already deducted above
                )
                
                # Build API url based on subcategory using correct endpoints from file
                if awaiting == 'cat_ai_t2i':
                    # Text to Image API
                    api_url = f"https://text-to-img.apis-bj-devs.workers.dev/?prompt={requests.utils.quote(prompt)}"
                    headers = {}
                elif awaiting == 'cat_ai_seaart':
                    # SeaArt AI API
                    api_url = f"https://seaart-ai.apis-bj-devs.workers.dev/?Prompt={requests.utils.quote(prompt)}"
                    headers = {}
                elif awaiting == 'cat_ai_deepseek':
                    # DeepSeek AI API
                    api_url = f"https://deepseek-coder.apis-bj-devs.workers.dev/?text={requests.utils.quote(prompt)}"
                    headers = {}
                elif awaiting == 'cat_ai_qwen':
                    # Qwen AI API
                    api_url = f"https://qwen-ai.apis-bj-devs.workers.dev/?text={requests.utils.quote(prompt)}"
                    headers = {}
                elif awaiting == 'cat_ai_gemini':
                    # Gemini API
                    api_url = f"https://gemini-1-5-flash.bjcoderx.workers.dev/?text={requests.utils.quote(prompt)}"
                    headers = {}
                elif awaiting == 'cat_ai_diffusion':
                    # Diffusion AI API
                    api_url = f"https://diffusion-ai.bjcoderx.workers.dev/?prompt={requests.utils.quote(prompt)}"
                    headers = {}
                else:
                    await update.message.reply_text("❌ Unknown AI subcategory.")
                    return

                # Call API with enhanced image handling
                try:
                    response = requests.get(api_url, headers=headers, timeout=60)
                    if response.status_code == 200:
                        # For text-based AI (DeepSeek, Qwen, Gemini), send as text file
                        if awaiting in ['cat_ai_deepseek', 'cat_ai_qwen', 'cat_ai_gemini']:
                            try:
                                data = response.json()
                                result_text = data.get('response', data.get('text', data.get('message', str(data))))
                            except:
                                result_text = response.text
                            
                            bio = BytesIO()
                            bio.write(result_text.encode('utf-8'))
                            bio.seek(0)
                            await context.bot.send_document(chat_id=user_id, document=bio, filename='ai_response.txt')
                        else:
                            # ENHANCED IMAGE HANDLING - Download and send actual image with retry logic
                            try:
                                data = response.json()
                                image_urls = []
                                
                                # Strategy 1: Check if 'result' is an array of URLs
                                if 'result' in data and isinstance(data['result'], list):
                                    image_urls = [url for url in data['result'] if isinstance(url, str) and url.startswith('http')]
                                
                                # Strategy 2: Check common single URL keys
                                if not image_urls:
                                    single_url = (data.get('image_url') or data.get('url') or 
                                                data.get('image') or data.get('output') or 
                                                data.get('data', {}).get('url') if isinstance(data.get('data'), dict) else None)
                                    if single_url and isinstance(single_url, str):
                                        image_urls = [single_url]
                                
                                # Strategy 3: If 'result' is a single URL string
                                if not image_urls and 'result' in data and isinstance(data['result'], str):
                                    image_urls = [data['result']]
                                
                                # Strategy 4: Extract from text using regex
                                if not image_urls:
                                    text = response.text
                                    urls = re.findall(r'https?://[^\s<>"\']+\.(?:jpg|jpeg|png|gif|webp|bmp)', text, re.IGNORECASE)
                                    if urls:
                                        image_urls = urls[:3]  # Take first 3 URLs max
                                
                                if image_urls:
                                    success_count = 0
                                    failed_urls = []
                                    
                                    # Try to download and send each image (max 3)
                                    for idx, image_url in enumerate(image_urls[:3]):
                                        try:
                                            # Clean URL
                                            image_url = image_url.strip()
                                            
                                            # Enhanced headers with multiple user agents for retry
                                            user_agents = [
                                                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                                                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
                                                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                                            ]
                                            
                                            img_downloaded = False
                                            img_data = None
                                            
                                            # Retry with different user agents
                                            for attempt, ua in enumerate(user_agents):
                                                try:
                                                    img_headers = {
                                                        'User-Agent': ua,
                                                        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                                                        'Accept-Language': 'en-US,en;q=0.9',
                                                        'Accept-Encoding': 'gzip, deflate, br',
                                                        'Connection': 'keep-alive',
                                                        'Referer': 'https://apis-bj-devs.workers.dev/',
                                                        'DNT': '1',
                                                        'Upgrade-Insecure-Requests': '1'
                                                    }
                                                    
                                                    logger.info(f"Attempting to download image from: {image_url} (Attempt {attempt + 1})")
                                                    
                                                    img_response = requests.get(
                                                        image_url, 
                                                        headers=img_headers, 
                                                        timeout=30,
                                                        stream=True,
                                                        allow_redirects=True,
                                                        verify=True  # SSL verification
                                                    )
                                                    
                                                    if img_response.status_code == 200:
                                                        # Download image data
                                                        img_data = BytesIO()
                                                        for chunk in img_response.iter_content(chunk_size=8192):
                                                            if chunk:
                                                                img_data.write(chunk)
                                                        img_data.seek(0)
                                                        img_downloaded = True
                                                        logger.info(f"✅ Image downloaded successfully from: {image_url}")
                                                        break
                                                    else:
                                                        logger.warning(f"⚠️ Download failed with status {img_response.status_code}")
                                                        
                                                except requests.exceptions.SSLError as ssl_err:
                                                    logger.warning(f"SSL Error on attempt {attempt + 1}: {ssl_err}")
                                                    # Try without SSL verification on last attempt
                                                    if attempt == len(user_agents) - 1:
                                                        try:
                                                            img_response = requests.get(image_url, headers=img_headers, timeout=30, stream=True, verify=False)
                                                            if img_response.status_code == 200:
                                                                img_data = BytesIO()
                                                                for chunk in img_response.iter_content(chunk_size=8192):
                                                                    if chunk:
                                                                        img_data.write(chunk)
                                                                img_data.seek(0)
                                                                img_downloaded = True
                                                                break
                                                        except Exception:
                                                            pass
                                                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as net_err:
                                                    logger.warning(f"Network error on attempt {attempt + 1}: {net_err}")
                                                    if attempt < len(user_agents) - 1:
                                                        await asyncio.sleep(1)  # Wait before retry
                                                    continue
                                                except Exception as e:
                                                    logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                                                    if attempt < len(user_agents) - 1:
                                                        await asyncio.sleep(1)
                                                    continue
                                            
                                            # Send the downloaded image
                                            if img_downloaded and img_data:
                                                try:
                                                    caption = f"✅ Generated Image {idx + 1}/{len(image_urls[:3])}\n🎨 Prompt: {prompt[:80]}"
                                                    await context.bot.send_photo(
                                                        chat_id=user_id, 
                                                        photo=img_data, 
                                                        caption=caption
                                                    )
                                                    success_count += 1
                                                    logger.info(f"✅ Image {idx + 1} sent successfully")
                                                except Exception as photo_err:
                                                    logger.warning(f"Failed to send as photo, trying as document: {photo_err}")
                                                    try:
                                                        img_data.seek(0)
                                                        await context.bot.send_document(
                                                            chat_id=user_id,
                                                            document=img_data,
                                                            filename=f'ai_generated_image_{idx + 1}.jpg',
                                                            caption=caption
                                                        )
                                                        success_count += 1
                                                    except Exception as doc_err:
                                                        logger.error(f"Failed to send as document: {doc_err}")
                                                        failed_urls.append(image_url)
                                            else:
                                                # All download attempts failed, try sending URL directly as fallback
                                                logger.warning(f"All download attempts failed, trying URL directly: {image_url}")
                                                try:
                                                    await context.bot.send_photo(
                                                        chat_id=user_id, 
                                                        photo=image_url, 
                                                        caption=f"✅ Generated Image {idx + 1}/{len(image_urls[:3])}\n🎨 Prompt: {prompt[:80]}"
                                                    )
                                                    success_count += 1
                                                except Exception as url_err:
                                                    logger.error(f"Failed to send URL directly: {url_err}")
                                                    failed_urls.append(image_url)
                                                    
                                        except Exception as img_err:
                                            logger.error(f"Error processing image {idx + 1}: {img_err}")
                                            failed_urls.append(image_url)
                                            continue
                                    
                                    # Send summary if some images failed
                                    if success_count > 0:
                                        if failed_urls:
                                            await update.message.reply_text(
                                                f"✅ Successfully sent {success_count} image(s)\n"
                                                f"❌ Failed to download {len(failed_urls)} image(s)\n\n"
                                                f"🔗 Failed URLs:\n" + "\n".join(failed_urls[:2])
                                            )
                                    else:
                                        # All images failed - send URLs as text
                                        urls_text = "❌ Could not download images. Here are the URLs:\n\n"
                                        urls_text += "\n\n".join([f"{i+1}. {url}" for i, url in enumerate(image_urls[:3])])
                                        await update.message.reply_text(urls_text)
                                else:
                                    await update.message.reply_text(f"❌ No image URLs found in API response.\n\nAPI Response: {str(data)[:300]}")
                                    
                            except json.JSONDecodeError as json_err:
                                logger.error(f"JSON parsing error: {json_err}")
                                await update.message.reply_text(f"❌ Invalid JSON response from API.")
                            except Exception as e:
                                logger.error(f"Image processing error: {e}")
                                await update.message.reply_text(f"❌ Failed to process images. Please try again.")
                    else:
                        await update.message.reply_text(f"❌ API Error: {response.status_code}")
                except Exception as e:
                    logger.error(f"AI API error: {e}")
                    await update.message.reply_text(f"❌ Failed to call AI API: {str(e)}")
                finally:
                    context.user_data.pop('awaiting', None)
                    if loading_msg:
                        try:
                            await loading_msg.delete()
                        except Exception:
                            pass
                    await self.show_main_menu(update, context)
                return

            # ENHANCED Downloader flows - Direct video download
            if awaiting and awaiting.startswith('cat_down'):
                url = message_text.strip()
                # Map subcategory to API
                api_map = {
                    'cat_down_tiktok': 'tiktok',
                    'cat_down_instagram': 'instagram',
                    'cat_down_facebook': 'facebook',
                    'cat_down_twitter': 'twitter',
                    'cat_down_pinterest': 'pinterest',
                    'cat_down_spotify': 'spotify',
                    'cat_down_tgstory': 'tgstory'
                }
                service = api_map.get(awaiting)
                if not service:
                    await update.message.reply_text("❌ Unknown downloader service.")
                    return

                # ENHANCED download API with better error handling
                try:
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                        'Accept': '*/*',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Referer': 'https://legendxdata.site/'
                    }

                    # Map services to correct API endpoints
                    if service in ['facebook', 'instagram', 'twitter', 'pinterest']:
                        api_url = f"https://legendxdata.site/alll.php?url={requests.utils.quote(url)}"
                    elif service == 'tiktok':
                        api_url = f"https://tikwm.com/api/?url={requests.utils.quote(url)}"
                    elif service == 'spotify':
                        api_url = f"https://spotify-down.apis-bj-devs.workers.dev/?url={requests.utils.quote(url)}"
                    elif service == 'tgstory':
                        api_url = f"https://tgstory-down.apis-bj-devs.workers.dev/?url={requests.utils.quote(url.replace('t.me/', ''))}"
                    else:
                        await update.message.reply_text("❌ Unsupported service.")
                        return

                    response = requests.get(api_url, headers=headers, timeout=60)
                    if response.status_code == 200:
                        # ENHANCED URL extraction with multiple strategies
                        def extract_media_url(response_obj):
                            media_url = None
                            try:
                                # Strategy 1: Try JSON parsing
                                try:
                                    data = response_obj.json()
                                    
                                    # TikTok specific
                                    if 'data' in data:
                                        if isinstance(data['data'], dict):
                                            media_url = (data['data'].get('play') or 
                                                       data['data'].get('wmplay') or 
                                                       data['data'].get('hdplay') or
                                                       data['data'].get('download'))
                                    
                                    # Common patterns
                                    if not media_url:
                                        for key in ['url', 'download_url', 'play_url', 'media_url', 'video_url', 
                                                   'download', 'link', 'media', 'video', 'audio_url']:
                                            if key in data and data[key]:
                                                media_url = data[key]
                                                break
                                    
                                    # Nested in results array
                                    if not media_url and 'results' in data:
                                        if isinstance(data['results'], list) and len(data['results']) > 0:
                                            media_url = data['results'][0].get('url') or data['results'][0].get('download_url')
                                        elif isinstance(data['results'], dict):
                                            media_url = data['results'].get('url') or data['results'].get('download_url')
                                    
                                    # Check for nested video object
                                    if not media_url and 'video' in data:
                                        if isinstance(data['video'], str):
                                            media_url = data['video']
                                        elif isinstance(data['video'], dict):
                                            media_url = data['video'].get('url') or data['video'].get('download_url')
                                            
                                except json.JSONDecodeError:
                                    pass
                                
                                # Strategy 2: Regex extraction from text
                                if not media_url:
                                    text = response_obj.text
                                    # Look for video URLs
                                    patterns = [
                                        r'https?://[^\s<>"\']+\.(?:mp4|webm|m3u8|ts)(?:\?[^\s<>"\']*)?',
                                        r'"(?:url|download_url|play_url|video_url|media_url)"\s*:\s*"([^"]+)"',
                                        r'href="([^"]+\.(?:mp4|webm))"'
                                    ]
                                    
                                    for pattern in patterns:
                                        matches = re.findall(pattern, text, re.IGNORECASE)
                                        if matches:
                                            media_url = matches[0] if isinstance(matches[0], str) else matches[0][0]
                                            break
                                
                            except Exception as e:
                                logger.error(f"URL extraction error: {e}")
                            
                            return media_url

                        media_url = extract_media_url(response)
                        
                        if media_url:
                            try:
                                # Clean up URL
                                if media_url.startswith('//'):
                                    media_url = 'https:' + media_url
                                elif not media_url.startswith('http'):
                                    media_url = 'https://' + media_url
                                
                                # Download media with enhanced headers
                                download_headers = {
                                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                                    'Accept': '*/*',
                                    'Accept-Encoding': 'identity',
                                    'Range': 'bytes=0-'
                                }
                                
                                media_response = requests.get(media_url, stream=True, headers=download_headers, timeout=120, allow_redirects=True)
                                media_response.raise_for_status()
                                
                                # Check file size
                                content_length = int(media_response.headers.get('content-length', 0))
                                max_size = 50 * 1024 * 1024  # 50MB for Telegram
                                
                                if content_length > max_size:
                                    await update.message.reply_text(
                                        f"❌ File too large: {content_length/(1024*1024):.1f}MB\n"
                                        f"Maximum allowed: {max_size/(1024*1024):.0f}MB\n\n"
                                        f"🔗 Direct Link: {media_url}"
                                    )
                                    return

                                # Download file with progress indication
                                media_data = BytesIO()
                                downloaded = 0
                                
                                for chunk in media_response.iter_content(chunk_size=16384):
                                    if chunk:
                                        media_data.write(chunk)
                                        downloaded += len(chunk)
                                
                                media_data.seek(0)
                                
                                # Determine file type and extension
                                content_type = media_response.headers.get('content-type', '').lower()
                                ext = '.mp4'  # default
                                
                                if 'video/webm' in content_type or media_url.endswith('.webm'):
                                    ext = '.webm'
                                elif 'video/quicktime' in content_type or media_url.endswith('.mov'):
                                    ext = '.mov'
                                elif 'audio/mpeg' in content_type or media_url.endswith('.mp3'):
                                    ext = '.mp3'
                                elif 'audio/' in content_type:
                                    ext = '.mp3'
                                elif media_url.endswith('.m3u8'):
                                    # HLS stream - inform user
                                    await update.message.reply_text(
                                        f"⚠️ This is a streaming link (HLS).\n\n"
                                        f"🔗 Direct Link: {media_url}\n\n"
                                        f"💡 Use a video downloader app to save this video."
                                    )
                                    return
                                    
                                filename = f"{service}_{int(time.time())}{ext}"
                                
                                # Send based on file type
                                if ext in ['.mp4', '.webm', '.mov']:
                                    # Try as video first
                                    try:
                                        await context.bot.send_video(
                                            chat_id=user_id,
                                            video=media_data,
                                            filename=filename,
                                            caption=f"✅ Downloaded from {service.capitalize()}\n📦 Size: {downloaded/(1024*1024):.1f}MB",
                                            supports_streaming=True,
                                            read_timeout=180,
                                            write_timeout=180
                                        )
                                    except Exception as video_err:
                                        logger.warning(f"Video send failed, trying document: {video_err}")
                                        media_data.seek(0)
                                        await context.bot.send_document(
                                            chat_id=user_id,
                                            document=media_data,
                                            filename=filename,
                                            caption=f"✅ Downloaded from {service.capitalize()}",
                                            read_timeout=180,
                                            write_timeout=180
                                        )
                                elif ext == '.mp3':
                                    # Send as audio
                                    try:
                                        await context.bot.send_audio(
                                            chat_id=user_id,
                                            audio=media_data,
                                            filename=filename,
                                            caption=f"✅ Downloaded from {service.capitalize()}",
                                            read_timeout=180,
                                            write_timeout=180
                                        )
                                    except Exception:
                                        media_data.seek(0)
                                        await context.bot.send_document(
                                            chat_id=user_id,
                                            document=media_data,
                                            filename=filename,
                                            caption=f"✅ Downloaded from {service.capitalize()}"
                                        )
                                else:
                                    # Send as document
                                    await context.bot.send_document(
                                        chat_id=user_id,
                                        document=media_data,
                                        filename=filename,
                                        caption=f"✅ Downloaded from {service.capitalize()}"
                                    )
                                    
                            except requests.exceptions.RequestException as e:
                                logger.error(f"Media download network error: {e}")
                                await update.message.reply_text(
                                    f"❌ Download failed: {str(e)}\n\n"
                                    f"🔗 Try this direct link:\n{media_url}"
                                )
                            except Exception as e:
                                logger.error(f"Media send error: {e}")
                                await update.message.reply_text(
                                    f"❌ Failed to send media: {str(e)}\n\n"
                                    f"🔗 Direct Link: {media_url}"
                                )
                        else:
                            # No media URL found - provide detailed response
                            try:
                                data = response.json()
                                formatted_response = json.dumps(data, indent=2, ensure_ascii=False)
                            except:
                                formatted_response = response.text
                            
                            bio = BytesIO(formatted_response.encode('utf-8'))
                            bio.seek(0)
                            await context.bot.send_document(
                                chat_id=user_id,
                                document=bio,
                                filename=f"{service}_api_response.txt",
                                caption=f"❌ Could not extract media URL from {service.capitalize()}\n\n📄 Full API response attached for debugging."
                            )
                    else:
                        await update.message.reply_text(
                            f"❌ API returned error: {response.status_code}\n\n"
                            f"Service: {service.capitalize()}\n"
                            f"Please check if the URL is valid."
                        )
                except requests.exceptions.Timeout:
                    await update.message.reply_text("❌ Request timed out. The video might be too large or the server is slow.")
                except requests.exceptions.ConnectionError:
                    await update.message.reply_text("❌ Connection error. Please check your internet and try again.")
                except Exception as e:
                    logger.error(f"Download API error: {e}")
                    await update.message.reply_text(f"❌ Unexpected error: {str(e)}")
                finally:
                    context.user_data.pop('awaiting', None)
                    if loading_msg:
                        try:
                            await loading_msg.delete()
                        except Exception:
                            pass
                    await self.show_main_menu(update, context)
                return

            # Search flows
            if awaiting and awaiting.startswith('cat_search'):
                query_text = message_text.strip()
                
                # Map subcategory to API using correct endpoints
                if awaiting == 'cat_search_apk':
                    # APK Search API
                    api_url = f"https://apk-downloader.bjcoderx.workers.dev/?query={requests.utils.quote(query_text)}"
                elif awaiting == 'cat_search_google':
                    # Google Search API
                    api_url = f"https://google-search.bjcoderx.workers.dev/?q={requests.utils.quote(query_text)}"
                elif awaiting == 'cat_search_pinterest':
                    # Pinterest Search API - extract limit if provided
                    parts = query_text.split()
                    search_term = parts[0] if parts else query_text
                    limit = parts[1] if len(parts) > 1 else '5'
                    api_url = f"https://pinterest-search.apis-bj-devs.workers.dev/?search={requests.utils.quote(search_term)}&limit={limit}"
                elif awaiting == 'cat_search_bing':
                    # Bing Search API - extract limit if provided
                    parts = query_text.split()
                    search_term = parts[0] if parts else query_text
                    limit = parts[1] if len(parts) > 1 else '2'
                    api_url = f"https://bing-search.apis-bj-devs.workers.dev/?search={requests.utils.quote(search_term)}&limit={limit}"
                else:
                    await update.message.reply_text("❌ Unknown search service.")
                    return

                # Call search API
                try:
                    response = requests.get(api_url, timeout=30)
                    if response.status_code == 200:
                        try:
                            data = response.json()
                            # Format and send results as JSON file
                            result_text = json.dumps(data, indent=2, ensure_ascii=False)
                        except:
                            result_text = response.text
                        
                        bio = BytesIO()
                        bio.write(result_text.encode('utf-8'))
                        bio.seek(0)
                        await context.bot.send_document(chat_id=user_id, document=bio, filename=f'search_results.txt')
                    else:
                        await update.message.reply_text(f"❌ Search API Error: {response.status_code}")
                except Exception as e:
                    logger.error(f"Search API error: {e}")
                    await update.message.reply_text("❌ Failed to search.")
                finally:
                    context.user_data.pop('awaiting', None)
                    if loading_msg:
                        try:
                            await loading_msg.delete()
                        except Exception:
                            pass
                    await self.show_main_menu(update, context)
                return

            # Tools flows
            if awaiting and awaiting.startswith('cat_tools'):
                tool = context.user_data.get('current_tool')
                if not tool:
                    await update.message.reply_text("❌ No tool selected.")
                    return

                input_text = message_text.strip()
                # Call tool API using correct endpoints
                try:
                    if tool == 'sim':
                        api_url = f"https://legendxdata.site/Api/simdata.php?phone={requests.utils.quote(input_text)}"
                    elif tool == 'imei':
                        api_url = f"https://legendxdata.site/Api/imei.php?imei_num={requests.utils.quote(input_text)}"
                    elif tool == 'ip':
                        api_url = f"https://ip-info.bjcoderx.workers.dev/?ip={requests.utils.quote(input_text)}"
                    elif tool == 'country':
                        api_url = f"https://countrys-information.apis-bj-devs.workers.dev/?name={requests.utils.quote(input_text)}"
                    elif tool == 'nation':
                        api_url = f"https://nation-info.apis-bj-devs.workers.dev/?name={requests.utils.quote(input_text)}"
                    elif tool == 'translate':
                        # Format: text fr to (e.g., "Hello en ur")
                        parts = input_text.split()
                        if len(parts) >= 3:
                            text = ' '.join(parts[:-2])
                            fr = parts[-2]
                            to = parts[-1]
                            api_url = f"https://translator.bjcoderx.workers.dev/?text={requests.utils.quote(text)}&fr={fr}&to={to}"
                        else:
                            await update.message.reply_text("❌ Format: text from_lang to_lang (e.g., Hello en ur)")
                            return
                    elif tool == 'exchange':
                        # Format: From Amount To (e.g., "USD 10 INR")
                        parts = input_text.split()
                        if len(parts) >= 3:
                            from_curr = parts[0]
                            amount = parts[1]
                            to_curr = parts[2]
                            api_url = f"https://real-time-global-exchange-rates.bjcoderx.workers.dev/?From={from_curr}&Amount={amount}&To={to_curr}"
                        else:
                            await update.message.reply_text("❌ Format: From Amount To (e.g., USD 10 INR)")
                            return
                    elif tool == 'qr':
                        api_url = f"https://dynamic-qr-code.bjcoderx.workers.dev/?message={requests.utils.quote(input_text)}"
                    elif tool == 'encrypt':
                        api_url = f"https://txtmoji-lock.manzoor76b.workers.dev/?Encrypt={requests.utils.quote(input_text)}"
                    elif tool == 'enhance':
                        api_url = f"https://image-enhance.apis-bj-devs.workers.dev/?imageurl={requests.utils.quote(input_text)}"
                    else:
                        await update.message.reply_text("❌ Unknown tool.")
                        return
                    
                    headers = {}
                    response = requests.get(api_url, headers=headers, timeout=30)
                    if response.status_code == 200:
                        try:
                            data = response.json()
                            result_text = json.dumps(data, indent=2, ensure_ascii=False)
                        except:
                            result_text = response.text
                        
                        # For QR code, try to send as image
                        if tool == 'qr':
                            try:
                                data = response.json()
                                qr_url = data.get('qr_url') or data.get('url') or data.get('image')
                                if qr_url:
                                    img_response = requests.get(qr_url, timeout=30)
                                    if img_response.status_code == 200:
                                        img_data = BytesIO(img_response.content)
                                        img_data.seek(0)
                                        await context.bot.send_photo(chat_id=user_id, photo=img_data, caption="✅ QR Code Generated")
                                    else:
                                        await context.bot.send_photo(chat_id=user_id, photo=qr_url, caption="✅ QR Code Generated")
                                else:
                                    # Fallback to text
                                    bio = BytesIO()
                                    bio.write(result_text.encode('utf-8'))
                                    bio.seek(0)
                                    await context.bot.send_document(chat_id=user_id, document=bio, filename=f'{tool}_result.txt')
                            except:
                                bio = BytesIO()
                                bio.write(result_text.encode('utf-8'))
                                bio.seek(0)
                                await context.bot.send_document(chat_id=user_id, document=bio, filename=f'{tool}_result.txt')
                        elif tool == 'enhance':
                            # Image enhance - send as image
                            try:
                                data = response.json()
                                img_url = data.get('enhanced_url') or data.get('url') or data.get('image')
                                if img_url:
                                    img_response = requests.get(img_url, timeout=30)
                                    if img_response.status_code == 200:
                                        img_data = BytesIO(img_response.content)
                                        img_data.seek(0)
                                        await context.bot.send_photo(chat_id=user_id, photo=img_data, caption="✅ Image Enhanced")
                                    else:
                                        await context.bot.send_photo(chat_id=user_id, photo=img_url, caption="✅ Image Enhanced")
                                else:
                                    bio = BytesIO()
                                    bio.write(result_text.encode('utf-8'))
                                    bio.seek(0)
                                    await context.bot.send_document(chat_id=user_id, document=bio, filename=f'{tool}_result.txt')
                            except:
                                bio = BytesIO()
                                bio.write(result_text.encode('utf-8'))
                                bio.seek(0)
                                await context.bot.send_document(chat_id=user_id, document=bio, filename=f'{tool}_result.txt')
                        else:
                            # Send as text file
                            bio = BytesIO()
                            bio.write(result_text.encode('utf-8'))
                            bio.seek(0)
                            await context.bot.send_document(chat_id=user_id, document=bio, filename=f'{tool}_info.txt')
                    else:
                        await update.message.reply_text(f"❌ Tool API Error: {response.status_code}")
                except Exception as e:
                    logger.error(f"Tool API error: {e}")
                    await update.message.reply_text("❌ Failed to get tool information.")
                finally:
                    context.user_data.pop('awaiting', None)
                    context.user_data.pop('current_tool', None)
                    if loading_msg:
                        try:
                            await loading_msg.delete()
                        except Exception:
                            pass
                    await self.show_main_menu(update, context)
                return

        # If not awaiting specific input, show main menu
        await self.show_main_menu(update, context)

    async def handle_admin_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, message_text: str):
        """Handle admin actions with user notifications and owner protection"""
        admin_user_id = update.effective_user.id
        
        try:
            if action == 'add_credits':
                parts = message_text.split()
                if len(parts) != 2:
                    await update.message.reply_text("❌ Invalid format. Use: `user_id amount`")
                    return
                
                target_user_id = int(parts[0])
                
                # PROTECTION: Prevent targeting the owner
                if target_user_id == BOT_OWNER_ID and admin_user_id != BOT_OWNER_ID:
                    await update.message.reply_text(
                        "❌ **ACCESS DENIED!**\n\n"
                        "🔒 You cannot modify the bot owner's credits.\n\n"
                        f"👑 Owner: @{BOT_OWNER_USERNAME} is protected.",
                        parse_mode='Markdown'
                    )
                    return
                amount = int(parts[1])
                
                MAX_CREDITS = 99999
                
                conn = self.get_db_connection()
                cursor = conn.cursor()
                
                # Get current credits first
                cursor.execute("SELECT credits, username FROM users WHERE user_id = ?", (target_user_id,))
                result = cursor.fetchone()
                if not result:
                    conn.close()
                    await update.message.reply_text(f"❌ User {target_user_id} not found!")
                    return
                
                current_credits, username = result[0], result[1]
                new_credits = min(current_credits + amount, MAX_CREDITS)
                actual_added = new_credits - current_credits
                
                # Update credits with limit check
                cursor.execute("UPDATE users SET credits = ? WHERE user_id = ?", (new_credits, target_user_id))
                conn.commit()
                conn.close()
                
                # Notify the user about credit addition
                try:
                    user_notification = f"""🎁 **CREDITS ADDED!**

━━━━━━━━━━━━━━━━━━━━━

💰 Admin has added credits to your account!

🎁 **Credits Added:** `{actual_added}`
💎 **New Balance:** `{new_credits}`
📊 **Previous Balance:** `{current_credits}`

━━━━━━━━━━━━━━━━━━━━━

✨ Use your credits wisely!

� **Contact Admin:** @{BOT_OWNER_USERNAME}
🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                    
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=user_notification,
                        parse_mode='Markdown'
                    )
                except Exception as notify_err:
                    logger.warning(f"Failed to notify user {target_user_id}: {notify_err}")
                
                # Send confirmation to admin
                if actual_added < amount:
                    await update.message.reply_text(
                        f"✅ Added {actual_added} credits to user {target_user_id} (@{username})\n"
                        f"⚠️ Credit limit reached (Max: {MAX_CREDITS})\n"
                        f"💰 New balance: {new_credits}\n"
                        f"📬 User has been notified!"
                    )
                else:
                    await update.message.reply_text(
                        f"✅ Added {amount} credits to user {target_user_id} (@{username})\n"
                        f"💰 New balance: {new_credits}\n"
                        f"📬 User has been notified!"
                    )
                
            elif action == 'remove_credits':
                parts = message_text.split()
                if len(parts) != 2:
                    await update.message.reply_text("❌ Invalid format. Use: `user_id amount`")
                    return
                
                target_user_id = int(parts[0])
                
                # PROTECTION: Prevent targeting the owner
                if target_user_id == BOT_OWNER_ID and admin_user_id != BOT_OWNER_ID:
                    await update.message.reply_text(
                        "❌ **ACCESS DENIED!**\n\n"
                        "🔒 You cannot remove credits from the bot owner.\n\n"
                        f"👑 Owner: @{BOT_OWNER_USERNAME} is protected.",
                        parse_mode='Markdown'
                    )
                    return
                
                amount = int(parts[1])
                
                conn = self.get_db_connection()
                cursor = conn.cursor()
                
                # Get current credits first
                cursor.execute("SELECT credits, username FROM users WHERE user_id = ?", (target_user_id,))
                result = cursor.fetchone()
                if not result:
                    conn.close()
                    await update.message.reply_text(f"❌ User {target_user_id} not found!")
                    return
                
                current_credits, username = result[0], result[1]
                new_credits = max(0, current_credits - amount)  # Prevent negative credits
                actual_removed = current_credits - new_credits
                
                # Update credits with validation
                cursor.execute("UPDATE users SET credits = ? WHERE user_id = ?", (new_credits, target_user_id))
                conn.commit()
                conn.close()
                
                # Notify the user about credit removal
                try:
                    user_notification = f"""⚠️ **CREDITS DEDUCTED**

━━━━━━━━━━━━━━━━━━━━━

💰 Admin has deducted credits from your account.

📉 **Credits Removed:** `{actual_removed}`
💎 **New Balance:** `{new_credits}`
📊 **Previous Balance:** `{current_credits}`

━━━━━━━━━━━━━━━━━━━━━

💡 Use /invite to earn more credits!

� **Contact Admin:** @{BOT_OWNER_USERNAME}
�🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                    
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=user_notification,
                        parse_mode='Markdown'
                    )
                except Exception as notify_err:
                    logger.warning(f"Failed to notify user {target_user_id}: {notify_err}")
                
                # Send confirmation to admin
                if actual_removed < amount:
                    await update.message.reply_text(
                        f"✅ Removed {actual_removed} credits from user {target_user_id} (@{username})\n"
                        f"⚠️ Credits cannot go below 0\n"
                        f"💰 New balance: {new_credits}\n"
                        f"📬 User has been notified!"
                    )
                else:
                    await update.message.reply_text(
                        f"✅ Removed {amount} credits from user {target_user_id} (@{username})\n"
                        f"💰 New balance: {new_credits}\n"
                        f"📬 User has been notified!"
                    )
                
            elif action == 'ban_user':
                target_user_id = int(message_text.strip())
                
                # PROTECTION: Prevent banning the owner
                if target_user_id == BOT_OWNER_ID:
                    await update.message.reply_text(
                        "❌ **ACCESS DENIED!**\n\n"
                        "🔒 The bot owner cannot be banned.\n\n"
                        f"👑 Owner: @{BOT_OWNER_USERNAME} has ultimate protection.",
                        parse_mode='Markdown'
                    )
                    return
                
                # PROTECTION: Prevent non-owner from banning other admins
                if admin_user_id != BOT_OWNER_ID:
                    conn = self.get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT user_id FROM bot_admins 
                        WHERE user_id = ? AND status = 'active'
                    """, (target_user_id,))
                    is_admin = cursor.fetchone()
                    conn.close()
                    
                    if is_admin:
                        await update.message.reply_text(
                            "❌ **ACCESS DENIED!**\n\n"
                            "🔒 You cannot ban another admin.\n\n"
                            "💡 Only the owner can ban admins.",
                            parse_mode='Markdown'
                        )
                        return
                
                conn = self.get_db_connection()
                cursor = conn.cursor()
                
                # Check if user exists
                cursor.execute("SELECT user_id, username FROM users WHERE user_id = ?", (target_user_id,))
                result = cursor.fetchone()
                if not result:
                    conn.close()
                    await update.message.reply_text(f"❌ User {target_user_id} not found!")
                    return
                
                username = result[1]
                
                cursor.execute("UPDATE users SET is_banned = TRUE WHERE user_id = ?", (target_user_id,))
                conn.commit()
                conn.close()
                
                # Notify the user about ban
                try:
                    user_notification = f"""🚫 **ACCOUNT SUSPENDED**

━━━━━━━━━━━━━━━━━━━━━

⚠️ Your account has been suspended.

❌ You can no longer use bot features.

━━━━━━━━━━━━━━━━━━━━━

📧 **For Appeals, Contact:** @{BOT_OWNER_USERNAME}

🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                    
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=user_notification,
                        parse_mode='Markdown'
                    )
                except Exception as notify_err:
                    logger.warning(f"Failed to notify banned user {target_user_id}: {notify_err}")
                
                await update.message.reply_text(
                    f"✅ User {target_user_id} (@{username}) has been banned\n"
                    f"📬 User has been notified!"
                )
                
            elif action == 'unban_user':
                target_user_id = int(message_text.strip())
                
                conn = self.get_db_connection()
                cursor = conn.cursor()
                
                # Check if user exists
                cursor.execute("SELECT user_id, username FROM users WHERE user_id = ?", (target_user_id,))
                result = cursor.fetchone()
                if not result:
                    conn.close()
                    await update.message.reply_text(f"❌ User {target_user_id} not found!")
                    return
                
                username = result[1]
                
                cursor.execute("UPDATE users SET is_banned = FALSE WHERE user_id = ?", (target_user_id,))
                conn.commit()
                conn.close()
                
                # Notify the user about unban
                try:
                    user_notification = f"""✅ **ACCOUNT RESTORED**

━━━━━━━━━━━━━━━━━━━━━

🎉 Great news! Your account has been unbanned!

✨ You can now use all bot features again.

━━━━━━━━━━━━━━━━━━━━━

💡 Please follow our guidelines.

📧 **Need Help? Contact:** @{BOT_OWNER_USERNAME}
🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                    
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=user_notification,
                        parse_mode='Markdown'
                    )
                except Exception as notify_err:
                    logger.warning(f"Failed to notify unbanned user {target_user_id}: {notify_err}")
                
                await update.message.reply_text(
                    f"✅ User {target_user_id} (@{username}) has been unbanned\n"
                    f"📬 User has been notified!"
                )
                
            elif action == 'change_admin':
                # This now ADDS a new admin instead of replacing
                new_admin_id = int(message_text.strip())
                
                # Only owner can add new admins
                admin_user_id = update.effective_user.id
                if not self.is_owner(admin_user_id):
                    await update.message.reply_text(
                        "❌ **Permission Denied!**\n\n"
                        "Only the bot owner can add new admins.\n\n"
                        f"📧 **Contact Owner:** @{BOT_OWNER_USERNAME}",
                        parse_mode='Markdown'
                    )
                    context.user_data.pop('admin_action', None)
                    return
                
                conn = self.get_db_connection()
                cursor = conn.cursor()
                
                # Check if user exists
                cursor.execute("SELECT username FROM users WHERE user_id = ?", (new_admin_id,))
                result = cursor.fetchone()
                if not result:
                    conn.close()
                    await update.message.reply_text(f"❌ User {new_admin_id} not found in database!")
                    context.user_data.pop('admin_action', None)
                    return
                
                new_admin_username = result[0] or "Unknown"
                
                # Check if already an admin
                cursor.execute("SELECT user_id FROM bot_admins WHERE user_id = ?", (new_admin_id,))
                if cursor.fetchone():
                    conn.close()
                    await update.message.reply_text(
                        f"⚠️ User {new_admin_id} (@{new_admin_username}) is already an admin!"
                    )
                    context.user_data.pop('admin_action', None)
                    return
                
                # Add new admin
                cursor.execute("""
                    INSERT INTO bot_admins (user_id, username, added_by, is_owner, can_export)
                    VALUES (?, ?, ?, FALSE, FALSE)
                """, (new_admin_id, new_admin_username, admin_user_id))
                conn.commit()
                
                # Get all users for broadcast
                cursor.execute("SELECT user_id FROM users WHERE is_banned = FALSE")
                users = cursor.fetchall()
                conn.close()
                
                # Notify all users (without admin commands info)
                broadcast_text = f"""🔔 **SYSTEM UPDATE**

━━━━━━━━━━━━━━━━━━━━━

Bot has been updated with new features.

━━━━━━━━━━━━━━━━━━━━━

📧 **Need Help? Contact:** @{BOT_OWNER_USERNAME}

🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                
                successful = 0
                for user_row in users:
                    try:
                        await context.bot.send_message(
                            chat_id=user_row[0],
                            text=broadcast_text,
                            parse_mode='Markdown'
                        )
                        successful += 1
                        await asyncio.sleep(0.05)
                    except Exception:
                        continue
                
                # Notify the new admin privately
                try:
                    admin_welcome = f"""🎉 **ADMIN ACCESS GRANTED!**

━━━━━━━━━━━━━━━━━━━━━

👑 You are now a Bot Admin!

✨ You have admin privileges to manage the bot.

⚠️ **Note:** Only the owner can export database.

━━━━━━━━━━━━━━━━━━━━━

📧 **Owner:** @{BOT_OWNER_USERNAME}
🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                    
                    await context.bot.send_message(
                        chat_id=new_admin_id,
                        text=admin_welcome,
                        parse_mode='Markdown'
                    )
                except Exception:
                    pass
                
                await update.message.reply_text(
                    f"✅ **New admin added successfully!**\n\n"
                    f"👤 User ID: {new_admin_id}\n"
                    f"📛 Username: @{new_admin_username}\n"
                    f"📢 System updated notification sent to {successful} users\n\n"
                    f"⚠️ **Note:** Owner (@{BOT_OWNER_USERNAME}) cannot be removed.",
                    parse_mode='Markdown'
                )
                
            elif action == 'change_channels':
                parts = message_text.split()
                if len(parts) != 2:
                    await update.message.reply_text("❌ Invalid format. Use: `channel1 channel2`")
                    return
                
                channel1 = parts[0]
                channel2 = parts[1]
                
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("UPDATE admin_settings SET channel_1 = ?, channel_2 = ? WHERE id = 1", (channel1, channel2))
                conn.commit()
                
                # Get all users for broadcast
                cursor.execute("SELECT user_id FROM users WHERE is_banned = FALSE")
                users = cursor.fetchall()
                conn.close()
                
                # Notify all users about channel change
                broadcast_text = f"""🔔 **IMPORTANT UPDATE**

━━━━━━━━━━━━━━━━━━━━━

📢 **Required Channels Updated!**

New subscription channels:
📍 Channel 1: {channel1}
📍 Channel 2: {channel2}

⚠️ Please join to continue using the bot.

━━━━━━━━━━━━━━━━━━━━━

📧 **Need Help? Contact:** @{BOT_OWNER_USERNAME}
🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                
                successful = 0
                for user_row in users:
                    try:
                        await context.bot.send_message(
                            chat_id=user_row[0],
                            text=broadcast_text,
                            parse_mode='Markdown'
                        )
                        successful += 1
                        await asyncio.sleep(0.05)
                    except Exception:
                        continue
                
                await update.message.reply_text(
                    f"✅ Channels updated:\n"
                    f"📢 Channel 1: {channel1}\n"
                    f"📢 Channel 2: {channel2}\n\n"
                    f"📡 Broadcasted to {successful} users!"
                )
            
            elif action == 'remove_admin':
                # Only owner can remove admins
                admin_user_id = update.effective_user.id
                if not self.is_owner(admin_user_id):
                    await update.message.reply_text(
                        "❌ **Permission Denied!**\n\n"
                        "Only the bot owner can remove admins.",
                        parse_mode='Markdown'
                    )
                    context.user_data.pop('admin_action', None)
                    return
                
                target_user_id = int(message_text.strip())
                
                # Prevent owner from removing themselves
                if target_user_id == BOT_OWNER_ID:
                    await update.message.reply_text(
                        "❌ **Cannot Remove Owner!**\n\n"
                        "The bot owner cannot be removed.",
                        parse_mode='Markdown'
                    )
                    context.user_data.pop('admin_action', None)
                    return
                
                conn = self.get_db_connection()
                cursor = conn.cursor()
                
                # Check if user is an admin
                cursor.execute("""
                    SELECT username FROM bot_admins 
                    WHERE user_id = ? AND status = 'active'
                """, (target_user_id,))
                result = cursor.fetchone()
                
                if not result:
                    conn.close()
                    await update.message.reply_text(
                        f"❌ User {target_user_id} is not an active admin!",
                        parse_mode='Markdown'
                    )
                    context.user_data.pop('admin_action', None)
                    return
                
                removed_username = result[0]
                
                # Remove admin (set status to inactive instead of deleting)
                cursor.execute("""
                    UPDATE bot_admins 
                    SET status = 'inactive' 
                    WHERE user_id = ?
                """, (target_user_id,))
                conn.commit()
                conn.close()
                
                # Notify the removed admin
                try:
                    removal_notification = f"""🔔 **ADMIN STATUS REMOVED**

━━━━━━━━━━━━━━━━━━━━━

⚠️ Your admin privileges have been revoked.

You can now only use regular bot features.

━━━━━━━━━━━━━━━━━━━━━

📧 **Contact Owner:** @{BOT_OWNER_USERNAME}
🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                    
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=removal_notification,
                        parse_mode='Markdown'
                    )
                except Exception:
                    pass
                
                await update.message.reply_text(
                    f"✅ **Admin removed successfully!**\n\n"
                    f"👤 User ID: {target_user_id}\n"
                    f"📛 Username: @{removed_username}\n"
                    f"📬 User has been notified.",
                    parse_mode='Markdown'
                )
                
            elif action == 'credit_settings':
                parts = message_text.split()
                if len(parts) != 2:
                    await update.message.reply_text("❌ Invalid format. Use: `invite_reward starting_credits`")
                    return
                
                invite_reward = int(parts[0])
                starting_credits = int(parts[1])
                
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("UPDATE admin_settings SET credits_per_invite = ?, starting_credits = ? WHERE id = 1", 
                              (invite_reward, starting_credits))
                conn.commit()
                
                # Get all users for broadcast
                cursor.execute("SELECT user_id FROM users WHERE is_banned = FALSE")
                users = cursor.fetchall()
                conn.close()
                
                # Notify all users about credit system changes
                broadcast_text = f"""🔔 **CREDIT SYSTEM UPDATE**

━━━━━━━━━━━━━━━━━━━━━

💰 **New Credit Rewards!**

🎁 Invite Reward: `{invite_reward}` credits
🆕 Starting Credits: `{starting_credits}` credits

💡 Invite friends to earn more credits!
Use /invite to get your referral link.

━━━━━━━━━━━━━━━━━━━━━

🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                
                successful = 0
                for user_row in users:
                    try:
                        await context.bot.send_message(
                            chat_id=user_row[0],
                            text=broadcast_text,
                            parse_mode='Markdown'
                        )
                        successful += 1
                        await asyncio.sleep(0.05)
                    except Exception:
                        continue
                
                await update.message.reply_text(
                    f"✅ Credit settings updated:\n"
                    f"🎁 Invite reward: {invite_reward}\n"
                    f"💰 Starting credits: {starting_credits}\n\n"
                    f"📡 Broadcasted to {successful} users!"
                )
            
            # Clear admin action
            context.user_data.pop('admin_action', None)
            
            # Return to admin panel
            await self.show_admin_panel_from_message(update, context)
            
        except ValueError:
            await update.message.reply_text("❌ Invalid input format. Please check your input.")
        except Exception as e:
            logger.error(f"Admin action error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def show_admin_panel_from_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show admin panel from message context"""
        keyboard = [
            [InlineKeyboardButton("📊 Bot Statistics", callback_data="admin_stats")],
            [InlineKeyboardButton("👥 User Management", callback_data="admin_users")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "🔧 **Admin Panel**\n\nSelect an option:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def show_admin_users(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Show user management options"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
            
        keyboard = [
            [InlineKeyboardButton("💰 Add Credits", callback_data="admin_add_credits")],
            [InlineKeyboardButton("💰 Remove Credits", callback_data="admin_remove_credits")],
            [InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban_user")],
            [InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_user")],
            [InlineKeyboardButton("🔙 Back to Admin", callback_data="back_to_admin")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👥 **User Management**\n\nSelect an action:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def show_admin_settings(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Show admin settings options"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
        
        user_id = query.from_user.id
        is_owner = self.is_owner(user_id)
        
        # Different buttons for owner vs regular admin
        if is_owner:
            keyboard = [
                [InlineKeyboardButton("➕ Add Admin", callback_data="admin_change_admin")],
                [InlineKeyboardButton("➖ Remove Admin", callback_data="admin_remove_admin")],
                [InlineKeyboardButton("📋 View All Admins", callback_data="admin_view_admins")],
                [InlineKeyboardButton("📢 Change Channels", callback_data="admin_change_channels")],
                [InlineKeyboardButton("💰 Credit Settings", callback_data="admin_credit_settings")],
                [InlineKeyboardButton("🔄 Reset Settings", callback_data="admin_reset_settings")],
                [InlineKeyboardButton("🔙 Back to Admin", callback_data="back_to_admin")]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("📢 Change Channels", callback_data="admin_change_channels")],
                [InlineKeyboardButton("💰 Credit Settings", callback_data="admin_credit_settings")],
                [InlineKeyboardButton("🔙 Back to Admin", callback_data="back_to_admin")]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        settings_text = "⚙️ **Admin Settings**\n\n"
        if is_owner:
            settings_text += "👑 **Owner Mode** - Full Access\n\n"
        settings_text += "Select an option:"
        
        await query.edit_message_text(
            settings_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Secure admin panel access with comprehensive security checks - WITH LOGGING"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        # Log admin panel access attempt
        logger.info(f"👤 Admin panel access attempt from user {user_id} (@{username})")
        
        # LOG: Admin panel access attempt
        await self.log_user_activity(
            user_id=user_id,
            activity_type="Admin Panel Access Attempt",
            activity_details=f"Username: @{username}"
        )
        
        # Initialize admin security context if not exists
        if not hasattr(self, '_admin_security'):
            self._admin_security = {
                'failed_attempts': {},
                'lockout_until': {},
                'last_access': {}
            }
        
        # Check if user is in lockout period
        current_time = time.time()
        if user_id in self._admin_security['lockout_until']:
            lockout_time = self._admin_security['lockout_until'][user_id]
            if current_time < lockout_time:
                remaining = int(lockout_time - current_time)
                
                # LOG: Locked out attempt
                await self.log_user_activity(
                    user_id=user_id,
                    activity_type="Admin Access Denied - Locked Out",
                    activity_details=f"Remaining lockout: {remaining}s"
                )
                
                await update.message.reply_text(
                    f"🔒 Access temporarily locked. Try again in {remaining} seconds.",
                    parse_mode='Markdown'
                )
                return
            else:
                # Reset after lockout expires
                del self._admin_security['lockout_until'][user_id]
                self._admin_security['failed_attempts'][user_id] = 0
        
        # Verify admin status
        if not await self.verify_admin(user_id):
            # Increment failed attempts
            self._admin_security['failed_attempts'][user_id] = self._admin_security['failed_attempts'].get(user_id, 0) + 1
            attempts = self._admin_security['failed_attempts'][user_id]
            
            # LOG: Failed access
            await self.log_user_activity(
                user_id=user_id,
                activity_type="Admin Access Denied - Unauthorized",
                activity_details=f"Failed attempts: {attempts}"
            )
            
            # Implement progressive lockout
            if attempts >= 5:
                lockout_duration = min(300 * (2 ** (attempts - 5)), 86400)  # Max 24 hour lockout
                self._admin_security['lockout_until'][user_id] = current_time + lockout_duration
                await update.message.reply_text(
                    f"🚫 Access Denied! Too many failed attempts.\nLocked for {lockout_duration//60} minutes.",
                    parse_mode='Markdown'
                )
                logger.warning(f"🔒 User {user_id} locked out for {lockout_duration} seconds after {attempts} failed attempts")
                return
            else:
                await update.message.reply_text(
                    f"❌ Access Denied! {5-attempts} attempts remaining.",
                    parse_mode='Markdown'
                )
                return
        
        # Reset security counters on successful access
        self._admin_security['failed_attempts'][user_id] = 0
        self._admin_security['last_access'][user_id] = current_time
        
        # LOG: Successful admin access
        await self.log_admin_action(
            admin_id=user_id,
            action_type="Admin Panel Accessed",
            details=f"Username: @{username}, Time: {datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M:%S')}",
            status="success"
        )
        
        # Show admin panel
        try:
            keyboard = [
                [InlineKeyboardButton("📊 Bot Statistics", callback_data="admin_stats")],
                [InlineKeyboardButton("👥 User Management", callback_data="admin_users")],
                [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
                [InlineKeyboardButton("🔒 Security Log", callback_data="admin_security_log")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "🔧 **Admin Panel**\n\n"
                f"👤 Admin: `{username}`\n"
                f"🕒 Last Access: {datetime.fromtimestamp(self._admin_security['last_access'].get(user_id, current_time)).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "Select an option:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
            logger.info(f"✅ Admin {user_id} (@{username}) accessed admin panel successfully")
            
        except Exception as e:
            logger.error(f"❌ Error showing admin panel: {e}")
            await update.message.reply_text(
                "❌ Error loading admin panel. Please try again.",
                parse_mode='Markdown'
            )

    async def verify_admin(self, user_id: int) -> bool:
        """Enhanced admin verification with multiple admins support"""
        # Rate limiting for admin verification attempts
        rate_limit_key = f'admin_verify_{user_id}'
        current_time = time.time()
        
        # Initialize or get rate limiting storage
        if not hasattr(self, '_admin_verify_time'):
            self._admin_verify_time = {}
        if not hasattr(self, '_admin_verify_count'):
            self._admin_verify_count = {}
            
        # Clear old entries (older than 1 hour)
        self._admin_verify_time = {k: v for k, v in self._admin_verify_time.items() 
                                 if current_time - v < 3600}
        self._admin_verify_count = {k: v for k, v in self._admin_verify_count.items() 
                                  if current_time - self._admin_verify_time.get(k, 0) < 3600}
        
        # Check rate limit (max 5 attempts per hour)
        if self._admin_verify_count.get(rate_limit_key, 0) >= 5:
            logger.warning(f"🚫 Admin verification rate limit exceeded for user {user_id}")
            return False
        
        # Update verification attempt count
        self._admin_verify_count[rate_limit_key] = self._admin_verify_count.get(rate_limit_key, 0) + 1
        self._admin_verify_time[rate_limit_key] = current_time
        
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Check if user is banned
            cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
            user_row = cursor.fetchone()
            if user_row and user_row[0]:
                logger.warning(f"🚫 Banned user {user_id} attempted admin access")
                conn.close()
                return False
            
            # Check if user is in bot_admins table (multiple admins support)
            cursor.execute("""
                SELECT user_id, is_owner, status 
                FROM bot_admins 
                WHERE user_id = ? AND status = 'active'
            """, (user_id,))
            admin_row = cursor.fetchone()
            
            if admin_row:
                # Update last admin action timestamp
                cursor.execute("""
                    UPDATE admin_settings 
                    SET last_admin_action = CURRENT_TIMESTAMP
                    WHERE id = 1
                """)
                conn.commit()
                conn.close()
                
                # Reset rate limiting on successful verification
                if rate_limit_key in self._admin_verify_count:
                    del self._admin_verify_count[rate_limit_key]
                if rate_limit_key in self._admin_verify_time:
                    del self._admin_verify_time[rate_limit_key]
                
                return True
            
            # Fallback: Check old admin_settings table for backward compatibility
            cursor.execute("""
                SELECT admin_user_id
                FROM admin_settings 
                WHERE id = 1
            """)
            row = cursor.fetchone()
            
            if row and user_id == int(row[0]):
                # Add to new bot_admins table if not already there
                cursor.execute("""
                    INSERT OR IGNORE INTO bot_admins (user_id, is_owner, can_export, added_by)
                    VALUES (?, TRUE, TRUE, ?)
                """, (user_id, user_id))
                conn.commit()
                conn.close()
                return True
            
            conn.close()
            logger.warning(f"❌ Failed admin verification from user {user_id}")
            return False
            
        except Exception as e:
            logger.error(f"❌ Admin verification error: {e}")
            return False
        finally:
            try:
                if 'conn' in locals():
                    conn.close()
            except Exception:
                pass

    def is_owner(self, user_id: int) -> bool:
        """Check if user is the bot owner"""
        return user_id == BOT_OWNER_ID
    
    def can_export_database(self, user_id: int) -> bool:
        """Check if user can export database (only owner)"""
        return user_id == BOT_OWNER_ID

    async def is_suspicious_user(self, user_id: int, update: Update) -> bool:
        """
        ANTI-REPORT PROTECTION
        Detect suspicious users who might be trying to spam/report the bot
        """
        try:
            # Initialize suspicious activity tracker
            if not hasattr(self, '_suspicious_activity'):
                self._suspicious_activity = {}
            
            current_time = time.time()
            user_key = f'suspicious_{user_id}'
            
            # Check for rapid-fire starts (more than 5 starts in 1 minute)
            if user_key not in self._suspicious_activity:
                self._suspicious_activity[user_key] = {
                    'start_times': [],
                    'last_check': current_time
                }
            
            user_activity = self._suspicious_activity[user_key]
            
            # Clean old entries (older than 1 minute)
            user_activity['start_times'] = [
                t for t in user_activity['start_times'] 
                if current_time - t < 60
            ]
            
            # Add current start time
            user_activity['start_times'].append(current_time)
            
            # Check if user is spamming (more than 5 starts per minute)
            if len(user_activity['start_times']) > 5:
                logger.warning(f"🚨 ANTI-REPORT: User {user_id} is spamming starts - {len(user_activity['start_times'])} attempts in 1 minute")
                return True
            
            # Check if user has no username (common for fake/spam accounts)
            if not update.effective_user.username:
                # Allow owner and existing users without username
                if user_id == BOT_OWNER_ID:
                    return False
                
                # Check if existing user
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
                existing_user = cursor.fetchone()
                conn.close()
                
                # If new user without username, mark as suspicious
                if not existing_user:
                    logger.warning(f"🚨 ANTI-REPORT: New user {user_id} has no username")
                    # Don't block, just log - telegram users can be legit without username
            
            # Owner is never suspicious
            if user_id == BOT_OWNER_ID:
                return False
            
            # All checks passed
            return False
            
        except Exception as e:
            logger.error(f"Error in suspicious user check: {e}")
            return False  # On error, allow user (fail-safe)

    def is_user_banned(self, user_id: int) -> bool:
        """Check if user is banned"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        return result and result[0] == 1

    def get_user_credits(self, user_id: int) -> int:
        """Get user credits"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        return result[0] if result else 0

    def check_and_deduct_credit(self, user_id: int) -> bool:
        """Check if user has credits and deduct 1 credit. Returns True if successful, False if insufficient credits."""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        # Get current credits
        cursor.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        if not result:
            conn.close()
            return False
        
        current_credits = result[0]
        
        # Check if user has sufficient credits (at least 1)
        if current_credits < 1:
            conn.close()
            return False
        
        # Deduct credit only if user has credits
        cursor.execute("UPDATE users SET credits = credits - 1 WHERE user_id = ? AND credits >= 1", (user_id,))
        conn.commit()
        
        # Verify the deduction was successful
        cursor.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
        new_credits = cursor.fetchone()[0]
        conn.close()
        
        # Ensure credits don't go negative
        if new_credits < 0:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET credits = 0 WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            return False
        
        return True
    
    def deduct_credit(self, user_id: int):
        """Deduct 1 credit from user (legacy method - use check_and_deduct_credit instead)"""
        self.check_and_deduct_credit(user_id)

    def generate_invite_code(self) -> str:
        """Generate unique invite code"""
        while True:
            code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users WHERE invite_code = ?", (code,))
            if cursor.fetchone()[0] == 0:
                conn.close()
                return code
            conn.close()

    async def handle_invite_reward(self, invite_code: str, new_user_id: int, bot_instance=None):
        """Handle invite reward system - gives credits to both inviter and invitee"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        # Find inviter
        cursor.execute("SELECT user_id FROM users WHERE invite_code = ?", (invite_code,))
        result = cursor.fetchone()
        
        if result:
            inviter_id = result[0]
            
            # Get credit reward setting
            cursor.execute("SELECT credits_per_invite FROM admin_settings WHERE id = 1")
            reward_result = cursor.fetchone()
            credit_reward = reward_result[0] if reward_result and reward_result[0] else 2
            
            # Award credits to inviter
            cursor.execute("SELECT credits FROM users WHERE user_id = ?", (inviter_id,))
            inviter_credit_result = cursor.fetchone()
            inviter_current_credits = inviter_credit_result[0] if inviter_credit_result else 0
            inviter_new_credits = min(inviter_current_credits + credit_reward, 99999)
            
            cursor.execute("UPDATE users SET credits = ?, total_invites = total_invites + 1 WHERE user_id = ?", 
                          (inviter_new_credits, inviter_id))
            
            # Award credits to invitee (new user)
            cursor.execute("SELECT credits FROM users WHERE user_id = ?", (new_user_id,))
            invitee_credit_result = cursor.fetchone()
            invitee_current_credits = invitee_credit_result[0] if invitee_credit_result else 0
            invitee_new_credits = min(invitee_current_credits + credit_reward, 99999)
            
            cursor.execute("UPDATE users SET credits = ? WHERE user_id = ?", 
                          (invitee_new_credits, new_user_id))
            
            # Record the invite
            cursor.execute('''
                INSERT INTO invites (inviter_id, invitee_id, invite_code, credits_awarded)
                VALUES (?, ?, ?, ?)
            ''', (inviter_id, new_user_id, invite_code, True))
            
            conn.commit()
            
            # Get total invites for notification
            cursor.execute("SELECT total_invites FROM users WHERE user_id = ?", (inviter_id,))
            total_invites_result = cursor.fetchone()
            total_invites = total_invites_result[0] if total_invites_result else 0
            
            # Send notification to inviter
            if bot_instance:
                try:
                    notification_text = f"""🎉 <b>Credit Received!</b>

✅ Aapke referral link se ek naya member join hua hai!

💰 <b>Credits Received:</b> {credit_reward}
💎 <b>Total Credits:</b> {inviter_new_credits}

👥 <b>Total Invites:</b> {total_invites}

📣 Apne aur dosto ko invite karke zyada credits hasil karo!"""
                    
                    await bot_instance.send_message(
                        chat_id=inviter_id,
                        text=notification_text,
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"Failed to send notification to inviter {inviter_id}: {e}")
        
        conn.close()

    def get_db_connection(self):
        """Get database connection with admin security features"""
        conn = sqlite3.connect('bot_database.db', check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
        
    async def log_admin_action(self, admin_id: int, action_type: str, details: str = None, status: str = "success"):
        """Log admin actions securely to database"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Generate unique session ID if not exists
            if not hasattr(self, '_admin_session'):
                self._admin_session = secrets.token_hex(16)
            
            cursor.execute("""
                INSERT INTO admin_log (
                    admin_id, action_type, action_details, 
                    status, session_id
                )
                VALUES (?, ?, ?, ?, ?)
            """, (admin_id, action_type, details, status, self._admin_session))
            
            # Update last admin action timestamp
            cursor.execute("""
                UPDATE admin_settings 
                SET last_admin_action = CURRENT_TIMESTAMP 
                WHERE id = 1
            """)
            
            conn.commit()
            logger.info(f"📝 Admin action logged: {action_type} by {admin_id}")
            
        except Exception as e:
            logger.error(f"❌ Failed to log admin action: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    async def log_user_activity(self, user_id: int, activity_type: str, activity_details: str = None, 
                                credits_used: int = 0, api_response: str = None, input_data: str = None):
        """
        Log all user activities to database for tracking and export
        - Every action is logged with timestamp
        - Input data, usage count, and response stored
        - Can be exported for analysis
        """
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Create detailed activity record
            activity_record = {
                'timestamp': datetime.now().isoformat(),
                'activity': activity_type,
                'details': activity_details or '',
                'input': input_data or '',
                'credits_used': credits_used,
                'status': 'completed' if api_response else 'initiated'
            }
            
            # Insert activity log
            cursor.execute("""
                INSERT INTO user_activity (
                    user_id, activity_type, activity_details, 
                    credits_used, api_response, timestamp
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                user_id, 
                activity_type, 
                json.dumps(activity_record),
                credits_used,
                api_response or 'N/A'
            ))
            
            # Update user's last active time
            cursor.execute("""
                UPDATE users 
                SET last_active = CURRENT_TIMESTAMP 
                WHERE user_id = ?
            """, (user_id,))
            
            conn.commit()
            logger.info(f"📊 User activity logged: {activity_type} by user {user_id}")
            
        except Exception as e:
            logger.error(f"❌ Failed to log user activity: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Exception while handling an update: {context.error}")
        
        # Notify admin about critical errors
        try:
            if context.error:
                error_msg = f"❌ Bot Error:\n{type(context.error).__name__}: {context.error}"
                await context.bot.send_message(chat_id=ADMIN_USER_ID, text=error_msg)
        except Exception:
            pass

    # Other methods remain the same...
    async def credits_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /credits command - WITH LOGGING"""
        user_id = update.effective_user.id
        credits = self.get_user_credits(user_id)
        
        # LOG: Command used
        await self.log_user_activity(
            user_id=user_id,
            activity_type="Command: /credits",
            activity_details=f"Current credits: {credits}"
        )
        
        await update.message.reply_text(f"💰 **Your Credits:** `{credits}`\n\nUse /invite to earn more credits!", parse_mode='Markdown')

    async def invite_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /invite command - WITH LOGGING"""
        user_id = update.effective_user.id
        
        # LOG: Command used
        await self.log_user_activity(
            user_id=user_id,
            activity_type="Command: /invite",
            activity_details="User requested invite link"
        )
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT invite_code, total_invites FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            invite_code, total_invites = result
            # Get bot username safely
            bot_username = context.bot.username if context.bot and context.bot.username else update.get_bot().username if update.get_bot() else None
            if not bot_username:
                # Fallback: try to get from application
                try:
                    bot_username = context.application.bot.username if hasattr(context, 'application') and context.application else None
                except:
                    bot_username = None
            
            if bot_username:
                invite_link = f"https://t.me/{bot_username}?start={invite_code}"
            else:
                # Fallback if username not available
                invite_link = f"https://t.me/share/url?url=start={invite_code}"
            
            # Get credit reward per invite
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT credits_per_invite FROM admin_settings WHERE id = 1")
            reward_result = cursor.fetchone()
            credit_reward = reward_result[0] if reward_result and reward_result[0] else 2
            conn.close()
            
            invite_text = f"""👥 <b>Invite & Earn Credits</b>

🔗 <b>Your Invite Link:</b>
{invite_link}

📊 <b>Total Invites:</b> {total_invites}
🎁 <b>Reward per Invite:</b> {credit_reward} Credits

📣 <b>Share this link with friends:</b>
{invite_link}

💡 <b>How it works:</b>
1. Share your invite link
2. When someone joins using your link
3. You get {credit_reward} credits automatically!"""
            await update.message.reply_text(invite_text, parse_mode='HTML')
        else:
            await update.message.reply_text("❌ User not found!")

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command - WITH LOGGING"""
        user_id = update.effective_user.id
        
        # LOG: Command used
        await self.log_user_activity(
            user_id=user_id,
            activity_type="Command: /stats",
            activity_details="User viewed statistics"
        )
        
        await self.show_user_stats_from_message(update, context)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command - WITH LOGGING"""
        user_id = update.effective_user.id
        
        # LOG: Command used
        await self.log_user_activity(
            user_id=user_id,
            activity_type="Command: /help",
            activity_details="User requested help"
        )
        
        help_text = """
🤖 **Pak INNO CYBER BOT - Help Guide**

📋 **Available Commands:**
• /start - Start the bot
• /credits - Check your credits  
• /invite - Get invite link to earn credits
• /stats - View your statistics
• /help - Show this help message

🎯 **How to Use:**
1. Use /start to begin
2. Join required channels
3. Verify subscription  
4. Select a category from menu
5. Follow instructions for each Search

💰 **Earning Credits:**
• Start: 5 free credits
• Invite friends: 1 credits each
• Regular bonuses

⚠️ **Important:**
• 1 credit per Search
• Use responsibly
• Follow terms of service

Need help? Contact admin.
        """
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def export_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /export command - ONLY BOT OWNER can export"""
        user_id = update.effective_user.id
        
        # Verify OWNER access only (not just admin)
        if not self.can_export_database(user_id):
            await update.message.reply_text(
                "❌ **Access Denied!**\n\n"
                "Only the bot owner can export the database.\n\n"
                f"📧 Contact: @{BOT_OWNER_USERNAME}",
                parse_mode='Markdown'
            )
            return
        
        try:
            # Create CSV export
            csv_file = self.export_database_to_csv()
            
            if csv_file:
                # Send CSV file to owner
                with open(csv_file, 'rb') as f:
                    await update.message.reply_document(
                        document=f,
                        filename=os.path.basename(csv_file),
                        caption="📊 **Complete Database Export**\n\n✅ All user data has been exported successfully!"
                    )
                
                # Clean up temporary file after sending
                try:
                    os.remove(csv_file)
                except:
                    pass
                
                logger.info(f"📤 Database exported by OWNER {user_id}")
            else:
                await update.message.reply_text("❌ Error creating export file. Please check logs.")
                
        except Exception as e:
            logger.error(f"Export error: {e}")
            await update.message.reply_text(f"❌ Export failed: {str(e)}")

    async def announce_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /announce command - Admin only broadcast to all users - PROFESSIONAL VERSION"""
        user_id = update.effective_user.id
        
        # Verify admin access
        if not await self.verify_admin(user_id):
            await update.message.reply_text("❌ Access Denied! This command is for admins only.")
            return
        
        # Check if message text is provided
        if not context.args or len(context.args) == 0:
            await update.message.reply_text(
                "📢 **Professional Announcement System**\n\n"
                "**Usage:** `/announce Your message here`\n\n"
                "**Features:**\n"
                "• Beautifully formatted messages\n"
                "• Automatic emoji styling\n"
                "• Professional layout\n\n"
                "**Example:**\n"
                "`/announce New AI features added! Check out Text-to-Image generator now.`\n\n"
                "**Note:** Your message will be sent to all active users with a professional format.",
                parse_mode='Markdown'
            )
            return
        
        # Get announcement message
        announcement_text = ' '.join(context.args)
        
        try:
            # Show preview to admin first
            preview_text = f"""📢 **ANNOUNCEMENT PREVIEW**

━━━━━━━━━━━━━━━━━━━━

{announcement_text}

━━━━━━━━━━━━━━━━━━━━

**Do you want to send this announcement?**
Type `/confirm_announce` to proceed or wait 30 seconds to cancel."""
            
            await update.message.reply_text(preview_text, parse_mode='Markdown')
            
            # Store announcement in context for confirmation
            context.user_data['pending_announcement'] = announcement_text
            context.user_data['announcement_time'] = time.time()
            
        except Exception as e:
            logger.error(f"Announcement preview error: {e}")
            await update.message.reply_text(f"❌ Error creating announcement preview: {str(e)}")

    async def givecreditsall_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /givecreditsall command - Give credits to all users"""
        user_id = update.effective_user.id
        
        # Verify admin access
        if not await self.verify_admin(user_id):
            await update.message.reply_text("❌ Access Denied! This command is for admins only.")
            return
        
        # Check if credits amount is provided
        if not context.args or len(context.args) == 0:
            await update.message.reply_text(
                "� **Give Credits to All Users**\n\n"
                "**Usage:** `/givecreditsall <amount>`\n\n"
                "**Examples:**\n"
                "• `/givecreditsall 10` - Give 10 credits to everyone\n"
                "• `/givecreditsall 100` - Give 100 credits to everyone\n\n"
                "**Range:** 1 to 10000 credits\n"
                "**Note:** This will give credits to ALL active users!",
                parse_mode='Markdown'
            )
            return
        
        try:
            # Parse credits amount
            credits_amount = int(context.args[0])
            
            # Validate range
            if credits_amount < 1 or credits_amount > 10000:
                await update.message.reply_text(
                    "❌ **Invalid Amount!**\n\n"
                    "Please enter a number between 1 and 10000.",
                    parse_mode='Markdown'
                )
                return
            
            # Get all non-banned users
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, username, credits FROM users WHERE is_banned = FALSE")
            users = cursor.fetchall()
            total_users = len(users)
            
            if total_users == 0:
                await update.message.reply_text("❌ No active users found!")
                conn.close()
                return
            
            # Ask for confirmation
            confirmation_text = f"""💰 **GIVE CREDITS TO ALL - CONFIRMATION**

📊 **Details:**
• Credits to Give: `{credits_amount}`
• Total Recipients: `{total_users}` users
• Total Credits: `{credits_amount * total_users}`

⚠️ **Warning:** This action will:
1. Add {credits_amount} credits to each user
2. Send notification to all users
3. Cannot be undone automatically

**Reply with `/confirm_givecredits` to proceed**
**Or wait 30 seconds to cancel**"""
            
            await update.message.reply_text(confirmation_text, parse_mode='Markdown')
            
            # Store in context for confirmation
            context.user_data['pending_credit_gift'] = {
                'amount': credits_amount,
                'users': users,
                'timestamp': time.time()
            }
            
            conn.close()
            
        except ValueError:
            await update.message.reply_text(
                "❌ **Invalid Input!**\n\n"
                "Please enter a valid number.\n"
                "Example: `/givecreditsall 50`",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Give credits all error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    def export_database_to_csv(self):
        """Export complete database to CSV file"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Get all user data
            cursor.execute('''
                SELECT 
                    user_id, username, first_name, last_name, language_code, is_premium,
                    credits, invited_by, invite_code, total_invites,
                    join_date, last_active, is_banned,
                    phone_number, bio, profile_photo_id,
                    total_groups, total_bots, total_contacts,
                    ip_address, user_agent, device_info,
                    location_data, session_data, additional_info
                FROM users
                ORDER BY join_date DESC
            ''')
            
            users = cursor.fetchall()
            
            # Create CSV file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_filename = f"database_export_{timestamp}.csv"
            
            with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    'User ID', 'Username', 'First Name', 'Last Name', 'Language Code', 'Is Premium',
                    'Credits', 'Invited By', 'Invite Code', 'Total Invites',
                    'Join Date', 'Last Active', 'Is Banned',
                    'Phone Number', 'Bio', 'Profile Photo ID',
                    'Total Groups', 'Total Bots', 'Total Contacts',
                    'IP Address', 'User Agent', 'Device Info',
                    'Location Data', 'Session Data', 'Additional Info'
                ]
                
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for user in users:
                    # Parse JSON fields
                    session_data = user[23] if user[23] else '{}'
                    additional_info = user[24] if user[24] else '{}'
                    
                    try:
                        session_json = json.loads(session_data) if session_data != 'N/A' and session_data else {}
                        additional_json = json.loads(additional_info) if additional_info != 'N/A' and additional_info else {}
                    except:
                        session_json = {}
                        additional_json = {}
                    
                    writer.writerow({
                        'User ID': user[0] or 'N/A',
                        'Username': user[1] or 'N/A',
                        'First Name': user[2] or 'N/A',
                        'Last Name': user[3] or 'N/A',
                        'Language Code': user[4] or 'N/A',
                        'Is Premium': 'Yes' if user[5] else 'No',
                        'Credits': user[6] or 0,
                        'Invited By': user[7] or 'N/A',
                        'Invite Code': user[8] or 'N/A',
                        'Total Invites': user[9] or 0,
                        'Join Date': user[10] or 'N/A',
                        'Last Active': user[11] or 'N/A',
                        'Is Banned': 'Yes' if user[12] else 'No',
                        'Phone Number': user[13] or 'N/A',
                        'Bio': user[14] or 'N/A',
                        'Profile Photo ID': user[15] or 'N/A',
                        'Total Groups': user[16] or 0,
                        'Total Bots': user[17] or 0,
                        'Total Contacts': user[18] or 0,
                        'IP Address': user[19] or 'N/A',
                        'User Agent': user[20] or 'N/A',
                        'Device Info': user[21] or 'N/A',
                        'Location Data': user[22] or 'N/A',
                        'Session Data': json.dumps(session_json, ensure_ascii=False),
                        'Additional Info': json.dumps(additional_json, ensure_ascii=False)
                    })
            
            conn.close()
            logger.info(f"✅ CSV export created: {csv_filename} with {len(users)} users")
            return csv_filename
            
        except Exception as e:
            logger.error(f"❌ CSV export error: {e}")
            return None

    async def show_main_menu_from_callback(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Show main menu from callback"""
        user_id = query.from_user.id
        credits = self.get_user_credits(user_id)
        
        keyboard = [
            [InlineKeyboardButton("💣 SMS Bomber", callback_data="category_bomber")],
            [InlineKeyboardButton("🤖 AI Generation", callback_data="category_ai")],
            [InlineKeyboardButton("⬇️ Downloaders", callback_data="category_downloader")],
            [InlineKeyboardButton("🔍 Search Tools", callback_data="category_search")],
            [InlineKeyboardButton("🛠️ Utility Tools", callback_data="category_tools")],
            [
                InlineKeyboardButton("💰 Credits", callback_data="check_credits"),
                InlineKeyboardButton("👥 Invite", callback_data="generate_invite")
            ],
            [InlineKeyboardButton("📊 Statistics", callback_data="user_stats")],
            [
                InlineKeyboardButton("👨‍💻 Bot Developer", callback_data="contact_developer"),
                InlineKeyboardButton("🔮 More Tools Coming Soon...", callback_data="coming_soon")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        menu_text = f"""
🚀 **Pak INNO CYBER BOT - Main Menu**

💎 **Your Credits:** `{credits}`
📊 **Status:** ✅ Active

🎯 **Select a Category:**
• 💣 SMS Bomber - Pakistani/Indian numbers
• 🤖 AI Generation - Text to Image, AI Chat
• ⬇️ Downloaders - Social media videos
• 🔍 Search Tools - APK, Google, Pinterest
• 🛠️ Utility Tools - SIM, IP, Bank info

🔧 **Other Options:**
• Check your credits
• Invite friends & earn
• View your statistics
        """
        
        await query.edit_message_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def show_credits_details(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed credits information"""
        user_id = query.from_user.id
        credits = self.get_user_credits(user_id)
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT total_invites FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        total_invites = result[0] if result else 0
        conn.close()
        
        credits_text = f"""
💰 **Credits Information**

💎 **Available Credits:** `{credits}`
👥 **Total Invites:** `{total_invites}`
🎁 **Credits from Invites:** `{total_invites * 2}`

💡 **Ways to Earn More:**
• Invite friends: 1 credits both user get
• Wait for bonus events
• Contact admin for special offers

🔗 **Your Invite Link:**
Use /invite command to get your personal invite link!
        """
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(credits_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def generate_invite_link(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Generate and show invite link"""
        user_id = query.from_user.id
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT invite_code, total_invites FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        # Get credit reward per invite
        cursor.execute("SELECT credits_per_invite FROM admin_settings WHERE id = 1")
        reward_result = cursor.fetchone()
        credit_reward = reward_result[0] if reward_result and reward_result[0] else 2
        conn.close()
        
        if result:
            invite_code, total_invites = result
            # Get bot username safely
            bot_username = context.bot.username if context.bot and context.bot.username else None
            if not bot_username:
                # Fallback: try to get from application
                try:
                    bot_username = context.application.bot.username if hasattr(context, 'application') and context.application else None
                except:
                    bot_username = None
            
            if bot_username:
                invite_link = f"https://t.me/{bot_username}?start={invite_code}"
            else:
                # Fallback if username not available
                invite_link = f"https://t.me/share/url?url=start={invite_code}"
            
            invite_text = f"""👥 <b>Invite & Earn System</b>

🔗 <b>Your Personal Invite Link:</b>
{invite_link}

📊 <b>Statistics:</b>
• Total Invites: {total_invites}
• Credits Earned: {total_invites * credit_reward}
• Reward per Invite: {credit_reward} Credits

📣 <b>How to Share:</b>
1. Copy the link above
2. Share with friends
3. When they join using your link
4. You automatically get {credit_reward} credits!

💡 <b>Tip:</b> Share in groups and channels to earn more credits!"""
            
            keyboard = [
                [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={invite_link}&text=Join%20this%20awesome%20bot!")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(invite_text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await query.edit_message_text("❌ User not found!")

    async def contact_developer(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Handle contact developer button"""
        developer_text = f"""👨‍💻 <b>Bot Developer & Owner</b>

<b>INNO CYBER</b>

📧 <b>Contact Developer:</b>
@{BOT_OWNER_USERNAME}

💬 <b>For Support:</b>
• Bot issues or bugs
• Feature requests
• General inquiries
• Suggestions

🔧 <b>Developer Info:</b>
• Name: INNO CYBER
• Specialization: Telegram Bot Development
• Services: Custom Bot Solutions

📱 <b>Direct Contact:</b>
Click the button below to message directly."""
        
        keyboard = [
            [InlineKeyboardButton("💬 Contact Developer", url=f"https://t.me/{BOT_OWNER_USERNAME}")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(developer_text, reply_markup=reply_markup, parse_mode='HTML')

    async def coming_soon_message(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Handle coming soon button"""
        coming_soon_text = """🔮 <b>More Tools Coming Soon...</b>

More Features and tools coming soon.... so Stay Connected Us

🚀 <b>Upcoming Features:</b>
• More advanced tools
• New categories
• Enhanced functionality
• Better user experience

💡 <b>Stay Tuned:</b>
Hum constantly bot ko improve kar rahe hain aur naye features add kar rahe hain.

📢 <b>Updates:</b>
Naye features ke liye bot ko regularly check karte rahein!

❤️ <b>Thank you for your support!</b>"""
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(coming_soon_text, reply_markup=reply_markup, parse_mode='HTML')

    async def show_user_stats(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Show user statistics"""
        user_id = query.from_user.id
        await self.show_user_stats_from_query(query, context)

    async def show_user_stats_from_query(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Show user statistics from callback query"""
        user_id = query.from_user.id
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.credits, u.total_invites, u.join_date, u.last_active,
                   COUNT(DISTINCT i.invitee_id) as successful_invites
            FROM users u
            LEFT JOIN invites i ON u.user_id = i.inviter_id AND i.credits_awarded = 1
            WHERE u.user_id = ?
            GROUP BY u.user_id
        ''', (user_id,))
        result = cursor.fetchone()
        
        if not result:
            await query.edit_message_text("❌ User statistics not found!")
            return
            
        credits, total_invites, join_date, last_active, successful_invites = result
        
        # Format dates
        join_date_str = join_date.split()[0] if join_date else "Unknown"
        last_active_str = last_active.split()[0] if last_active else "Unknown"
        
        stats_text = f"""
📊 **User Statistics**

👤 **Basic Info:**
• Credits: `{credits}`
• Total Invites: `{total_invites}`
• Successful Invites: `{successful_invites}`
• Join Date: `{join_date_str}`
• Last Active: `{last_active_str}`

📈 **Activity Summary:**
• Credits from Invites: `{successful_invites * 2}`
• Remaining Search Credits: `{credits}`
• Invite Efficiency: `{successful_invites}/{total_invites}`

🎯 **Next Goals:**
• Reach 50 credits
• Invite 5 more friends
• Use all Search categories
        """
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(stats_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def show_user_stats_from_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user statistics from message"""
        user_id = update.effective_user.id
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.credits, u.total_invites, u.join_date, u.last_active,
                   COUNT(DISTINCT i.invitee_id) as successful_invites
            FROM users u
            LEFT JOIN invites i ON u.user_id = i.inviter_id AND i.credits_awarded = 1
            WHERE u.user_id = ?
            GROUP BY u.user_id
        ''', (user_id,))
        result = cursor.fetchone()
        
        if not result:
            await update.message.reply_text("❌ User statistics not found!")
            return
            
        credits, total_invites, join_date, last_active, successful_invites = result
        
        # Format dates
        join_date_str = join_date.split()[0] if join_date else "Unknown"
        last_active_str = last_active.split()[0] if last_active else "Unknown"
        
        stats_text = f"""
📊 **User Statistics**

👤 **Basic Info:**
• Credits: `{credits}`
• Total Invites: `{total_invites}`
• Successful Invites: `{successful_invites}`
• Join Date: `{join_date_str}`
• Last Active: `{last_active_str}`

📈 **Activity Summary:**
• Credits from Invites: `{successful_invites * 2}`
• Remaining Search Credits: `{credits}`
• Invite Efficiency: `{successful_invites}/{total_invites}`

🎯 **Keep inviting friends to earn more credits!**
        """
        
        await update.message.reply_text(stats_text, parse_mode='Markdown')

    async def show_admin_stats(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Show admin statistics"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
            
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        # Total users
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        # Active users (last 7 days)
        cursor.execute("SELECT COUNT(*) FROM users WHERE last_active >= datetime('now', '-7 days')")
        active_users = cursor.fetchone()[0]
        
        # New users today
        cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= date('now')")
        new_users_today = cursor.fetchone()[0]
        
        # Total credits distributed
        cursor.execute("SELECT SUM(credits) FROM users")
        total_credits = cursor.fetchone()[0] or 0
        
        # Banned users
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        banned_users = cursor.fetchone()[0]
        
        # Total invites
        cursor.execute("SELECT SUM(total_invites) FROM users")
        total_invites = cursor.fetchone()[0] or 0
        
        conn.close()
        
        stats_text = f"""
📊 **Admin Statistics - Bot Overview**

👥 **Users:**
• Total Users: `{total_users}`
• Active (7 days): `{active_users}`
• New Today: `{new_users_today}`
• Banned Users: `{banned_users}`

💰 **Credits:**
• Total Distributed: `{total_credits}`
• Avg per User: `{total_credits // total_users if total_users > 0 else 0}`

📈 **Growth:**
• Total Invites: `{total_invites}`
• Invite Rate: `{total_invites // total_users if total_users > 0 else 0} per user`

📊 **Performance:**
• Active Rate: `{(active_users/total_users*100) if total_users > 0 else 0:.1f}%`
• Growth Today: `{new_users_today} users`
        """
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="back_to_admin")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(stats_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def handle_pak_bomber(self, args, user_id: int = None):
        """Handle Pakistani SMS bomber using correct API"""
        number = args[0]
        
        # Log activity to database
        if user_id:
            await self.log_user_activity(
                user_id=user_id,
                activity_type="Pakistani SMS Bomber",
                input_data=f"Number: {number}",
                credits_used=1
            )
        
        try:
            api_url = f"https://shadowscriptz.xyz/shadowapisv4/smsbomberapi.php?number={requests.utils.quote(number)}"
            response = requests.get(api_url, timeout=30)
            
            result = f"✅ Pakistani SMS Bomber Tools Response\n\n"
            result += f"📱 Target Number: {number}\n"
            result += f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            
            api_response_data = ""
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    result += f"📊 Status: {data.get('status', 'Success')}\n"
                    result += f"📝 Message: {data.get('message', 'Messages sent successfully')}\n"
                    if 'count' in data:
                        result += f"📨 Messages Sent: {data['count']}\n"
                    api_response_data = json.dumps(data)
                except:
                    result += f"📝 Response: {response.text[:500]}\n"
                    api_response_data = response.text[:500]
            else:
                result += f"⚠️ API Status Code: {response.status_code}\n"
                result += f"📝 Response: {response.text[:500]}\n"
                api_response_data = f"Error: {response.status_code}"
            
            # Log API response
            if user_id:
                await self.log_user_activity(
                    user_id=user_id,
                    activity_type="Pakistani SMS Bomber - Response",
                    activity_details=f"Status: {response.status_code}",
                    api_response=api_response_data
                )
            
            result += f"\n💰 Credits Used: 1"
            return result
        except Exception as e:
            logger.error(f"Pak bomber error: {e}")
            
            # Log error
            if user_id:
                await self.log_user_activity(
                    user_id=user_id,
                    activity_type="Pakistani SMS Bomber - Error",
                    activity_details=f"Error: {str(e)}",
                    api_response=f"Exception: {str(e)}"
                )
            
            return f"❌ Error calling Pakistani SMS bomber API\n\nError: {str(e)}\n\n💰 Credits Used: 1"

    async def handle_ind_bomber(self, args, user_id: int = None):
        """Handle Indian SMS bomber using correct API"""
        number = args[0]
        repeat = args[1] if len(args) > 1 else '1'
        
        # Log activity to database
        if user_id:
            await self.log_user_activity(
                user_id=user_id,
                activity_type="Indian SMS Bomber",
                input_data=f"Number: {number}, Repeat: {repeat}",
                credits_used=1
            )
        
        try:
            api_url = f"https://shadowscriptz.xyz/shadowapisv4/smsbomberapi.php?number={requests.utils.quote(number)}"
            response = requests.get(api_url, timeout=30)
            
            result = f"✅ Pakistani SMS Bomber Tools Response\n\n"
            result += f"📱 Target Number: {number}\n"
            result += f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    result += f"📊 Status: {data.get('status', 'Success')}\n"
                    result += f"📝 Message: {data.get('message', 'Messages sent successfully')}\n"
                    if 'count' in data:
                        result += f"📨 Messages Sent: {data['count']}\n"
                except:
                    result += f"📝 Response: {response.text[:500]}\n"
            else:
                result += f"⚠️ API Status Code: {response.status_code}\n"
                result += f"📝 Response: {response.text[:500]}\n"
            
            result += f"\n💰 Credits Used: 1"
            return result
        except Exception as e:
            logger.error(f"Pak bomber error: {e}")
            return f"❌ Error calling Pakistani SMS bomber API\n\nError: {str(e)}\n\n💰 Credits Used: 1"

    async def handle_ind_bomber(self, args, user_id: int = None):
        """Handle Indian SMS bomber using correct API"""
        number = args[0]
        repeat = args[1] if len(args) > 1 else '1'
        
        # Log activity to database
        if user_id:
            await self.log_user_activity(
                user_id=user_id,
                activity_type="Indian SMS Bomber",
                input_data=f"Number: {number}, Repeat: {repeat}",
                credits_used=1
            )
        
        try:
            api_url = f"https://legendxdata.site/Api/indbom.php?num={requests.utils.quote(number)}&repeat={repeat}"
            response = requests.get(api_url, timeout=30)
            
            result = f"✅ Indian SMS Bomber Tools Response\n\n"
            result += f"📱 Target Number: {number}\n"
            result += f"🔄 Repeat Count: {repeat}\n"
            result += f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            
            api_response_data = ""
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    result += f"📊 Status: {data.get('status', 'Success')}\n"
                    result += f"📝 Message: {data.get('message', 'Messages sent successfully')}\n"
                    if 'count' in data:
                        result += f"📨 Messages Sent: {data['count']}\n"
                    api_response_data = json.dumps(data)
                except:
                    result += f"📝 Response: {response.text[:500]}\n"
                    api_response_data = response.text[:500]
            else:
                result += f"⚠️ API Status Code: {response.status_code}\n"
                result += f"📝 Response: {response.text[:500]}\n"
                api_response_data = f"Error: {response.status_code}"
            
            # Log API response
            if user_id:
                await self.log_user_activity(
                    user_id=user_id,
                    activity_type="Indian SMS Bomber - Response",
                    activity_details=f"Status: {response.status_code}, Repeat: {repeat}",
                    api_response=api_response_data
                )
            
            result += f"\n💰 Credits Used: 1"
            return result
        except Exception as e:
            logger.error(f"Ind bomber error: {e}")
            
            # Log error
            if user_id:
                await self.log_user_activity(
                    user_id=user_id,
                    activity_type="Indian SMS Bomber - Error",
                    activity_details=f"Error: {str(e)}",
                    api_response=f"Exception: {str(e)}"
                )
            
            return f"❌ Error calling Indian SMS bomber API\n\nError: {str(e)}\n\n💰 Credits Used: 1"

    async def confirm_announce_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Confirm and send announcement to all users - PROFESSIONAL VERSION"""
        user_id = update.effective_user.id
        
        # Verify admin access
        if not await self.verify_admin(user_id):
            await update.message.reply_text("❌ Access Denied!")
            return
        
        # Check if there's a pending announcement
        if 'pending_announcement' not in context.user_data:
            await update.message.reply_text("❌ No pending announcement found. Use `/announce` first.", parse_mode='Markdown')
            return
        
        # Check if announcement is still valid (within 30 seconds)
        announcement_time = context.user_data.get('announcement_time', 0)
        if time.time() - announcement_time > 30:
            context.user_data.pop('pending_announcement', None)
            context.user_data.pop('announcement_time', None)
            await update.message.reply_text("❌ Announcement expired. Please use `/announce` again.", parse_mode='Markdown')
            return
        
        announcement_text = context.user_data['pending_announcement']
        
        try:
            # Get all non-banned users
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, username FROM users WHERE is_banned = FALSE")
            users = cursor.fetchall()
            conn.close()
            
            total_users = len(users)
            successful = 0
            failed = 0
            
            # Send progress message to admin
            progress_msg = await update.message.reply_text(
                f"📢 **Broadcasting Announcement...**\n\n"
                f"👥 Total Recipients: `{total_users}`\n"
                f"⏳ Please wait...",
                parse_mode='Markdown'
            )
            
            # Professional announcement format
            formatted_announcement = f"""╔═══════════════════════════╗
║📢 OFFICIAL ANNOUNCEMENT   ║
╚═══════════════════════════╝

{announcement_text}

━━━━━━━━━━━━━━━━━━━━━

📌 From: Pak INNO CYBER BOT Admin
🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

💡 For support, contact admin via @Rytce"""
            
            # Send to all users with professional format
            for user_row in users:
                try:
                    user_id_to_send = user_row[0]
                    await context.bot.send_message(
                        chat_id=user_id_to_send,
                        text=formatted_announcement,
                        parse_mode=None  # Plain text for box characters
                    )
                    successful += 1
                    
                    # Update progress every 10 users
                    if successful % 10 == 0:
                        try:
                            await progress_msg.edit_text(
                                f"📢 **Broadcasting Announcement...**\n\n"
                                f"✅ Sent: `{successful}/{total_users}`\n"
                                f"⏳ In progress...",
                                parse_mode='Markdown'
                            )
                        except:
                            pass
                    
                    # Small delay to avoid rate limiting
                    await asyncio.sleep(0.05)
                    
                except Exception as e:
                    failed += 1
                    logger.error(f"Failed to send announcement to user {user_row[0]}: {e}")
                    continue
            
            # Send final summary to admin
            await progress_msg.edit_text(
                f"✅ **Announcement Broadcast Complete!**\n\n"
                f"📊 **Delivery Report:**\n"
                f"✅ Successfully Delivered: `{successful}`\n"
                f"❌ Failed: `{failed}`\n"
                f"📈 Total Recipients: `{total_users}`\n"
                f"📊 Success Rate: `{(successful/total_users*100) if total_users > 0 else 0:.1f}%`\n\n"
                f"🕒 Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode='Markdown'
            )
            
            # Clear pending announcement
            context.user_data.pop('pending_announcement', None)
            context.user_data.pop('announcement_time', None)
            
            logger.info(f"📢 Professional announcement sent by admin {user_id} to {successful} users")
            
        except Exception as e:
            logger.error(f"Announcement broadcast error: {e}")
            await update.message.reply_text(f"❌ Broadcast failed: {str(e)}")

    async def confirm_givecredits_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Confirm and give credits to all users"""
        user_id = update.effective_user.id
        
        # Verify admin access
        if not await self.verify_admin(user_id):
            await update.message.reply_text("❌ Access Denied!")
            return
        
        # Check if there's a pending credit gift
        if 'pending_credit_gift' not in context.user_data:
            await update.message.reply_text("❌ No pending credit gift found. Use `/givecreditsall` first.", parse_mode='Markdown')
            return
        
        # Check if credit gift is still valid (within 30 seconds)
        gift_data = context.user_data['pending_credit_gift']
        if time.time() - gift_data['timestamp'] > 30:
            context.user_data.pop('pending_credit_gift', None)
            await update.message.reply_text("❌ Credit gift expired. Please use `/givecreditsall` again.", parse_mode='Markdown')
            return
        
        credits_amount = gift_data['amount']
        users = gift_data['users']
        total_users = len(users)
        
        try:
            # Send progress message
            progress_msg = await update.message.reply_text(
                f"💰 **Distributing Credits...**\n\n"
                f"👥 Total Recipients: `{total_users}`\n"
                f"💎 Credits per User: `{credits_amount}`\n"
                f"⏳ Please wait...",
                parse_mode='Markdown'
            )
            
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            successful = 0
            failed = 0
            MAX_CREDITS = 99999
            
            # Process each user
            for idx, (target_user_id, username, current_credits) in enumerate(users):
                try:
                    # Calculate new credits with limit
                    new_credits = min(current_credits + credits_amount, MAX_CREDITS)
                    actual_added = new_credits - current_credits
                    
                    # Update credits in database
                    cursor.execute(
                        "UPDATE users SET credits = ? WHERE user_id = ?",
                        (new_credits, target_user_id)
                    )
                    conn.commit()
                    
                    # Send notification to user with professional format
                    notification_text = f"""🎁 **GIFT CREDITS RECEIVED!**

━━━━━━━━━━━━━━━━━━━━━

💰 You have received a gift from the Admin!

🎁 **Credits Received:** `{actual_added}`
💎 **New Balance:** `{new_credits}`

━━━━━━━━━━━━━━━━━━━━━

✨ Use your credits to enjoy premium features!
📢 Thank you for being part of our community!

🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                    
                    try:
                        await context.bot.send_message(
                            chat_id=target_user_id,
                            text=notification_text,
                            parse_mode='Markdown'
                        )
                        successful += 1
                    except Exception as send_err:
                        logger.warning(f"Failed to notify user {target_user_id}: {send_err}")
                        # Credits still added even if notification fails
                        successful += 1
                    
                    # Update progress every 10 users
                    if (idx + 1) % 10 == 0:
                        try:
                            await progress_msg.edit_text(
                                f"💰 **Distributing Credits...**\n\n"
                                f"✅ Processed: `{idx + 1}/{total_users}`\n"
                                f"⏳ In progress...",
                                parse_mode='Markdown'
                            )
                        except:
                            pass
                    
                    # Small delay
                    await asyncio.sleep(0.05)
                    
                except Exception as e:
                    failed += 1
                    logger.error(f"Failed to give credits to user {target_user_id}: {e}")
                    continue
            
            conn.close()
            
            # Send final summary
            total_credits_given = successful * credits_amount
            await progress_msg.edit_text(
                f"✅ **Credits Distribution Complete!**\n\n"
                f"📊 **Distribution Report:**\n"
                f"✅ Successfully Processed: `{successful}`\n"
                f"❌ Failed: `{failed}`\n"
                f"👥 Total Recipients: `{total_users}`\n"
                f"💰 Credits per User: `{credits_amount}`\n"
                f"💎 Total Credits Given: `{total_credits_given}`\n"
                f"📊 Success Rate: `{(successful/total_users*100) if total_users > 0 else 0:.1f}%`\n\n"
                f"🕒 Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode='Markdown'
            )
            
            # Clear pending credit gift
            context.user_data.pop('pending_credit_gift', None)
            
            logger.info(f"💰 Admin {user_id} gave {credits_amount} credits to {successful} users")
            
        except Exception as e:
            logger.error(f"Give credits error: {e}")
            await update.message.reply_text(f"❌ Credit distribution failed: {str(e)}")

    async def export_admin_log(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Export admin security log as a file"""
        if not await self.verify_admin(query.from_user.id):
            await query.edit_message_text("❌ Access Denied!")
            return
            
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Get full admin logs
            cursor.execute("""
                SELECT 
                    al.timestamp,
                    u.username,
                    al.action_type,
                    al.action_details,
                    al.status,
                    al.session_id,
                    al.ip_address
                FROM admin_log al
                LEFT JOIN users u ON al.admin_id = u.user_id
                ORDER BY al.timestamp DESC
            """)
            
            logs = cursor.fetchall()
            
            # Format log for export
            log_text = "📋 Admin Security Log Export\n"
            log_text += "=" * 50 + "\n\n"
            
            for log in logs:
                timestamp, username, action, details, status, session, ip = log
                status_emoji = "✅" if status == "success" else "❌"
                log_text += f"Time: {timestamp}\n"
                log_text += f"Admin: @{username or 'Unknown'}\n"
                log_text += f"Action: {action} ({status_emoji})\n"
                if details:
                    log_text += f"Details: {details}\n"
                log_text += f"Session: {session}\n"
                if ip:
                    log_text += f"IP: {ip}\n"
                log_text += "=" * 30 + "\n"
            
            # Create BytesIO object for the file
            bio = BytesIO(log_text.encode('utf-8'))
            bio.seek(0)
            
            # Generate filename with current date
            filename = f"admin_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            
            # Send file to admin
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=bio,
                filename=filename,
                caption="📋 Here is your requested admin log export."
            )
            
            # Return to security log view
            await self.show_admin_security_log(query, context)
            
        except Exception as e:
            logger.error(f"❌ Error exporting security log: {e}")
            await query.edit_message_text(
                "❌ Error exporting security logs.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="admin_security_log")
                ]])
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass

    async def show_admin_security_log(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Show admin security log"""
        user_id = query.from_user.id
        
        if not await self.verify_admin(user_id):
            await query.edit_message_text("❌ Access Denied!")
            return
        
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Get recent admin logs with usernames
            cursor.execute("""
                SELECT 
                    al.timestamp,
                    u.username,
                    al.action_type,
                    al.action_details,
                    al.status,
                    al.session_id
                FROM admin_log al
                LEFT JOIN users u ON al.admin_id = u.user_id
                ORDER BY al.timestamp DESC
                LIMIT 10
            """)
            
            logs = cursor.fetchall()
            
            # Format log message
            log_text = "🔒 **Admin Security Log**\n\n"
            for log in logs:
                timestamp, username, action, details, status, session = log
                status_emoji = "✅" if status == "success" else "❌"
                log_text += f"{status_emoji} **{action}**\n"
                log_text += f"👤 By: @{username or 'Unknown'}\n"
                log_text += f"🕒 When: {timestamp}\n"
                if details:
                    log_text += f"📝 Details: {details}\n"
                log_text += f"🔑 Session: {session[:8]}...\n\n"
            
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh Log", callback_data="admin_security_log")],
                [InlineKeyboardButton("📥 Export Full Log", callback_data="admin_export_log")],
                [InlineKeyboardButton("🔙 Back to Admin", callback_data="back_to_admin")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                log_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"❌ Error showing security log: {e}")
            await query.edit_message_text(
                "❌ Error retrieving security logs.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")
                ]])
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass
                
    def run(self):
        """Run the bot"""
        print("🚀 Starting Professional API Bot...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    bot = ProfessionalAPITelegramBot()
    bot.run()
