# DataAsk: chat with your data (Arabic / English)

Ask business questions in Arabic (Egyptian dialect included) or English. DataAsk turns the question into SQL, runs it read-only, draws a chart, shows the SQL, and cites the KPI definition it used. It also takes several of your own CSV tables, finds how they link, and answers across them.

## Quick start on your computer (step by step)
You need Python 3.9 or newer. Nothing else to install.
1. Download the project: `git clone https://github.com/omarmohamedalawa/DataAsk.git` then `cd DataAsk` (or "Code > Download ZIP" on GitHub and unzip).
2. Start it: `python3 app/server.py` (on Windows: `py app/server.py`). The first start builds the demo database, about 10 seconds.
3. Open http://127.0.0.1:8000 in your browser.
4. Click "Create account", enter any email and a password (8+ characters). The account lives only on your computer.
5. Tab "Sales demo": click an example question or type your own, Arabic or English.
6. Tab "My tables": click "Load sample tables" (or drop your own CSV files), confirm the proposed links between tables, then ask, for example "total revenue by city".
7. Stop with Ctrl+C. Your accounts and uploaded tables stay in the `data/` folder.
Optional: set an LLM key for smarter question understanding: `LLM_API_KEY=... python3 app/server.py` (without a key the built-in rule planner is used).
Run the tests: `python3 tests/eval.py && python3 tests/test_saas.py && python3 tests/test_multi.py && python3 tests/test_security.py`.

### ملخص بالعربي
1. نزّل المشروع من GitHub (Code > Download ZIP) وفكّه، أو `git clone`.
2. افتح الـ terminal جوه الفولدر واكتب: `python3 app/server.py` (على ويندوز: `py app/server.py`).
3. افتح المتصفح على http://127.0.0.1:8000
4. اعمل حساب جديد (أي إيميل وباسورد 8 حروف أو أكتر). الحساب بيتخزن على جهازك بس.
5. تاب "Sales demo": اسأل بالعربي أو الإنجليزي، مثلا "أعلى 5 منتجات مبيعا في 2026".
6. تاب "My tables": اضغط "Load sample tables" أو ارفع ملفات CSV بتاعتك (لحد 8 جداول)، وأكّد الروابط اللي بيقترحها بين الجداول، وبعدها اسأل "إجمالي المبيعات حسب المدينة".
7. وقّف البرنامج بـ Ctrl+C. بياناتك فاضلة في فولدر `data/`.
البيانات كلها تجريبية (synthetic) ومفيهاش أي بيانات حقيقية.

**Live demo (GitHub Pages, runs fully in your browser):** https://omarmohamedalawa.github.io/DataAsk/ . Data is synthetic Egyptian retail (7 cities, 7 categories incl. OTC pharmacy, about 24.7k orders, Jan 2025 to Oct 2026). Halal-compatible by design: no interest/credit products, no gambling or alcohol categories.

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
    python3 app/server.py                      # http://127.0.0.1:8000 (seeds the demo DB on first start; HOST=0.0.0.0 to expose, PORT=... to change)
    LLM_API_KEY=... LLM_MODEL=... LLM_BASE_URL=https://api.openai.com/v1 python3 app/server.py
    docker build -t dataask . && docker run -p 8000:8000 -e LLM_API_KEY=... dataask
Python 3.9+, standard library only. The server listens on 127.0.0.1 by default. If you ever expose it, put it behind HTTPS (the Docker image sets secure cookies and listens on 0.0.0.0) and set `TRUST_PROXY=1` only behind a proxy you control.

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

## Multiple tables and relationships (backend)
Upload several CSVs (up to 8 tables, 60 columns, 200k rows each, 5 MB per file) from "My tables".
1. **Profiling on upload** (`app/schema.py`): per column type (int/real/date/text), null count, distinct count, uniqueness, and a role (key, measure, date, dimension). Identifiers are sanitized to `[a-z0-9_]`.
2. **Relationship inference**: a column becomes a proposed foreign key when its values are at least 90% contained in a unique column of another table (names matching raise confidence; without a name match it needs 99%). These are proposals only.
3. **You confirm or reject** each join (or add one manually; the target must be a unique column). Only confirmed joins are ever used.
4. **Questions across tables** (`app/multi.py`): the planner (rules, or an LLM if `LLM_API_KEY` is set) produces a JSON spec `{measure, group_by, filters, order, top}`. The spec is validated against the catalog (tables, columns, types, operators, limits) and turned into parameterized SQL. Joins follow confirmed relationships from the fact side to the lookup side (many-to-one) so rows are never duplicated. The model never writes SQL, and the database connection is read-only.
API: `GET /api/tables`, `POST /api/tables/upload` (header `X-Table-Name`), `POST /api/tables/relationship`, `POST /api/tables/ask`, `POST /api/tables/reset`. Also `POST /api/tables/sample` loads the sample tables. Tests: `python3 tests/test_multi.py` (32 checks). Sample CSVs in `samples/`.
Limits: no many-to-many or composite keys; join inference is heuristic (always confirm); the rule planner understands simple "measure by dimension, filter, top N" questions; the LLM planner has only been tested against a mock provider. Not available on the static GitHub Pages demo (needs the backend).

## Security (what is protected and how it is tested: `python3 tests/test_security.py`, 81 checks)
- **Passwords**: salted PBKDF2-SHA256 (200k rounds); 8+ chars, common passwords rejected; no password hash or token is ever returned by the API.
- **Sessions**: random 256-bit token in an `HttpOnly; SameSite=Strict` cookie (`Secure` when `COOKIE_SECURE=1`); only a SHA-256 of the token is stored; 7-day expiry; deleted on logout; max 10 live sessions per user.
- **Brute force**: 8 failed logins per email or 30 per address in 15 minutes locks login for that window; unknown-email and wrong-password paths cost the same time; 20 auth requests/min and 10 signups/hour per address.
- **CSRF**: SameSite=Strict plus an Origin check on every POST.
- **XSS**: every value from data, questions or answers is HTML-escaped in the UI; a strict Content-Security-Policy (script nonce per response, `default-src 'none'`, no framing). Tested in a real browser with hostile CSV values.
- **SQL injection**: users and the LLM never write SQL. Questions become a validated JSON spec; table and column names must exist in the catalog and are quoted from there; values are bound parameters. The database connection is read-only with a 5-second query limit.
- **Uploads**: 5 MB, 200k rows, 8 tables, 60 columns, 100 MB per user; strict CSV parsing; table and column names are reduced to `[a-z0-9_]` (no path characters, no `sqlite_` names); NaN/Infinity and huge integers are handled.
- **Path traversal**: the server serves one HTML page and a JSON API only; it never opens a file path taken from a request. Per-user files are named by numeric user id.
- **Isolation**: each user has a separate database file; tests confirm one user cannot see, query, reset or overwrite another's tables or CSV.
- **Rate limits**: 30 questions/min and 30 upload or change requests/min per user; request body caps (16 KB JSON, 5 MB upload); socket timeout against slow clients.
- **Not covered**: this is a stdlib server meant for local or small-team use. No email verification or password reset, no 2FA, no audit log, no per-user disk quota beyond 100 MB, and no TLS (use a reverse proxy). Not a substitute for an external penetration test.

## Honest limits
- The rule planner covers the listed metrics, dimensions and filters; unseen phrasing can misroute. The tests were written alongside it, so they are regression tests, not a generalization score.
- The LLM planner is tested against a mock provider only, not a live provider yet.
- Uploaded CSVs must use the columns: order_id, order_date, customer_id, city, category, product, channel, payment, qty, revenue (profit optional). The LLM prompt lists the demo's cities and categories, so uploads with other names rely on the rule planner for now.
- No payments or billing. "SaaS" here means accounts, per-user data, and an API you can host; see PLAN.md for next steps.

All data is synthetic. No real company, patient or customer data.
