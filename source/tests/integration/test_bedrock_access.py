"""Tier 2: real Bedrock access tests (requires AWS_PROFILE in environment)."""

import json
import os

import boto3
import pytest

if "AWS_PROFILE" not in os.environ:
    pytest.skip("AWS_PROFILE not set — skipping Tier 2 Bedrock tests", allow_module_level=True)


@pytest.mark.aws
def test_list_foundation_models():
    """list_foundation_models returns at least one Claude model."""
    client = boto3.client("bedrock")
    resp = client.list_foundation_models()
    model_ids = [m["modelId"] for m in resp.get("modelSummaries", [])]
    claude_models = [m for m in model_ids if "claude" in m.lower()]
    assert len(claude_models) > 0, f"No Claude models found. Got: {model_ids[:10]}"


@pytest.mark.aws
@pytest.mark.slow
def test_invoke_model():
    """invoke_model with a 1-token prompt returns valid JSON with a content key."""
    client = boto3.client("bedrock-runtime")
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "Hi"}],
        }
    )
    resp = client.invoke_model(
        modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    response_body = json.loads(resp["body"].read())
    assert "content" in response_body, f"Unexpected response shape: {response_body}"
