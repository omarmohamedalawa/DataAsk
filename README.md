# DataAsk: chat with your data (Arabic / English)

Ask business questions in Arabic (Egyptian dialect included) or English. DataAsk turns the question into SQL on a retail warehouse, runs it read-only, draws a chart, shows the SQL, and cites the KPI definition it used.

**Live demo (GitHub Pages, runs fully in your browser):** see the repo's Pages link. Data is synthetic Egyptian retail (7 cities, 7 categories incl. OTC pharmacy, about 24.7k orders, Jan 2025 to Oct 2026). Halal-compatible by design: no interest/credit products, no gambling or alcohol categories.

## Two ways to run
| | Static demo (GitHub Pages) | SaaS backend (Python, `app/`) |
|---|---|---|
| Where | `docs/` | `app/server.py`, `Dockerfile` |
| Planner | rules (JS port of the Python planner) | rules, or an LLM when `LLM_API_KEY` is set |
| Accounts / login | no | yes (PBKDF2, HttpOnly session cookie) |
| Upload your own sales CSV | no | yes, isolated per user |
| Needs a server | no | yes (any host that runs Docker or Python 3) |

GitHub Pages is static hosting, so accounts, uploads and the LLM call (it needs a secret key) cannot run there. They run in the backend.

## Run the backend
    python3 app/server.py                      # http://localhost:8000 (seeds the demo DB on first start)
    LLM_API_KEY=... LLM_MODEL=... LLM_BASE_URL=https://api.openai.com/v1 python3 app/server.py
    docker build -t dataask . && docker run -p 8000:8000 -e LLM_API_KEY=... dataask
Python 3.9+, standard library only. Put it behind HTTPS in production (the Docker image sets secure cookies).

## Build and test the static demo
    python3 tools/build_site.py      # seeds DB, writes docs/config.js, downloads sql.js (needs npm)
    python3 tests/eval.py            # 30 checks, Python planner
    python3 tests/test_saas.py       # 17 checks: auth, isolation, rate limit, upload, LLM planner (mock provider)
    node tests/eval_js.cjs           # same 30 checks against the browser planner
    cd docs && python3 -m http.server 8000

## How it works
- `app/nl2sql.py`: question to spec {metric, dimension, top-N, filters}, then parameterized SQL on two views (`sales_fact`, `returns_fact`). `guard()` allows one SELECT on whitelisted views only; the database is opened read-only.
- `app/llm.py`: optional LLM planner. The model returns only a JSON spec, validated against fixed enums and safe-value patterns, and turned into SQL by the same builder. Invalid output or errors fall back to the rule planner.
- `app/rag.py`: TF-IDF retrieval over `kb/*.md` (bilingual KPI and policy docs), used for definition questions and the citation under each answer.
- `app/auth.py`, `app/server.py`: accounts, sessions, rate limit (30 questions/min/user), CSV upload (5 MB, 200k rows) into a per-user database.
- `docs/planner.js`: browser port of planner + RAG; SQL runs in SQLite WebAssembly.

## Honest limits
- The rule planner covers the listed metrics, dimensions and filters; unseen phrasing can misroute. The tests were written alongside it, so they are regression tests, not a generalization score.
- The LLM planner is tested against a mock provider only, not a live provider yet.
- Uploaded CSVs must use the columns: order_id, order_date, customer_id, city, category, product, channel, payment, qty, revenue (profit optional). The LLM prompt lists the demo's cities and categories, so uploads with other names rely on the rule planner for now.
- No payments or billing. "SaaS" here means accounts, per-user data, and an API you can host; see PLAN.md for next steps.

All data is synthetic. No real company, patient or customer data.
