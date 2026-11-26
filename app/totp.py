"""RFC 6238 TOTP (SHA-1, 6 digits, 30 s) using only the standard library. Compatible with Google Authenticator, Aegis, 1Password, etc."""
import hmac, hashlib, struct, time, base64, secrets
def new_secret(): return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
def _key(secret): return base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
def hotp(secret, counter):
    h = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest(); o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7fffffff) % 10**6:06d}"
def now_code(secret, t=None): return hotp(secret, int((time.time() if t is None else t) // 30))
def verify(secret, code, last_step=0, t=None, window=1):
    """Return the matched time step (must be > last_step: a code cannot be reused) or None."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6): return None
    cur = int((time.time() if t is None else t) // 30)
    for st in range(cur - window, cur + window + 1):
        if st > last_step and hmac.compare_digest(hotp(secret, st), code): return st
    return None
def uri(secret, email, issuer="DataAsk"):
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
