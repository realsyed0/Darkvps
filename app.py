from flask import Flask, request, render_template, send_from_directory
import subprocess
import os
import re
import threading
import time

app = Flask(name)

BASE_DIR = os.getcwd()
current_dir = BASE_DIR

running_bots = {}
bot_processes = {}


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/files/<path:filename>")
def serve_file(filename):
    return send_from_directory(current_dir, filename)


# 🔥 AUTO INSTALL
def auto_install_and_run(cmd):
    output = subprocess.getoutput(f"cd '{current_dir}' && {cmd}")

    match = re.search(r"No module named '(.+?)'", output)
    if match:
        module = match.group(1)

        if module == "telegram":
            module = "python-telegram-bot"

        subprocess.getoutput(f"pip install {module}")
        return subprocess.getoutput(f"cd '{current_dir}' && {cmd}")

    return output


# 🔥 BOT RUNNER (AUTO RESTART)
def run_bot_forever(file_path, name):
    while True:
        try:
            process = subprocess.Popen(["python", file_path])
            bot_processes[name] = process
            process.wait()
        except Exception as e:
            print(f"{name} crashed:", e)
        time.sleep(5)


@app.route("/run", methods=["POST"])
def run():
    global current_dir

    cmd = request.form.get("command", "").strip()

    if not cmd:
        return ""

    parts = cmd.split()
    command = parts[0].lower()
    args = parts[1:]

    if command == "clear":
        return "CLEAR"

    elif command == "help":
        return """Commands:
ls
cd <folder>
pwd
clear
darkinfo
startbot <file.py>
stopbot <file.py>
serve <file.html>
"""

    elif command == "darkinfo":
        return f"""🐺 Dark VPS Panel

Owner: @Darkeyy0
System: Render Linux
Path: {current_dir}

Running bots: {list(running_bots.keys())}
"""

    elif command == "ls":
        try:
            items = os.listdir(current_dir)

            folders = sorted([
                f"[DIR] {i}/"
                for i in items
                if os.path.isdir(os.path.join(current_dir, i))
            ])

            files = sorted([
                i for i in items
                if not os.path.isdir(os.path.join(current_dir, i))
            ])

            return "\n".join(folders + files)

        except Exception as e:
            return str(e)

    elif command == "pwd":
        return current_dir

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

    # 🔥 START BOT
    elif command == "startbot":
        if not args:
            return "Usage: startbot <file.py>"

        file_name = args[0]
        file_path = os.path.join(current_dir, file_name)

        if not os.path.exists(file_path):
            return "File not found"

        if file_name in running_bots:
            return "Bot already running"

        thread = threading.Thread(
            target=run_bot_forever,
            args=(file_path, file_name)
        )
        thread.daemon = True
        thread.start()

        running_bots[file_name] = thread

        return f"✅ Bot started: {file_name}"

    # 🔥 STOP BOT
    elif command == "stopbot":
        if not args:
            return "Usage: stopbot <file.py>"

        name = args[0]

        if name not in bot_processes:
            return "Bot not running"

        try:
            bot_processes[name].terminate()
            del bot_processes[name]
            del running_bots[name]
            return f"🛑 Bot stopped: {name}"
        except Exception as e:
            return str(e)

    elif command == "serve":
        if not args:
            return "Usage: serve <file.html>"

        return f"/files/{args[0]}"

    return auto_install_and_run(cmd)


# 🔥 KEEP ALIVE
@app.route("/ping")
def ping():
# 🔥 AUTO START BOT
def auto_start():
    try:
        file_path = os.path.join(BASE_DIR, "Osintbot/main.py")

        if os.path.exists(file_path):
            thread = threading.Thread(
                target=run_bot_forever,
                args=(file_path, "main.py")
            )
            thread.daemon = True
            thread.start()

            running_bots["main.py"] = thread
            print("✅ Auto bot started")

    except Exception as e:
        print("Auto start error:", e)


# 🔥 RUN SERVER
if name == "main":
    auto_start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
    return "alive"
