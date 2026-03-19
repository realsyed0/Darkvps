# family.py
import requests

# ----------------------- PRETTY FORMAT -----------------------
def pretty_format(data):
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

# ----------------------- HANDLE FAMILY -----------------------
# family.py
def handle_aadhfamily(bot, call, user_state):
    bot.send_message(call.message.chat.id, "🤵🏻 Send the Aadhar number for Family info:")
    user_state[call.from_user.id] = "aadhfamily_input"

# ----------------------- HANDLE FAMILY INPUT -----------------------
def handle_input(bot, msg, user_state):
    """
    Jab user number bhejta hai Family ke liye.
    API call hota hai aur response pretty_format ke through Telegram me bheja jata hai.
    """
    user_id = msg.from_user.id
    number = msg.text.strip()

    url = f"https://anon-num-info.vercel.app/aadhar?key=temp153&id="

    try:
        response = requests.get(url, timeout=10).json()
    except Exception as e:
        bot.send_message(msg.chat.id, f"❌ API Error: {e}")
        user_state.pop(user_id, None)
        return

    # API response se data nikalna
    data = response.get("data") or response.get("result") or response.get("info") or response

    formatted = pretty_format(data)
    final_msg = f"### 🤵🏻 **AADHAR Family Info Result**\n\n{formatted}"

    bot.send_message(msg.chat.id, final_msg, parse_mode="Markdown")
    user_state.pop(user_id, None)
  
