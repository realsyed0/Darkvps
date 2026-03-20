from flask import Flask, request, render_template, send_from_directory
import subprocess
import os
import re
import threading
import time

app = Flask(__name__)

BASE_DIR = os.getcwd()
current_dir = BASE_DIR

# 🔥 Track running bots
running_bots = {}

@app.route("/")
def home():
    return render_template("index.html")


# ✅ Serve files
@app.route("/files/<path:filename>")
def serve_file(filename):
    return send_from_directory(current_dir, filename)


# 🔥 AUTO INSTALL + RUN
def auto_install_and_run(cmd):
    output = subprocess.getoutput(f"cd '{current_dir}' && {cmd}")

    match = re.search(r"No module named '(.+?)'", output)
    if match:
        module = match.group(1)

        if module == "telegram":
            module = "python-telegram-bot"

        install = subprocess.getoutput(f"pip install {module}")
        retry = subprocess.getoutput(f"cd '{current_dir}' && {cmd}")

        return f"📦 Installing {module}...\n\n{install}\n\n--- RETRY ---\n\n{retry}"

    return output


# 🔥 BOT RUNNER (AUTO RESTART)
def run_bot_forever(file_path):
    while True:
        try:
            print(f"Running {file_path}")
            subprocess.run(["python", file_path])
        except Exception as e:
            print("Bot crashed:", e)
            time.sleep(5)


@app.route("/run", methods=["POST"])
def run():
    global current_dir, running_bots

    cmd = request.form.get("command", "").strip()

    if not cmd:
        return ""

    parts = cmd.split()
    command = parts[0].lower()
    args = parts[1:]

    # 🔥 CLEAR
    if command == "clear":
        return "__CLEAR__"

    # 🔥 HELP
    elif command == "help":
        return """Commands:
ls
cd <folder>
pwd
clear

# Custom
darkinfo
startbot <file.py>
stopbot <file.py>
serve <file.html>
"""

    # 🔥 INFO
    elif command == "darkinfo":
        return f"""🐺 Dark VPS Panel

Owner: @Darkeyy0
System: Web Linux (Render)
Path: {current_dir}
Running bots: {list(running_bots.keys())}
"""

    # 🔥 LS
    elif command == "ls":
        try:
            items = os.listdir(current_dir)

            folders = sorted([f"[DIR] {i}/" for i in items if os.path.isdir(os.path.join(current_dir, i))])
            files = sorted([i for i in items if not os.path.isdir(os.path.join(current_dir, i))])

            return "\n".join(folders + files)

        except Exception as e:
            return str(e)

    # 🔥 PWD
    elif command == "pwd":
        return current_dir

    # 🔥 CD
    elif command == "cd":
        if not args:
            return "Usage: cd <folder>"

        new_path = os.path.abspath(os.path.join(current_dir, args[0]))

        if not new_path.startswith(BASE_DIR):
            return "Access denied"

        if os.path.isdir(new_path):
            current_dir = new_path
            return "OK"
        else:
            return "Folder not found"

    # 🔥 START BOT (AUTO RESTART + BACKGROUND)
    elif command == "startbot":
        if not args:
            return "Usage: startbot <file.py>"

        file_path = os.path.join(current_dir, args[0])

        if not os.path.exists(file_path):
            return "File not found"

        if args[0] in running_bots:
            return "Bot already running"

        thread = threading.Thread(target=run_bot_forever, args=(file_path,))
        thread.daemon = True
        thread.start()

        running_bots[args[0]] = thread

        return f"✅ Bot started: {args[0]}"

    # 🔥 STOP BOT (basic)
    elif command == "stopbot":
        return "⚠️ Stop feature limited (Render restrictions)"

    # 🔥 SERVE HTML
    elif command == "serve":
        if not args:
            return "Usage: serve <file.html>"

        return f"/files/{args[0]}"

    # 🔥 DEFAULT COMMAND
    return auto_install_and_run(cmd)


# 🔥 KEEP ALIVE ROUTE
@app.route("/ping")
def ping():
    return "alive"


# ✅ RUN SERVER
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
