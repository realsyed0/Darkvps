# upi.py
import requests

def pretty_format(data, indent=2):
    """Recursive pretty format for any JSON structure."""
    if isinstance(data, dict):
        text = ""
        for k, v in data.items():
            if v in [None, "", "N/A"]:
                continue
            key = str(k).replace("_", " ").title()
            text += f"🔹 **{key}:** {pretty_format(v)}\n"
        return text
    elif isinstance(data, list):
        text = ""
        for i, item in enumerate(data, 1):
            text += f"\n------ 🌸 Record 🌸{i} ------\n"
            text += pretty_format(item)
        return text
    else:
        return str(data)

# ---------------- HANDLE UPI BUTTON / MESSAGE ----------------
def handle_upi(bot, obj, user_state):
    """
    obj can be either CallbackQuery or Message
    """
    # Determine chat id
    if hasattr(obj, "message"):  # CallbackQuery
        chat_id = obj.message.chat.id
        user_id = obj.from_user.id
    else:  # Message
        chat_id = obj.chat.id
        user_id = obj.from_user.id

    bot.send_message(chat_id, "💳 Send the UPI ID for lookup:")
    user_state[user_id] = "upi_input"

# ---------------- HANDLE USER INPUT ----------------
def handle_upi_input(msg, bot, user_state):
    user_id = msg.from_user.id
    upi_id = msg.text.strip()

    url = f"https://api.b77bf911.workers.dev/upi?id={requests.utils.quote(upi_id)}"

    try:
        response = requests.get(url, timeout=10).json()
    except Exception as e:
        bot.send_message(msg.chat.id, f"❌ API Error: {e}")
        user_state.pop(user_id, None)
        return

    data = response.get("data") or response.get("result") or response.get("info") or response

    formatted = pretty_format(data)
    final_msg = f"### 💳 **UPI Info Result**\n\n{formatted}"

    bot.send_message(msg.chat.id, final_msg, parse_mode="Markdown")
    user_state.pop(user_id, None)

