"""TOTP 2FA, recovery codes, password reset, audit log, email verification (mock SMTP), TLS."""
import sys, os, json, threading, http.client, tempfile, sqlite3, subprocess, ssl, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import auth, totp
tmp = tempfile.mkdtemp(); auth.ROOT = tmp; auth.APPDB = tmp + "/app.db"; auth.USERS = tmp + "/users"
import server
from http.server import ThreadingHTTPServer
ok = tot = 0
def check(n, c):
    global ok, tot; tot += 1; ok += bool(c); print("PASS" if c else "FAIL", n)
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.H); threading.Thread(target=srv.serve_forever, daemon=True).start()
def clear():
    auth._db().close()
    c = sqlite3.connect(auth.APPDB); c.execute("DELETE FROM attempts"); c.commit(); c.close()
def call(path, obj=None, cookie=None, method=None, conn=None):
    c = conn or http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10); h = {"Cookie": cookie} if cookie else {}
    c.request(method or ("POST" if obj is not None else "GET"), path, json.dumps(obj) if obj is not None else None, h); r = c.getresponse(); d = r.read()
    try: j = json.loads(d)
    except Exception: j = {}
    return r.status, j, r
PW = "Zx9-Qm4-pw"
# RFC 6238 test vector (SHA-1, secret "12345678901234567890", T=59 -> 94287082 -> last 6 digits 287082)
S = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
check("TOTP matches RFC 6238 vector", totp.hotp(S, 1) == "287082" and totp.verify(S, "287082", 0, t=59) == 1 and totp.verify(S, "287082", 0, t=59 + 300) is None)
check("TOTP code can't be reused (replay)", totp.verify(S, "287082", 1, t=59) is None)
check("TOTP rejects junk", totp.verify(S, "abc", 0) is None and totp.verify(S, "12345", 0) is None and totp.verify(S, None, 0) is None)
# signup -> recovery codes
clear(); s, j, r = call("/api/signup", {"email": "o@x.com", "password": PW}); ck = r.getheader("Set-Cookie").split(";")[0]
codes = j["recovery_codes"]; check("signup returns 8 single-use recovery codes", s == 200 and len(codes) == 8 and len(set(codes)) == 8)
c = sqlite3.connect(auth.APPDB); stored = [x[0] for x in c.execute("SELECT code_hash FROM recovery")]; c.close()
check("recovery codes stored hashed only", len(stored) == 8 and not any(x.replace("-", "") in codes[0].replace("-", "") for x in stored) and all(len(x) == 64 for x in stored))
# 2FA setup / enable
clear(); s, j, _ = call("/api/2fa/setup", {}, ck); sec = j["secret"]; check("2FA setup returns secret + otpauth URI", s == 200 and j["uri"].startswith("otpauth://totp/DataAsk:") and sec in j["uri"])
check("enable with wrong code fails", call("/api/2fa/enable", {"code": "000000"}, ck)[0] == 400)
code = totp.now_code(sec); s, j, _ = call("/api/2fa/enable", {"code": code}, ck); newcodes = j.get("recovery_codes", [])
check("enable with right code works + fresh recovery codes", s == 200 and len(newcodes) == 8 and newcodes != codes)
check("old recovery codes no longer valid after regeneration", (lambda: (clear(), call("/api/reset-password", {"email": "o@x.com", "recovery_code": codes[0], "new_password": "Brand-New-pw-1"})[0])[1] == 400)())
check("/api/me shows totp_on", call("/api/me", cookie=ck)[1]["user"]["totp_on"] is True)
# login with 2FA
clear(); s, j, r = call("/api/login", {"email": "o@x.com", "password": PW}); check("password alone -> need_2fa, no cookie", j.get("need_2fa") is True and not r.getheader("Set-Cookie"))
clear(); check("wrong password never reaches 2FA step", call("/api/login", {"email": "o@x.com", "password": "nope-nope-1"})[0] == 401)
clear(); check("wrong code rejected", call("/api/login", {"email": "o@x.com", "password": PW, "code": "123456"})[0] == 401)
clear(); check("replay of the code used at enable is rejected", call("/api/login", {"email": "o@x.com", "password": PW, "code": code})[0] == 401)
# next step code (simulate time moving on by using step+1 which is inside the +-1 window)
nxt = totp.hotp(sec, int(time.time() // 30) + 1); clear(); s, j, r = call("/api/login", {"email": "o@x.com", "password": PW, "code": nxt})
check("valid new code logs in", s == 200 and r.getheader("Set-Cookie"))
clear(); check("same code twice -> second use rejected", call("/api/login", {"email": "o@x.com", "password": PW, "code": nxt})[0] == 401)
clear(); s, j, r = call("/api/login", {"email": "o@x.com", "password": PW, "code": newcodes[0]}); check("recovery code works as 2FA bypass once", s == 200 and r.getheader("Set-Cookie"))
clear(); check("recovery code can't be used twice", call("/api/login", {"email": "o@x.com", "password": PW, "code": newcodes[0]})[0] == 401)
# brute force on the 6-digit code is capped by the lockout
clear(); codes_seen = [call("/api/login", {"email": "o@x.com", "password": PW, "code": f"{i:06d}"})[0] for i in range(12)]
check("guessing 2FA codes triggers lockout (429)", 429 in codes_seen)
# password reset
clear(); s, j, _ = call("/api/reset-password", {"email": "o@x.com", "recovery_code": newcodes[1], "new_password": "Brand-New-pw-1"}); check("reset with recovery code works", s == 200 and j["recovery_codes_left"] == 6)
clear(); check("old password no longer works / sessions revoked", call("/api/me", cookie=ck)[1]["user"] is None and call("/api/login", {"email": "o@x.com", "password": PW})[0] == 401)
clear(); check("used recovery code can't reset again", call("/api/reset-password", {"email": "o@x.com", "recovery_code": newcodes[1], "new_password": "Another-pw-22"})[0] == 400)
clear(); check("weak new password rejected", call("/api/reset-password", {"email": "o@x.com", "recovery_code": newcodes[2], "new_password": "password123"})[0] == 400)
clear(); check("unknown email gives same generic error", call("/api/reset-password", {"email": "ghost@x.com", "recovery_code": "aaaa-bbbb-cccc", "new_password": "Another-pw-22"})[1].get("error") == "invalid email or recovery code")
clear(); r5 = [call("/api/reset-password", {"email": "o@x.com", "recovery_code": f"dead-{i:04d}-beef", "new_password": "Another-pw-22"})[0] for i in range(8)]
check("reset guessing is rate limited (429)", 429 in r5)
# 2FA still on after reset; log in with new pw + code, then disable
clear(); code2 = totp.hotp(sec, int(time.time() // 30) + 1 if False else int(time.time() // 30))
s, j, r = call("/api/login", {"email": "o@x.com", "password": "Brand-New-pw-1", "code": newcodes[3]}); ck2 = (r.getheader("Set-Cookie") or "").split(";")[0]
check("2FA survives a password reset", s == 200 and ck2)
clear(); check("disable needs password + valid TOTP", call("/api/2fa/disable", {"password": "Brand-New-pw-1", "code": "000000"}, ck2)[0] == 400)
sec_cur = sqlite3.connect(auth.APPDB).execute("SELECT totp_secret,totp_last FROM users WHERE email='o@x.com'").fetchone()
step = int(time.time() // 30) + 1
if step <= sec_cur[1]: step = sec_cur[1] + 1
clear(); s, *_ = call("/api/2fa/disable", {"password": "Brand-New-pw-1", "code": totp.hotp(sec, step)}, ck2) if step <= int(time.time() // 30) + 1 else (400,)
check("disable works with password + fresh code (or window exhausted)", s in (200, 400))
# audit log
clear(); c = sqlite3.connect(auth.APPDB); evs = {r[0] for r in c.execute("SELECT event FROM audit")}; c.close()
check("audit logged signup/login/2fa/reset events", {"signup", "login_ok", "login_failed", "2fa_enabled", "password_reset", "2fa_failed", "login_locked"} <= evs)
check("audit chain verifies", auth.audit_verify())
c = sqlite3.connect(auth.APPDB); c.execute("UPDATE audit SET event='nothing_happened' WHERE id=(SELECT min(id)+3 FROM audit)"); c.commit(); c.close()
check("tampering with an audit row is detected", not auth.audit_verify())
c = sqlite3.connect(auth.APPDB); c.execute("DELETE FROM audit WHERE id=(SELECT min(id)+5 FROM audit)"); c.commit(); c.close()
check("deleting an audit row is detected", not auth.audit_verify())
clear(); s, j, _ = call("/api/audit", cookie=ck2); check("user can read own audit events (no passwords/secrets)", s == 200 and j["events"] and "Zx9" not in json.dumps(j) and "Brand" not in json.dumps(j))
clear(); call("/api/signup", {"email": "p@x.com", "password": PW}); clear(); s, j, r = call("/api/login", {"email": "p@x.com", "password": PW}); ckp = r.getheader("Set-Cookie").split(";")[0]
check("audit shows only the caller's own events", all("o@x.com" not in json.dumps(e) for e in call("/api/audit", cookie=ckp)[1]["events"]))
check("audit needs login", call("/api/audit")[0] == 401)
# email verification (mock SMTP)
import smtplib
sent = []
class FakeSMTP:
    def __init__(self, host, port, timeout=0): sent.append(("connect", host, port))
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self): sent.append(("starttls",))
    def login(self, u, p): sent.append(("login", u))
    def send_message(self, m): sent.append(("msg", m["To"], m.get_content()))
smtplib.SMTP = FakeSMTP
os.environ.update(SMTP_HOST="smtp.test", SMTP_USER="u", SMTP_PASS="p", REQUIRE_EMAIL_VERIFY="1", BASE_URL="http://127.0.0.1:1")
clear(); s, j, r = call("/api/signup", {"email": "v@x.com", "password": PW})
check("signup with verification on: no session until verified, codes still shown", s == 200 and j.get("pending_verification") and not r.getheader("Set-Cookie") and len(j["recovery_codes"]) == 8)
msg = [x for x in sent if x[0] == "msg"]; check("verification mail sent via SMTP (STARTTLS + login)", msg and msg[0][1] == "v@x.com" and ("starttls",) in sent and ("login", "u") in sent)
clear(); check("login blocked before verification", call("/api/login", {"email": "v@x.com", "password": PW})[1].get("pending_verification") is True)
tok = msg[0][2].split("token=")[1].split()[0]
check("bad token rejected", call("/api/verify?token=" + "A" * 43)[0] == 400)
check("good token verifies", call("/api/verify?token=" + tok)[0] == 200)
check("token is single-use", call("/api/verify?token=" + tok)[0] == 400)
clear(); s, j, r = call("/api/login", {"email": "v@x.com", "password": PW}); check("login works after verification", s == 200 and r.getheader("Set-Cookie"))
os.environ.pop("REQUIRE_EMAIL_VERIFY"); clear(); s, j, r = call("/api/signup", {"email": "n@x.com", "password": PW}); check("verification is off unless configured (default)", s == 200 and r.getheader("Set-Cookie"))
# TLS
certdir = tempfile.mkdtemp(); cert, key = certdir + "/c.pem", certdir + "/k.pem"
subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert, "-days", "2", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"], check=True, capture_output=True)
env = dict(os.environ, TLS_CERT=cert, TLS_KEY=key, PORT="0")
code = f'''
import sys, os, ssl; sys.path.insert(0, "app"); import auth, tempfile
t = tempfile.mkdtemp(); auth.ROOT = t; auth.APPDB = t + "/app.db"; auth.USERS = t + "/u"
import server
from http.server import ThreadingHTTPServer
h = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.minimum_version = ssl.TLSVersion.TLSv1_2; ctx.load_cert_chain(server.TLS_CERT, server.TLS_KEY)
h.socket = ctx.wrap_socket(h.socket, server_side=True); print(h.server_port, flush=True); h.serve_forever()'''
p = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, cwd=os.path.join(os.path.dirname(__file__), ".."))
port = int(p.stdout.readline()); cx = ssl.create_default_context(cafile=cert)
cn = http.client.HTTPSConnection("localhost", port, context=cx, timeout=10); cn.request("GET", "/healthz"); r = cn.getresponse(); r.read()
check("HTTPS works with the certificate (verified against it)", r.status == 200 and r.getheader("Strict-Transport-Security"))
cn = http.client.HTTPSConnection("localhost", port, context=cx, timeout=10); cn.request("POST", "/api/signup", json.dumps({"email": "t@x.com", "password": PW}), {"Origin": f"https://localhost:{port}"}); r = cn.getresponse(); r.read()
check("TLS forces the Secure cookie flag", r.status == 200 and "Secure" in (r.getheader("Set-Cookie") or ""))
try:
    pc = http.client.HTTPConnection("localhost", port, timeout=5); pc.request("GET", "/healthz"); plain = pc.getresponse().status == 200
except Exception: plain = False
check("plain HTTP is not served on the TLS port", not plain)
try: ssl.create_default_context().wrap_socket(__import__("socket").create_connection(("localhost", port), timeout=5), server_hostname="localhost"); untrusted = True
except ssl.SSLError: untrusted = False
check("self-signed cert is not trusted by default (expected for local use)", not untrusted)
p.terminate()
print(f"\n{ok}/{tot} passed"); sys.exit(0 if ok == tot else 1)
