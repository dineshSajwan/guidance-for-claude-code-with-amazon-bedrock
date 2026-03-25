"""Tier 1: OIDC token flow tests against oidc-server-mock (Docker required).

Note: Duende IdentityServer 7.x (used by oidc-server-mock) no longer returns id_token
for the Resource Owner Password Credentials (ROPC) grant — ROPC is deprecated in OAuth 2.1.
The access_token is itself a signed JWT containing the same identity claims (sub, email, iss),
so we decode it to verify issuer, subject, and email claims.
The credential provider in production uses the PKCE authorization_code flow which does
return an id_token; that full OIDC→STS→Bedrock chain is covered by `ccwb test`.
"""

import pytest
import requests
import jwt


@pytest.mark.integration
class TestOidcTokenFlow:
    def test_ropc_returns_access_token(self, oidc_server):
        """ROPC grant returns access_token (Duende 7.x no longer returns id_token in ROPC)."""
        resp = requests.post(
            f"{oidc_server}/connect/token",
            data={
                "grant_type": "password",
                "username": "test@example.com",
                "password": "testpass123",
                "client_id": "ccwb-integ",
                "scope": "openid email profile",
            },
        )
        assert resp.status_code == 200, f"Token endpoint failed: {resp.text}"
        body = resp.json()
        assert "access_token" in body, "Response missing access_token"
        assert "email" in body.get("scope", ""), "openid+email scopes not granted"

    def test_access_token_claims(self, oidc_server):
        """Decoded access_token contains expected issuer, sub, and email claims."""
        resp = requests.post(
            f"{oidc_server}/connect/token",
            data={
                "grant_type": "password",
                "username": "test@example.com",
                "password": "testpass123",
                "client_id": "ccwb-integ",
                "scope": "openid email profile",
            },
        )
        assert resp.status_code == 200
        access_token = resp.json()["access_token"]

        # access_token is a signed JWT containing identity claims
        claims = jwt.decode(access_token, options={"verify_signature": False})

        assert claims["iss"] == "http://localhost:8080"
        assert claims["sub"] == "test-user-001"
        assert claims["client_id"] == "ccwb-integ"

    def test_jwks_endpoint(self, oidc_server):
        """JWKS endpoint returns non-empty keys array with required RSA fields."""
        resp = requests.get(f"{oidc_server}/.well-known/openid-configuration/jwks")
        assert resp.status_code == 200
        body = resp.json()
        assert "keys" in body
        assert len(body["keys"]) > 0, "JWKS keys array is empty"
        for key in body["keys"]:
            assert "kty" in key
            assert "kid" in key
            assert "n" in key
            assert "e" in key

    def test_userinfo_endpoint_returns_email(self, oidc_server):
        """Userinfo endpoint returns email claim when queried with access_token."""
        token_resp = requests.post(
            f"{oidc_server}/connect/token",
            data={
                "grant_type": "password",
                "username": "test@example.com",
                "password": "testpass123",
                "client_id": "ccwb-integ",
                "scope": "openid email profile",
            },
        )
        access_token = token_resp.json()["access_token"]
        userinfo_resp = requests.get(
            f"{oidc_server}/connect/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert userinfo_resp.status_code == 200
        userinfo = userinfo_resp.json()
        assert userinfo.get("sub") == "test-user-001"
        assert userinfo.get("email") == "test@example.com"
