"""Eval: each question has a hand-written gold SQL. We compare executed planner output to gold result (and mode)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from engine import ask
from nl2sql import run, guard
CASES = [
 ("Top 5 products by revenue in 2026", "SELECT product,ROUND(SUM(revenue),0) FROM sales_fact WHERE year='2026' GROUP BY product ORDER BY 2 DESC LIMIT 5"),
 ("اعلى 5 منتجات مبيعا في 2026", "SELECT product,ROUND(SUM(revenue),0) FROM sales_fact WHERE year='2026' GROUP BY product ORDER BY 2 DESC LIMIT 5"),
 ("Revenue by month in 2026", "SELECT month,ROUND(SUM(revenue),0) FROM sales_fact WHERE year='2026' GROUP BY month ORDER BY month"),
 ("المبيعات حسب الشهر في 2025", "SELECT month,ROUND(SUM(revenue),0) FROM sales_fact WHERE year='2025' GROUP BY month ORDER BY month"),
 ("Revenue by city in 2026", "SELECT city,ROUND(SUM(revenue),0) FROM sales_fact WHERE year='2026' GROUP BY city ORDER BY 2 DESC"),
 ("المبيعات حسب المدينة", "SELECT city,ROUND(SUM(revenue),0) FROM sales_fact GROUP BY city ORDER BY 2 DESC"),
 ("Which category has the highest profit?", "SELECT category,ROUND(SUM(profit),0) FROM sales_fact GROUP BY category ORDER BY 2 DESC"),
 ("كام عدد الطلبات الشهر اللي فات؟", "SELECT COUNT(DISTINCT order_id) FROM sales_fact WHERE month='2026-10'"),
 ("How many orders last month?", "SELECT COUNT(DISTINCT order_id) FROM sales_fact WHERE month='2026-10'"),
 ("Total revenue in Cairo in 2026", "SELECT ROUND(SUM(revenue),0) FROM sales_fact WHERE city='Cairo' AND year='2026'"),
 ("اجمالي المبيعات في القاهرة في 2026", "SELECT ROUND(SUM(revenue),0) FROM sales_fact WHERE city='Cairo' AND year='2026'"),
 ("Average order value online vs store", "SELECT channel,ROUND(SUM(revenue)/COUNT(DISTINCT order_id),1) FROM sales_fact GROUP BY channel ORDER BY 2 DESC"),
 ("متوسط قيمة الطلب حسب القناة", "SELECT channel,ROUND(SUM(revenue)/COUNT(DISTINCT order_id),1) FROM sales_fact GROUP BY channel ORDER BY 2 DESC"),
 ("Revenue by payment method in 2026", "SELECT payment,ROUND(SUM(revenue),0) FROM sales_fact WHERE year='2026' GROUP BY payment ORDER BY 2 DESC"),
 ("Top 3 dairy products by units", "SELECT product,SUM(qty) FROM sales_fact WHERE category='Dairy' GROUP BY product ORDER BY 2 DESC LIMIT 3"),
 ("اعلى 3 منتجات البان من حيث الكمية", "SELECT product,SUM(qty) FROM sales_fact WHERE category='Dairy' GROUP BY product ORDER BY 2 DESC LIMIT 3"),
 ("Profit margin by category", "SELECT category,ROUND(100.0*SUM(profit)/SUM(revenue),1) FROM sales_fact GROUP BY category ORDER BY 2 DESC"),
 ("Returned units by category", "SELECT category,SUM(qty) FROM returns_fact GROUP BY category ORDER BY 2 DESC"),
 ("How many active customers in March 2026?", "SELECT COUNT(DISTINCT customer_id) FROM sales_fact WHERE month='2026-03'"),
 ("Total revenue in February 2026", "SELECT ROUND(SUM(revenue),0) FROM sales_fact WHERE month='2026-02'"),
 ("What are the 5 worst selling products in 2026?", "SELECT product,ROUND(SUM(revenue),0) FROM sales_fact WHERE year='2026' GROUP BY product ORDER BY 2 ASC LIMIT 5"),
 ("ايه اكتر مدينة بتجيب ربح؟", "SELECT city,ROUND(SUM(profit),0) FROM sales_fact GROUP BY city ORDER BY 2 DESC"),
]
RAG = [("ازاي بنحسب الربح؟", "kpi_definitions_ar.md"), ("How is revenue defined?", "kpi_definitions_en.md"),
       ("What is the return policy for dairy?", "returns_policy.md"), ("ايه تأثير رمضان على المبيعات؟", "promotions_and_seasonality.md"),
       ("What payment methods are supported?", "payments_and_channels.md")]
ok = 0
for q, gold in CASES:
    r = ask(q); exp = [list(x) for x in run(gold, [])[1]]
    good = r["mode"] == "sql" and r["rows"] == exp
    ok += good; print(("PASS" if good else "FAIL"), q)
    if not good: print("   got", r.get("sql"), r.get("params"), r["rows"][:2], "\n   exp", exp[:2])
for q, doc in RAG:
    r = ask(q); good = r["mode"] == "rag" and r["sources"] and r["sources"][0]["doc"] == doc
    ok += good; print(("PASS" if good else "FAIL"), "[rag]", q, "->", r["sources"][0]["doc"] if r["sources"] else None)
tot = len(CASES) + len(RAG)
for bad in ["DROP TABLE orders", "SELECT * FROM customers", "SELECT 1; DELETE FROM orders"]:
    try: guard(bad); print("FAIL guard", bad); tot += 1
    except ValueError: ok += 1; tot += 1; print("PASS guard blocks:", bad)
print(f"\n{ok}/{tot} passed")
