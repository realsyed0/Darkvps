import requests

def handle_ip(bot, call, user_state):
    bot.send_message(call.message.chat.id, "🌐 Send the IP address to lookup (or leave empty for server IP):")
    user_state[call.from_user.id] = "ip_input"

def handle_input(bot, msg, user_state):
    user_id = msg.from_user.id
    ip = msg.text.strip()

    # If empty, API will return server IP info
    url = f"https://anon-multi-info.vercel.app/ipinfo?key=temp053&ip=8.8.8.8"

    try:
        response = requests.get(url, timeout=10).json()
    except Exception as e:
        bot.send_message(msg.chat.id, f"❌ API Error: {e}")
        user_state.pop(user_id, None)
        return

    if response.get("status") != "success":
        msg_text = response.get("message", "⚠️ No data found for this IP")
        bot.send_message(msg.chat.id, f"⚠️ {msg_text}")
        user_state.pop(user_id, None)
        return

    # Build a clean output
    lines = [
        f"🌐 IP: {response.get('query', '-')}",
        f"🏳️ Country: {response.get('country', '-')}",
        f"🏷️ Region: {response.get('regionName', '-')}",
        f"🏙️ City: {response.get('city', '-')}",
        f"🏤 ZIP: {response.get('zip', '-')}",
        f"📍 Location: {response.get('lat', '-')}, {response.get('lon', '-')}",
        f"⏱️ Timezone: {response.get('timezone', '-')}",
        f"📡 ISP: {response.get('isp', '-')}",
        f"🏢 Org: {response.get('org', '-')}",
        f"🔗 AS: {response.get('as', '-')}",
        f"🔁 Reverse DNS: {response.get('reverse', '-')}",
        f"🔒 Proxy: {'Yes' if response.get('proxy') else 'No'}",
        f"📱 Mobile: {'Yes' if response.get('mobile') else 'No'}",
        f"🖥️ Hosting: {'Yes' if response.get('hosting') else 'No'}"
    ]

    final_msg = "🔎 IP Lookup Result\n\n" + "\n".join(lines)
    bot.send_message(msg.chat.id, final_msg)
    user_state.pop(user_id, None)

