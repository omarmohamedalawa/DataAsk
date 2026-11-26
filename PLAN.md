# DataAsk: chat-with-your-data BI assistant (Arabic / English)

Goal: ask business questions in Arabic or English, get a verified SQL answer, a chart, and cited definitions from company docs.
Market fit: Egyptian retail, e-commerce and OTC pharmacy/FMCG. Halal-compatible: no interest products, no gambling or alcohol data; payments are cash, card, wallet, COD.
Data: synthetic only (no employer, patient or customer data).

## Week by week
Week 1: Data and baseline. Star-schema warehouse (PostgreSQL in production, SQLite in the demo), synthetic Egyptian retail generator, sales view, 30 question eval set. Output: data + eval set.
Week 2: Text-to-SQL. Arabic normalization (alef/ya/ta marbuta, diacritics), intent + entity planner, schema-grounded SQL builder, read-only SQL guard. Then swap the planner for an LLM with tool calling, keeping the guard and the eval set as the safety net. Output: eval accuracy report.
Week 3: RAG. Index KPI definitions, returns and promo policies (bilingual), retrieval with citations, hybrid answer (number from SQL + definition from docs). Output: cited answers.
Week 4: Product. Web UI (RTL), charts, history, Dockerfile, auth, per-user row limits. Output: running app.
Week 5: Hardening and portfolio. Evals in CI, failure cases write-up, README, demo video, case study. Output: GitHub repo + short case study.
Week 6 (optional, sell it): connect to a real client DB (read-only role), pilot with one retailer or pharmacy chain, Arabic dialect tuning.

## Built today (demo scope = weeks 1-4 core)
Synthetic data, planner, SQL guard, RAG with citations, web UI with charts, eval set, screenshots, README, zip.

## SaaS step (added 26 Nov 2026)
Done: accounts and sessions, per-user CSV upload, rate limit, LLM planner with validation and fallback, Dockerfile, static GitHub Pages demo.
Next: live-provider LLM tests and a bigger question set, schema-agnostic text-to-SQL for uploads, billing (halal-compatible flat plans), a managed host with HTTPS and backups.
