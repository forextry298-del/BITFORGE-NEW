import os, sqlite3, shutil
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-before-deployment")
# Use Render's persistent disk when available; otherwise keep the local Termux DB.
DATA_DIR = os.environ.get("BITFORGE_DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else os.path.dirname(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
DB = os.path.join(DATA_DIR, "bitforge.db")
LEGACY_DB = os.path.join(os.path.dirname(__file__), "bitforge.db")
# On the first deployment with a newly attached persistent disk, carry forward
# the database shipped with the app if the persistent location has no database.
if not os.path.exists(DB) and os.path.isfile(LEGACY_DB) and os.path.abspath(DB) != os.path.abspath(LEGACY_DB):
    shutil.copy2(LEGACY_DB, DB)


PRODUCTS = [
    {"id": 1, "name": "BITCOIN MINER · SHA-256", "category": "SHA-256", "description": "Entry-level Bitcoin mining simulator", "minimum": 5000, "maximum": 100000, "daily_rate": 0.20, "daily_projection": "20% demo estimate", "duration": "4549 days", "risk": "High", "image": "solar_farm.jpg", "hashrate": "1.2 TH/s"},
    {"id": 2, "name": "BITCOIN MINER · ASIC PRO", "category": "ASIC PRO", "description": "Advanced ASIC mining simulator", "minimum": 150000, "maximum": 1000000, "daily_rate": 0.34, "daily_projection": "34% demo estimate", "duration": "4549 days", "risk": "High", "image": "real_estate.jpg", "hashrate": "8.5 TH/s"},
    {"id": 3, "name": "BITCOIN MINER · HYDRO MAX", "category": "HYDRO MAX", "description": "High-capacity Bitcoin mining simulator", "minimum": 2000000, "maximum": 5000000, "daily_rate": 0.476, "daily_projection": "47.6% demo estimate", "duration": "4549 days", "risk": "High", "image": "green_energy.jpg", "hashrate": "25 TH/s"},
]

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

def normalize_phone(value):
    """Normalize Ugandan mobile numbers to +256XXXXXXXXX."""
    raw = (value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("256") and len(digits) == 12:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+256" + digits[1:]
    if len(digits) == 9:
        return "+256" + digits
    # Keep international/non-Ugandan numbers in a consistent +digits form.
    return "+" + digits if digits else ""

def phone_lookup_values(value):
    """Match accounts created before normalization as well as new accounts."""
    raw = (value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    canonical = normalize_phone(raw)
    variants = {raw, digits, canonical}
    if digits.startswith("256") and len(digits) == 12:
        variants.add("0" + digits[3:])
        variants.add(digits[3:])
    elif digits.startswith("0") and len(digits) == 10:
        variants.add("256" + digits[1:])
        variants.add(digits[1:])
    elif len(digits) == 9:
        variants.add("0" + digits)
        variants.add("256" + digits)
    return tuple(v for v in variants if v)

def init_db():
    with sqlite3.connect(DB) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            balance REAL NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        # Upgrade older account tables safely; existing users and IDs remain intact.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "country" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN country TEXT NOT NULL DEFAULT 'Uganda'")
        if "balance" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN balance REAL NOT NULL DEFAULT 0")
        if "created_at" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
            conn.execute("UPDATE users SET created_at=CURRENT_TIMESTAMP WHERE created_at IS NULL")

        conn.execute("""CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS mine_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending review',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("register"))
        return fn(*args, **kwargs)
    return wrapped

@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("home"))
    return redirect(url_for("register"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("full_name", "").strip()
        phone = normalize_phone(request.form.get("phone", ""))
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not name or not phone or not password:
            flash("Complete all fields.")
        elif password != confirm:
            flash("Passwords do not match.")
        elif len(password) < 6:
            flash("Use at least 6 characters for your password.")
        else:
            try:
                existing = db().execute(
                    "SELECT id FROM users WHERE phone IN (" + ",".join("?" for _ in phone_lookup_values(phone)) + ") LIMIT 1",
                    phone_lookup_values(phone)
                ).fetchone()
                if existing:
                    flash("That phone number is already registered. Please sign in.")
                    return render_template("register.html")
                country = request.form.get("country", "Uganda").strip() or "Uganda"
                db().execute(
                    "INSERT INTO users(full_name, country, phone, password_hash, created_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)",
                    (name, country, phone, generate_password_hash(password))
                )
                db().commit()
                flash("Registration successful. Please sign in.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError as error:
                db().rollback()
                print("[BITFORGE] Registration constraint error:", error, flush=True)
                flash("Registration could not be completed. Please check your details.")
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone_values = phone_lookup_values(request.form.get("phone", ""))
        password = request.form.get("password", "")
        user = None
        if phone_values:
            user = db().execute(
                "SELECT * FROM users WHERE phone IN (" + ",".join("?" for _ in phone_values) + ") ORDER BY id LIMIT 1",
                phone_values
            ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("home"))
        flash("Incorrect phone number or password.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/home")
@login_required
def home():
    user = db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    txns = db().execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 5",
                        (session["user_id"],)).fetchall()
    return render_template("home.html", user=user, txns=txns, products=PRODUCTS)

@app.route("/market")
@login_required
def market():
    category = request.args.get("category", "All")
    query = request.args.get("q", "").strip().lower()
    products = PRODUCTS if category == "All" else [p for p in PRODUCTS if p["category"] == category]
    current_user = db().execute("SELECT balance FROM users WHERE id=?", (session["user_id"],)).fetchone()
    current_user_balance = float(current_user["balance"] or 0) if current_user else 0
    if query:
        products = [p for p in products if query in (p["name"] + " " + p["category"] + " " + p["description"]).lower()]
    return render_template("market.html", products=products, category=category, query=query, current_user_balance=current_user_balance)

@app.route("/mine/<int:product_id>", methods=["POST"])
@login_required
def mine(product_id):
    product = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if product is None:
        flash("That Bitcoin mining rig is unavailable.")
        return redirect(url_for("market"))
    try:
        amount = float(request.form.get("amount", "0"))
    except (TypeError, ValueError):
        amount = 0
    if amount < product["minimum"] or amount > product["maximum"]:
        flash("Enter an amount within this mining rig's UGX range.")
        return redirect(url_for("market"))
    user = db().execute("SELECT balance FROM users WHERE id=?", (session["user_id"],)).fetchone()
    balance = float(user["balance"] or 0) if user else 0
    if balance < amount:
        shortfall = amount - balance
        flash(f"INSUFFICIENT BALANCE. Available: UGX {balance:,.0f}. You need UGX {shortfall:,.0f} more.")
        return redirect(url_for("market"))
    # A sufficient balance is detected, but this demo only creates a pending
    # request. No wallet debit or mining payout occurs without verified mining.
    db().execute(
        "INSERT INTO mine_requests(user_id, product_id, product_name, amount) VALUES(?,?,?,?)",
        (session["user_id"], product["id"], product["name"], amount)
    )
    db().commit()
    daily_demo = amount * product["daily_rate"]
    flash(f"SUFFICIENT BALANCE DETECTED. Mining request submitted for review. Illustrative daily estimate: UGX {daily_demo:,.0f}. No funds were deducted and no Bitcoin earnings were credited.")
    return redirect(url_for("market"))

@app.route("/wallet")
@login_required
def wallet():
    user = db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    txns = db().execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC",
                        (session["user_id"],)).fetchall()
    return render_template("wallet.html", user=user, txns=txns)

@app.route("/deposit", methods=["GET", "POST"])
@login_required
def deposit():
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount", "0"))
        except ValueError:
            amount = 0
        method = request.form.get("method", "")
        if amount < 10000:
            flash("Minimum deposit is UGX 10,000.")
        elif method not in ("MTN Mobile Money", "Airtel Money"):
            flash("Choose a payment method.")
        else:
            db().execute("INSERT INTO transactions(user_id,kind,amount,status) VALUES(?,?,?,?)",
                         (session["user_id"], "Deposit · " + method, amount, "Pending"))
            db().commit()
            flash("Deposit request submitted for review. No money has been collected.")
            return redirect(url_for("wallet"))
    return render_template("deposit.html")

@app.route("/withdraw", methods=["GET", "POST"])
@login_required
def withdraw():
    user = db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    txns = db().execute("SELECT * FROM transactions WHERE user_id=? AND kind LIKE '%Withdrawal%' ORDER BY id DESC LIMIT 10", (session["user_id"],)).fetchall()
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount", "0"))
        except ValueError:
            amount = 0
        if amount < 10000:
            flash("Minimum withdrawal request is UGX 10,000.")
        elif amount > user["balance"]:
            flash("Insufficient available balance.")
        else:
            db().execute("INSERT INTO transactions(user_id,kind,amount,status) VALUES(?,?,?,?)",
                         (session["user_id"], "Withdrawal", amount, "Pending"))
            db().commit()
            flash("Withdrawal request submitted for review.")
            return redirect(url_for("wallet"))
    return render_template("withdraw.html", user=user, txns=txns)

@app.route("/orders")
@login_required
def orders():
    txns = db().execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC",
                        (session["user_id"],)).fetchall()
    return render_template("orders.html", txns=txns)

@app.route("/account")
@login_required
def account():
    user = db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    return render_template("account.html", user=user)

init_db()
try:
    with sqlite3.connect(DB) as _check:
        _users = _check.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    print(f"[BITFORGE] Database active: {DB} | registered users: {_users}", flush=True)
except Exception as _err:
    print(f"[BITFORGE] Database startup check failed: {_err}", flush=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
