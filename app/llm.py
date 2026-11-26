"""Optional LLM planner (any OpenAI-compatible /chat/completions endpoint).
Env: LLM_API_KEY (required to enable), LLM_BASE_URL (default https://api.openai.com/v1), LLM_MODEL.
The model only returns a JSON spec; it never writes SQL. The spec is validated against fixed enums and
turned into parameterized SQL by nl2sql.build_sql, so a bad or malicious model output cannot reach the database
beyond the same whitelisted views. Any error or invalid output returns None and the rule planner takes over."""
import os, json, re, urllib.request
from nl2sql import METRICS, is_arabic

DIMS = {None, "month", "city", "category", "payment", "channel", "product"}
FKEYS = {"city", "category", "channel", "year", "month"}
SAFE = re.compile(r"^[\w \-\.&/()]{1,40}$")
SYSTEM = """You convert a business question (Arabic or English) about a retail sales table into a JSON spec. Reply with JSON only.
Schema: {"metric": one of revenue|profit|orders|units|customers|aov|margin|returns,
 "dim": one of month|city|category|payment|channel|product|null (group-by),
 "top": integer or null, "order": "asc"|"desc",
 "filters": object with optional keys city, category, channel, year (YYYY), month (YYYY-MM)}.
Cities use English names (Cairo, Giza, Alexandria, Mansoura, Tanta, Assiut, Aswan). Categories: Groceries, Dairy, Beverages, Personal Care, OTC Pharmacy, Cleaning, Baby.
Channels: online, store. Latest data month is 2026-10; "last month" means 2026-10. If the question is not a data question, reply {"metric":null}."""

def validate(d, question):
    if not isinstance(d, dict) or d.get("metric") not in METRICS: return None
    dim = d.get("dim")
    if dim not in DIMS: return None
    top = d.get("top")
    if top is not None:
        if not isinstance(top, int) or isinstance(top, bool) or not (1 <= top <= 50): return None
    order = d.get("order", "desc")
    if order not in ("asc", "desc"): return None
    f = d.get("filters") or {}
    if not isinstance(f, dict) or set(f) - FKEYS: return None
    for k, v in f.items():
        if not isinstance(v, str) or not SAFE.match(v): return None
    if "year" in f and not re.fullmatch(r"20\d\d", f["year"]): return None
    if "month" in f and not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", f["month"]): return None
    return {"metric": d["metric"], "dim": dim, "top": top, "order": order, "filters": f, "lang": "ar" if is_arabic(question) else "en"}

def plan_with_llm(question):
    key = os.environ.get("LLM_API_KEY")
    if not key: return None
    url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    body = {"model": os.environ.get("LLM_MODEL", "gpt-4o-mini"), "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question[:500]}]}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            txt = json.loads(r.read())["choices"][0]["message"]["content"]
        return validate(json.loads(txt), question)
    except Exception:
        return None
