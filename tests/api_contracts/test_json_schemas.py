import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas"

SCHEMAS = {
    "hermes_run_request": "agents/hermes-run-request.schema.json",
    "hermes_run_result": "agents/hermes-run-result.schema.json",
    "model_gateway_profile": "agents/model-gateway-profile.schema.json",
    "workflow_capability": "integrations/workflow-capability.schema.json",
    "draft_operation": "integrations/draft-operation.schema.json",
}

EXPECTED_RUNTIME_RULE_IDS = {
    "draft_operation": {
        "civicloop.draft_operation.v1.action_digest_matches_approval",
        "civicloop.draft_operation.v1.approval_precedes_execution",
        "civicloop.draft_operation.v1.distinct_operator_approver",
        "civicloop.draft_operation.v1.revision_matches_approval",
    },
    "workflow_capability": {
        "civicloop.workflow_capability.v1.issued_before_expires",
        "civicloop.workflow_capability.v1.not_expired_at_use",
        "civicloop.workflow_capability.v1.not_revoked_at_use",
        "civicloop.workflow_capability.v1.ttl_matches_timestamps",
    },
}


def _load_schema(name: str) -> dict[str, object]:
    with (SCHEMA_ROOT / SCHEMAS[name]).open(encoding="utf-8") as source:
        return json.load(source)


def _validator(name: str) -> Draft202012Validator:
    schema = _load_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _valid_payloads() -> dict[str, dict[str, object]]:
    return {
        "hermes_run_request": {
            "schema_version": "1.0",
            "workflow_id": "843a756b-b9a4-4fb7-89ee-05be3f38fc6d",
            "revision_id": "9db20441-e440-4708-a60c-8bc332410ce2",
            "actor_id": "54279488-44c8-40de-a18e-85ae9b42866a",
            "correlation_id": "f273616f-2517-40b2-869f-87d5e71af8ea",
            "capability_token": "cap_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH",
            "model_alias": "event-draft-primary",
            "budgets": {
                "max_input_tokens": 16000,
                "max_output_tokens": 4000,
                "max_cost_microusd": 250000,
                "timeout_seconds": 120,
            },
        },
        "hermes_run_result": {
            "schema_version": "1.0",
            "run_id": "fa464cc0-5330-475c-bad4-06f79f1c16a4",
            "workflow_id": "843a756b-b9a4-4fb7-89ee-05be3f38fc6d",
            "revision_id": "9db20441-e440-4708-a60c-8bc332410ce2",
            "status": "succeeded",
            "proposal_references": [
                {
                    "proposal_id": "bd8b30ba-d2e2-4635-aa1f-e1f946079ffc",
                    "schema_id": "urn:civicloop:schema:proposals:event-draft:v1.0",
                    "proposal_digest": "a" * 64,
                }
            ],
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 300,
                "cost_microusd": 12000,
            },
            "failure_category": None,
        },
        "model_gateway_profile": {
            "schema_version": "1.0",
            "profile_id": "event-draft-primary",
            "revision": 1,
            "model_alias": "event-draft-primary",
            "base_url": "http://litellm:4000/v1",
            "timeout_seconds": 120,
            "budgets": {
                "max_input_tokens": 16000,
                "max_output_tokens": 4000,
                "max_cost_microusd": 250000,
            },
        },
        "workflow_capability": {
            "schema_version": "1.0",
            "capability_id": "b21bfd11-e61b-464f-9438-92e10887db27",
            "token_digest": "b" * 64,
            "workflow_id": "843a756b-b9a4-4fb7-89ee-05be3f38fc6d",
            "revision_id": "9db20441-e440-4708-a60c-8bc332410ce2",
            "tools": ["get_event_revision", "validate_proposal"],
            "audience": "civicloop-hermes",
            "issued_at": "2026-09-13T12:00:00Z",
            "expires_at": "2026-09-13T12:05:00Z",
            "ttl_seconds": 300,
            "revoked_at": None,
        },
        "draft_operation": {
            "schema_version": "1.0",
            "operation_id": "8914fc41-e10f-4812-ac63-da3ab33eed25",
            "workflow_id": "843a756b-b9a4-4fb7-89ee-05be3f38fc6d",
            "revision_id": "9db20441-e440-4708-a60c-8bc332410ce2",
            "provider": "eventbrite",
            "operation_kind": "create_eventbrite_draft",
            "action_digest": "c" * 64,
            "idempotency_key": "draft-8914fc41-e10f-4812-ac63-da3ab33eed25",
            "approval": {
                "approval_id": "64103a07-1db2-43ea-b5c0-7512f2b420fd",
                "submitted_by_actor_id": "54279488-44c8-40de-a18e-85ae9b42866a",
                "approved_by_actor_id": "511b9297-ff78-4790-bca7-f2d56fd1f3cb",
                "approved_revision_id": "9db20441-e440-4708-a60c-8bc332410ce2",
                "approved_action_digest": "c" * 64,
                "four_eyes_verified": True,
                "approved_at": "2026-09-13T12:10:00Z",
            },
            "status": "succeeded",
            "receipt": {
                "receipt_type": "eventbrite_draft",
                "provider_request_id": "req_01K50EXAMPLE",
                "provider_resource_id": "evt_01K50EXAMPLE",
                "reconciliation_status": "confirmed",
                "recorded_at": "2026-09-13T12:11:00Z",
            },
        },
    }


@pytest.mark.parametrize("name", SCHEMAS)
def test_contract_accepts_complete_payload_and_rejects_unknown_properties(name: str) -> None:
    validator = _validator(name)
    payload = _valid_payloads()[name]
    validator.validate(payload)

    payload_with_unknown = deepcopy(payload)
    payload_with_unknown["unexpected"] = True
    with pytest.raises(ValidationError):
        validator.validate(payload_with_unknown)


@pytest.mark.parametrize(
    ("name", "required_binding"),
    [
        ("hermes_run_request", "workflow_id"),
        ("hermes_run_request", "revision_id"),
        ("hermes_run_request", "actor_id"),
        ("hermes_run_request", "correlation_id"),
        ("hermes_run_request", "capability_token"),
        ("hermes_run_request", "model_alias"),
        ("hermes_run_request", "budgets"),
        ("workflow_capability", "workflow_id"),
        ("workflow_capability", "revision_id"),
        ("workflow_capability", "tools"),
        ("workflow_capability", "audience"),
        ("workflow_capability", "expires_at"),
        ("workflow_capability", "revoked_at"),
        ("draft_operation", "action_digest"),
        ("draft_operation", "idempotency_key"),
        ("draft_operation", "approval"),
        ("draft_operation", "receipt"),
    ],
)
def test_contract_rejects_missing_required_binding(name: str, required_binding: str) -> None:
    payload = _valid_payloads()[name]
    del payload[required_binding]
    with pytest.raises(ValidationError):
        _validator(name).validate(payload)


def test_capability_is_short_lived_tool_scoped_audience_bound_and_revocable() -> None:
    schema = _load_schema("workflow_capability")
    validator = _validator("workflow_capability")
    payload = _valid_payloads()["workflow_capability"]

    payload["ttl_seconds"] = 301
    with pytest.raises(ValidationError):
        validator.validate(payload)

    payload = _valid_payloads()["workflow_capability"]
    payload["tools"] = ["terminal"]
    with pytest.raises(ValidationError):
        validator.validate(payload)

    payload = _valid_payloads()["workflow_capability"]
    payload["audience"] = "general-purpose-agent"
    with pytest.raises(ValidationError):
        validator.validate(payload)

    payload = _valid_payloads()["workflow_capability"]
    payload["revoked_at"] = "2026-09-13T12:01:00Z"
    validator.validate(payload)
    assert "current time is at or after expires_at" in schema["$comment"]


@pytest.mark.parametrize("name", ["workflow_capability", "draft_operation"])
def test_security_digests_are_lowercase_sha256(name: str) -> None:
    payload = _valid_payloads()[name]
    digest_field = "token_digest" if name == "workflow_capability" else "action_digest"
    payload[digest_field] = "SHA256:not-an-exact-digest"
    with pytest.raises(ValidationError):
        _validator(name).validate(payload)


def test_draft_operation_requires_exact_approved_digest_and_typed_success_receipt() -> None:
    schema = _load_schema("draft_operation")
    validator = _validator("draft_operation")
    payload = _valid_payloads()["draft_operation"]

    payload["approval"]["approved_action_digest"] = "D" * 64
    with pytest.raises(ValidationError):
        validator.validate(payload)

    payload = _valid_payloads()["draft_operation"]
    payload["receipt"] = None
    with pytest.raises(ValidationError):
        validator.validate(payload)

    payload = _valid_payloads()["draft_operation"]
    payload["operation_kind"] = "publish_event"
    with pytest.raises(ValidationError):
        validator.validate(payload)

    assert "action_digest equals approval.approved_action_digest" in schema["$comment"]
    assert "submitting and approving actors are distinct" in schema["$comment"]


@pytest.mark.parametrize(
    ("provider", "operation_kind", "receipt_type"),
    [
        ("eventbrite", "create_eventbrite_draft", "iterable_draft"),
        ("iterable", "create_iterable_email_draft", "eventbrite_draft"),
    ],
)
def test_draft_operation_rejects_receipt_type_for_the_other_provider(
    provider: str, operation_kind: str, receipt_type: str
) -> None:
    payload = _valid_payloads()["draft_operation"]
    payload["provider"] = provider
    payload["operation_kind"] = operation_kind
    payload["receipt"]["receipt_type"] = receipt_type

    with pytest.raises(ValidationError):
        _validator("draft_operation").validate(payload)


@pytest.mark.parametrize("name", EXPECTED_RUNTIME_RULE_IDS)
def test_cross_field_and_current_time_rules_have_stable_machine_readable_ids(name: str) -> None:
    schema = _load_schema(name)
    rules = schema["x-civicloop-runtime-rules"]

    assert {rule["id"] for rule in rules} == EXPECTED_RUNTIME_RULE_IDS[name]
    for rule in rules:
        assert set(rule) == {"id", "enforcement", "failure_behavior", "requirement"}
        assert rule["enforcement"] == "runtime_required"
        assert rule["failure_behavior"] == "reject"
        assert rule["requirement"]


def test_revoked_capability_rule_is_unconditional_and_fail_closed() -> None:
    schema = _load_schema("workflow_capability")
    rules = {rule["id"]: rule for rule in schema["x-civicloop-runtime-rules"]}
    revoked = rules["civicloop.workflow_capability.v1.not_revoked_at_use"]

    assert revoked["requirement"] == (
        "Reject every use when revoked_at is non-null, regardless of issued_at, "
        "expires_at, or ttl_seconds."
    )
    assert revoked["failure_behavior"] == "reject"


def test_result_and_operation_contracts_prohibit_raw_sensitive_content_fields() -> None:
    forbidden = {
        "prompt",
        "response",
        "raw_model_text",
        "provider_draft_body",
        "constituent_data",
        "credentials",
        "authorization_headers",
    }
    for name in ("hermes_run_result", "draft_operation"):
        schema_text = json.dumps(_load_schema(name)).lower()
        for field in forbidden:
            assert f'"{field}"' not in schema_text


def test_model_gateway_profile_is_provider_neutral() -> None:
    schema = _load_schema("model_gateway_profile")
    properties = schema["properties"]
    assert {"model_alias", "base_url", "timeout_seconds", "budgets"} <= properties.keys()
    for provider_specific_field in ("provider", "api_key", "credential"):
        assert provider_specific_field not in properties
    assert "openai" not in json.dumps(schema).lower()


def test_all_new_contract_object_shapes_are_closed_and_indexed() -> None:
    index = (SCHEMA_ROOT / "README.md").read_text(encoding="utf-8")
    for relative_path in SCHEMAS.values():
        schema = _load_schema(next(name for name, path in SCHEMAS.items() if path == relative_path))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert str(schema["$id"]).endswith(":v1.0")
        _assert_object_shapes_are_closed(schema)
        assert relative_path in index
        assert schema["$id"] in index


def _assert_object_shapes_are_closed(value: object) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
        for nested in value.values():
            _assert_object_shapes_are_closed(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_object_shapes_are_closed(nested)
