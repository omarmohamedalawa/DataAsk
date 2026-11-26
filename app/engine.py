from nl2sql import plan, build_sql, run, describe, METRICS
from rag import Index
IDX = Index()
DOC_KW = ["policy", "سياسه", "definition", "define", "meaning", "mean", "تعريف", "يعني", "ازاي بتتحسب", "ازاي بنحسب", "ازاي بيتحسب", "ازاي بتحسب", "how do you calculate", "how is", "how is", "how do we calculate", "ramadan", "رمضان", "black friday", "الجمعه البيضا", "payment methods", "طرق الدفع", "refund", "return window", "ارجع", "ترجيع"]
METRIC_DOC = {"revenue": "Revenue is quantity times unit price", "profit": "Profit (gross profit)", "margin": "Profit (gross profit)", "aov": "Average order value", "customers": "Active customer", "returns": "Customers can return", "orders": "Orders is the count"}
def ask(question, db=None, use_llm=True):
    from nl2sql import norm
    q = " " + norm(question) + " "
    spec = None
    planner = "rules"
    if use_llm:
        import llm
        spec = llm.plan_with_llm(question)
        if spec: planner = "llm"
    if spec is None: spec = plan(question)
    doc_only = any(norm(k) in q for k in DOC_KW) and not any(k in q for k in ["how much", "كام", "اعلي", "top", "by ", " حسب ", "per ", "total", "اجمالي"])
    src = IDX.search(question, k=2)
    out = {"planner": planner, "spec": spec, "sources": src, "lang": spec["lang"]}
    if doc_only and src:
        out.update(mode="rag", answer=src[0]["text"], sql=None, columns=[], rows=[], chart=None)
        return out
    sql, params = build_sql(spec)
    cols, rows = run(sql, params, db=db)
    out.update(mode="sql", sql=sql, params=params, columns=cols, rows=[list(r) for r in rows])
    m = METRICS[spec["metric"]]
    out["answer"] = describe(spec, cols, rows)
    if len(cols) == 2 and rows:
        out["chart"] = {"type": "line" if spec["dim"] == "month" else "bar", "labels": [str(r[0]) for r in rows], "values": [r[1] for r in rows], "title": m[2] if spec["lang"] == "ar" else m[1]}
    else: out["chart"] = None
    # cite the definition of the metric from docs
    key = METRIC_DOC.get(spec["metric"])
    if not key: out["sources"] = [x for x in src if x["score"] >= 0.3]
    if key:
        hits = [c for c in IDX.chunks if key in c["text"]]
        ar_hits = [c for c in IDX.chunks if c["doc"].endswith("_ar.md") and (("الإيراد" in c["text"] and spec["metric"] == "revenue") or ("الربح" in c["text"] and spec["metric"] in ("profit", "margin")) or ("متوسط قيمة" in c["text"] and spec["metric"] == "aov") or ("العميل النشط" in c["text"] and spec["metric"] == "customers") or ("المرتجع" in c["text"] and spec["metric"] == "returns") or ("عدد الطلبات هو" in c["text"] and spec["metric"] == "orders"))]
        pick = (ar_hits if spec["lang"] == "ar" else hits)[:1]
        out["sources"] = [{"doc": c["doc"], "line": c["line"], "score": 1.0, "text": c["text"]} for c in pick] or [x for x in src if x['score']>=0.3]
    return out
