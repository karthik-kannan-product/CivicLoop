"""Exact MCP input contracts and bounded validation without accepting arbitrary JSON."""

import json
import re
from uuid import UUID

MAX_PAYLOAD_BYTES = 65_536
MAX_DEPTH = 8


class InvalidToolArguments(Exception):
    def __init__(self):
        super().__init__("Invalid tool arguments.")


def obj(properties):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def string(maximum, **kwargs):
    return {"type": "string", "minLength": 1, "maxLength": maximum, **kwargs}


UUID_SCHEMA = string(36, format="uuid")
COMMON = {
    "workflow_id": UUID_SCHEMA,
    "revision_id": {"type": "integer", "minimum": 1},
    "actor_id": string(50, pattern=r"^[a-zA-Z0-9_-]+$"),
    "correlation_id": UUID_SCHEMA,
    "request_id": UUID_SCHEMA,
    "idempotency_key": string(128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"),
}
EMAIL = obj({"subject": string(240), "body": string(12000)})
PROPOSAL = obj(
    {
        "event_copy": string(12000),
        "invitation": EMAIL,
        "reminder": EMAIL,
        "social": obj({"body": string(4000)}),
    }
)
EXTRAS = {
    "get_event_revision": {},
    "get_policy_context": {},
    "request_clarification": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": obj({"field": string(64, pattern=r"^[a-z_]+$"), "question": string(500)}),
        }
    },
    "propose_campaign_drafts": {"proposal": PROPOSAL},
    "validate_proposal": {"proposal_id": UUID_SCHEMA},
    "request_eventbrite_draft": {"proposal_id": UUID_SCHEMA},
    "request_iterable_drafts": {"proposal_id": UUID_SCHEMA},
    "get_operation_status": {"operation_id": UUID_SCHEMA},
}
TOOL_SCHEMAS = {name: obj({**COMMON, **extra}) for name, extra in EXTRAS.items()}


def bounded_json(value):
    nodes = 0

    def walk(item, depth):
        nonlocal nodes
        nodes += 1
        if depth > MAX_DEPTH or nodes > 2048:
            raise InvalidToolArguments()
        if type(item) is dict:
            if len(item) > 100 or any(type(key) is not str or len(key) > 128 for key in item):
                raise InvalidToolArguments()
            for child in item.values():
                walk(child, depth + 1)
        elif type(item) is list:
            if len(item) > 100:
                raise InvalidToolArguments()
            for child in item:
                walk(child, depth + 1)
        elif type(item) is str:
            if len(item) > MAX_PAYLOAD_BYTES:
                raise InvalidToolArguments()
        elif item is not None and type(item) not in (int, bool, float):
            raise InvalidToolArguments()

    walk(value, 0)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
            raise InvalidToolArguments()
        return encoded
    except ValueError, TypeError, OverflowError, UnicodeError:
        raise InvalidToolArguments() from None


def validate(value, schema):
    expected = {"object": dict, "array": list, "string": str, "integer": int}[schema["type"]]
    if type(value) is not expected:
        raise InvalidToolArguments()
    if expected is dict:
        if set(value) != set(schema["properties"]):
            raise InvalidToolArguments()
        for key, child in schema["properties"].items():
            validate(value[key], child)
    elif expected is list:
        if not schema["minItems"] <= len(value) <= schema["maxItems"]:
            raise InvalidToolArguments()
        for child in value:
            validate(child, schema["items"])
    elif expected is str:
        if not schema["minLength"] <= len(value) <= schema["maxLength"]:
            raise InvalidToolArguments()
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise InvalidToolArguments()
        if schema.get("format") == "uuid":
            try:
                if str(UUID(value)) != value:
                    raise ValueError
            except ValueError:
                raise InvalidToolArguments() from None
    elif value < schema["minimum"] or value > 2**63 - 1:
        raise InvalidToolArguments()


def validate_tool_arguments(tool_name, arguments):
    encoded = bounded_json(arguments)
    if type(tool_name) is not str or tool_name not in TOOL_SCHEMAS:
        raise InvalidToolArguments()
    validate(arguments, TOOL_SCHEMAS[tool_name])
    return encoded
