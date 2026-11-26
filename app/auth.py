"""Accounts: PBKDF2 password hashes, random session tokens (stored hashed), per-user data DB, rate limit."""
import totp, json, threading, os, sqlite3, hashlib, secrets, time, re, csv, io, shutil
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
    CREATE TABLE IF NOT EXISTS attempts(k TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS recovery(user_id INT, code_hash TEXT, used INT DEFAULT 0);
    CREATE TABLE IF NOT EXISTS vtokens(token_hash TEXT PRIMARY KEY, user_id INT, expires REAL);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts REAL, user_id INT, email TEXT, ip TEXT, event TEXT, detail TEXT, prev TEXT, h TEXT);""")
    have = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    for col, ddl in (("totp_secret", "TEXT"), ("totp_on", "INT DEFAULT 0"), ("totp_last", "INT DEFAULT 0"), ("verified", "INT DEFAULT 1")):
        if col not in have: c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    return c

def _hash(pw, salt): return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)

COMMON = {"password", "password1", "password123", "12345678", "123456789", "qwertyui", "iloveyou", "11111111", "00000000"}

def signup(email, pw):
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}", email): raise ValueError("invalid email")
    if len(pw) < 8 or len(pw) > 200: raise ValueError("password must be 8 to 200 characters")
    if pw.lower() in COMMON or pw.lower() == email.split("@")[0]: raise ValueError("password is too easy to guess")
    c = _db(); salt = secrets.token_bytes(16)
    try: c.execute("INSERT INTO users(email,salt,pw,created,verified) VALUES(?,?,?,?,?)", (email, salt, _hash(pw, salt), time.time(), 0 if REQUIRE_VERIFY() else 1)); c.commit()
    except sqlite3.IntegrityError: raise ValueError("email already registered")
    finally: pass
    uid = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]; c.close()
    audit("signup", uid, email)
    if REQUIRE_VERIFY():
        try: send_verification(uid, email)
        except Exception: audit("verify_mail_failed", uid, email)
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


class NotVerified(PermissionError): pass
SMTP = lambda: os.environ.get("SMTP_HOST")
REQUIRE_VERIFY = lambda: os.environ.get("REQUIRE_EMAIL_VERIFY") == "1" and bool(SMTP())

def audit(event, user_id=None, email=None, detail=""):
    """Append-only, hash-chained log (each row hashes the previous one, so edits or deletions in the middle are detectable)."""
    c = _db(); prev = (c.execute("SELECT h FROM audit ORDER BY id DESC LIMIT 1").fetchone() or ("0" * 64,))[0]
    ts = round(time.time(), 3); ip = CURRENT_IP.get(); detail = str(detail)[:200]
    h = hashlib.sha256(json.dumps([prev, ts, user_id, email, ip, event, detail]).encode()).hexdigest()
    c.execute("INSERT INTO audit(ts,user_id,email,ip,event,detail,prev,h) VALUES(?,?,?,?,?,?,?,?)", (ts, user_id, email, ip, event, detail, prev, h)); c.commit(); c.close()

def audit_verify():
    c = _db(); prev = "0" * 64; n = 0
    for r in c.execute("SELECT ts,user_id,email,ip,event,detail,prev,h FROM audit ORDER BY id"):
        if r[6] != prev or hashlib.sha256(json.dumps([r[6], r[0], r[1], r[2], r[3], r[4], r[5]]).encode()).hexdigest() != r[7]: c.close(); return False
        prev = r[7]; n += 1
    c.close(); return True

def audit_for(user, limit=50):
    c = _db(); rows = c.execute("SELECT ts,event,ip,detail FROM audit WHERE user_id=? ORDER BY id DESC LIMIT ?", (user["id"], limit)).fetchall(); c.close()
    return [{"ts": r[0], "event": r[1], "ip": r[2], "detail": r[3]} for r in rows]

def _rh(code): return hashlib.sha256(code.strip().lower().replace("-", "").encode()).hexdigest()

def new_recovery_codes(uid, n=8):
    codes = ["-".join(secrets.token_hex(2) for _ in range(3)) for _ in range(n)]  # 48 bits each, single use
    c = _db(); c.execute("DELETE FROM recovery WHERE user_id=?", (uid,))
    c.executemany("INSERT INTO recovery VALUES(?,?,0)", [(uid, _rh(x)) for x in codes]); c.commit(); c.close(); return codes

def _use_recovery(c, uid, code):
    r = c.execute("UPDATE recovery SET used=1 WHERE user_id=? AND code_hash=? AND used=0", (uid, _rh(code or ""))); return r.rowcount == 1

def reset_password(email, code, new_pw):
    email = email.strip().lower()[:320]
    if not throttle("rst:" + email, 5, 900) or not throttle("rstip:" + CURRENT_IP.get(), 20, 900): raise PermissionError("too many attempts, try again in 15 minutes")
    if len(new_pw) < 8 or len(new_pw) > 200 or new_pw.lower() in COMMON: raise ValueError("password must be 8 to 200 characters and not a common one")
    c = _db(); r = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if not r or not _use_recovery(c, r[0], code):
        c.commit(); c.close(); audit("password_reset_failed", r[0] if r else None, email); raise ValueError("invalid email or recovery code")
    salt = secrets.token_bytes(16)
    c.execute("UPDATE users SET salt=?, pw=? WHERE id=?", (salt, _hash(new_pw, salt), r[0]))
    c.execute("DELETE FROM sessions WHERE user_id=?", (r[0],))  # everyone is logged out
    left = c.execute("SELECT count(*) FROM recovery WHERE user_id=? AND used=0", (r[0],)).fetchone()[0]; c.commit(); c.close()
    audit("password_reset", r[0], email, f"{left} recovery codes left"); return left

def totp_begin(user):
    sec = totp.new_secret(); c = _db(); c.execute("UPDATE users SET totp_secret=?, totp_on=0, totp_last=0 WHERE id=?", (sec, user["id"])); c.commit(); c.close()
    return {"secret": sec, "uri": totp.uri(sec, user["email"])}

def totp_enable(user, code):
    c = _db(); r = c.execute("SELECT totp_secret,totp_on FROM users WHERE id=?", (user["id"],)).fetchone()
    st = totp.verify(r[0], code) if r and r[0] and not r[1] else None
    if st is None: c.close(); raise ValueError("wrong code (or 2FA setup not started)")
    c.execute("UPDATE users SET totp_on=1, totp_last=? WHERE id=?", (st, user["id"])); c.commit(); c.close()
    codes = new_recovery_codes(user["id"]); audit("2fa_enabled", user["id"], user["email"]); return codes

def totp_disable(user, pw, code):
    c = _db(); r = c.execute("SELECT salt,pw,totp_secret,totp_on,totp_last FROM users WHERE id=?", (user["id"],)).fetchone()
    if not r or not r[3] or not secrets.compare_digest(_hash(pw[:200], r[0]), r[1]) or totp.verify(r[2], code, r[4]) is None:
        c.close(); audit("2fa_disable_failed", user["id"], user["email"]); raise ValueError("wrong password or code")
    c.execute("UPDATE users SET totp_on=0, totp_secret=NULL WHERE id=?", (user["id"],)); c.commit(); c.close(); audit("2fa_disabled", user["id"], user["email"])

def send_verification(uid, email):
    import smtplib
    from email.message import EmailMessage
    tok = secrets.token_urlsafe(32); c = _db()
    c.execute("INSERT INTO vtokens VALUES(?,?,?)", (hashlib.sha256(tok.encode()).hexdigest(), uid, time.time() + 86400)); c.commit(); c.close()
    m = EmailMessage(); m["Subject"] = "Confirm your DataAsk account"; m["From"] = os.environ.get("SMTP_FROM", "dataask@localhost"); m["To"] = email
    m.set_content(f"Open this link to confirm your email (valid 24 hours):\n{os.environ.get('BASE_URL', 'http://127.0.0.1:8000')}/api/verify?token={tok}\n")
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=15) as s:
        if os.environ.get("SMTP_STARTTLS", "1") == "1": s.starttls()
        if os.environ.get("SMTP_USER"): s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
        s.send_message(m)

def verify_email(tok):
    c = _db(); r = c.execute("SELECT user_id FROM vtokens WHERE token_hash=? AND expires>?", (hashlib.sha256(tok.encode()).hexdigest(), time.time())).fetchone()
    if not r: c.close(); return False
    c.execute("UPDATE users SET verified=1 WHERE id=?", (r[0],)); c.execute("DELETE FROM vtokens WHERE user_id=?", (r[0],)); c.commit(); c.close(); audit("email_verified", r[0]); return True

def login(email, pw, code=None):
    email = email.strip().lower()[:320]
    if failed_logins("fail:" + email) >= 8 or failed_logins("failip:" + CURRENT_IP.get(), 900) >= 30:
        audit("login_locked", None, email); raise PermissionError("too many failed attempts, try again in 15 minutes")
    c = _db(); r = c.execute("SELECT id,salt,pw,totp_on,totp_secret,totp_last,verified FROM users WHERE email=?", (email,)).fetchone()
    h = _hash(pw[:200], r[1] if r else DUMMY_SALT)  # always hash: no timing difference between unknown email and wrong password
    ok = r and secrets.compare_digest(h, r[2])
    if not ok:
        c.close(); throttle("fail:" + email, 10**6, 900); throttle("failip:" + CURRENT_IP.get(), 10**6, 900); audit("login_failed", r[0] if r else None, email); return None
    if r[3]:
        if not code: c.close(); return "need_2fa"
        st = totp.verify(r[4], code, r[5])
        if st is not None:
            if c.execute("UPDATE users SET totp_last=? WHERE id=? AND totp_last<?", (st, r[0], st)).rowcount != 1: st = None  # replay
        if st is None and not _use_recovery(c, r[0], code):
            c.commit(); c.close(); throttle("fail:" + email, 10**6, 900); throttle("failip:" + CURRENT_IP.get(), 10**6, 900); audit("2fa_failed", r[0], email); return None
        c.commit()
    if not r[6]: c.close(); raise NotVerified("please confirm your email first (check your inbox)")
    tok = secrets.token_urlsafe(32)
    c.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
    c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE user_id=? ORDER BY expires DESC LIMIT 9)", (r[0], r[0]))
    c.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(tok.encode()).hexdigest(), r[0], time.time() + SESSION_SECS)); c.commit(); c.close()
    audit("login_ok", r[0], email); return tok

def user_from_token(tok):
    if not tok: return None
    c = _db(); r = c.execute("SELECT u.id,u.email,u.has_upload,u.totp_on FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires>?", (hashlib.sha256(tok.encode()).hexdigest(), time.time())).fetchone(); c.close()
    return {"id": r[0], "email": r[1], "has_upload": bool(r[2]), "totp_on": bool(r[3])} if r else None

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
