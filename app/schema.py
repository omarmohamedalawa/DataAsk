"""Multi-table workspace: CSV ingest, schema profiling, relationship inference, per-user storage.
Everything here works on sanitized identifiers ([a-z][a-z0-9_]{0,39}) so they can be quoted safely."""
import os, re, csv, io, json, sqlite3, time
import auth
MAX_TABLES, MAX_COLS, MAX_ROWS = 8, 60, 200_000

def ident(s, fallback):
    s = re.sub(r"[^a-z0-9]+", "_", str(s).strip().lower()).strip("_")
    if not s or not s[0].isalpha(): s = fallback if not s else "c_" + s
    return s[:40]

def ws_paths(user):
    return os.path.join(auth.USERS, f"{user['id']}_ws.db"), os.path.join(auth.USERS, f"{user['id']}_ws.json")

def _kind(vals):
    vals = [v for v in vals if v not in ("", None)]
    if not vals: return "text"
    def isint(v):
        try: return str(int(v)) == v.strip().lstrip("+") or float(v) == int(float(v)) and "." not in v and "e" not in v.lower()
        except Exception: return False
    def isnum(v):
        try: float(v); return True
        except Exception: return False
    if all(re.fullmatch(r"20\d\d-\d\d-\d\d.*", v) or re.fullmatch(r"19\d\d-\d\d-\d\d.*", v) for v in vals): return "date"
    if all(isint(v) for v in vals): return "int"
    if all(isnum(v) for v in vals): return "real"
    return "text"

def profile_table(name, header, rows):
    cols = []
    for i, h in enumerate(header):
        raw = [r[i] if i < len(r) else "" for r in rows]
        kind = _kind(raw); nn = [v for v in raw if v != ""]
        d = len(set(nn))
        c = {"name": h, "type": kind, "rows": len(raw), "nulls": len(raw) - len(nn), "distinct": d, "unique": bool(nn) and d == len(nn) and len(nn) == len(raw)}
        if kind == "text" and d <= 200: c["values"] = sorted(set(nn))
        low = h.lower()
        if c["unique"] and kind in ("int", "text") and (low == "id" or low.endswith("_id") or low.endswith("id") or (kind == "text" and d == len(raw))): c["role"] = "key"
        elif kind == "date": c["role"] = "date"
        elif kind in ("int", "real") and not low.endswith("_id") and low != "id": c["role"] = "measure"
        elif low.endswith("_id") or low == "id": c["role"] = "key"
        else: c["role"] = "dimension"
        cols.append(c)
    return {"name": name, "rows": len(rows), "columns": cols}

def load_state(user):
    p = ws_paths(user)[1]
    return json.load(open(p)) if os.path.exists(p) else {"tables": {}, "relationships": []}

def save_state(user, st): json.dump(st, open(ws_paths(user)[1], "w"))

def add_table(user, name, raw: bytes):
    if len(raw) > auth.MAX_UPLOAD: raise ValueError("file too large (5 MB max)")
    os.makedirs(auth.USERS, exist_ok=True)
    name = ident(name, "table"); st = load_state(user)
    if name not in st["tables"] and len(st["tables"]) >= MAX_TABLES: raise ValueError(f"max {MAX_TABLES} tables")
    rd = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
    try: head = next(rd)
    except StopIteration: raise ValueError("empty file")
    if len(head) > MAX_COLS: raise ValueError(f"max {MAX_COLS} columns")
    names, seen = [], set()
    for i, h in enumerate(head):
        n = ident(h, f"col{i + 1}"); k = n; j = 2
        while k in seen: k = f"{n[:36]}_{j}"; j += 1
        seen.add(k); names.append(k)
    rows = []
    for i, r in enumerate(rd):
        if i >= MAX_ROWS: raise ValueError("too many rows (200k max)")
        if len(r) != len(names): raise ValueError(f"row {i + 2} has {len(r)} values, expected {len(names)}")
        rows.append([v.strip() for v in r])
    if not rows: raise ValueError("no data rows")
    prof = profile_table(name, names, rows)
    dbp = ws_paths(user)[0]; c = sqlite3.connect(dbp)
    c.execute(f'DROP TABLE IF EXISTS "{name}"')
    c.execute(f'CREATE TABLE "{name}" (' + ",".join(f'"{n}" {"INTEGER" if k["type"] == "int" else "REAL" if k["type"] == "real" else "TEXT"}' for n, k in zip(names, prof["columns"])) + ")")
    conv = []
    for r in rows:
        conv.append([(None if v == "" else int(float(v)) if k["type"] == "int" else float(v) if k["type"] == "real" else v) for v, k in zip(r, prof["columns"])])
    c.executemany(f'INSERT INTO "{name}" VALUES (' + ",".join("?" * len(names)) + ")", conv); c.commit(); c.close()
    st["tables"][name] = prof
    st["relationships"] = [r for r in st["relationships"] if name not in (r["from_table"], r["to_table"])] + [
        r for r in infer(st, dbp, only_with=name)]
    save_state(user, st); return prof

def _singular(s): return s[:-1] if s.endswith("s") and len(s) > 3 else s

def _colvals(dbp, t, c):
    cn = sqlite3.connect(f"file:{os.path.abspath(dbp)}?mode=ro", uri=True)
    try: return {r[0] for r in cn.execute(f'SELECT DISTINCT "{c}" FROM "{t}" WHERE "{c}" IS NOT NULL')}
    finally: cn.close()

def infer(st, dbp, only_with=None):
    """Propose foreign keys: a column whose values are (almost) all present in another table's unique column."""
    out = []; T = st["tables"]; seen = {(r["from_table"], r["from_col"], r["to_table"], r["to_col"]): r for r in st["relationships"]}
    for t1, p1 in T.items():
        for t2, p2 in T.items():
            if t1 == t2 or (only_with and only_with not in (t1, t2)): continue
            for a in p1["columns"]:
                if a["type"] not in ("int", "text") or a["role"] in ("measure", "date"): continue
                for b in p2["columns"]:
                    if not b["unique"] or b["type"] != a["type"]: continue
                    if (t1, a["name"], t2, b["name"]) in seen: continue
                    nameok = a["name"] == b["name"] or a["name"] in (t2 + "_id", _singular(t2) + "_id") or (b["name"] == "id" and a["name"].startswith(_singular(t2)))
                    if not nameok and not (b["distinct"] >= 5 and a["distinct"] >= 5): continue
                    va, vb = _colvals(dbp, t1, a["name"]), _colvals(dbp, t2, b["name"])
                    if not va: continue
                    cont = len(va & vb) / len(va)
                    if cont < 0.9 or (not nameok and cont < 0.99): continue
                    card = "one-to-one" if a["unique"] else "many-to-one"
                    out.append({"id": f"{t1}.{a['name']}->{t2}.{b['name']}", "from_table": t1, "from_col": a["name"], "to_table": t2, "to_col": b["name"],
                                "cardinality": card, "match": round(cont, 3), "confidence": "high" if nameok and cont >= 0.99 else "medium",
                                "status": "proposed"})
    return out

def set_relationship(user, rid, confirmed):
    st = load_state(user)
    for r in st["relationships"]:
        if r["id"] == rid: r["status"] = "confirmed" if confirmed else "rejected"; save_state(user, st); return r
    raise ValueError("unknown relationship")

def add_manual_relationship(user, ft, fc, tt, tc):
    st = load_state(user); T = st["tables"]
    for t, c in ((ft, fc), (tt, tc)):
        if t not in T or c not in [x["name"] for x in T[t]["columns"]]: raise ValueError("unknown table or column")
    rid = f"{ft}.{fc}->{tt}.{tc}"
    if any(r["id"] == rid for r in st["relationships"]): raise ValueError("relationship exists")
    b = next(x for x in T[tt]["columns"] if x["name"] == tc)
    if not b["unique"]: raise ValueError("target column must be unique (a key)")
    r = {"id": rid, "from_table": ft, "from_col": fc, "to_table": tt, "to_col": tc, "cardinality": "many-to-one", "match": None, "confidence": "manual", "status": "confirmed"}
    st["relationships"].append(r); save_state(user, st); return r

def reset(user):
    for p in ws_paths(user):
        if os.path.exists(p): os.remove(p)
