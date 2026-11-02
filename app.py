from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3, os, hashlib, secrets
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bitforge-local-demo-change-this")
DB = os.path.join(os.path.dirname(__file__), "bitforge.db")

PRODUCTS = [
    {"id": 1, "name": "Solar Farm Plot", "category": "Solar", "description": "Participation in a renewable-energy solar project.", "minimum": 25000, "duration": "36 months", "risk": "Low", "image": "solar.svg"},
    {"id": 2, "name": "Commercial Real Estate Unit", "category": "Real Estate", "description": "Fractional participation in a commercial property project.", "minimum": 50000, "duration": "48 months", "risk": "Medium", "image": "building.svg"},
    {"id": 3, "name": "Green Energy Fund", "category": "Green Energy", "description": "A diversified clean-energy project portfolio.", "minimum": 10000, "duration": "24 months", "risk": "Medium", "image": "wind.svg"},
]

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL,
            country TEXT NOT NULL, phone TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, created_at TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            kind TEXT NOT NULL, amount INTEGER NOT NULL, method TEXT,
            phone TEXT, status TEXT NOT NULL DEFAULT 'Pending',
            created_at TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS investments(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL, amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'Submitted', created_at TEXT NOT NULL)""")

init_db()

@app.context_processor
def inject_globals():
    return {"brand": "BitForge", "year": datetime.now().year}

@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("home"))
    return redirect(url_for("register"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        country = request.form.get("country", "Uganda")
        phone = request.form.get("phone", "").strip().replace(" ", "")
        password = request.form.get("password", "")
        if not full_name or not phone or len(password) < 6:
            flash("Enter your name, phone number and a password of at least 6 characters.", "error")
            return render_template("register.html")
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        try:
            with db() as con:
                con.execute("INSERT INTO users(full_name,country,phone,password_hash,created_at) VALUES(?,?,?,?,?)",
                    (full_name, country, phone, password_hash, datetime.now().isoformat(timespec="seconds")))
            return redirect(url_for("login", registered="1"))
        except sqlite3.IntegrityError:
            flash("That phone number is already registered. Please sign in.", "error")
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form.get("phone", "").strip().replace(" ", "")
        password_hash = hashlib.sha256(request.form.get("password", "").encode()).hexdigest()
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE phone=? AND password_hash=?", (phone, password_hash)).fetchone()
        if user:
            session["user_id"] = user["id"]
            return redirect(url_for("home"))
        flash("Incorrect phone number or password.", "error")
    return render_template("login.html")

def current_user():
    if not session.get("user_id"):
        return None
    with db() as con:
        return con.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

@app.route("/home")
def home():
    user = current_user()
    if not user:
        return redirect(url_for("register"))
    with db() as con:
        pending = con.execute("SELECT COUNT(*) n FROM requests WHERE user_id=? AND status='Pending'", (user["id"],)).fetchone()["n"]
        investments = con.execute("SELECT * FROM investments WHERE user_id=? ORDER BY id DESC LIMIT 5", (user["id"],)).fetchall()
    return render_template("home.html", user=user, products=PRODUCTS, pending=pending, investments=investments)

@app.route("/market")
def market():
    if not current_user(): return redirect(url_for("register"))
    return render_template("market.html", user=current_user(), products=PRODUCTS)

@app.route("/invest/<int:product_id>", methods=["POST"])
def invest(product_id):
    user = current_user()
    if not user: return redirect(url_for("login"))
    product = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if not product: return redirect(url_for("market"))
    try: amount = int(request.form.get("amount", "0"))
    except ValueError: amount = 0
    if amount < product["minimum"]:
        flash(f"Minimum amount for this project is {product['minimum']:,} UGX.", "error")
    else:
        with db() as con:
            con.execute("INSERT INTO investments(user_id,product_id,amount,status,created_at) VALUES(?,?,?,?,?)",
                (user["id"], product_id, amount, "Submitted for review", datetime.now().isoformat(timespec="seconds")))
        flash("Investment request submitted for review. No money has been deducted.", "success")
    return redirect(url_for("market"))

@app.route("/deposit", methods=["GET", "POST"])
def deposit():
    user = current_user()
    if not user: return redirect(url_for("login"))
    if request.method == "POST":
        try: amount = int(request.form.get("amount", "0"))
        except ValueError: amount = 0
        method = request.form.get("method", "")
        phone = request.form.get("phone", "").strip()
        if amount < 10000 or method not in ("MTN Mobile Money", "Airtel Money") or not phone:
            flash("Minimum deposit is 10,000 UGX. Select a payment method and enter the payment number.", "error")
        else:
            with db() as con:
                con.execute("INSERT INTO requests(user_id,kind,amount,method,phone,status,created_at) VALUES(?,?,?,?,?,?,?)",
                    (user["id"], "Deposit", amount, method, phone, "Pending verification", datetime.now().isoformat(timespec="seconds")))
            flash("Deposit request saved as pending. Connect a verified payment provider before accepting real payments.", "success")
            return redirect(url_for("home"))
    return render_template("deposit.html", user=user)

@app.route("/withdraw", methods=["GET", "POST"])
def withdraw():
    user = current_user()
    if not user: return redirect(url_for("login"))
    if request.method == "POST":
        try: amount = int(request.form.get("amount", "0"))
        except ValueError: amount = 0
        method = request.form.get("method", "")
        phone = request.form.get("phone", "").strip()
        if amount < 5000 or method not in ("MTN Mobile Money", "Airtel Money") or not phone:
            flash("Minimum withdrawal request is 5,000 UGX. Complete all fields.", "error")
        else:
            with db() as con:
                con.execute("INSERT INTO requests(user_id,kind,amount,method,phone,status,created_at) VALUES(?,?,?,?,?,?,?)",
                    (user["id"], "Withdrawal", amount, method, phone, "Pending review", datetime.now().isoformat(timespec="seconds")))
            flash("Withdrawal request saved for review. This demo does not send money.", "success")
            return redirect(url_for("home"))
    return render_template("withdraw.html", user=user)

@app.route("/orders")
def orders():
    user = current_user()
    if not user: return redirect(url_for("login"))
    with db() as con:
        rows = con.execute("SELECT i.*, p.name FROM investments i LEFT JOIN (SELECT 1 id,'Solar Farm Plot' name UNION SELECT 2,'Commercial Real Estate Unit' UNION SELECT 3,'Green Energy Fund') p ON p.id=i.product_id WHERE i.user_id=? ORDER BY i.id DESC", (user["id"],)).fetchall()
    return render_template("orders.html", user=user, rows=rows)

@app.route("/account")
def account():
    user = current_user()
    if not user: return redirect(url_for("login"))
    with db() as con:
        rows = con.execute("SELECT * FROM requests WHERE user_id=? ORDER BY id DESC LIMIT 10", (user["id"],)).fetchall()
    return render_template("account.html", user=user, rows=rows)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
