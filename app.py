from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import subprocess, os, zipfile, functools, sqlite3, shutil, psutil, time, signal
from datetime import datetime

app = Flask(__name__)
# Public karne ke liye secret key strong honi chahiye
app.secret_key = os.environ.get("SECRET_KEY", "ZAINU_ULTRA_SECURE_786_PRO")

# --- DATABASE SETUP ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'database.db')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            path TEXT,
            auto_restart INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('CREATE TABLE IF NOT EXISTS activity_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT, type TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    conn.commit()
    conn.close()

# In-memory storage for processes
RUNNING_PROCESSES = {}

# --- HELPER FUNCTIONS ---
def log_event(msg, msg_type="info"):
    try:
        conn = get_db_connection()
        conn.execute("INSERT INTO activity_logs (message, type) VALUES (?, ?)", (msg, msg_type))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Logging Error: {e}")

def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- SAFE SYSTEM STATS ---
@app.route("/api/stats")
def sys_stats():
    try:
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
        uptime = time.time() - psutil.boot_time()
    except:
        cpu = ram = disk = uptime = 0

    return jsonify({
        "cpu": cpu,
        "ram": ram,
        "disk": disk,
        "uptime": uptime,
        "active_pids": len(RUNNING_PROCESSES)
    })

# --- BOTS API ---
@app.route("/api/bots")
@login_required
def bots():
    conn = get_db_connection()
    db_bots = conn.execute("SELECT * FROM bots").fetchall()
    conn.close()

    data = []
    for b in db_bots:
        name = b['name']
        p = RUNNING_PROCESSES.get(name)
        status = "Running" if p and p.poll() is None else "Stopped"
        data.append({
            "name": name,
            "status": status,
            "path": b['path'],
            "auto_restart": b['auto_restart']
        })
    return jsonify(data)

@app.route("/api/start/<name>")
@login_required
def start(name):
    conn = get_db_connection()
    bot = conn.execute("SELECT * FROM bots WHERE name=?", (name,)).fetchone()
    conn.close()

    if not bot: return "Bot not found"
    if name in RUNNING_PROCESSES: stop(name)

    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{name}.txt")

    # Start logging
    log_handler = open(log_file, "a")
    log_handler.write(f"\n--- [ {datetime.now()} ] SYSTEM BOOT ---\n")

    try:
        # Popen optimized for Linux Servers
        p = subprocess.Popen(
            ["python3", bot['path']],
            stdout=log_handler,
            stderr=log_handler,
            start_new_session=True # Process group create karta hai
        )
        RUNNING_PROCESSES[name] = p
        log_event(f"Node {name} is now online.", "success")
        return "Started"
    except Exception as e:
        return f"Error: {str(e)}"

@app.route("/api/stop/<name>")
@login_required
def stop(name):
    if name in RUNNING_PROCESSES:
        p = RUNNING_PROCESSES[name]
        try:
            # Full process tree kill
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except:
            p.terminate()
        
        del RUNNING_PROCESSES[name]
        log_event(f"Node {name} was shut down.", "error")
    return "Stopped"

@app.route("/api/delete/<name>")
@login_required
def delete_bot(name):
    stop(name)
    conn = get_db_connection()
    bot = conn.execute("SELECT * FROM bots WHERE name=?", (name,)).fetchone()
    if bot:
        conn.execute("DELETE FROM bots WHERE name=?", (name,))
        conn.commit()
        try:
            if os.path.exists(bot['path']):
                if "_dir" in bot['path']: shutil.rmtree(os.path.dirname(bot['path']))
                else: os.remove(bot['path'])
        except: pass
    conn.close()
    return "Deleted"

@app.route("/upload", methods=["POST"])
@login_required
def upload():
    if 'file' not in request.files: return "No file"
    f = request.files["file"]
    if f.filename == '': return "No filename"

    upload_dir = os.path.join(BASE_DIR, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    path = os.path.join(upload_dir, f.filename)
    f.save(path)

    run_path = path
    if f.filename.endswith(".zip"):
        extract_dir = path + "_dir"
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(path, 'r') as z:
            z.extractall(extract_dir)
        # Scan for entry point
        for root, _, files in os.walk(extract_dir):
            for file in files:
                if file in ["main.py", "bot.py", "index.py"]:
                    run_path = os.path.join(root, file)
                    break

    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO bots (name, path) VALUES (?, ?)", (f.filename, run_path))
    conn.commit()
    conn.close()
    log_event(f"New deployment: {f.filename}", "info")
    return "OK"

# --- AUTH ROUTES ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u, p = request.form.get("username"), request.form.get("password")
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username=? AND password=?", (u, p)).fetchone()
        conn.close()
        if user:
            session["logged_in"] = True
            session["user"] = u
            return redirect(url_for('home'))
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        u, p = request.form.get("username"), request.form.get("password")
        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (u, p))
            conn.commit()
            return redirect(url_for('login'))
        except: return "Username exists"
        finally: conn.close()
    return render_template("signup.html")

@app.route("/")
@login_required
def home(): return render_template("index.html", user=session.get("user"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/api/logs/<name>")
@login_required
def logs(name):
    try:
        path = os.path.join(BASE_DIR, "logs", f"{name}.txt")
        if os.path.exists(path):
            with open(path, "r") as f:
                # Sirf aakhri 3000 chars read karo taaki page heavy na ho
                content = f.read()
                return content[-3000:]
    except: pass
    return "No logs available for this node."

# --- START SERVER ---
if __name__ == "__main__":
    init_db()
    # Web hosting ke liye PORT environment variable zaroori hai
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
