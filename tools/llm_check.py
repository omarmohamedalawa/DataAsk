"""Run the eval questions through a LIVE LLM provider and compare with the gold SQL.
Usage (your key stays in your shell, never in files or chat):
  export LLM_API_KEY=...   LLM_PROVIDER=openai|anthropic|gemini   LLM_MODEL=<model name>
  python3 tools/llm_check.py
Prints per-question agreement and how often the LLM plan was used vs the rule fallback."""
import ast, os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))
import llm
from engine import ask
from nl2sql import run

def cases():
    src = open(os.path.join(ROOT, "tests", "eval.py"), encoding="utf-8").read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "CASES":
            return ast.literal_eval(node.value)
    raise SystemExit("CASES not found")

if not llm.enabled():
    raise SystemExit("Set LLM_API_KEY (and LLM_PROVIDER / LLM_MODEL) first. Status: %s" % llm.status())
print("provider:", llm.status())
ok = used = 0
cs = cases()
for q, gold in cs:
    try:
        r = ask(q)
        exp = [list(x) for x in run(gold, [])[1]]
        good = r["mode"] == "sql" and r["rows"] == exp
        pl = r.get("planner", "?")
    except Exception as e:
        good, pl = False, "error: %s" % type(e).__name__
    ok += good; used += pl == "llm"
    print("PASS" if good else "FAIL", "[%s]" % pl, q)
print("agreement with gold: %d/%d, LLM plan used: %d/%d" % (ok, len(cs), used, len(cs)))
