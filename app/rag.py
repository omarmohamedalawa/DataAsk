"""Small TF-IDF retriever over markdown docs, paragraph level, with citations."""
import os, math, re
from collections import Counter
from textnorm import tokens
DOCS = os.path.join(os.path.dirname(__file__), "..", "kb")
class Index:
    def __init__(self):
        self.chunks = []
        for fn in sorted(os.listdir(DOCS)):
            if not fn.endswith(".md"): continue
            for i, para in enumerate(re.split(r"\n+", open(os.path.join(DOCS, fn), encoding="utf8").read())):
                if para.startswith("#") or len(para) < 20: continue
                self.chunks.append({"doc": fn, "line": i + 1, "text": para, "tf": Counter(tokens(para))})
        df = Counter(t for c in self.chunks for t in c["tf"])
        n = len(self.chunks)
        self.idf = {t: math.log(1 + n / d) for t, d in df.items()}
    def search(self, q, k=2, min_score=0.15):
        qt = Counter(tokens(q)); res = []
        for c in self.chunks:
            num = sum(qt[t] * c["tf"][t] * self.idf.get(t, 0) ** 2 for t in qt if t in c["tf"])
            den = math.sqrt(sum((v * self.idf.get(t, 0)) ** 2 for t, v in c["tf"].items())) * math.sqrt(sum((v * self.idf.get(t, 0)) ** 2 for t, v in qt.items())) or 1
            s = num / den
            if s >= min_score: res.append((s, c))
        res.sort(key=lambda x: -x[0])
        return [{"doc": c["doc"], "line": c["line"], "score": round(s, 3), "text": c["text"]} for s, c in res[:k]]
