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
    {"id": 1, "name": "Solar Farm Project", "category": "Solar", "description": "Solar energy infrastructure project", "minimum": 25000, "duration": "36 months", "risk": "Low", "return_text": "Indicative 8–10% p.a.", "image": "solar_farm.jpg"},
    {"id": 2, "name": "Commercial Property", "category": "Real Estate", "description": "Commercial office property project", "minimum": 50000, "duration": "48 months", "risk": "Medium", "return_text": "Indicative 9–12% p.a.", "image": "real_estate.jpg"},
    {"id": 3, "name": "Green Energy Fund", "category": "Green Energy", "description": "Wind and clean-energy infrastructure", "minimum": 10000, "duration": "24 months", "risk": "Medium", "return_text": "Indicative 6–7% p.a.", "image": "green_energy.jpg"},
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
                db().execute(
                    "INSERT INTO users(full_name, country, phone, password_hash, created_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)",
                    (name, request.form.get("country", "Uganda").strip(), phone, generate_password_hash(password))
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
    if query:
        products = [p for p in products if query in (p["name"] + " " + p["category"] + " " + p["description"]).lower()]
    return render_template("market.html", products=products, category=category, query=query)

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
    return render_template("withdraw.html", user=user)

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
