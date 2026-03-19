from pymongo import MongoClient
from telebot import TeleBot

# ------------------ CONFIG ------------------
# Yaha apna MongoDB URI dal do
MONGO_URI = "mongodb+srv://editingtution99:kLKimOFEX1MN1v0G@cluster0.fxbujjd.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "osint_bot"
COLLECTION_NAME = "users"

# Connect to MongoDB
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

# ------------------ FUNCTIONS ------------------

def add_user(user_id: int):
    """
    Add a user to MongoDB if not already present.
    """
    if not collection.find_one({"user_id": user_id}):
        collection.insert_one({"user_id": user_id})

def broadcast_message(bot: TeleBot, text: str):
    """
    Broadcasts 'text' to all users in MongoDB.
    Returns a dictionary with success, failed, and total counts.
    """
    users = list(collection.find({}, {"user_id": 1, "_id": 0}))
    success = 0
    failed = 0
    for user in users:
        try:
            bot.send_message(user["user_id"], text)
            success += 1
        except Exception as e:
            print(f"Failed to send to {user['user_id']}: {e}")
            failed += 1
    return {"success": success, "failed": failed, "total": len(users)}
  
