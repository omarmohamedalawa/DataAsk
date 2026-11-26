"""Multi-table tests: profiling, relationship inference, confirm flow, joined queries, validation/injection, isolation, HTTP."""
import sys, os, json, tempfile, threading, http.client
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import auth
tmp = tempfile.mkdtemp(); auth.ROOT = tmp; auth.APPDB = tmp + "/app.db"; auth.USERS = tmp + "/users"
import schema, multi, server
from http.server import ThreadingHTTPServer
ok = tot = 0
def check(n, c):
    global ok, tot; tot += 1; ok += bool(c); print("PASS" if c else "FAIL", n)
def raises(f):
    try: f(); return False
    except ValueError: return True
CUST = "customer_id,name,city,segment\nC1,Ahmed,Cairo,retail\nC2,Mona,Giza,retail\nC3,Omar,Alexandria,wholesale\nC4,Sara,Cairo,retail\nC5,Hany,Tanta,wholesale\n"
PROD = "product_id,product_name,category\n1,Milk,Dairy\n2,Cheese,Dairy\n3,Panadol,OTC Pharmacy\n4,Tea,Beverages\n5,Rice,Groceries\n"
rows = ["order_id,customer_id,product_id,order_date,qty,revenue"]
import itertools
for i in range(1, 41):
    rows.append(f"O{1000+i},C{i%5+1},{i%5+1},2026-0{i%3+1}-1{i%9},{i%4+1},{(i%7+1)*10}.5")
ORD = "\n".join(rows) + "\n"
U = {"id": 101}; V = {"id": 102}
p = schema.add_table(U, "Orders", ORD.encode()); schema.add_table(U, "customers", CUST.encode()); schema.add_table(U, "products", PROD.encode())
cols = {c["name"]: c for c in p["columns"]}
check("profile: types", cols["qty"]["type"] == "int" and cols["revenue"]["type"] == "real" and cols["order_date"]["type"] == "date" and cols["customer_id"]["type"] == "text")
check("profile: key + roles", cols["order_id"]["unique"] and cols["order_id"]["role"] == "key" and cols["revenue"]["role"] == "measure" and cols["order_date"]["role"] == "date")
st = schema.load_state(U); ids = {r["id"] for r in st["relationships"]}
check("infer orders.customer_id->customers", "orders.customer_id->customers.customer_id" in ids)
check("infer orders.product_id->products", "orders.product_id->products.product_id" in ids)
check("no relationship from measure columns", not any(r["from_col"] in ("qty", "revenue") for r in st["relationships"]))
check("proposals start unconfirmed", all(r["status"] == "proposed" for r in st["relationships"]))
check("join blocked before confirm", raises(lambda: multi.run(U, {"measure": {"agg": "sum", "table": "orders", "col": "revenue"}, "group_by": [{"table": "customers", "col": "city"}]})))
for r in st["relationships"]: schema.set_relationship(U, r["id"], True)
spec = {"measure": {"agg": "sum", "table": "orders", "col": "revenue"}, "group_by": [{"table": "customers", "col": "city"}]}
res = multi.run(U, spec); total = sum(float(l.split(",")[-1]) for l in ORD.strip().split("\n")[1:])
check("join sum by city matches total (no row loss/dup)", abs(sum(r[1] for r in res["rows"]) - total) < 1e-6 and len(res["rows"]) == 4)
res2 = multi.run(U, {"measure": {"agg": "sum", "table": "orders", "col": "qty"}, "group_by": [{"table": "products", "col": "category"}, {"table": "customers", "col": "segment"}]})
check("two joins at once", len(res2["joins"]) == 2 and res2["rows"])
res3 = multi.run(U, {"measure": {"agg": "count", "table": "orders", "col": None}, "group_by": [{"table": "orders", "col": "order_date", "grain": "month"}]})
check("month grain on date", all(len(str(r[0])) == 7 for r in res3["rows"]) and sum(r[1] for r in res3["rows"]) == 40)
r = multi.ask_multi(U, "total revenue by city", use_llm=False)
check("rule planner EN: revenue by city across join", r["planner"] == "rules" and r["spec"]["group_by"][0]["col"] == "city" and r["spec"]["measure"]["col"] == "revenue" and r["chart"])
r = multi.ask_multi(U, "إجمالي المبيعات حسب city", use_llm=False)
check("rule planner AR question", r["spec"]["measure"]["col"] == "revenue" and r["spec"]["group_by"][0]["col"] == "city")
r = multi.ask_multi(U, "total qty for Dairy by segment", use_llm=False)
check("rule planner: filter from value + group", any(f["value"] == "Dairy" for f in r["spec"]["filters"]) and r["spec"]["group_by"][0]["col"] == "segment")
r = multi.ask_multi(U, "how many orders by category", use_llm=False)
check("count + join", r["spec"]["measure"]["agg"] == "count" and r["joins"])
r = multi.ask_multi(U, "number of orders by month", use_llm=False)
check("rule planner: 'by month' uses date grain", r["spec"]["group_by"] and r["spec"]["group_by"][0]["grain"] == "month" and r["chart"]["type"] == "line")
# safety
bad = [
 {"measure": {"agg": "sum", "table": "orders; DROP TABLE orders", "col": "revenue"}},
 {"measure": {"agg": "sum", "table": "orders", "col": 'revenue") FROM x; --'}},
 {"measure": {"agg": "sum", "table": "orders", "col": "customer_id"}},
 {"measure": {"agg": "sum", "table": "orders", "col": "revenue"}, "group_by": [{"table": "customers", "col": "city"}] * 3},
 {"measure": {"agg": "sum", "table": "orders", "col": "revenue"}, "filters": [{"table": "orders", "col": "qty", "op": "OR 1=1", "value": 1}]},
 {"measure": {"agg": "sum", "table": "users", "col": "pw"}},
 {"measure": {"agg": "sum", "table": "orders", "col": "revenue"}, "top": 10**9},
 {"measure": {"agg": "sum", "table": "orders", "col": "revenue"}, "group_by": [{"table": "customers", "col": "city", "grain": "month"}]},
]
check("malicious/invalid specs all rejected", all(raises(lambda s=s: multi.run(U, s)) for s in bad))
r = multi.run(U, {"measure": {"agg": "count", "table": "customers", "col": None}, "filters": [{"table": "customers", "col": "city", "op": "=", "value": "Cairo' OR '1'='1"}]})
check("injection in filter value is a parameter (0 rows matched)", r["rows"][0][-1] == 0 and "OR" not in r["sql"])
check("wrong join direction refused (lookup->fact)", raises(lambda: multi.run(U, {"measure": {"agg": "count", "table": "customers", "col": None}, "group_by": [{"table": "orders", "col": "order_date"}]})))
check("manual relationship needs unique target", raises(lambda: schema.add_manual_relationship(U, "orders", "customer_id", "orders", "qty")))
schema.set_relationship(U, "orders.product_id->products.product_id", False)
check("rejected relationship not used", raises(lambda: multi.run(U, {"measure": {"agg": "sum", "table": "orders", "col": "qty"}, "group_by": [{"table": "products", "col": "category"}]})))
check("ident sanitization", schema.ident("Order Date; DROP", "c") == "order_date_drop" and schema.ident("1abc", "c") == "c_1abc")
check("bad csv rejected (ragged / empty)", raises(lambda: schema.add_table(U, "x", b"a,b\n1\n")) and raises(lambda: schema.add_table(U, "x", b"")))
check("table limit", (lambda: [schema.add_table(V, f"t{i}", b"a\n1\n") for i in range(schema.MAX_TABLES)] and raises(lambda: schema.add_table(V, "extra", b"a\n1\n")))())
schema.reset(V); schema.add_table(V, "orders", b"order_id,revenue\nA,1\n")
check("user isolation", multi.run(V, {"measure": {"agg": "sum", "table": "orders", "col": "revenue"}})["rows"][0][0] == 1 and "customers" not in schema.load_state(V)["tables"])
check("other user cannot see tables", raises(lambda: multi.run(V, {"measure": {"agg": "sum", "table": "customers", "col": "name"}})))
# LLM planner with mock
from http.server import BaseHTTPRequestHandler
REPLY = {"v": ""}
class M(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"])); b = json.dumps({"choices": [{"message": {"content": REPLY["v"]}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
m = ThreadingHTTPServer(("127.0.0.1", 0), M); threading.Thread(target=m.serve_forever, daemon=True).start()
os.environ["LLM_API_KEY"] = "t"; os.environ["LLM_BASE_URL"] = f"http://127.0.0.1:{m.server_port}/v1"
REPLY["v"] = json.dumps(spec); r = multi.ask_multi(U, "which cities bring the most money")
check("LLM spec over joined tables used", r["planner"] == "llm" and r["rows"])
REPLY["v"] = json.dumps({"measure": {"agg": "sum", "table": "orders", "col": "revenue"}, "group_by": [{"table": "secret", "col": "x"}]}); r = multi.ask_multi(U, "total revenue by city")
check("LLM invented table -> rules fallback", r["planner"] == "rules")
REPLY["v"] = "SELECT * FROM users"; r = multi.ask_multi(U, "total revenue by city")
check("LLM raw SQL ignored -> fallback", r["planner"] == "rules")
del os.environ["LLM_API_KEY"]
# HTTP flow
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.H); threading.Thread(target=srv.serve_forever, daemon=True).start()
def call(path, body=None, cookie=None, hdr=None, raw=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_port); h = dict(hdr or {})
    if cookie: h["Cookie"] = cookie
    c.request("POST" if body is not None or raw is not None else "GET", path, raw if raw is not None else (json.dumps(body) if body is not None else None), h); r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}"), r.getheader("Set-Cookie")
check("tables API needs login", call("/api/tables")[0] == 401 and call("/api/tables/upload", raw=b"a\n1")[0] == 401)
_, _, ck = call("/api/signup", {"email": "m@x.com", "password": "password123"}); ck = ck.split(";")[0]
for n, d in (("orders", ORD), ("customers", CUST)): call("/api/tables/upload", cookie=ck, hdr={"X-Table-Name": n}, raw=d.encode())
s, j, _ = call("/api/tables", cookie=ck, body=None); 
c = http.client.HTTPConnection("127.0.0.1", srv.server_port); c.request("GET", "/api/tables", headers={"Cookie": ck}); j = json.loads(c.getresponse().read())
check("HTTP upload + profile", set(j["tables"]) == {"orders", "customers"} and j["relationships"])
rid = j["relationships"][0]["id"]
s, _, _ = call("/api/tables/ask", {"question": "total revenue by city"}, ck); check("HTTP ask before confirm -> 400", s == 400)
call("/api/tables/relationship", {"id": rid, "confirmed": True}, ck)
s, j, _ = call("/api/tables/ask", {"question": "total revenue by city"}, ck); check("HTTP ask after confirm", s == 200 and j["rows"] and j["joins"])
print(f"\n{ok}/{tot} passed"); sys.exit(0 if ok == tot else 1)
