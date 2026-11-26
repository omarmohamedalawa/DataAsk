"""Schema-grounded planner: question (ar/en) -> structured spec -> SQL -> guarded execution.
The planner is deterministic and offline. An LLM planner can replace plan() and must return the same spec;
guard() and the eval set stay as the safety net."""
import re, sqlite3, os
from textnorm import norm, is_arabic
DB = os.path.join(os.path.dirname(__file__), "..", "data", "retail.db")
LATEST = "2026-10"

METRICS = {  # key: (sql expr, en label, ar label, view)
 "revenue": ("ROUND(SUM(revenue),0)", "revenue (EGP)", "الإيراد (جنيه)"),
 "profit": ("ROUND(SUM(profit),0)", "gross profit (EGP)", "الربح (جنيه)"),
 "orders": ("COUNT(DISTINCT order_id)", "orders", "عدد الطلبات"),
 "units": ("SUM(qty)", "units sold", "الكمية المباعة"),
 "customers": ("COUNT(DISTINCT customer_id)", "active customers", "العملاء النشطين"),
 "aov": ("ROUND(SUM(revenue)/COUNT(DISTINCT order_id),1)", "average order value (EGP)", "متوسط قيمة الطلب (جنيه)"),
 "margin": ("ROUND(100.0*SUM(profit)/SUM(revenue),1)", "margin %", "هامش الربح %"),
 "returns": ("SUM(qty)", "returned units", "الوحدات المرتجعة"),
}
METRIC_KW = [("returns", ["return", "مرتجع", "رجع", "ترجيع"]),
 ("margin", ["margin", "هامش"]), ("profit", ["profit", "ربح", "ارباح", "مكسب"]),
 ("aov", ["average order", "aov", "average basket", "basket size", "متوسط الطلب", "متوسط قيمه", "متوسط السله", "متوسط الفاتوره"]),
 ("customers", ["customer", "عميل", "عملاء", "زبون", "زباين"]),
 ("orders", ["orders", "order count", "how many orders", "طلبات", "عدد الطلب", "اوردر"]),
 ("units", ["units", "quantity", "pieces", "قطع", "كميه", "وحدات"]),
 ("revenue", ["revenue", "sales", "sold", "ايراد", "مبيعات", "بيع", "باع", "دخل"])]
DIM_KW = [("month", ["by month", "monthly", "per month", "each month", "trend", "over time", "شهري", "كل شهر", "حسب الشهر", "لكل شهر", "اتجاه", "تطور"]),
 ("city", ["by city", "per city", "each city", "which city", "cities", "كل مدينه", "حسب المدينه", "لكل مدينه", "مدن", "اي مدينه", "اي محافظه", "محافظات", "كل محافظه", "مدينه"]),
 ("category", ["by category", "per category", "each category", "categories", "which category", "كل فئه", "حسب الفئه", "لكل فئه", "فئات", "اي فئه", "كل قسم", "اقسام"]),
 ("payment", ["payment", "pay method", "دفع", "كاش ولا"]),
 ("channel", ["by channel", "channels", "online vs", "store vs", "قنوات", "حسب القناه"]),
 ("product", ["product", "item", "best sell", "top sell", "best-sell", "منتج", "منتجات", "صنف", "اصناف", "اكتر حاجه"])]
CITIES = {"Cairo": ["cairo", "قاهره"], "Giza": ["giza", "جيزه"], "Alexandria": ["alexandria", "alex", "اسكندريه"],
          "Mansoura": ["mansoura", "منصوره"], "Tanta": ["tanta", "طنطا"], "Assiut": ["assiut", "asyut", "اسيوط"], "Aswan": ["aswan", "اسوان"]}
CATS = {"Groceries": ["grocer", "بقاله"], "Dairy": ["dairy", "البان", "الباني"], "Beverages": ["beverage", "drinks", "مشروبات"],
        "Personal Care": ["personal care", "عنايه شخصيه", "عنايه"], "OTC Pharmacy": ["pharmacy", "otc", "صيدليه", "ادويه"],
        "Cleaning": ["cleaning", "منظفات"], "Baby": ["baby", "اطفال", "طفل"]}
MONTHS = {1: ["january", "يناير"], 2: ["february", "فبراير"], 3: ["march", "مارس"], 4: ["april", "ابريل"], 5: ["may", "مايو"], 6: ["june", "يونيو"],
          7: ["july", "يوليو"], 8: ["august", "اغسطس"], 9: ["september", "سبتمبر"], 10: ["october", "اكتوبر"], 11: ["november", "نوفمبر"], 12: ["december", "ديسمبر"]}
CHANNELS = {"online": ["online", "اونلاين", "اون لاين"], "store": ["in store", "in-store", "stores only", "فرع", "الفروع"]}
LASTMONTH = ["last month", "الشهر اللي فات", "الشهر الماضي", "الشهر الي فات", "الشهر اللى فات"]

def has(q, kws): return any(k in q for k in kws)

def plan(question: str) -> dict:
    q = " " + norm(question) + " "
    spec = {"metric": "revenue", "dim": None, "top": None, "order": "desc", "filters": {}, "lang": "ar" if is_arabic(question) else "en"}
    for m, kws in METRIC_KW:
        if has(q, kws): spec["metric"] = m; break
    for d, kws in DIM_KW:
        if has(q, kws): spec["dim"] = d; break
    f = spec["filters"]
    for city, kws in CITIES.items():
        if has(q, kws) and not has(q, ["which city", "اي مدينه", "اي محافظه"]) and spec["dim"] != "city": f["city"] = city
        elif has(q, kws) and spec["dim"] == "city" and has(q, ["in " + kws[0], "في " + kws[-1], "ب" + kws[-1]]): f["city"] = city; spec["dim"] = None
    for cat, kws in CATS.items():
        if has(q, kws) and spec["dim"] != "category": f["category"] = cat
    for ch, kws in CHANNELS.items():
        if has(q, kws) and spec["dim"] != "channel": f["channel"] = ch
    if has(q, ["online vs", "store vs", "اونلاين ولا", "اونلاين مقابل"]): spec["dim"] = "channel"; f.pop("channel", None)
    ym = re.search(r"\b(20\d\d)\b", q)
    year = ym.group(1) if ym else None
    month = next((n for n, kws in MONTHS.items() if has(q, kws)), None)
    if has(q, LASTMONTH): f["month"] = LATEST
    elif month and year: f["month"] = f"{year}-{month:02d}"
    elif month: f["month"] = f"2026-{month:02d}" if month <= 10 else f"2025-{month:02d}"
    elif year: f["year"] = year
    tm = re.search(r"(?:top|best|highest|اعلي|اعلى|اكتر|افضل|اكثر|اول)\s*(\d+)", q) or re.search(r"(\d+)\s*(?:top|best|منتجات|مدن|فئات)", q)
    if tm: spec["top"] = int(tm.group(1))
    if has(q, ["top", "best", "highest", "most", "اعلي", "اعلى", "اكتر", "افضل", "اكثر", "اول"]) and not spec["top"] and spec["dim"] in ("product", "city", "category", "payment"):
        spec["top"] = None if (has(q, ["which", "اي ", "اكتر مدينه", "اكتر فئه", "افضل مدينه", "افضل فئه", "best city", "best category", "top city", "top category"]) and spec["dim"] != "product") else 5
    if has(q, ["lowest", "worst", "least", "اقل", "اضعف"]): spec["order"] = "asc"; spec["top"] = spec["top"] or 5
    if spec["dim"] is None and has(q, ["best sell", "top", "افضل", "اكتر"]):
        pass
    return spec

def build_sql(spec):
    m = spec["metric"]; expr = METRICS[m][0]; view = "sales_fact"
    where = []; params = []
    if m == "returns":
        view = "returns_fact"
    for k, v in spec["filters"].items():
        if k == "channel" and view == "returns_fact": continue
        where.append(f"{k} = ?"); params.append(v)
    dim = spec["dim"]
    if view == "returns_fact" and dim in ("payment", "channel"): dim = None
    col = {"month": "month", "city": "city", "category": "category", "payment": "payment", "channel": "channel", "product": "product"}.get(dim)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    if col:
        order = "month ASC" if dim == "month" else f"value {spec['order'].upper()}"
        lim = f" LIMIT {int(spec['top'])}" if spec["top"] else ""
        sql = f"SELECT {col} AS label, {expr} AS value FROM {view}{w} GROUP BY {col} ORDER BY {order}{lim}"
    else:
        sql = f"SELECT {expr} AS value FROM {view}{w}"
    return sql, params

ALLOWED = {"sales_fact", "returns_fact"}
def guard(sql):
    s = sql.strip().rstrip(";")
    if ";" in s or not re.match(r"(?is)^select\b", s): raise ValueError("only a single SELECT is allowed")
    if re.search(r"(?i)\b(insert|update|delete|drop|alter|attach|pragma|create|replace)\b", s): raise ValueError("write/DDL keywords blocked")
    for t in re.findall(r"(?i)\b(?:from|join)\s+([a-z_]+)", s):
        if t.lower() not in ALLOWED: raise ValueError(f"table {t} not allowed")
    return s

def run(sql, params, limit=200, db=None):
    s = guard(sql)
    c = sqlite3.connect(f"file:{os.path.abspath(db or DB)}?mode=ro", uri=True)
    try:
        cur = c.execute(s, params); cols = [d[0] for d in cur.description]
        return cols, cur.fetchmany(limit)
    finally: c.close()

AR_DIM = {"month": "الشهر", "city": "المدينة", "category": "الفئة", "payment": "طريقة الدفع", "channel": "القناة", "product": "المنتج"}
AR_CITY = {"Cairo": "القاهرة", "Giza": "الجيزة", "Alexandria": "الإسكندرية", "Mansoura": "المنصورة", "Tanta": "طنطا", "Assiut": "أسيوط", "Aswan": "أسوان"}
AR_CAT = {"Groceries": "بقالة", "Dairy": "ألبان", "Beverages": "مشروبات", "Personal Care": "عناية شخصية", "OTC Pharmacy": "صيدلية", "Cleaning": "منظفات", "Baby": "مستلزمات أطفال"}
def fmt(v): return f"{v:,.0f}" if isinstance(v, (int, float)) and abs(v) >= 100 else (f"{v:,.1f}" if isinstance(v, float) else str(v))

def describe(spec, cols, rows):
    ar = spec["lang"] == "ar"; m = METRICS[spec["metric"]]; ml = m[2] if ar else m[1]
    fl = []
    for k, v in spec["filters"].items():
        fl.append((AR_CITY.get(v, v) if k == "city" else AR_CAT.get(v, v) if k == "category" else v))
    scope = (" (" + "، ".join(fl) + ")" if ar else " (" + ", ".join(fl) + ")") if fl else ""
    if not rows: return "مفيش بيانات مطابقة." if ar else "No matching data."
    if len(cols) == 1:
        return f"{ml}{scope}: {fmt(rows[0][0])}" if ar else f"{ml}{scope}: {fmt(rows[0][0])}"
    lab = rows[0][0]
    if spec["dim"] == "city": lab = AR_CITY.get(lab, lab) if ar else lab
    if spec["dim"] == "category": lab = AR_CAT.get(lab, lab) if ar else lab
    if spec["dim"] == "month":
        best = max(rows, key=lambda r: r[1]); return (f"{ml}{scope} حسب الشهر. أعلى شهر {best[0]} بقيمة {fmt(best[1])}." if ar else f"{ml}{scope} by month. Peak month {best[0]}: {fmt(best[1])}.")
    return (f"{ml}{scope} حسب {AR_DIM[spec['dim']]}. الأول: {lab} بقيمة {fmt(rows[0][1])}." if ar else f"{ml}{scope} by {spec['dim']}. Leader: {lab} at {fmt(rows[0][1])}.")
