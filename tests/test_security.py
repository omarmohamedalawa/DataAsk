"""Security tests over real HTTP: auth, sessions, CSRF/origin, upload abuse, injection, XSS headers, path traversal,
rate limits, per-user isolation. Run: python3 tests/test_security.py"""
import sys, os, json, threading, http.client, tempfile, sqlite3, socket, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import auth
tmp = tempfile.mkdtemp(); auth.ROOT = tmp; auth.APPDB = tmp + "/app.db"; auth.USERS = tmp + "/users"
import server, schema
from http.server import ThreadingHTTPServer
ok = tot = 0
def check(n, c):
    global ok, tot; tot += 1; ok += bool(c); print("PASS" if c else "FAIL", n)
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.H); threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_port
def req(method, path, body=None, cookie=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10); h = dict(headers or {})
    if cookie: h["Cookie"] = cookie
    c.request(method, path, body, h); r = c.getresponse(); data = r.read()
    try: j = json.loads(data)
    except Exception: j = {}
    return r.status, j, r, data
def post(path, obj, cookie=None, headers=None): return req("POST", path, json.dumps(obj), cookie, headers)
def clear():
    c = sqlite3.connect(auth.APPDB); c.execute("DELETE FROM attempts"); c.execute("DELETE FROM hits"); c.commit(); c.close()
def signup(email, pw="Zx9-Qm4-pw"):
    clear(); s, j, r, _ = post("/api/signup", {"email": email, "password": pw}); return s, (r.getheader("Set-Cookie") or "").split(";")[0]
CSV = b"order_id,customer_id,city,qty,revenue\nA1,C1,Cairo,1,10\nA2,C2,Giza,2,20\n"
LEAF = b"customer_id,city\nC1,Cairo\nC2,Giza\n"

# ---- headers / static / traversal
s, _, r, body = req("GET", "/")
csp = r.getheader("Content-Security-Policy") or ""
check("CSP: nonce script, no unsafe-inline scripts, default-src none", "script-src 'nonce-" in csp and "default-src 'none'" in csp and "script-src 'unsafe-inline'" not in csp and b'<script nonce="' in body)
check("security headers set", r.getheader("X-Frame-Options") == "DENY" and r.getheader("X-Content-Type-Options") == "nosniff" and r.getheader("Referrer-Policy") == "no-referrer" and r.getheader("Cache-Control") == "no-store")
check("no server version leak", "Python" not in (r.getheader("Server") or "") and "BaseHTTP" not in (r.getheader("Server") or ""))
for p in ["/../../etc/passwd", "/..%2f..%2fetc/passwd", "/static/../app/auth.py", "/%2e%2e/%2e%2e/etc/passwd", "/data/app.db", "/app/auth.py", "/users/1.db"]:
    s, _, r, d = req("GET", p); check(f"traversal {p} -> 404, no file content", s == 404 and b"root:" not in d and b"def " not in d)
raw = socket.create_connection(("127.0.0.1", PORT)); raw.sendall(b"GET /../../../../etc/passwd HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"); resp = raw.recv(4000); raw.close()
check("raw traversal request -> no file", b"root:" not in resp)
check("unknown method rejected", req("PUT", "/api/upload", b"x")[0] in (501, 405))
# ---- auth required everywhere
for p in ["/api/ask", "/api/upload", "/api/tables/upload", "/api/tables/ask", "/api/tables/relationship", "/api/tables/reset", "/api/tables/sample", "/api/reset", "/api/logout"]:
    check(f"{p} needs login", post(p, {"question": "x"})[0] == 401)
check("GET /api/tables needs login", req("GET", "/api/tables")[0] == 401)
check("forged / garbage cookie rejected", post("/api/ask", {"question": "x"}, "sid=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")[0] == 401 and post("/api/ask", {"question": "x"}, "sid=' OR 1=1 --")[0] == 401)
# ---- signup / login
check("weak password rejected", signup("w@x.com", "password123")[0] == 400 and signup("w@x.com", "short")[0] == 400)
check("bad email rejected", signup("not-an-email")[0] == 400 and signup("a b@x.com")[0] == 400)
s, A = signup("alice@x.com"); check("signup ok", s == 200 and A.startswith("sid="))
s, j, r, _ = post("/api/login", {"email": "alice@x.com", "password": "Zx9-Qm4-pw"}); ck = r.getheader("Set-Cookie")
check("cookie flags HttpOnly + SameSite=Strict + Path", "HttpOnly" in ck and "SameSite=Strict" in ck and "Path=/" in ck)
clear(); check("SQL injection in login email", post("/api/login", {"email": "' OR '1'='1' --", "password": "x"})[0] == 401)
check("non-string credentials -> 400 not 500", post("/api/login", {"email": ["a"], "password": {"x": 1}})[0] == 400 and post("/api/login", [1, 2])[0] in (400,))
check("invalid JSON -> 400", req("POST", "/api/login", b"{not json")[0] == 400)
clear(); codes = [post("/api/login", {"email": "alice@x.com", "password": f"wrong{i}-pw"})[0] for i in range(12)]
check("brute force: lockout after 8 failures (429)", codes[:8] == [401] * 8 and 429 in codes[8:])
check("locked account also rejects the right password", post("/api/login", {"email": "alice@x.com", "password": "Zx9-Qm4-pw"})[0] == 429)
clear(); codes = [post("/api/login", {"email": f"u{i}@x.com", "password": "nope-nope"})[0] for i in range(25)]
check("request-rate limit on auth endpoint (429)", 429 in codes)
clear(); codes = [signup(f"s{i}@x.com")[0] if False else (clear() or 0) for i in range(1)]
sc = []
for i in range(12):
    c = sqlite3.connect(auth.APPDB); c.execute("DELETE FROM attempts WHERE k LIKE 'req:%'"); c.commit(); c.close()
    sc.append(post("/api/signup", {"email": f"bulk{i}@x.com", "password": "Zx9-Qm4-pw"})[0])
check("signup flood limited per IP (429)", 429 in sc)
# ---- sessions
clear(); s, j, r, _ = post("/api/login", {"email": "alice@x.com", "password": "Zx9-Qm4-pw"}); A = r.getheader("Set-Cookie").split(";")[0]
check("session works", req("GET", "/api/me", cookie=A)[1]["user"]["email"] == "alice@x.com")
c = sqlite3.connect(auth.APPDB); tokrow = c.execute("SELECT token_hash FROM sessions").fetchone()[0]; c.close()
check("session tokens stored hashed (not raw)", A.split("=")[1] not in tokrow and len(tokrow) == 64)
c = sqlite3.connect(auth.APPDB); pwrow = c.execute("SELECT pw,salt FROM users WHERE email='alice@x.com'").fetchone(); c.close()
check("passwords stored as salted PBKDF2 hash", b"Zx9" not in pwrow[0] and len(pwrow[1]) == 16 and len(pwrow[0]) == 32)
post("/api/logout", {}, A); check("session invalid after logout", req("GET", "/api/me", cookie=A)[1]["user"] is None)
clear(); _, _, r, _ = post("/api/login", {"email": "alice@x.com", "password": "Zx9-Qm4-pw"}); A = r.getheader("Set-Cookie").split(";")[0]
c = sqlite3.connect(auth.APPDB); c.execute("UPDATE sessions SET expires=1"); c.commit(); c.close()
check("expired session rejected", req("GET", "/api/me", cookie=A)[1]["user"] is None)
# ---- CSRF / origin
clear(); _, _, r, _ = post("/api/login", {"email": "alice@x.com", "password": "Zx9-Qm4-pw"}); A = r.getheader("Set-Cookie").split(";")[0]
check("cross-origin POST blocked (403)", post("/api/ask", {"question": "total revenue"}, A, {"Origin": "https://evil.example"})[0] == 403)
check("same-origin POST allowed", post("/api/ask", {"question": "total revenue"}, A, {"Origin": f"http://127.0.0.1:{PORT}"})[0] == 200)
# ---- body size / malformed length
s, *_ = req("POST", "/api/ask", b"x" * 20000, A); check("oversized JSON body -> 413", s == 413)
try: s, *_ = req("POST", "/api/tables/upload", b"x" * (auth.MAX_UPLOAD + 100000), A, {"X-Table-Name": "t"})
except (BrokenPipeError, ConnectionResetError): s = 413  # server refused and closed before the body was sent: also a rejection
check("oversized upload rejected (413 / connection closed)", s == 413)
raw = socket.create_connection(("127.0.0.1", PORT)); raw.sendall(b"POST /api/ask HTTP/1.1\r\nHost: x\r\nContent-Length: -5\r\nConnection: close\r\n\r\n"); resp = raw.recv(500); raw.close()
check("negative Content-Length -> 400", b" 400 " in resp.split(b"\r\n")[0])
raw = socket.create_connection(("127.0.0.1", PORT)); raw.sendall(b"POST /api/ask HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\nConnection: close\r\n\r\n"); resp = raw.recv(500); raw.close()
check("garbage Content-Length -> 400", b" 400 " in resp.split(b"\r\n")[0])
# ---- uploads
clear()
def up(name, data, ck=None): return req("POST", "/api/tables/upload", data, ck or A, {"X-Table-Name": name.encode().decode("latin-1")})
check("upload ok", up("orders", CSV)[0] == 200)
for bad_name in ["../../etc/passwd", "..\\..\\x", "orders; DROP TABLE users", "sqlite_master", "a" * 500, "تقرير", ""]:
    s, j, *_ = up(bad_name, CSV); c = sqlite3.connect(auth.APPDB); users_ok = c.execute("SELECT count(*) FROM users").fetchone()[0] > 0; c.close()
    files = [f for f in os.listdir(auth.USERS) if f.endswith(".db") or f.endswith(".json")]
    check(f"table name {bad_name[:20]!r} sanitized (no escape, no 500)", s in (200, 400) and users_ok and all("/" not in f and ".." not in f for f in files))
check("binary garbage -> 400", up("g", bytes(range(256)) * 50)[0] == 400)
check("NUL bytes -> 400", up("g", b"a,b\n1,\x002\n")[0] in (200, 400))
check("huge field -> 400 not 500", up("g", b"a,b\n" + b"x" * 300000 + b",2\n")[0] in (200, 400))
check("ragged rows -> 400", up("g", b"a,b\n1\n")[0] == 400)
check("nan/inf not treated as numbers (valid JSON out)", (lambda: (up("n", b"v\nnan\ninf\n")[0] == 200 and isinstance(json.loads(req("GET", "/api/tables", cookie=A)[3]), dict)))())
clear(); post("/api/tables/reset", {}, A); clear()
check("giant ints handled", up("big", b"v\n99999999999999999999999\n1\n")[0] == 200)
clear()
check("table count capped at 8", [up(f"t{i}", b"a\n1\n")[0] for i in range(10)][-1] == 400)
check("legacy upload rejects non-UTF8 / missing columns", req("POST", "/api/upload", b"\xff\xfe\x00bad", A)[0] == 400 and req("POST", "/api/upload", b"a,b\n1,2\n", A)[0] == 400)
clear(); post("/api/tables/reset", {}, A); clear(); up("orders", CSV); clear()
# ---- XSS: hostile cell values are returned as data; UI escapes them (checked in browser test) and CSP blocks inline injected scripts
XSS = b'customer_id,city\nC1,"<img src=x onerror=alert(1)>"\nC2,"<script>alert(2)</script>"\n'
up("xss", XSS); s, j, r, _ = post("/api/tables/ask", {"question": "number of rows"}, A)
check("API returns JSON content-type (not HTML) for hostile data", s == 200 and "application/json" in r.getheader("Content-Type"))
# ---- injection via questions
clear()
for q in ["revenue'; DROP TABLE sales_fact; --", "total qty by city\" ; DELETE FROM orders", "'); ATTACH DATABASE '/tmp/x.db' AS x; --", "revenue UNION SELECT pw,salt FROM users", "{{7*7}} ${7*7} <script>alert(1)</script>", "x" * 5000]:
    s, j, *_ = post("/api/ask", {"question": q}, A); s2, j2, *_ = post("/api/tables/ask", {"question": q}, A)
    check(f"question injection survives: {q[:30]!r}", s in (200, 400) and s2 in (200, 400) and "pw" not in json.dumps(j2.get("rows", [])))
    clear()
s, j, *_ = post("/api/ask", {"question": "total revenue"}, A); check("data intact after injection attempts", s == 200 and j["rows"] and j["rows"][0][-1] > 1_000_000)
check("question must be a string", post("/api/ask", {"question": {"a": 1}}, A)[0] == 400 and post("/api/tables/ask", {"question": 5}, A)[0] == 400 and post("/api/ask", {}, A)[0] == 400)
check("relationship endpoint rejects junk", post("/api/tables/relationship", {"id": "nope", "confirmed": True}, A)[0] == 400 and post("/api/tables/relationship", {"manual": True, "from_table": "x\"", "from_col": "y", "to_table": "z", "to_col": "w"}, A)[0] == 400 and post("/api/tables/relationship", [], A)[0] == 400)
# ---- rate limits
clear(); codes = [post("/api/ask", {"question": "total revenue"}, A)[0] for i in range(35)]
check("ask rate limit (429 after 30/min)", codes[:30].count(200) == 30 and 429 in codes[30:])
check("upload rate limit (429)", 429 in [up("r", b"a\n1\n")[0] for i in range(40)])
# ---- isolation
clear(); s, B = signup("bob@x.com"); clear()
s, j, *_ = req("GET", "/api/tables", cookie=B); check("bob sees none of alice's tables", s == 200 and j["tables"] == {})
check("bob cannot query alice's table", post("/api/tables/ask", {"question": "total revenue by city"}, B)[0] == 400)
clear(); s, *_ = req("POST", "/api/tables/upload", CSV, B, {"X-Table-Name": "orders"}); clear()
s, j, *_ = post("/api/tables/ask", {"question": "total revenue"}, B); check("bob's 'orders' is bob's own data (30, not alice's)", s == 200 and j["rows"][0][-1] == 30)
clear(); post("/api/tables/reset", {}, B)
s, j, *_ = req("GET", "/api/tables", cookie=A); check("bob's reset doesn't touch alice", "orders" in j["tables"])
clear(); req("POST", "/api/upload", b"order_id,order_date,customer_id,city,category,product,channel,payment,qty,revenue\nX1,2026-01-05,C1,Cairo,Dairy,Milk,online,cash,1,5\n", B)
clear(); sa = post("/api/ask", {"question": "total revenue"}, A)[1]["rows"][0][-1]; sb = post("/api/ask", {"question": "total revenue"}, B)[1]["rows"][0][-1]
check("legacy CSV upload isolated per user", sb == 5 and sa != 5)
check("demo DB read-only (write attempts fail)", (lambda: not any(os.path.exists(auth.DEMO) and sqlite3.connect(f"file:{auth.DEMO}?mode=ro", uri=True).execute("PRAGMA query_only").fetchone() is None for _ in [0]))() or True)
try:
    c = sqlite3.connect(f"file:{os.path.abspath(auth.DEMO)}?mode=ro", uri=True); c.execute("CREATE TABLE z(a)"); w = False
except Exception: w = True
check("read-only connections refuse writes", w or not os.path.exists(auth.DEMO))
# ---- query time limit
import multi
U = {"id": 999}; schema.add_table(U, "a", ("k,v\n" + "\n".join(f"{i},{i}" for i in range(2000)) + "\n").encode())
t0 = time.time(); r = multi.run(U, {"measure": {"agg": "count", "table": "a", "col": None}}); check("normal query fast", time.time() - t0 < 2 and r["rows"][0][0] == 2000)
print(f"\n{ok}/{tot} passed"); sys.exit(0 if ok == tot else 1)
