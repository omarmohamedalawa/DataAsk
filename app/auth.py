"""Accounts: PBKDF2 password hashes, random session tokens (stored hashed), per-user data DB, rate limit."""
import threading, os, sqlite3, hashlib, secrets, time, re, csv, io, shutil
ROOT = os.path.join(os.path.dirname(__file__), "..", "data")
APPDB = os.path.join(ROOT, "app.db")
USERS = os.path.join(ROOT, "users")
DEMO = os.path.join(ROOT, "retail.db")
ITER = 200_000
class _Ip(threading.local):
    v = "-"
    def get(self): return self.v
CURRENT_IP = _Ip()
SESSION_SECS = 7 * 86400
MAX_UPLOAD = 5 * 1024 * 1024
REQUIRED = ["order_id", "order_date", "customer_id", "city", "category", "product", "channel", "payment", "qty", "revenue"]

def _db():
    os.makedirs(USERS, exist_ok=True)
    c = sqlite3.connect(APPDB)
    c.executescript("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, salt BLOB, pw BLOB, created REAL, has_upload INT DEFAULT 0);
    CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INT, expires REAL);
    CREATE TABLE IF NOT EXISTS hits(user_id INT, ts REAL);
    CREATE TABLE IF NOT EXISTS attempts(k TEXT, ts REAL);""")
    return c

def _hash(pw, salt): return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)

COMMON = {"password", "password1", "password123", "12345678", "123456789", "qwertyui", "iloveyou", "11111111", "00000000"}

def signup(email, pw):
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}", email): raise ValueError("invalid email")
    if len(pw) < 8 or len(pw) > 200: raise ValueError("password must be 8 to 200 characters")
    if pw.lower() in COMMON or pw.lower() == email.split("@")[0]: raise ValueError("password is too easy to guess")
    c = _db(); salt = secrets.token_bytes(16)
    try: c.execute("INSERT INTO users(email,salt,pw,created) VALUES(?,?,?,?)", (email, salt, _hash(pw, salt), time.time())); c.commit()
    except sqlite3.IntegrityError: raise ValueError("email already registered")
    finally: pass
    uid = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]; c.close()
    return uid

DUMMY_SALT = b"\0" * 16

def throttle(key, limit, window):
    """Sliding-window counter for anything keyed by a string (login attempts per email/IP, signups per IP). True = allowed."""
    c = _db(); now = time.time()
    c.execute("DELETE FROM attempts WHERE ts<?", (now - 86400,))
    n = c.execute("SELECT count(*) FROM attempts WHERE k=? AND ts>?", (key, now - window)).fetchone()[0]
    if n < limit: c.execute("INSERT INTO attempts VALUES(?,?)", (key, now))
    c.commit(); c.close(); return n < limit

def failed_logins(key, window=900):
    c = _db(); n = c.execute("SELECT count(*) FROM attempts WHERE k=? AND ts>?", (key, time.time() - window)).fetchone()[0]; c.close(); return n

def login(email, pw):
    email = email.strip().lower()[:320]
    if failed_logins("fail:" + email) >= 8 or failed_logins("failip:" + CURRENT_IP.get(), 900) >= 30:
        raise PermissionError("too many failed attempts, try again in 15 minutes")
    c = _db(); r = c.execute("SELECT id,salt,pw FROM users WHERE email=?", (email,)).fetchone()
    h = _hash(pw[:200], r[1] if r else DUMMY_SALT)  # always hash: no timing difference between unknown email and wrong password
    ok = r and secrets.compare_digest(h, r[2])
    if not ok:
        c.close(); throttle("fail:" + email, 10**6, 900); throttle("failip:" + CURRENT_IP.get(), 10**6, 900); return None
    tok = secrets.token_urlsafe(32)
    c.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
    c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE user_id=? ORDER BY expires DESC LIMIT 9)", (r[0], r[0]))
    c.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(tok.encode()).hexdigest(), r[0], time.time() + SESSION_SECS)); c.commit(); c.close()
    return tok

def user_from_token(tok):
    if not tok: return None
    c = _db(); r = c.execute("SELECT u.id,u.email,u.has_upload FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires>?", (hashlib.sha256(tok.encode()).hexdigest(), time.time())).fetchone(); c.close()
    return {"id": r[0], "email": r[1], "has_upload": bool(r[2])} if r else None

def logout(tok):
    if not tok: return
    c = _db(); c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(tok.encode()).hexdigest(),)); c.commit(); c.close()

def rate_ok(uid, limit=30, window=60):
    c = _db(); now = time.time()
    c.execute("DELETE FROM hits WHERE ts<?", (now - window,))
    n = c.execute("SELECT count(*) FROM hits WHERE user_id=?", (uid,)).fetchone()[0]
    if n < limit: c.execute("INSERT INTO hits VALUES(?,?)", (uid, now))
    c.commit(); c.close(); return n < limit

def user_db(user):
    """Per-user uploaded data if present, else the shared read-only demo database."""
    p = os.path.join(USERS, f"{user['id']}.db")
    return p if user["has_upload"] and os.path.exists(p) else DEMO

def _safe_rows(rd):
    try:
        for r in rd: yield r
    except csv.Error: raise ValueError("not a valid CSV file")

def upload_csv(user, raw: bytes):
    if len(raw) > MAX_UPLOAD: raise ValueError("file too large (5 MB max)")
    try: rd = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    except UnicodeDecodeError: raise ValueError("file must be UTF-8 text (CSV)")
    miss = [c for c in REQUIRED if c not in (rd.fieldnames or [])]
    if miss: raise ValueError("missing columns: " + ", ".join(miss))
    rows = []
    try: miss = [c for c in REQUIRED if c not in (rd.fieldnames or [])]
    except csv.Error: raise ValueError("not a valid CSV file")
    if miss: raise ValueError("missing columns: " + ", ".join(miss))
    for i, r in enumerate(_safe_rows(rd)):
        if i >= 200_000: raise ValueError("too many rows (200k max)")
        try:
            d = r["order_date"][:10]; assert re.fullmatch(r"20\d\d-\d\d-\d\d", d)
            qty = int(float(r["qty"])); rev = float(r["revenue"]); prof = float(r["profit"]) if r.get("profit") not in (None, "") else None
        except Exception: raise ValueError(f"bad value on row {i + 2}")
        rows.append((r["order_id"], d, d[:7], d[:4], r["customer_id"], r["city"], r["category"], r["product"], r["product"], r["channel"], r["payment"], qty, rev, prof))
    if not rows: raise ValueError("no rows")
    p = os.path.join(USERS, f"{user['id']}.db"); os.makedirs(USERS, exist_ok=True)
    if os.path.exists(p): os.remove(p)
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE sales_fact(order_id,order_date,month,year,customer_id,city,category,product,product_ar,channel,payment,qty INT,revenue REAL,profit REAL)")
    c.executemany("INSERT INTO sales_fact VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.execute("CREATE VIEW returns_fact AS SELECT order_id,month,year,city,category,product,qty,0.0 AS revenue FROM sales_fact WHERE 0")
    c.commit(); c.close()
    a = _db(); a.execute("UPDATE users SET has_upload=1 WHERE id=?", (user["id"],)); a.commit(); a.close()
    return len(rows)

def reset_demo(user):
    p = os.path.join(USERS, f"{user['id']}.db")
    if os.path.exists(p): os.remove(p)
    a = _db(); a.execute("UPDATE users SET has_upload=0 WHERE id=?", (user["id"],)); a.commit(); a.close()
