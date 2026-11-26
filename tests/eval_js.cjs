// Parity test: runs the same 30 checks as tests/eval.py against the browser planner (Node + sql.js).
const fs = require("fs"), path = require("path");
globalThis.DATAASK_CONFIG = (new Function("window", fs.readFileSync(path.join(__dirname, "../docs/config.js"), "utf8") + ";return window.DATAASK_CONFIG;"))({});
const P = require("../docs/planner.js");
const py = fs.readFileSync(path.join(__dirname, "eval.py"), "utf8");
const cases = [...py.matchAll(/^ \("((?:[^"\\]|\\.)*)", "((?:[^"\\]|\\.)*)"\),?$/gm)].map(m => [m[1], m[2].replace(/\\"/g, '"')]);
const rag = [...py.matchAll(/\("([^"]+)", "([a-z_]+\.md)"\)/g)].map(m => [m[1], m[2]]).filter(x => !cases.some(c => c[0] === x[0]));
(async () => {
  const init = require(path.join(__dirname, "../docs/sql-wasm.js"));
  const SQL = await init({ locateFile: f => path.join(__dirname, "../docs", f) });
  const db = new SQL.Database(fs.readFileSync(path.join(__dirname, "../docs/retail.db")));
  const run = (sql) => { const r = db.exec(sql); return r.length ? r[0].values : []; };
  let ok = 0, tot = 0;
  for (const [q, gold] of cases) { tot++; const r = P.ask(db, q); const exp = run(gold); const good = r.mode === "sql" && JSON.stringify(r.rows) === JSON.stringify(exp); ok += good; console.log(good ? "PASS" : "FAIL", q); if (!good) console.log("  got", r.sql, JSON.stringify(r.rows.slice(0, 2)), "exp", JSON.stringify(exp.slice(0, 2))); }
  for (const [q, doc] of rag) { tot++; const r = P.ask(db, q); const good = r.mode === "rag" && r.sources[0].doc === doc; ok += good; console.log(good ? "PASS" : "FAIL", "[rag]", q); }
  for (const bad of ["DROP TABLE orders", "SELECT * FROM customers", "SELECT 1; DELETE FROM orders"]) { tot++; try { P.guard(bad); console.log("FAIL guard", bad); } catch (e) { ok++; console.log("PASS guard blocks:", bad); } }
  console.log(`\n${ok}/${tot} passed`);
})();
