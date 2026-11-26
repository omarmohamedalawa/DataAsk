import json, os, sys, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(__file__))
import auth
from engine import ask
STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
SECURE = os.environ.get("COOKIE_SECURE", "") == "1"
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _tok(self):
        m = re.search(r"(?:^|;\s*)sid=([\w\-]+)", self.headers.get("Cookie", "")); return m.group(1) if m else None
    def _send(self, code, obj, cookie=None, ctype="application/json; charset=utf-8"):
        b = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'")
        if cookie: self.send_header("Set-Cookie", cookie)
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/healthz": return self._send(200, {"ok": True})
        if self.path == "/api/me": 
            u = auth.user_from_token(self._tok()); return self._send(200, {"user": u})
        self._send(200, open(os.path.join(STATIC, "index.html"), "rb").read(), ctype="text/html; charset=utf-8")
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if n > auth.MAX_UPLOAD + 4096: return self._send(413, {"error": "too large"})
        body = self.rfile.read(n); path = self.path
        ck = lambda v, age: f"sid={v}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}" + ("; Secure" if SECURE else "")
        try:
            if path in ("/api/signup", "/api/login"):
                d = json.loads(body)
                if path == "/api/signup": auth.signup(d.get("email", ""), d.get("password", ""))
                tok = auth.login(d.get("email", ""), d.get("password", ""))
                if not tok: return self._send(401, {"error": "wrong email or password"})
                return self._send(200, {"ok": True}, cookie=ck(tok, auth.SESSION_SECS))
            user = auth.user_from_token(self._tok())
            if not user: return self._send(401, {"error": "login required"})
            if path == "/api/logout": auth.logout(self._tok()); return self._send(200, {"ok": True}, cookie=ck("", 0))
            if path == "/api/upload": return self._send(200, {"rows": auth.upload_csv(user, body)})
            if path == "/api/reset": auth.reset_demo(user); return self._send(200, {"ok": True})
            if path == "/api/ask":
                if not auth.rate_ok(user["id"]): return self._send(429, {"error": "too many questions, wait a minute"})
                q = json.loads(body)["question"]
                return self._send(200, ask(q[:500], db=auth.user_db(user)))
            self._send(404, {"error": "not found"})
        except ValueError as e: self._send(400, {"error": str(e)})
        except Exception: self._send(500, {"error": "server error"})
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    if not os.path.exists(auth.DEMO):
        import seed_data; seed_data.build()
    print(f"DataAsk SaaS on :{port}"); ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
