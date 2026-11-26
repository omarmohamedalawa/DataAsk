"""DataAsk web server (stdlib only). Security notes are in README.md, section Security."""
import json, os, sys, re, secrets, sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(__file__))
import auth, schema, multi
from engine import ask
STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
SECURE = os.environ.get("COOKIE_SECURE", "") == "1"
TRUST_PROXY = os.environ.get("TRUST_PROXY", "") == "1"
MAX_JSON = 16 * 1024

class H(BaseHTTPRequestHandler):
    timeout = 20  # slow-client protection
    server_version = "DataAsk"; sys_version = ""
    def log_message(self, *a): pass
    def _ip(self):
        if TRUST_PROXY and self.headers.get("X-Forwarded-For"): return self.headers["X-Forwarded-For"].split(",")[-1].strip()[:64]
        return self.client_address[0]
    def _tok(self):
        m = re.search(r"(?:^|;\s*)sid=([\w\-]{20,80})", self.headers.get("Cookie", "")); return m.group(1) if m else None
    def _send(self, code, obj, cookie=None, ctype="application/json; charset=utf-8", nonce=None):
        b = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer"); self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin"); self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'none'; connect-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; "
                         f"script-src 'nonce-{nonce}'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'" if nonce else "default-src 'none'; frame-ancestors 'none'")
        if cookie: self.send_header("Set-Cookie", cookie)
        self.end_headers(); self.wfile.write(b)
    def _page(self):
        nonce = secrets.token_urlsafe(16)
        html = open(os.path.join(STATIC, "index.html"), "rb").read().replace(b"<script>", f'<script nonce="{nonce}">'.encode())
        self._send(200, html, ctype="text/html; charset=utf-8", nonce=nonce)
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/healthz": return self._send(200, {"ok": True})
        if path == "/api/me": return self._send(200, {"user": auth.user_from_token(self._tok())})
        if path == "/api/tables":
            u = auth.user_from_token(self._tok())
            return self._send(200, schema.load_state(u)) if u else self._send(401, {"error": "login required"})
        if path in ("/", "/index.html"): return self._page()
        self._send(404, {"error": "not found"})
    def do_POST(self):
        auth.CURRENT_IP.v = self._ip()
        try: n = int(self.headers.get("Content-Length", "0"))
        except ValueError: return self._send(400, {"error": "bad request"})
        if n < 0: return self._send(400, {"error": "bad request"})
        path = self.path.split("?")[0]
        limit = auth.MAX_UPLOAD + 4096 if path in ("/api/upload", "/api/tables/upload") else MAX_JSON
        if n > limit: self.close_connection = True; return self._send(413, {"error": "too large"})
        origin = self.headers.get("Origin")  # CSRF defence in depth (cookie is also SameSite=Strict)
        if origin and origin.split("://", 1)[-1] != self.headers.get("Host", ""): return self._send(403, {"error": "cross-origin request blocked"})
        body = self.rfile.read(n)
        ck = lambda v, age: f"sid={v}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}" + ("; Secure" if SECURE else "")
        try:
            if path in ("/api/signup", "/api/login"):
                d = json.loads(body); em, pw = d.get("email", ""), d.get("password", "")
                if not isinstance(em, str) or not isinstance(pw, str): raise ValueError("bad request")
                if not auth.throttle("req:" + self._ip(), 20, 60): return self._send(429, {"error": "too many requests, wait a minute"})
                if path == "/api/signup":
                    if not auth.throttle("signup:" + self._ip(), 10, 3600): return self._send(429, {"error": "too many signups from this address"})
                    auth.signup(em, pw)
                tok = auth.login(em, pw)
                if not tok: return self._send(401, {"error": "wrong email or password"})
                return self._send(200, {"ok": True}, cookie=ck(tok, auth.SESSION_SECS))
            user = auth.user_from_token(self._tok())
            if not user: return self._send(401, {"error": "login required"})
            if path == "/api/logout": auth.logout(self._tok()); return self._send(200, {"ok": True}, cookie=ck("", 0))
            if path in ("/api/upload", "/api/tables/upload", "/api/tables/relationship", "/api/tables/sample", "/api/reset", "/api/tables/reset") and not auth.rate_ok(user["id"], 30, 60):
                return self._send(429, {"error": "too many requests, wait a minute"})
            if path == "/api/upload": return self._send(200, {"rows": auth.upload_csv(user, body)})
            if path == "/api/tables/upload":
                name = re.sub(r"[^\w\-]", "", self.headers.get("X-Table-Name", "table"))[:60]
                return self._send(200, {"table": schema.add_table(user, name, body)})
            if path == "/api/tables/relationship":
                d = json.loads(body)
                if d.get("manual"): return self._send(200, schema.add_manual_relationship(user, d["from_table"], d["from_col"], d["to_table"], d["to_col"]))
                return self._send(200, schema.set_relationship(user, d["id"], bool(d.get("confirmed"))))
            if path == "/api/tables/sample":  # fixed file list, no user-controlled path
                sd = os.path.join(os.path.dirname(__file__), "..", "samples")
                for n in ("customers", "products", "orders"): schema.add_table(user, n, open(os.path.join(sd, n + ".csv"), "rb").read())
                return self._send(200, {"ok": True})
            if path == "/api/tables/reset": schema.reset(user); return self._send(200, {"ok": True})
            if path == "/api/reset": auth.reset_demo(user); return self._send(200, {"ok": True})
            if path in ("/api/ask", "/api/tables/ask"):
                if not auth.rate_ok(user["id"]): return self._send(429, {"error": "too many questions, wait a minute"})
                q = json.loads(body)["question"]
                if not isinstance(q, str): raise ValueError("bad request")
                if path == "/api/ask": return self._send(200, ask(q[:500], db=auth.user_db(user)))
                return self._send(200, multi.ask_multi(user, q[:500]))
            self._send(404, {"error": "not found"})
        except PermissionError as e: self._send(429, {"error": str(e)})
        except ValueError as e: self._send(400, {"error": str(e) if not isinstance(e, json.JSONDecodeError) else "bad request"})
        except (KeyError, TypeError, AttributeError): self._send(400, {"error": "bad request"})
        except sqlite3.OperationalError: self._send(400, {"error": "the query could not be run (too slow or invalid)"})
        except Exception: self._send(500, {"error": "server error"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000)); host = os.environ.get("HOST", "127.0.0.1")
    if not os.path.exists(auth.DEMO):
        import seed_data; seed_data.build()
    print(f"DataAsk running at http://{host}:{port}"); ThreadingHTTPServer((host, port), H).serve_forever()
