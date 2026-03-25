"""Integration test fixtures: Docker OIDC server lifecycle and test profile management."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import requests

OIDC_URL = "http://localhost:8080"
COMPOSE_FILE = Path(__file__).parent / "docker-compose.yml"
CCWB_CONFIG_PATH = Path.home() / "claude-code-with-bedrock" / "config.json"


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires Docker (oidc-server-mock)")
    config.addinivalue_line("markers", "aws: requires AWS credentials")
    config.addinivalue_line("markers", "slow: calls Bedrock invoke_model (costs tokens)")


def _is_oidc_up() -> bool:
    """Return True if OIDC server is already responding."""
    try:
        resp = requests.get(f"{OIDC_URL}/.well-known/openid-configuration", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def _wait_for_oidc(timeout: int = 90) -> bool:
    """Poll OIDC discovery endpoint until ready or timeout. Returns True if ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_oidc_up():
            return True
        time.sleep(1)
    return False


@pytest.fixture(scope="session")
def oidc_server():
    """Start oidc-server-mock via Docker Compose, wait for readiness, yield base URL."""
    # If already up (e.g. from a previous test run left running), skip the docker up step
    already_running = _is_oidc_up()

    if not already_running:
        result = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"],
            capture_output=True,
        )
        if result.returncode != 0:
            pytest.fail(
                f"docker compose up failed:\n{result.stderr.decode()}\n{result.stdout.decode()}"
            )

        if not _wait_for_oidc(timeout=90):
            subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), "down"], capture_output=True)
            pytest.fail("oidc-server-mock did not become ready within 90 seconds")

    yield OIDC_URL

    if not already_running:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down"],
            capture_output=True,
        )


@pytest.fixture(scope="session")
def integ_profile(oidc_server):
    """Write a temporary ccwb config for the integration test profile, yield the profile name."""
    profile_name = "integ-test"
    role_arn = os.environ.get("CCWB_INTEG_ROLE_ARN", "arn:aws:iam::123456789012:role/integ-test-role")

    test_profile_config = {
        "provider_domain": "localhost:8080",
        "client_id": "ccwb-integ",
        "federation_type": "direct",
        "federated_role_arn": role_arn,
        "provider_type": "okta",
        "aws_region": "us-east-1",
    }

    # Back up existing config if present
    backup = None
    if CCWB_CONFIG_PATH.exists():
        backup = CCWB_CONFIG_PATH.read_text()
        existing = json.loads(backup)
        # Merge our test profile into the existing profiles dict
        if "profiles" in existing:
            existing["profiles"][profile_name] = test_profile_config
        else:
            existing = {"profiles": {profile_name: test_profile_config, **existing}}
        CCWB_CONFIG_PATH.write_text(json.dumps(existing, indent=2))
    else:
        CCWB_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CCWB_CONFIG_PATH.write_text(json.dumps({"profiles": {profile_name: test_profile_config}}, indent=2))

    yield profile_name

    # Teardown: restore original config
    if backup is not None:
        CCWB_CONFIG_PATH.write_text(backup)
    else:
        CCWB_CONFIG_PATH.unlink(missing_ok=True)
