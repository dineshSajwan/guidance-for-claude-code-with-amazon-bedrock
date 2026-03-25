# Integration Test Suite — oidc-server-mock

## Purpose

This suite provides automated integration testing for the OIDC → STS → Bedrock credential
chain. It replaces the interactive Okta login with a local Docker-hosted IdP
([oidc-server-mock](https://github.com/Soluto/oidc-server-mock)) so the credential provider
logic can be exercised in CI without a browser, a real IdP, or building the PyInstaller binary.

The binary is only required for end-user distribution. Contributors run
`python -m credential_provider` directly; these tests call library code directly.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Tier 1  @pytest.mark.integration  (Docker only, ~20s)          │
│                                                                 │
│  test_oidc_token.py          oidc-server-mock (localhost:8080)  │
│    ROPC password grant  ─────►  id_token + access_token         │
│    JWT decode (no sig)  ◄─────  iss / sub / email claims        │
│    GET /.well-known/jwks ─────► keys[].{kty,kid,n,e}            │
│                                                                 │
│  test_credential_provider.py                                    │
│    ROPC token  ──► MultiProviderAuth.get_aws_credentials_direct │
│                         │  boto3.client patched (MagicMock)     │
│                         ▼                                       │
│                    STS call args asserted                       │
│                    credential_process dict asserted             │
│                    RoleSessionName sanitization asserted        │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Tier 2  @pytest.mark.aws  (requires AWS_PROFILE)               │
│                                                                 │
│  test_bedrock_access.py                                         │
│    boto3 bedrock.list_foundation_models  → Claude model present │
│    boto3 bedrock-runtime.invoke_model    → valid JSON response  │
│      (@pytest.mark.slow — costs tokens)                         │
└─────────────────────────────────────────────────────────────────┘
```

**Key constraint:** AWS STS must be able to reach the OIDC server's JWKS endpoint to verify
the `id_token` signature. Because `localhost` is unreachable from AWS, the full
OIDC → real STS chain is not automated here — it is covered by the interactive `ccwb test`
command. Tier 1 mocks STS to test everything else.

---

## File Structure

```
source/tests/integration/
├── docker-compose.yml           Docker service for oidc-server-mock (port 8080→80)
├── oidc_config/
│   ├── server.json              Issuer URI = http://localhost:8080, no response cache
│   ├── users.json               Test user: test@example.com / testpass123 / sub=test-user-001
│   └── clients.json             Client: ccwb-integ, password+code grants, no PKCE required
├── conftest.py                  Session fixtures (Docker lifecycle + test config)
├── test_oidc_token.py           Tier 1 — OIDC token flow
├── test_credential_provider.py  Tier 1 — credential provider + mocked STS
└── test_bedrock_access.py       Tier 2 — real Bedrock API
```

---

## What Each Test Does

### `test_oidc_token.py` — Tier 1

> **Note:** Duende IdentityServer 7.x (used by oidc-server-mock latest) no longer returns `id_token`
> in the ROPC (`password` grant) response — ROPC is deprecated by OAuth 2.1. The `access_token` is
> itself a signed JWT containing the same identity claims, so tests decode it directly.
> The production credential provider uses the PKCE `authorization_code` flow which does return an
> `id_token`; that end-to-end chain is covered by `ccwb test`.

| Test | What it verifies |
|------|-----------------|
| `test_ropc_returns_access_token` | POST to `/connect/token` returns HTTP 200 with `access_token` and `email` in granted scope |
| `test_access_token_claims` | Decoded `access_token` JWT has `iss=http://localhost:8080`, `sub=test-user-001`, `client_id=ccwb-integ` |
| `test_jwks_endpoint` | GET `/.well-known/openid-configuration/jwks` returns non-empty `keys` array; each key has `kty`, `kid`, `n`, `e` |
| `test_userinfo_endpoint_returns_email` | GET `/connect/userinfo` with bearer token returns `sub=test-user-001`, `email=test@example.com` |

### `test_credential_provider.py` — Tier 1

| Test | What it verifies |
|------|-----------------|
| `test_sts_called_with_correct_parameters` | `MultiProviderAuth.get_aws_credentials_direct` calls `AssumeRoleWithWebIdentity` with correct `WebIdentityToken`, `RoleArn`, and a `RoleSessionName` starting with `claude-code-` |
| `test_output_format_matches_aws_credential_process` | Return value has `Version=1` and keys `AccessKeyId`, `SecretAccessKey`, `SessionToken`, `Expiration` |
| `test_role_session_name_is_sanitized` | `RoleSessionName` only contains characters matching the AWS regex `[\w+=,.@-]*` |

STS is replaced with a `MagicMock` — no real AWS credentials are needed for Tier 1.

### `test_bedrock_access.py` — Tier 2

| Test | What it verifies |
|------|-----------------|
| `test_list_foundation_models` | `bedrock.list_foundation_models()` succeeds and includes at least one Claude model |
| `test_invoke_model` (`@slow`) | `bedrock-runtime.invoke_model` with a 1-token prompt returns valid JSON containing a `content` key |

The entire module is skipped automatically if `AWS_PROFILE` is not set.

---

## Fixtures (`conftest.py`)

### `oidc_server` (session-scoped)
1. Checks if `http://localhost:8080/.well-known/openid-configuration` is already reachable
   (idempotent — skips `docker compose up` and teardown if already running).
2. If not running, executes `docker compose up -d` using `docker-compose.yml` in this directory.
3. Polls the OIDC discovery endpoint every second for up to 90 s (container starts in ~8 s on Apple Silicon).
4. Yields `"http://localhost:8080"`.
5. Runs `docker compose down` on session teardown (only if it started the container).

### `integ_profile` (session-scoped, depends on `oidc_server`)
1. Writes (or merges) a test profile named `"integ-test"` into
   `~/claude-code-with-bedrock/config.json` — the location `MultiProviderAuth._load_config()`
   reads from.
2. The profile includes `provider_domain=localhost:8080`, `client_id=ccwb-integ`,
   `federation_type=direct`, `federated_role_arn` (from `CCWB_INTEG_ROLE_ARN` env var, or a
   dummy ARN for Tier 1), and `provider_type=okta` (bypasses domain auto-detection).
3. Yields `"integ-test"`.
4. Restores the original config file on teardown.

---

## Prerequisites

| Requirement | Tier 1 | Tier 2 |
|-------------|--------|--------|
| Docker (with Compose v2) | Required | Not needed |
| `poetry install` | Required | Required |
| `AWS_PROFILE` environment variable | Not needed | Required |
| `AWS_DEFAULT_REGION` environment variable | Not needed | Required (e.g. `us-east-1`) |
| `CCWB_INTEG_ROLE_ARN` environment variable | Optional (dummy used) | Optional |

---

## How to Run

```bash
cd source

# Tier 1 only — OIDC token flow + mocked STS (needs Docker, ~20s)
poetry run pytest tests/integration/ -m integration -v

# Tier 2 only — real Bedrock API (needs AWS credentials)
AWS_PROFILE=dinsajwa AWS_DEFAULT_REGION=us-east-1 \
  poetry run pytest tests/integration/ -m aws -v

# Tier 2 including the slow invoke_model test
AWS_PROFILE=dinsajwa AWS_DEFAULT_REGION=us-east-1 \
  poetry run pytest tests/integration/ -m "aws or slow" -v

# Both tiers together
AWS_PROFILE=dinsajwa AWS_DEFAULT_REGION=us-east-1 \
  poetry run pytest tests/integration/ -v

# Full test suite (unit + integration, excluding known e2e test)
AWS_PROFILE=dinsajwa AWS_DEFAULT_REGION=us-east-1 \
  poetry run pytest tests/ --ignore=tests/cli/commands/test_init_e2e.py -v
```

### Running only specific test files

```bash
# OIDC token tests only
poetry run pytest tests/integration/test_oidc_token.py -v

# Credential provider tests only
poetry run pytest tests/integration/test_credential_provider.py -v
```

### Stopping Docker after a failed run

If a test run is interrupted before teardown:

```bash
cd source/tests/integration
docker compose down
```

---

## Environment Variables

| Variable | Used by | Default | Notes |
|----------|---------|---------|-------|
| `CCWB_INTEG_ROLE_ARN` | `integ_profile` fixture | `arn:aws:iam::123456789012:role/integ-test-role` | Dummy ARN is fine for Tier 1 (STS is mocked) |
| `AWS_PROFILE` | Tier 2 tests | — | Module-level skip if absent |
| `AWS_DEFAULT_REGION` | Tier 2 tests | — | Required for boto3 |

---

## Troubleshooting

**`oidc-server-mock did not become ready within 90 seconds`**
- Ensure Docker Desktop is running: `docker info`
- Check if the container crashed: `docker ps -a | grep integration`
- Inspect container logs: `cd source/tests/integration && docker compose logs`
- Pull the latest image: `docker pull ghcr.io/soluto/oidc-server-mock:latest`
- The compose file pins `platform: linux/amd64` — required on Apple Silicon (M1/M2/M3). The ARM64 build crashes with SIGSEGV on macOS/Docker Desktop.

**`Profile 'integ-test' not found in configuration`**
- The `integ_profile` fixture may have failed to write the config. Check that
  `~/claude-code-with-bedrock/` is writable.

**`Unable to auto-detect provider type for domain 'localhost:8080'`**
- The config written by `integ_profile` must include `"provider_type": "okta"` — this is
  already set in the fixture. If you see this error, check the config written to
  `~/claude-code-with-bedrock/config.json`.

**Tier 2: `NoRegionError` or `NoCredentialsError`**
- Ensure `AWS_DEFAULT_REGION` and `AWS_PROFILE` are exported before running.
