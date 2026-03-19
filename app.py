from flask import Flask, request, render_template, send_from_directory
import subprocess
import os
import re

app = Flask(__name__)

# ✅ FIX: Use Render working directory (NO /storage path)
current_dir = os.getcwd()

@app.route("/")
def home():
    return render_template("index.html")


# Serve HTML files
@app.route("/files/<path:filename>")
def serve_file(filename):
    return send_from_directory(current_dir, filename)


# 🔥 Auto install missing modules
def auto_install_and_run(cmd):
    output = subprocess.getoutput(f"cd {current_dir} && {cmd}")

    match = re.search(r"No module named '(.+?)'", output)
    if match:
        module = match.group(1)

        # Fix common module name
        if module == "telegram":
            module = "python-telegram-bot"

        install = subprocess.getoutput(f"pip install {module}")
        retry = subprocess.getoutput(f"cd {current_dir} && {cmd}")

        return f"Installing {module}...\n\n{install}\n\n--- RETRY ---\n\n{retry}"

    return output


@app.route("/run", methods=["POST"])
def run():
    global current_dir

    cmd = request.form.get("command")

    if not cmd:
        return ""

    cmd = cmd.strip()
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
serve <file.html>
"""

    # 🔥 INFO
    elif command == "darkinfo":
        return "Dark VPS 😈 (Render Ready)"

    # 🔥 LIST FILES
    elif command == "ls":
        try:
            items = os.listdir(current_dir)
            folders = []
            files = []

            for item in items:
                if os.path.isdir(os.path.join(current_dir, item)):
                    folders.append(f"[DIR]{item}/")
                else:
                    files.append(item)

            return "\n".join(sorted(folders) + sorted(files))

        except Exception as e:
            return str(e)

    # 🔥 CURRENT PATH
    elif command == "pwd":
        return current_dir

    # 🔥 CHANGE DIRECTORY
    elif command == "cd":
        if not args:
            return "Usage: cd <folder>"

        new_path = os.path.join(current_dir, args[0])

        if os.path.isdir(new_path):
            current_dir = os.path.abspath(new_path)
            return "OK"
        else:
            return "Folder not found"

    # 🔥 RUN PYTHON FILE
    elif command == "startbot":
        if not args:
            return "Usage: startbot <file.py>"

        file_path = os.path.join(current_dir, args[0])

        if not os.path.exists(file_path):
            return "File not found"

        return auto_install_and_run(f"python {file_path}")

    # 🔥 OPEN HTML
    elif command == "serve":
        if not args:
            return "Usage: serve <file.html>"

        return f"/files/{args[0]}"

    # 🔥 DEFAULT COMMAND
    return auto_install_and_run(cmd)


# ✅ IMPORTANT FOR RENDER
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
            return "\n".join(folders + files)

        except Exception as e:
            return str(e)

    # PWD
    elif command == "pwd":
        return current_dir

    # CD
    elif command == "cd":
        if not args:
            return "Usage: cd <folder>"

        new_path = os.path.join(current_dir, args[0])

        if os.path.isdir(new_path):
            current_dir = os.path.abspath(new_path)
            return "OK"
        else:
            return "Folder not found"

    # DEFAULT
    return auto_install_and_run(cmd)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
