import os, sqlite3, shutil, secrets
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
    {"id": 1, "name": "BITCOIN MINER · SHA-256", "category": "SHA-256", "description": "Bitcoin SHA-256 mining rig · pool connection required", "minimum": 5000, "maximum": 100000, "daily_rate": 20.0, "daily_projection": "Pool-reported only; scenario rate 20% is not a mining guarantee", "duration": "4549 days (configured term)", "risk": "Mining risk", "image": "mining_rig_robot.jpg", "hashrate": "Pool-reported only"},
    {"id": 2, "name": "BITCOIN MINER · ASIC PRO", "category": "ASIC PRO", "description": "ASIC Bitcoin mining equipment · pool connection required", "minimum": 150000, "maximum": 1000000, "daily_rate": 34.0, "daily_projection": "Pool-reported only; scenario rate 34% is not a mining guarantee", "duration": "4549 days (configured term)", "risk": "Mining risk", "image": "bitcoin_mining_robot.jpg", "hashrate": "Pool-reported only"},
    {"id": 3, "name": "BITCOIN MINER · HYDRO MAX", "category": "HYDRO MAX", "description": "High-capacity mining facility · pool connection required", "minimum": 2000000, "maximum": 5000000, "daily_rate": 47.6, "daily_projection": "Pool-reported only; scenario rate 47.6% is not a mining guarantee", "duration": "4549 days (configured term)", "risk": "Mining risk", "image": "industrial_mining_robot.jpg", "hashrate": "Pool-reported only"},
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
        if "referral_code" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN referral_code TEXT")
        if "referred_by" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        for row in conn.execute("SELECT id FROM users WHERE referral_code IS NULL OR referral_code='' ").fetchall():
            code = secrets.token_hex(4).upper()
            conn.execute("UPDATE users SET referral_code=? WHERE id=?", (code, row[0]))
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)")
        conn.execute("""CREATE TABLE IF NOT EXISTS referral_commissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_user_id INTEGER NOT NULL,
            deposit_transaction_id INTEGER NOT NULL UNIQUE,
            deposit_amount REAL NOT NULL,
            commission_amount REAL NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        tx_columns = {row[1] for row in conn.execute("PRAGMA table_info(transactions)")}
        if "fee" not in tx_columns:
            conn.execute("ALTER TABLE transactions ADD COLUMN fee REAL NOT NULL DEFAULT 0")
        if "payout_amount" not in tx_columns:
            conn.execute("ALTER TABLE transactions ADD COLUMN payout_amount REAL NOT NULL DEFAULT 0")

        conn.execute("""CREATE TABLE IF NOT EXISTS mine_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending review',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        # Private user-to-admin support messages and the shared member community.
        conn.execute("""CREATE TABLE IF NOT EXISTS support_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            sender TEXT NOT NULL CHECK(sender IN ('user','admin')),
            message TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS community_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
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
    invite_code = (request.args.get("ref") or request.form.get("referral_code") or "").strip().upper()
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
                inviter = None
                if invite_code:
                    inviter_row = db().execute("SELECT id FROM users WHERE referral_code=?", (invite_code,)).fetchone()
                    if inviter_row:
                        inviter = inviter_row["id"]
                db().execute(
                    "INSERT INTO users(full_name, country, phone, password_hash, referred_by, created_at) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)",
                    (name, country, phone, generate_password_hash(password), inviter)
                )
                new_id = db().execute("SELECT last_insert_rowid()").fetchone()[0]
                db().execute("UPDATE users SET referral_code=? WHERE id=?", (secrets.token_hex(4).upper(), new_id))
                db().commit()
                flash("Registration successful. Please sign in.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError as error:
                db().rollback()
                print("[BITFORGE] Registration constraint error:", error, flush=True)
                flash("Registration could not be completed. Please check your details.")
    return render_template("register.html", invite_code=invite_code)

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
    # Do not accept deposits or fabricate mining earnings. Activation is blocked
    # until a real mining-pool API and verified payout wallet are configured.
    flash("BALANCE CHECK PASSED, but mining is not connected to a verified pool yet. No funds were deducted and no earnings were credited.")
    return redirect(url_for("market"))

@app.route("/chats", methods=["GET", "POST"])
@login_required
def chats():
    if request.method == "POST":
        message = request.form.get("message", "").strip()
        if not message:
            flash("Write a message before sending.")
        elif len(message) > 2000:
            flash("Message is too long. Maximum is 2,000 characters.")
        else:
            db().execute("INSERT INTO support_messages(user_id,sender,message) VALUES(?,?,?)",
                         (session["user_id"], "user", message))
            db().commit()
            flash("Your message has been sent to the support team.")
            return redirect(url_for("chats"))
    messages = db().execute("""SELECT sender,message,created_at FROM support_messages
                              WHERE user_id=? ORDER BY id DESC LIMIT 100""",
                            (session["user_id"],)).fetchall()
    return render_template("chats.html", messages=list(reversed(messages)))

@app.route("/community", methods=["GET", "POST"])
@login_required
def community():
    if request.method == "POST":
        message = request.form.get("message", "").strip()
        if not message:
            flash("Write a message before posting.")
        elif len(message) > 1000:
            flash("Community messages are limited to 1,000 characters.")
        else:
            db().execute("INSERT INTO community_messages(user_id,message) VALUES(?,?)",
                         (session["user_id"], message))
            db().commit()
            return redirect(url_for("community"))
    messages = db().execute("""SELECT c.message,c.created_at,u.full_name
                              FROM community_messages c JOIN users u ON u.id=c.user_id
                              ORDER BY c.id DESC LIMIT 100""").fetchall()
    return render_template("community.html", messages=list(reversed(messages)))

@app.route("/admin/chats", methods=["GET", "POST"])
def admin_chats():
    # Set BITFORGE_ADMIN_CHAT_KEY in Render before enabling the admin inbox.
    expected = os.environ.get("BITFORGE_ADMIN_CHAT_KEY", "")
    if not expected:
        return "Admin chat is disabled: configure BITFORGE_ADMIN_CHAT_KEY first.", 503
    if request.method == "POST" and request.form.get("admin_key"):
        if request.form.get("admin_key") == expected:
            session["bitforge_chat_admin"] = True
            return redirect(url_for("admin_chats"))
        flash("Invalid admin key.")
    if not session.get("bitforge_chat_admin"):
        return render_template("admin_chat_login.html")
    if request.method == "POST":
        try:
            user_id = int(request.form.get("user_id", "0"))
        except ValueError:
            user_id = 0
        message = request.form.get("message", "").strip()
        if user_id and message and len(message) <= 2000:
            exists = db().execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
            if exists:
                db().execute("INSERT INTO support_messages(user_id,sender,message) VALUES(?,?,?)",
                             (user_id, "admin", message))
                db().commit()
                flash("Reply sent.")
        return redirect(url_for("admin_chats"))
    threads = db().execute("""SELECT u.id AS user_id,u.full_name,u.phone,
                           MAX(m.id) AS last_id,MAX(m.created_at) AS last_time
                           FROM users u JOIN support_messages m ON m.user_id=u.id
                           GROUP BY u.id ORDER BY last_id DESC""").fetchall()
    selected = request.args.get("user", type=int)
    thread_messages = []
    if selected:
        thread_messages = db().execute("""SELECT sender,message,created_at FROM support_messages
                                         WHERE user_id=? ORDER BY id""", (selected,)).fetchall()
    return render_template("admin_chats.html", threads=threads, selected=selected,
                           thread_messages=thread_messages)

@app.route("/admin/chats/logout")
def admin_chats_logout():
    session.pop("bitforge_chat_admin", None)
    return redirect(url_for("admin_chats"))

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
    agents = {
        "agent_shakira": {"name": "Shakira Nantongo", "phone": "0758878297", "network": "Mobile Money Agent"},
        "agent_mary": {"name": "Mary Namara", "phone": "0757837051", "network": "Mobile Money Agent"},
    }
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount", "0"))
        except (TypeError, ValueError):
            amount = 0
        agent_key = request.form.get("agent", "")
        agent = agents.get(agent_key)
        if amount < 10000:
            flash("Minimum deposit is UGX 10,000.")
        elif amount > 100000000:
            flash("Please enter a valid deposit amount.")
        elif not agent:
            flash("Choose an available deposit agent.")
        else:
            # This records a pending payment instruction only. Wallet funds are
            # credited after an administrator verifies the incoming transfer.
            method = f"Deposit · Agent · {agent['name']} · {agent['phone']}"
            db().execute("INSERT INTO transactions(user_id,kind,amount,status) VALUES(?,?,?,?)",
                         (session["user_id"], method, amount, "Pending verification"))
            db().commit()
            flash(f"Deposit instruction saved for UGX {amount:,.0f}. Your wallet will be credited after the transfer to {agent['name']} is verified.")
            return redirect(url_for("orders"))
    return render_template("deposit.html", agents=agents)

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
            fee = round(amount * 0.06, 2)
            payout = round(amount - fee, 2)
            db().execute("INSERT INTO transactions(user_id,kind,amount,status,fee,payout_amount) VALUES(?,?,?,?,?,?)",
                         (session["user_id"], "Withdrawal", amount, "Pending", fee, payout))
            db().commit()
            flash(f"Withdrawal request submitted. 6% fee: UGX {fee:,.2f}; estimated payout: UGX {payout:,.2f}.")
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
    invite_count = db().execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (session["user_id"],)).fetchone()[0]
    commission_total = db().execute("SELECT COALESCE(SUM(commission_amount),0) FROM referral_commissions WHERE referrer_id=?", (session["user_id"],)).fetchone()[0]
    invite_url = request.url_root.rstrip("/") + url_for("register", ref=user["referral_code"])
    return render_template("account.html", user=user, invite_count=invite_count,
                           commission_total=commission_total, invite_url=invite_url)

@app.route("/invite")
@login_required
def invite():
    user = db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    invite_count = db().execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (session["user_id"],)).fetchone()[0]
    commission_total = db().execute("SELECT COALESCE(SUM(commission_amount),0) FROM referral_commissions WHERE referrer_id=?", (session["user_id"],)).fetchone()[0]
    invite_url = request.url_root.rstrip("/") + url_for("register", ref=user["referral_code"])
    return render_template("invite.html", user=user, invite_count=invite_count,
                           commission_total=commission_total, invite_url=invite_url)

@app.route("/admin/deposits", methods=["GET", "POST"])
def admin_deposits():
    expected = os.environ.get("BITFORGE_ADMIN_CHAT_KEY", "")
    if not expected:
        return "Admin deposits are disabled: configure BITFORGE_ADMIN_CHAT_KEY.", 503
    if request.method == "POST" and request.form.get("admin_key"):
        if secrets.compare_digest(request.form.get("admin_key", ""), expected):
            session["bitforge_chat_admin"] = True
        else:
            flash("Invalid admin key.")
        return redirect(url_for("admin_deposits"))
    if not session.get("bitforge_chat_admin"):
        return render_template("admin_chat_login.html")
    if request.method == "POST":
        txid = request.form.get("transaction_id", type=int)
        tx = db().execute("SELECT * FROM transactions WHERE id=? AND kind LIKE 'Deposit%' AND status='Pending verification'", (txid,)).fetchone()
        if tx:
            db().execute("UPDATE users SET balance=balance+? WHERE id=?", (tx["amount"], tx["user_id"]))
            db().execute("UPDATE transactions SET status='Completed' WHERE id=?", (txid,))
            ref = db().execute("SELECT referred_by FROM users WHERE id=?", (tx["user_id"],)).fetchone()
            if ref and ref["referred_by"]:
                commission = round(float(tx["amount"]) * 0.25, 2)
                cur = db().execute("INSERT OR IGNORE INTO referral_commissions(referrer_id,referred_user_id,deposit_transaction_id,deposit_amount,commission_amount) VALUES(?,?,?,?,?)",
                                   (ref["referred_by"], tx["user_id"], txid, tx["amount"], commission))
                if cur.rowcount:
                    db().execute("UPDATE users SET balance=balance+? WHERE id=?", (commission, ref["referred_by"]))
                    db().execute("INSERT INTO transactions(user_id,kind,amount,status) VALUES(?,?,?,'Completed')",
                                 (ref["referred_by"], "Level 1 referral commission (25%)", commission))
            db().commit()
            flash("Deposit verified and wallet credited. Any eligible Level 1 commission was also credited.")
        else:
            flash("Pending deposit not found, or it has already been processed.")
        return redirect(url_for("admin_deposits"))
    pending = db().execute("SELECT t.*,u.full_name,u.phone FROM transactions t JOIN users u ON u.id=t.user_id WHERE t.kind LIKE 'Deposit%' AND t.status='Pending verification' ORDER BY t.id").fetchall()
    return render_template("admin_deposits.html", pending=pending)

init_db()
try:
    with sqlite3.connect(DB) as _check:
        _users = _check.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    print(f"[BITFORGE] Database active: {DB} | registered users: {_users}", flush=True)
except Exception as _err:
    print(f"[BITFORGE] Database startup check failed: {_err}", flush=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
