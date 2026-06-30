"""OAuth 2.1 resource-server token verification for Wingman Cloud.

Wingman validates bearer tokens issued by the managed IdP (WorkOS AuthKit).
It does not issue tokens. The stable `sub` claim becomes the Wingman user_id.
"""
from __future__ import annotations

import jwt


class InvalidToken(Exception):
    pass


class TokenVerifier:
    def __init__(self, issuer: str, audience: str, jwks_uri: str):
        self._issuer = issuer
        self._audience = audience
        self._jwks_uri = jwks_uri
        self._jwk_client = jwt.PyJWKClient(jwks_uri) if jwks_uri else None

    def _signing_key(self, token: str):
        # Overridden in tests. In production, resolve the key from JWKS by the
        # token's `kid`. PyJWKClient caches keys internally.
        if self._jwk_client is None:
            raise InvalidToken("no JWKS client configured")
        return self._jwk_client.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> dict:
        try:
            key = self._signing_key(token)
            return jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "sub"]},
            )
        except InvalidToken:
            raise
        except Exception as exc:  # jwt.* errors, key errors, etc.
            raise InvalidToken(str(exc)) from exc


def resource_metadata(base_url: str, authorization_servers: list[str]) -> dict:
    """The /.well-known/oauth-protected-resource document (RFC 9728)."""
    return {
        "resource": base_url,
        "authorization_servers": authorization_servers,
        "bearer_methods_supported": ["header"],
    }
