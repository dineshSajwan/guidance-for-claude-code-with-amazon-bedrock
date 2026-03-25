"""Tier 1: credential provider logic with mocked STS (Docker required, no real AWS calls).

Duende IdentityServer 7.x does not return id_token for the ROPC grant.
We use the access_token (a signed JWT with sub/email/iss) as a stand-in for id_token
when exercising get_aws_credentials_direct — STS is mocked so any valid JWT works.
"""

import re
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
import requests

from credential_provider.__main__ import MultiProviderAuth


def _get_jwt_and_claims(oidc_server):
    """Get a JWT token and decoded claims from the OIDC server via ROPC."""
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
    resp.raise_for_status()
    # Use access_token as id_token — both are JWTs; STS is mocked so the value
    # doesn't matter for these tests; the real flow uses PKCE + id_token.
    token = resp.json()["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    # Supplement claims with email from userinfo (access_token claims don't include email)
    userinfo = requests.get(
        f"{oidc_server}/connect/userinfo",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    claims.setdefault("email", userinfo.get("email", "test@example.com"))
    return token, claims


def _fake_sts_response():
    return {
        "Credentials": {
            "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
            "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "SessionToken": "AQoDYXdzEJr...",
            "Expiration": datetime.now(timezone.utc),
        }
    }


@pytest.mark.integration
class TestGetAwsCredentialsDirect:
    def test_sts_called_with_correct_parameters(self, oidc_server, integ_profile):
        """get_aws_credentials_direct passes the right args to AssumeRoleWithWebIdentity."""
        token, claims = _get_jwt_and_claims(oidc_server)

        mock_sts = MagicMock()
        mock_sts.assume_role_with_web_identity.return_value = _fake_sts_response()

        with patch("boto3.client", return_value=mock_sts):
            auth = MultiProviderAuth(profile=integ_profile)
            result = auth.get_aws_credentials_direct(token, claims)

        call_kwargs = mock_sts.assume_role_with_web_identity.call_args.kwargs
        assert call_kwargs["WebIdentityToken"] == token
        assert "RoleArn" in call_kwargs
        assert call_kwargs["RoleSessionName"].startswith("claude-code-")

    def test_output_format_matches_aws_credential_process(self, oidc_server, integ_profile):
        """get_aws_credentials_direct returns Version=1 dict with all required credential keys."""
        token, claims = _get_jwt_and_claims(oidc_server)

        mock_sts = MagicMock()
        mock_sts.assume_role_with_web_identity.return_value = _fake_sts_response()

        with patch("boto3.client", return_value=mock_sts):
            auth = MultiProviderAuth(profile=integ_profile)
            result = auth.get_aws_credentials_direct(token, claims)

        assert result["Version"] == 1
        assert "AccessKeyId" in result
        assert "SecretAccessKey" in result
        assert "SessionToken" in result
        assert "Expiration" in result

    def test_role_session_name_is_sanitized(self, oidc_server, integ_profile):
        """RoleSessionName only contains characters valid per AWS regex [\\w+=,.@-]*"""
        token, claims = _get_jwt_and_claims(oidc_server)

        mock_sts = MagicMock()
        mock_sts.assume_role_with_web_identity.return_value = _fake_sts_response()

        with patch("boto3.client", return_value=mock_sts):
            auth = MultiProviderAuth(profile=integ_profile)
            auth.get_aws_credentials_direct(token, claims)

        call_kwargs = mock_sts.assume_role_with_web_identity.call_args.kwargs
        session_name = call_kwargs["RoleSessionName"]
        invalid_chars = re.sub(r"[\w+=,.@-]", "", session_name)
        assert invalid_chars == "", f"RoleSessionName '{session_name}' contains invalid chars: '{invalid_chars}'"
