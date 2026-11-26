"""Create a local self-signed TLS certificate (needs the `openssl` command, which ships with macOS, Linux and Git for Windows).
Usage: python3 tools/make_cert.py   ->  data/tls/cert.pem + key.pem. Then:
  TLS_CERT=data/tls/cert.pem TLS_KEY=data/tls/key.pem python3 app/server.py   ->  https://localhost:8000
The browser will warn once because the certificate is self-signed; that is expected for a local cert."""
import os, subprocess, sys
d = os.path.join(os.path.dirname(__file__), "..", "data", "tls"); os.makedirs(d, exist_ok=True)
cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert, "-days", "365", "-subj", "/CN=localhost",
                "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"], check=True)
os.chmod(key, 0o600); print("created", cert, key)
