import os, sqlite3, shutil, secrets, hmac
from functools import wraps
from datetime import datetime, timedelta, timezone
from flask import jsonify
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
    {"id": 1, "name": "BITCOIN MINER · SHA-256", "category": "SHA-256", "description": "Bitcoin SHA-256 mining rig", "minimum": 5000, "maximum": 100000, "daily_rate": 20.0, "daily_projection": "Illustrative daily estimate; paid only from funded reserve", "duration": "2458 days (configured term)", "risk": "Mining risk", "image": "mining_rig_robot.jpg", "hashrate": "Daily cycle"},
    {"id": 2, "name": "BITCOIN MINER · ASIC PRO", "category": "ASIC PRO", "description": "ASIC Bitcoin mining equipment", "minimum": 150000, "maximum": 1000000, "daily_rate": 34.0, "daily_projection": "Illustrative daily estimate; paid only from funded reserve", "duration": "2458 days (configured term)", "risk": "Mining risk", "image": "bitcoin_mining_robot.jpg", "hashrate": "Daily cycle"},
    {"id": 3, "name": "BITCOIN MINER · HYDRO MAX", "category": "HYDRO MAX", "description": "High-capacity mining facility", "minimum": 2000000, "maximum": 5000000, "daily_rate": 47.6, "daily_projection": "Illustrative daily estimate; paid only from funded reserve", "duration": "2458 days (configured term)", "risk": "Mining risk", "image": "industrial_mining_robot.jpg", "hashrate": "Daily cycle"},
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

@app.before_request
def run_due_daily_credits():
    if request.endpoint not in ("static", "internal_daily_earnings"):
        process_daily_earnings()


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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            is_blocked INTEGER NOT NULL DEFAULT 0
        )""")
        # Upgrade older account tables safely; existing users and IDs remain intact.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "country" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN country TEXT NOT NULL DEFAULT 'Uganda'")
        if "is_blocked" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0")
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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            daily_scenario REAL NOT NULL DEFAULT 0,
            pool_activated_at TEXT,
            last_credit_at TEXT
        )""")
        mine_columns = {row[1] for row in conn.execute("PRAGMA table_info(mine_requests)")}
        for col, definition in (("daily_scenario", "REAL NOT NULL DEFAULT 0"), ("pool_activated_at", "TEXT"), ("last_credit_at", "TEXT")):
            if col not in mine_columns:
                conn.execute(f"ALTER TABLE mine_requests ADD COLUMN {col} {definition}")
        if "payer_phone" not in tx_columns:
            conn.execute("ALTER TABLE transactions ADD COLUMN payer_phone TEXT")
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

def ensure_earnings_schema():
    with sqlite3.connect(DB) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS platform_reserve (id INTEGER PRIMARY KEY CHECK(id=1), available REAL NOT NULL DEFAULT 0)")
        conn.execute("INSERT OR IGNORE INTO platform_reserve(id,available) VALUES(1,0)")
        conn.execute("CREATE TABLE IF NOT EXISTS mining_credits (id INTEGER PRIMARY KEY AUTOINCREMENT, mine_request_id INTEGER NOT NULL, user_id INTEGER NOT NULL, cycle_number INTEGER NOT NULL, amount REAL NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(mine_request_id,cycle_number))")
        conn.commit()


def process_daily_earnings():
    """Credit one due 24-hour cycle per rig, only when reserve cash is funded."""
    ensure_earnings_schema()
    now = datetime.now(timezone.utc)
    credited = 0
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        rigs = conn.execute("SELECT * FROM mine_requests WHERE status LIKE 'Purchased%' OR status='Active'").fetchall()
        reserve_row = conn.execute("SELECT available FROM platform_reserve WHERE id=1").fetchone()
        reserve = float(reserve_row['available'] or 0) if reserve_row else 0.0
        for rig in rigs:
            try:
                start = datetime.strptime(rig['created_at'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            elapsed = max(0, int((now-start).total_seconds()))
            max_cycles = min(2458, elapsed // 86400)
            last = rig['last_credit_at']
            if last:
                try:
                    last_dt = datetime.strptime(last, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                    completed = max(0, int((last_dt-start).total_seconds()) // 86400)
                except ValueError:
                    completed = 0
            else:
                completed = 0
            daily = round(float(rig['daily_scenario'] or 0), 2)
            while completed < max_cycles and daily > 0 and reserve >= daily:
                cycle = completed + 1
                cur = conn.execute("INSERT OR IGNORE INTO mining_credits(mine_request_id,user_id,cycle_number,amount) VALUES(?,?,?,?)", (rig['id'], rig['user_id'], cycle, daily))
                if cur.rowcount:
                    conn.execute("UPDATE users SET balance=balance+? WHERE id=?", (daily, rig['user_id']))
                    conn.execute("INSERT INTO transactions(user_id,kind,amount,status) VALUES(?,?,?,'Completed')", (rig['user_id'], 'Daily mining income · cycle ' + str(cycle), daily))
                    reserve -= daily
                    credited += 1
                completed = cycle
                conn.execute("UPDATE mine_requests SET last_credit_at=? WHERE id=?", ((start+timedelta(days=completed)).strftime('%Y-%m-%d %H:%M:%S'), rig['id']))
        conn.execute("UPDATE platform_reserve SET available=? WHERE id=1", (reserve,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return credited


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("register"))
        return fn(*args, **kwargs)
    return wrapped

@app.route("/internal/daily-earnings", methods=["POST", "GET"])
def internal_daily_earnings():
    secret = os.environ.get("DAILY_EARNINGS_SECRET", "")
    supplied = request.headers.get("X-Cron-Secret", "") or request.args.get("key", "")
    if not secret or not hmac.compare_digest(secret, supplied):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    count = process_daily_earnings()
    return jsonify({"ok": True, "credits_processed": count})


@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("home"))
    return redirect(url_for("register"))

@app.route("/register", methods=["GET", "POST"])
def register():
    invite_code = (request.args.get("ref") or request.form.get("referral_code") or "").strip().upper()
    if request.method == "POST":
        phone = normalize_phone(request.form.get("phone", ""))
        name = request.form.get("full_name", "").strip() or phone or "BitForge Member"
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
            if user["is_blocked"]:
                flash("This account is blocked. Contact BitForge support.")
                return render_template("login.html")
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
    admin_phone = normalize_phone(os.environ.get("BITFORGE_ADMIN_PHONE", "0757837051"))
    is_admin = bool(user and user["phone"] == admin_phone)
    return render_template("home.html", user=user, txns=txns, products=PRODUCTS, is_admin=is_admin)

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
    # Purchase is charged immediately and atomically; daily credits draw only from funded reserve.
    scenario = round(amount * float(product["daily_rate"]) / 100, 2)
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        debit = conn.execute("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?",
                             (amount, session["user_id"], amount))
        if debit.rowcount != 1:
            conn.rollback()
            flash("INSUFFICIENT FUNDS")
            return redirect(url_for("market"))
        conn.execute("INSERT INTO mine_requests(user_id,product_id,product_name,amount,status,daily_scenario) VALUES(?,?,?,?,?,?)",
                     (session["user_id"], product_id, product["name"], amount, "Purchased · daily cycle active", scenario))
        conn.execute("INSERT INTO transactions(user_id,kind,amount,status) VALUES(?,?,?,'Completed')",
                     (session["user_id"], "Mining rig purchase · " + product["name"], amount))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    flash("Purchase successful. Your 24-hour daily income cycle has started. Withdrawable credits are paid only when the platform reserve is funded.")
    return redirect(url_for("orders"))

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
        payer_phone = normalize_phone(request.form.get("payer_phone", ""))
        if not payer_phone or len("".join(c for c in payer_phone if c.isdigit())) < 10:
            flash("Enter the mobile money number you will deposit from.")
        elif amount < 10000:
            flash("Minimum deposit is UGX 10,000.")
        elif amount > 100000000:
            flash("Please enter a valid deposit amount.")
        elif not agent:
            flash("Choose an available deposit agent.")
        else:
            # Create the pending request immediately when the user selects an agent.
            # The wallet is credited only after an administrator approves it.
            method = f"Deposit · Agent · {agent['name']} · {agent['phone']}"
            cur = db().execute(
                "INSERT INTO transactions(user_id,kind,amount,status,payer_phone) VALUES(?,?,?,?,?)",
                (session["user_id"], method, amount, "Pending verification", payer_phone)
            )
            db().commit()
            return redirect(url_for("deposit", request_id=cur.lastrowid))
    deposit_request = None
    request_id = request.args.get("request_id", type=int)
    if request_id:
        deposit_request = db().execute(
            "SELECT * FROM transactions WHERE id=? AND user_id=? AND kind LIKE 'Deposit%'",
            (request_id, session["user_id"])
        ).fetchone()
    return render_template("deposit.html", agents=agents, deposit_request=deposit_request)

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
        payout_phone = normalize_phone(request.form.get("payout_phone", ""))
        if not payout_phone or len("".join(c for c in payout_phone if c.isdigit())) < 10:
            flash("Enter the mobile money number that should receive your withdrawal.")
        elif amount < 10000:
            flash("Minimum withdrawal request is UGX 10,000.")
        elif amount > user["balance"]:
            flash("INSUFFICIENT FUNDS")
        else:
            fee = round(amount * 0.06, 2)
            payout = round(amount - fee, 2)
            # Atomically reserve the gross amount immediately; admin rejection refunds it.
            conn = db()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?",
                                   (amount, session["user_id"], amount))
                if cur.rowcount != 1:
                    conn.rollback()
                    flash("INSUFFICIENT FUNDS")
                else:
                    conn.execute("INSERT INTO transactions(user_id,kind,amount,status,fee,payout_amount,payer_phone) VALUES(?,?,?,?,?,?,?)",
                                 (session["user_id"], "Withdrawal", amount, "Pending", fee, payout, payout_phone))
                    conn.commit()
                    flash(f"Withdrawal submitted. UGX {amount:,.0f} deducted from available balance. Fee: UGX {fee:,.0f}; payout after approval: UGX {payout:,.0f}.")
                    return redirect(url_for("wallet"))
            except Exception:
                conn.rollback()
                raise
    return render_template("withdraw.html", user=user, txns=txns)

@app.route("/orders")
@login_required
def orders():
    rigs = db().execute("SELECT * FROM mine_requests WHERE user_id=? ORDER BY id DESC",
                        (session["user_id"],)).fetchall()
    rig_images = {p["id"]: p["image"] for p in PRODUCTS}
    return render_template("orders.html", rigs=rigs, rig_images=rig_images)

@app.route("/account")
@login_required
def account():
    user = db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    invite_count = db().execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (session["user_id"],)).fetchone()[0]
    commission_total = db().execute("SELECT COALESCE(SUM(commission_amount),0) FROM referral_commissions WHERE referrer_id=?", (session["user_id"],)).fetchone()[0]
    invite_url = request.url_root.rstrip("/") + url_for("register", ref=user["referral_code"])
    admin_phone = normalize_phone(os.environ.get("BITFORGE_ADMIN_PHONE", "0757837051"))
    is_admin = bool(user and user["phone"] == admin_phone)
    return render_template("account.html", user=user, invite_count=invite_count,
                           commission_total=commission_total, invite_url=invite_url, is_admin=is_admin)

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


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = db().execute("SELECT phone FROM users WHERE id=?", (session.get("user_id", -1),)).fetchone()
        admin_phone = normalize_phone(os.environ.get("BITFORGE_ADMIN_PHONE", "0757837051"))
        if not user or user["phone"] != admin_phone or not session.get("bitforge_admin_verified"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapped

@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin_login():
    user = db().execute("SELECT phone FROM users WHERE id=?", (session.get("user_id", -1),)).fetchone()
    admin_phone = normalize_phone(os.environ.get("BITFORGE_ADMIN_PHONE", "0757837051"))
    if not user or user["phone"] != admin_phone:
        flash("Admin access denied.")
        return redirect(url_for("home"))
    # The designated administrator must already be authenticated with their account password.
    session["bitforge_admin_verified"] = True
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/panel", methods=["GET", "POST"])
@admin_required
def admin_dashboard():
    if request.method == "POST":
        action = request.form.get("action")
        uid = request.form.get("user_id", type=int)
        txid = request.form.get("transaction_id", type=int)
        if action == "fund_reserve":
            try:
                funding = float(request.form.get("reserve_amount", "0"))
                if funding <= 0 or funding > 1e12:
                    raise ValueError()
                conn = db(); conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE platform_reserve SET available=available+? WHERE id=1", (funding,))
                conn.commit(); flash(f"Payout reserve funded by UGX {funding:,.0f}.")
            except (TypeError, ValueError):
                flash("Enter a valid positive reserve funding amount.")
        elif action == "adjust_balance" and uid:
            try:
                new_balance = float(request.form.get("new_balance", ""))
                if new_balance < 0 or new_balance > 1e12:
                    raise ValueError()
                old = db().execute("SELECT balance FROM users WHERE id=?", (uid,)).fetchone()
                if not old:
                    flash("User not found.")
                else:
                    delta = round(new_balance - float(old["balance"] or 0), 2)
                    db().execute("UPDATE users SET balance=? WHERE id=?", (new_balance, uid))
                    db().execute("INSERT INTO transactions(user_id,kind,amount,status) VALUES(?,?,?,'Completed')",
                                 (uid, "Admin balance adjustment", delta))
                    db().commit(); flash("User balance updated.")
            except (TypeError, ValueError):
                flash("Enter a valid non-negative balance.")
        elif action == "admin_reply":
            reply_uid = request.form.get("reply_user_id", type=int)
            message = request.form.get("message", "").strip()
            if reply_uid and message and len(message) <= 2000 and db().execute("SELECT id FROM users WHERE id=?", (reply_uid,)).fetchone():
                db().execute("INSERT INTO support_messages(user_id,sender,message) VALUES(?, 'admin', ?)", (reply_uid, message))
                db().commit(); flash("Reply sent to member.")
            else:
                flash("Select a member and enter a reply (maximum 2,000 characters).")
        elif action == "block" and uid:
            db().execute("UPDATE users SET is_blocked=1 WHERE id=?", (uid,))
            db().commit(); flash("User blocked.")
        elif action == "unblock" and uid:
            db().execute("UPDATE users SET is_blocked=0 WHERE id=?", (uid,))
            db().commit(); flash("User unblocked.")
        elif action == "reset_password" and uid:
            new_password = request.form.get("new_password", "")
            if len(new_password) >= 8:
                db().execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_password), uid))
                db().commit(); flash("Password reset.")
            else: flash("New password must contain at least 8 characters.")
        elif action == "reject_deposit" and txid:
            conn = db(); conn.execute("BEGIN IMMEDIATE")
            tx = conn.execute("SELECT * FROM transactions WHERE id=? AND kind LIKE 'Deposit%' AND status='Pending verification'", (txid,)).fetchone()
            if tx:
                conn.execute("UPDATE transactions SET status='Rejected' WHERE id=?", (txid,))
                conn.commit(); flash("Deposit request rejected. No wallet funds were added.")
            else:
                conn.rollback(); flash("Pending deposit not found or already processed.")
        elif action == "approve_deposit" and txid:
            conn = db(); conn.execute("BEGIN IMMEDIATE")
            tx = conn.execute("SELECT * FROM transactions WHERE id=? AND kind LIKE 'Deposit%' AND status='Pending verification'", (txid,)).fetchone()
            if tx:
                conn.execute("UPDATE users SET balance=balance+? WHERE id=?", (tx["amount"], tx["user_id"]))
                conn.execute("UPDATE transactions SET status='Completed' WHERE id=?", (txid,))
                ref = conn.execute("SELECT referred_by FROM users WHERE id=?", (tx["user_id"],)).fetchone()
                if ref and ref["referred_by"]:
                    commission = round(float(tx["amount"]) * 0.25, 2)
                    cur = conn.execute("INSERT OR IGNORE INTO referral_commissions(referrer_id,referred_user_id,deposit_transaction_id,deposit_amount,commission_amount) VALUES(?,?,?,?,?)", (ref["referred_by"], tx["user_id"], txid, tx["amount"], commission))
                    if cur.rowcount:
                        conn.execute("UPDATE users SET balance=balance+? WHERE id=?", (commission, ref["referred_by"]))
                        conn.execute("INSERT INTO transactions(user_id,kind,amount,status) VALUES(?,?,?,'Completed')", (ref["referred_by"], "Level 1 referral commission (25%)", commission))
                conn.commit(); flash("Deposit verified; user balance and any eligible referral commission credited.")
            else:
                conn.rollback(); flash("Pending deposit not found or already processed.")
        elif action in ("approve_withdrawal", "reject_withdrawal") and txid:
            conn = db(); conn.execute("BEGIN IMMEDIATE")
            tx = conn.execute("SELECT * FROM transactions WHERE id=? AND kind='Withdrawal' AND status='Pending'", (txid,)).fetchone()
            if tx:
                if action == "reject_withdrawal":
                    conn.execute("UPDATE users SET balance=balance+? WHERE id=?", (tx["amount"], tx["user_id"]))
                    conn.execute("UPDATE transactions SET status='Rejected · refunded' WHERE id=?", (txid,))
                    flash("Withdrawal rejected and reserved funds refunded.")
                else:
                    conn.execute("UPDATE transactions SET status='Approved · payout due' WHERE id=?", (txid,))
                    flash("Withdrawal approved. Send the displayed net payout through the payment provider.")
                conn.commit()
            else:
                conn.rollback(); flash("Pending withdrawal not found.")
        return redirect(url_for("admin_dashboard"))
    search = request.args.get("q", "").strip()
    if search:
        users = db().execute("SELECT u.id,u.full_name,u.phone,u.balance,u.created_at,u.is_blocked,u.referral_code,u.referred_by,ref.referral_code AS invited_by_code FROM users u LEFT JOIN users ref ON ref.id=u.referred_by WHERE u.full_name LIKE ? OR u.phone LIKE ? ORDER BY u.id DESC", (f"%{search}%", f"%{search}%")).fetchall()
    else:
        users = db().execute("SELECT u.id,u.full_name,u.phone,u.balance,u.created_at,u.is_blocked,u.referral_code,u.referred_by,ref.referral_code AS invited_by_code FROM users u LEFT JOIN users ref ON ref.id=u.referred_by ORDER BY u.id DESC").fetchall()
    reserve = db().execute("SELECT available FROM platform_reserve WHERE id=1").fetchone()[0]
    stats = {
        "users": db().execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "active": db().execute("SELECT COUNT(*) FROM users WHERE is_blocked=0").fetchone()[0],
        "blocked": db().execute("SELECT COUNT(*) FROM users WHERE is_blocked=1").fetchone()[0],
        "balance": db().execute("SELECT COALESCE(SUM(balance),0) FROM users").fetchone()[0],
    }
    deposits = db().execute("SELECT t.*,u.full_name,u.phone FROM transactions t JOIN users u ON u.id=t.user_id WHERE t.kind LIKE 'Deposit%' AND t.status='Pending verification' ORDER BY t.id").fetchall()
    withdrawals = db().execute("SELECT t.*,u.full_name,u.phone FROM transactions t JOIN users u ON u.id=t.user_id WHERE t.kind='Withdrawal' AND t.status='Pending' ORDER BY t.id").fetchall()
    support_threads = db().execute("""SELECT u.id AS user_id,u.full_name,u.phone,
        (SELECT message FROM support_messages m WHERE m.user_id=u.id ORDER BY m.id DESC LIMIT 1) AS last_message,
        (SELECT created_at FROM support_messages m WHERE m.user_id=u.id ORDER BY m.id DESC LIMIT 1) AS last_time
        FROM users u WHERE EXISTS (SELECT 1 FROM support_messages m WHERE m.user_id=u.id)
        ORDER BY (SELECT MAX(id) FROM support_messages m WHERE m.user_id=u.id) DESC LIMIT 30""").fetchall()
    return render_template("admin_panel.html", users=users, deposits=deposits, withdrawals=withdrawals, stats=stats, search=search, support_threads=support_threads, reserve=reserve)

@app.route("/admin/user/<int:user_id>")
@admin_required
def admin_user_detail(user_id):
    member = db().execute("SELECT u.id,u.full_name,u.phone,u.balance,u.created_at,u.is_blocked,u.referral_code,u.referred_by,ref.referral_code AS invited_by_code FROM users u LEFT JOIN users ref ON ref.id=u.referred_by WHERE u.id=?", (user_id,)).fetchone()
    if not member:
        flash("User not found.")
        return redirect(url_for("admin_dashboard"))
    transactions = db().execute("SELECT kind,amount,status,created_at FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,)).fetchall()
    mining_orders = db().execute("SELECT product_name,amount,status,created_at FROM mine_requests WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
    return render_template("admin_user_detail.html", member=member, transactions=transactions, mining_orders=mining_orders)

@app.route("/admin/logout")
def admin_logout():
    session.pop("bitforge_admin_verified", None)
    return redirect(url_for("account"))

init_db()
ensure_earnings_schema()
try:
    with sqlite3.connect(DB) as _check:
        _users = _check.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    print(f"[BITFORGE] Database active: {DB} | registered users: {_users}", flush=True)
except Exception as _err:
    print(f"[BITFORGE] Database startup check failed: {_err}", flush=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
