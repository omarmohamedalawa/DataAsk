// Browser/Node port of app/nl2sql.py + app/engine.py + app/rag.py. Tables come from config.js (generated from the Python module).
(function (root) {
const C = root.DATAASK_CONFIG;
const norm = s => s.toLowerCase().replace(/[\u064B-\u0652\u0640]/g, "").replace(/[إأآٱ]/g, "ا").replace(/ى/g, "ي").replace(/ة/g, "ه")
  .replace(/[٠-٩]/g, d => "٠١٢٣٤٥٦٧٨٩".indexOf(d)).replace(/\s+/g, " ").trim();
const isArabic = s => (s.match(/[\u0600-\u06FF]/g) || []).length > (s.match(/[A-Za-z]/g) || []).length;
const has = (q, kws) => kws.some(k => q.includes(k));
function plan(question) {
  const q = " " + norm(question) + " ";
  const spec = { metric: "revenue", dim: null, top: null, order: "desc", filters: {}, lang: isArabic(question) ? "ar" : "en" };
  for (const [m, kws] of C.METRIC_KW) if (has(q, kws)) { spec.metric = m; break; }
  for (const [d, kws] of C.DIM_KW) if (has(q, kws)) { spec.dim = d; break; }
  const f = spec.filters;
  for (const [city, kws] of Object.entries(C.CITIES)) {
    if (has(q, kws) && !has(q, ["which city", "اي مدينه", "اي محافظه"]) && spec.dim !== "city") f.city = city;
    else if (has(q, kws) && spec.dim === "city" && has(q, ["in " + kws[0], "في " + kws[kws.length - 1], "ب" + kws[kws.length - 1]])) { f.city = city; spec.dim = null; }
  }
  for (const [cat, kws] of Object.entries(C.CATS)) if (has(q, kws) && spec.dim !== "category") f.category = cat;
  for (const [ch, kws] of Object.entries(C.CHANNELS)) if (has(q, kws) && spec.dim !== "channel") f.channel = ch;
  if (has(q, ["online vs", "store vs", "اونلاين ولا", "اونلاين مقابل"])) { spec.dim = "channel"; delete f.channel; }
  const ym = q.match(/\b(20\d\d)\b/); const year = ym ? ym[1] : null;
  let month = null; for (const [n, kws] of Object.entries(C.MONTHS)) if (has(q, kws)) { month = +n; break; }
  const pad = n => String(n).padStart(2, "0");
  if (has(q, C.LASTMONTH)) f.month = C.LATEST;
  else if (month && year) f.month = `${year}-${pad(month)}`;
  else if (month) f.month = month <= 10 ? `2026-${pad(month)}` : `2025-${pad(month)}`;
  else if (year) f.year = year;
  const tm = q.match(/(?:top|best|highest|اعلي|اعلى|اكتر|افضل|اكثر|اول)\s*(\d+)/) || q.match(/(\d+)\s*(?:top|best|منتجات|مدن|فئات)/);
  if (tm) spec.top = +tm[1];
  if (has(q, ["top", "best", "highest", "most", "اعلي", "اعلى", "اكتر", "افضل", "اكثر", "اول"]) && !spec.top && ["product", "city", "category", "payment"].includes(spec.dim))
    spec.top = (has(q, ["which", "اي ", "اكتر مدينه", "اكتر فئه", "افضل مدينه", "افضل فئه", "best city", "best category", "top city", "top category"]) && spec.dim !== "product") ? null : 5;
  if (has(q, ["lowest", "worst", "least", "اقل", "اضعف"])) { spec.order = "asc"; spec.top = spec.top || 5; }
  return spec;
}
function buildSql(spec) {
  const m = spec.metric, expr = C.METRICS[m][0]; let view = m === "returns" ? "returns_fact" : "sales_fact";
  const where = [], params = [];
  for (const [k, v] of Object.entries(spec.filters)) { if (k === "channel" && view === "returns_fact") continue; where.push(`${k} = ?`); params.push(v); }
  let dim = spec.dim; if (view === "returns_fact" && (dim === "payment" || dim === "channel")) dim = null;
  const col = { month: "month", city: "city", category: "category", payment: "payment", channel: "channel", product: "product" }[dim];
  const w = where.length ? " WHERE " + where.join(" AND ") : "";
  let sql;
  if (col) {
    const order = dim === "month" ? "month ASC" : `value ${spec.order.toUpperCase()}`;
    const lim = spec.top ? ` LIMIT ${parseInt(spec.top)}` : "";
    sql = `SELECT ${col} AS label, ${expr} AS value FROM ${view}${w} GROUP BY ${col} ORDER BY ${order}${lim}`;
  } else sql = `SELECT ${expr} AS value FROM ${view}${w}`;
  return [sql, params];
}
function guard(sql) {
  const s = sql.trim().replace(/;+$/, "");
  if (s.includes(";") || !/^select\b/i.test(s)) throw new Error("only a single SELECT is allowed");
  if (/\b(insert|update|delete|drop|alter|attach|pragma|create|replace)\b/i.test(s)) throw new Error("write/DDL keywords blocked");
  for (const m of s.matchAll(/\b(?:from|join)\s+([a-z_]+)/gi)) if (!["sales_fact", "returns_fact"].includes(m[1].toLowerCase())) throw new Error(`table ${m[1]} not allowed`);
  return s;
}
function run(db, sql, params) {
  const s = guard(sql); const st = db.prepare(s); st.bind(params);
  const rows = []; while (st.step()) rows.push(st.get()); const cols = st.getColumnNames(); st.free();
  return [cols, rows.slice(0, 200)];
}
const fmt = v => typeof v === "number" ? (Math.abs(v) >= 100 ? Math.round(v).toLocaleString("en-US") : (Number.isInteger(v) ? String(v) : v.toFixed(1))) : String(v);
function describe(spec, cols, rows) {
  const ar = spec.lang === "ar", m = C.METRICS[spec.metric], ml = ar ? m[2] : m[1];
  const fl = Object.entries(spec.filters).map(([k, v]) => k === "city" ? (C.AR_CITY[v] || v) : k === "category" ? (C.AR_CAT[v] || v) : v);
  const scope = fl.length ? " (" + fl.join(ar ? "، " : ", ") + ")" : "";
  if (!rows.length) return ar ? "مفيش بيانات مطابقة." : "No matching data.";
  if (cols.length === 1) return `${ml}${scope}: ${fmt(rows[0][0])}`;
  let lab = rows[0][0];
  if (spec.dim === "city" && ar) lab = C.AR_CITY[lab] || lab;
  if (spec.dim === "category" && ar) lab = C.AR_CAT[lab] || lab;
  if (spec.dim === "month") { const b = rows.reduce((a, r) => r[1] > a[1] ? r : a); return ar ? `${ml}${scope} حسب الشهر. أعلى شهر ${b[0]} بقيمة ${fmt(b[1])}.` : `${ml}${scope} by month. Peak month ${b[0]}: ${fmt(b[1])}.`; }
  return ar ? `${ml}${scope} حسب ${C.AR_DIM[spec.dim]}. الأول: ${lab} بقيمة ${fmt(rows[0][1])}.` : `${ml}${scope} by ${spec.dim}. Leader: ${lab} at ${fmt(rows[0][1])}.`;
}
// ---- RAG ----
const tokens = s => (norm(s).match(/[a-z0-9]+|[\u0621-\u064A]+/g) || []).map(t => t.length > 3 && t.startsWith("ال") ? t.slice(2) : (t.length > 3 && "وبلف".includes(t[0]) && t.slice(1, 3) === "ال" ? t.slice(3) : t));
const CHUNKS = []; const df = {};
for (const [doc, text] of Object.entries(C.KB)) text.split(/\n+/).forEach((para, i) => {
  if (para.startsWith("#") || para.length < 20) return;
  const tf = {}; tokens(para).forEach(t => tf[t] = (tf[t] || 0) + 1); CHUNKS.push({ doc, line: i + 1, text: para, tf });
});
CHUNKS.forEach(c => Object.keys(c.tf).forEach(t => df[t] = (df[t] || 0) + 1));
const IDF = {}; Object.entries(df).forEach(([t, d]) => IDF[t] = Math.log(1 + CHUNKS.length / d));
function search(q, k = 2, min = 0.15) {
  const qt = {}; tokens(q).forEach(t => qt[t] = (qt[t] || 0) + 1);
  const nrm = tf => Math.sqrt(Object.entries(tf).reduce((a, [t, v]) => a + (v * (IDF[t] || 0)) ** 2, 0));
  const res = [];
  for (const c of CHUNKS) {
    let num = 0; for (const t in qt) if (t in c.tf) num += qt[t] * c.tf[t] * (IDF[t] || 0) ** 2;
    const s = num / ((nrm(c.tf) * nrm(qt)) || 1); if (s >= min) res.push([s, c]);
  }
  res.sort((a, b) => b[0] - a[0]);
  return res.slice(0, k).map(([s, c]) => ({ doc: c.doc, line: c.line, score: Math.round(s * 1000) / 1000, text: c.text }));
}
const DOC_KW = ["policy", "سياسه", "definition", "define", "meaning", "mean", "تعريف", "يعني", "ازاي بتتحسب", "ازاي بنحسب", "ازاي بيتحسب", "ازاي بتحسب", "how do you calculate", "how is", "how do we calculate", "ramadan", "رمضان", "black friday", "الجمعه البيضا", "payment methods", "طرق الدفع", "refund", "return window", "ارجع", "ترجيع"];
const METRIC_DOC = { revenue: "Revenue is quantity times unit price", profit: "Profit (gross profit)", margin: "Profit (gross profit)", aov: "Average order value", customers: "Active customer", returns: "Customers can return", orders: "Orders is the count" };
function ask(db, question) {
  const q = " " + norm(question) + " ";
  const spec = plan(question); let src = search(question, 2);
  const out = { planner: "rules", spec, sources: src, lang: spec.lang };
  const docOnly = DOC_KW.some(k => q.includes(norm(k))) && !["how much", "كام", "اعلي", "top", "by ", " حسب ", "per ", "total", "اجمالي"].some(k => q.includes(k));
  if (docOnly && src.length) return Object.assign(out, { mode: "rag", answer: src[0].text, sql: null, columns: [], rows: [], chart: null });
  const [sql, params] = buildSql(spec); const [cols, rows] = run(db, sql, params);
  Object.assign(out, { mode: "sql", sql, params, columns: cols, rows });
  out.answer = describe(spec, cols, rows);
  out.chart = cols.length === 2 && rows.length ? { type: spec.dim === "month" ? "line" : "bar", labels: rows.map(r => String(r[0])), values: rows.map(r => r[1]), title: spec.lang === "ar" ? C.METRICS[spec.metric][2] : C.METRICS[spec.metric][1] } : null;
  const key = METRIC_DOC[spec.metric];
  if (!key) out.sources = src.filter(x => x.score >= 0.3);
  else {
    const hits = CHUNKS.filter(c => c.text.includes(key));
    const m = spec.metric;
    const ar = CHUNKS.filter(c => c.doc.endsWith("_ar.md") && ((c.text.includes("الإيراد") && m === "revenue") || (c.text.includes("الربح") && (m === "profit" || m === "margin")) || (c.text.includes("متوسط قيمة") && m === "aov") || (c.text.includes("العميل النشط") && m === "customers") || (c.text.includes("المرتجع") && m === "returns") || (c.text.includes("عدد الطلبات هو") && m === "orders")));
    const pick = (spec.lang === "ar" ? ar : hits).slice(0, 1);
    out.sources = pick.length ? pick.map(c => ({ doc: c.doc, line: c.line, score: 1.0, text: c.text })) : src.filter(x => x.score >= 0.3);
  }
  return out;
}
const API = { ask, plan, buildSql, guard, search, norm };
if (typeof module !== "undefined") module.exports = API; else root.DataAsk = API;
})(typeof window !== "undefined" ? window : globalThis);
