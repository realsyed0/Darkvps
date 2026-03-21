from flask import Flask, request, render_template
import subprocess
import os

app = Flask(name)

BASE_DIR = os.getcwd()
current_dir = BASE_DIR


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/run", methods=["POST"])
def run():
    global current_dir

    cmd = request.form.get("command", "").strip()
    if not cmd:
        return ""

    parts = cmd.split()
    command = parts[0]
    args = parts[1:]

    # ===== BASIC =====
    if command == "clear":
        return "CLEAR"

    elif command == "pwd":
        return current_dir

    elif command == "ls":
        try:
            return "\n".join(os.listdir(current_dir))
        except:
            return "error"

    elif command == "cd":
        if not args:
            return "usage: cd folder"

        new_path = os.path.abspath(os.path.join(current_dir, args[0]))

        if os.path.isdir(new_path):
            current_dir = new_path
            return "ok"
        else:
            return "folder not found"

    # ===== DEFAULT SHELL =====
    try:
        return subprocess.getoutput(f"cd '{current_dir}' && {cmd}")
    except Exception as e:
        return str(e)


if name == "main":
    app.run(host="0.0.0.0", port=5000)
