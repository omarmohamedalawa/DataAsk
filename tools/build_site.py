"""Builds the static GitHub Pages demo in docs/: seeds the synthetic DB, writes config.js from the Python planner tables, fetches sql.js (SQLite WebAssembly) via npm."""
import os, sys, json, subprocess, shutil, tempfile, glob, tarfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "app"))
import seed_data, nl2sql
seed_data.build()
shutil.copy(os.path.join(ROOT, "data", "retail.db"), os.path.join(ROOT, "docs", "retail.db"))
kb = {f: open(os.path.join(ROOT, "kb", f), encoding="utf8").read() for f in sorted(os.listdir(os.path.join(ROOT, "kb"))) if f.endswith(".md")}
n = nl2sql
cfg = dict(METRICS={k: list(v) for k, v in n.METRICS.items()}, METRIC_KW=n.METRIC_KW, DIM_KW=n.DIM_KW, CITIES=n.CITIES, CATS=n.CATS,
           MONTHS={str(k): v for k, v in n.MONTHS.items()}, CHANNELS=n.CHANNELS, LASTMONTH=n.LASTMONTH, LATEST=n.LATEST,
           AR_DIM=n.AR_DIM, AR_CITY=n.AR_CITY, AR_CAT=n.AR_CAT, KB=kb)
open(os.path.join(ROOT, "docs", "config.js"), "w", encoding="utf8").write("window.DATAASK_CONFIG=" + json.dumps(cfg, ensure_ascii=False) + ";\n")
tmp = tempfile.mkdtemp()
subprocess.check_call(["npm", "pack", "sql.js@1.13.0"], cwd=tmp, stdout=subprocess.DEVNULL)
with tarfile.open(glob.glob(os.path.join(tmp, "sql.js-*.tgz"))[0]) as t:
    for name in ("sql-wasm.js", "sql-wasm.wasm"):
        open(os.path.join(ROOT, "docs", name), "wb").write(t.extractfile("package/dist/" + name).read())
print("site built in docs/")
