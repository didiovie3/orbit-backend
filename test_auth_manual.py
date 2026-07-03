"""
Proves get_current_user() correctly verifies BOTH token types:
  - ES256 tokens (like your real Supabase project actually issues) —
    verified via a real JWKS fetch over real HTTP, to a tiny local server
    standing in for Supabase's endpoint.
  - HS256 tokens (the legacy path, kept for other Supabase projects) —
    verified against a shared secret, same as before.

This can't reach your actual Supabase project from this sandbox — but it
uses a real EC key pair, a real JWKS-shaped response, and a real HTTP
server, so it proves the verification LOGIC is correct. Your own
Swagger UI test against the real project is still the final check.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, ".")

os.environ["SUPABASE_URL"] = "http://127.0.0.1:9999"  # overridden per-test below
os.environ["SUPABASE_SERVICE_KEY"] = "fake"
os.environ["SUPABASE_JWT_SECRET"] = "test-hs256-secret"

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from jwt.utils import to_base64url_uint  # noqa: E402


# ── Generate a real EC (P-256) key pair, same curve Supabase uses ──
private_key = ec.generate_private_key(ec.SECP256R1())
public_numbers = private_key.public_key().public_numbers()
kid = "test-key-1"

jwks_response = {
    "keys": [
        {
            "kty": "EC",
            "crv": "P-256",
            "kid": kid,
            "use": "sig",
            "alg": "ES256",
            "x": to_base64url_uint(public_numbers.x).decode(),
            "y": to_base64url_uint(public_numbers.y).decode(),
        }
    ]
}


class JWKSHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/auth/v1/.well-known/jwks.json":
            body = json.dumps(jwks_response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # quiet


server = HTTPServer(("127.0.0.1", 9999), JWKSHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(0.3)

from app.core.auth import get_current_user  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from fastapi.security import HTTPAuthorizationCredentials  # noqa: E402

get_settings.cache_clear()

print("=" * 60)
print("TEST 1: Real ES256 token, verified via real JWKS HTTP fetch")
es256_token = jwt.encode(
    {"sub": "22222222-2222-2222-2222-222222222222", "email": "es256@test.com", "aud": "authenticated"},
    private_key,
    algorithm="ES256",
    headers={"kid": kid},
)
creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=es256_token)
user = get_current_user(credentials=creds, settings=get_settings())
print(f"  Verified OK — user_id: {user.user_id}, email: {user.email}")
assert user.user_id == "22222222-2222-2222-2222-222222222222"

print("\nTEST 2: Real HS256 token, verified via shared secret (legacy path)")
hs256_token = jwt.encode(
    {"sub": "33333333-3333-3333-3333-333333333333", "email": "hs256@test.com", "aud": "authenticated"},
    "test-hs256-secret",
    algorithm="HS256",
)
creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=hs256_token)
user = get_current_user(credentials=creds, settings=get_settings())
print(f"  Verified OK — user_id: {user.user_id}, email: {user.email}")
assert user.user_id == "33333333-3333-3333-3333-333333333333"

print("\nTEST 3: ES256 token signed by a DIFFERENT key -> must be rejected")
wrong_key = ec.generate_private_key(ec.SECP256R1())
forged_token = jwt.encode(
    {"sub": "should-not-work", "aud": "authenticated"},
    wrong_key,
    algorithm="ES256",
    headers={"kid": kid},  # claims to be our real key, but isn't signed by it
)
creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=forged_token)
try:
    get_current_user(credentials=creds, settings=get_settings())
    print("  FAILED — forged token was accepted!")
    sys.exit(1)
except Exception as e:
    print(f"  Correctly rejected: {type(e).__name__}")

print("\nTEST 4: Garbage token -> must be rejected")
creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not.a.real.token")
try:
    get_current_user(credentials=creds, settings=get_settings())
    print("  FAILED — garbage token was accepted!")
    sys.exit(1)
except Exception as e:
    print(f"  Correctly rejected: {type(e).__name__}")

print("\n" + "=" * 60)
print("ALL 4 TESTS PASSED")
server.shutdown()
