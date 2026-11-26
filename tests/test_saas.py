"""Backend tests: auth, sessions, rate limit, per-user upload isolation, SQL guard over HTTP, LLM planner (mock provider + invalid output fallback)."""
import sys, os, json, threading, http.client, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import auth
tmp = tempfile.mkdtemp(); auth.ROOT = tmp; auth.APPDB = tmp + "/app.db"; auth.USERS = tmp + "/users"
import server, llm
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from engine import ask
ok = tot = 0
def check(name, cond):
    global ok, tot; tot += 1; ok += bool(cond); print("PASS" if cond else "FAIL", name)
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.H); threading.Thread(target=srv.serve_forever, daemon=True).start()
def call(method, path, body=None, cookie=None, raw=False):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_port); h = {"Cookie": cookie} if cookie else {}
    c.request(method, path, body if raw else (json.dumps(body) if body is not None else None), h); r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}"), r.getheader("Set-Cookie")
st, _, _ = call("POST", "/api/ask", {"question": "revenue"}); check("ask without login -> 401", st == 401)
st, j, _ = call("POST", "/api/signup", {"email": "a@x.com", "password": "short"}); check("weak password rejected", st == 400)
st, j, ck = call("POST", "/api/signup", {"email": "a@x.com", "password": "password123"}); check("signup ok + cookie", st == 200 and ck and "HttpOnly" in ck)
cookie = ck.split(";")[0]
st, j, _ = call("POST", "/api/signup", {"email": "a@x.com", "password": "password123"}); check("duplicate email rejected", st == 400)
st, j, _ = call("POST", "/api/login", {"email": "a@x.com", "password": "wrongpass1"}); check("wrong password -> 401", st == 401)
st, j, _ = call("POST", "/api/ask", {"question": "Revenue by city in 2026"}, cookie); check("ask with login (demo data)", st == 200 and j["mode"] == "sql" and j["rows"][0][0] == "Cairo")
csv = "order_id,order_date,customer_id,city,category,product,channel,payment,qty,revenue,profit\n1,2026-01-05,c1,Cairo,Dairy,Milk,online,card,2,100,20\n2,2026-02-05,c2,Giza,Dairy,Milk,store,cash,1,60,10\n"
st, j, _ = call("POST", "/api/upload", csv, cookie, raw=True); check("csv upload", st == 200 and j["rows"] == 2)
st, j, _ = call("POST", "/api/ask", {"question": "Total revenue"}, cookie); check("ask uses uploaded data (160)", j["rows"][0][0] == 160)
st, j, ck2 = call("POST", "/api/signup", {"email": "b@x.com", "password": "password123"}); cookie2 = ck2.split(";")[0]
st, j, _ = call("POST", "/api/ask", {"question": "Total revenue"}, cookie2); check("other user still on demo data (isolated)", j["rows"][0][0] > 1_000_000)
st, j, _ = call("POST", "/api/upload", "a,b\n1,2\n", cookie2, raw=True); check("bad csv rejected", st == 400 and "missing columns" in j["error"])
call("POST", "/api/logout", {}, cookie); st, _, _ = call("POST", "/api/ask", {"question": "x"}, cookie); check("session dead after logout", st == 401)
for i in range(31): st, _, _ = call("POST", "/api/ask", {"question": "orders"}, cookie2)
check("rate limit -> 429", st == 429)
# LLM planner against a mock OpenAI-compatible server
REPLY = {"v": ""}
class M(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"])); b = json.dumps({"choices": [{"message": {"content": REPLY["v"]}}]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(b)
m = ThreadingHTTPServer(("127.0.0.1", 0), M); threading.Thread(target=m.serve_forever, daemon=True).start()
os.environ["LLM_API_KEY"] = "test"; os.environ["LLM_BASE_URL"] = f"http://127.0.0.1:{m.server_port}/v1"
REPLY["v"] = json.dumps({"metric": "profit", "dim": "city", "top": 3, "order": "desc", "filters": {"year": "2026"}})
r = ask("which cities earn us the most money this year"); check("LLM spec used (planner=llm)", r["planner"] == "llm" and len(r["rows"]) == 3 and "profit" in r["sql"].lower())
REPLY["v"] = json.dumps({"metric": "revenue", "dim": "city; DROP TABLE x", "filters": {}})
r = ask("revenue by city"); check("invalid LLM dim -> rules fallback", r["planner"] == "rules")
REPLY["v"] = json.dumps({"metric": "revenue", "dim": None, "filters": {"city": "x' OR '1'='1"}})
r = ask("total revenue"); check("unsafe filter value -> fallback", r["planner"] == "rules")
REPLY["v"] = "not json"; r = ask("total revenue"); check("garbage LLM output -> fallback", r["planner"] == "rules")
del os.environ["LLM_API_KEY"]; r = ask("total revenue"); check("no key -> rules", r["planner"] == "rules")
print(f"\n{ok}/{tot} passed")
