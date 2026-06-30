# tests/cloud/test_auth.py
import time
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from wingman.cloud import auth

ISSUER = "https://idp.example.com"
AUD = "https://wingman.example.com"

@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key

def _make_token(key, **over):
    claims = {"sub": "user_xyz", "iss": ISSUER, "aud": AUD,
              "exp": int(time.time()) + 300, "email": "z@x.com", "name": "Zed"}
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256")

class _StubVerifier(auth.TokenVerifier):
    def __init__(self, pubkey):
        super().__init__(ISSUER, AUD, "https://idp.example.com/jwks")
        self._pub = pubkey
    def _signing_key(self, token):
        return self._pub

def test_valid_token_returns_sub(keypair):
    v = _StubVerifier(keypair.public_key())
    claims = v.verify(_make_token(keypair))
    assert claims["sub"] == "user_xyz"
    assert claims["email"] == "z@x.com"

def test_expired_token_rejected(keypair):
    v = _StubVerifier(keypair.public_key())
    with pytest.raises(auth.InvalidToken):
        v.verify(_make_token(keypair, exp=int(time.time()) - 10))

def test_wrong_audience_rejected(keypair):
    v = _StubVerifier(keypair.public_key())
    with pytest.raises(auth.InvalidToken):
        v.verify(_make_token(keypair, aud="https://evil.com"))

def test_bad_signature_rejected(keypair):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    v = _StubVerifier(keypair.public_key())
    with pytest.raises(auth.InvalidToken):
        v.verify(_make_token(other))

def test_resource_metadata_shape():
    doc = auth.resource_metadata(AUD, [ISSUER])
    assert doc["resource"] == AUD
    assert doc["authorization_servers"] == [ISSUER]
