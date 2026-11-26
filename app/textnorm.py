import re
_AR = re.compile(r"[\u064B-\u0652\u0640]")
def norm(s: str) -> str:
    s = s.lower()
    s = _AR.sub("", s)
    s = re.sub("[إأآٱ]", "ا", s).replace("ى", "ي").replace("ة", "ه")
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    return re.sub(r"\s+", " ", s).strip()
def is_arabic(s: str) -> bool:
    a = len(re.findall(r"[\u0600-\u06FF]", s)); l = len(re.findall(r"[A-Za-z]", s))
    return a > l
def tokens(s: str):
    out = []
    for t in re.findall(r"[a-z0-9]+|[\u0621-\u064A]+", norm(s)):
        if len(t) > 3 and t.startswith("ال"): t = t[2:]
        elif len(t) > 3 and t[0] in "وبلف" and t[1:3] == "ال": t = t[3:]
        out.append(t)
    return out
