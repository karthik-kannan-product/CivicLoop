from __future__ import annotations

import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from agents.budgets import (
    BudgetError,
    BudgetExhausted,
    release_budget,
    reserve_budget,
    settle_budget,
)
from agents.models import AgentRun, AgentStep, ModelProfile, RoutingPolicy
from django.core.exceptions import ValidationError
from django.utils import timezone
from integrations.exceptions import SecretUnavailable
from integrations.models import ConnectionState, IntegrationConnection
from integrations.secret_store import PostgresSecretStore
from integrations.types import SecretLease, SecretReference
from launchloop.models import Workflow
from observability.launchloop import workflow_operation, workflow_stage
from openinference.semconv.trace import OpenInferenceSpanKindValues

from evaluations.models import EvaluationResult

PROFILE_ID = "launchloop_openai_judge"
PROFILE_REVISION = 1
RUBRIC_ID = "launchloop_package_quality"
RUBRIC_VERSION = 1
PROMPT_REFERENCE = "launchloop_package_judge"
PROMPT_VERSION = 1
POLICY_ID = "launchloop_advisory_evaluation"
POLICY_VERSION = 1
DATASET_ID = "launchloop_synthetic_v1"
DATASET_VERSION = 3
FIXTURE_MANIFEST_DIGEST = "815d14762306d96bdc6449eb58a3e5739fb0ad95e08dc70b9025bd2cc8099d5f"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_RESPONSE_BYTES = 64 * 1024
TIMEOUT_SECONDS = 20
ALLOWED_LABELS = frozenset(
    {"expected_output", "policy_violation", "schema_invalid", "tool_error", "timeout"}
)


@dataclass(frozen=True)
class JudgeResponse:
    outcome: str
    score: float
    labels: list[str]
    rationale: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class JudgeClient(Protocol):
    def evaluate(
        self, *, credential: SecretLease, package: dict[str, Any], model: str
    ) -> JudgeResponse: ...


class JudgeProviderError(RuntimeError):
    pass


class JudgeOutputError(RuntimeError):
    pass


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class OpenAIResponsesJudgeClient:
    def evaluate(
        self, *, credential: SecretLease, package: dict[str, Any], model: str
    ) -> JudgeResponse:
        return credential.use(
            lambda scoped: self._request(scoped_credential=scoped, package=package, model=model)
        )

    def _request(
        self, *, scoped_credential: memoryview, package: dict[str, Any], model: str
    ) -> JudgeResponse:
        credential_text = str(scoped_credential, "utf-8")
        if not credential_text or "\r" in credential_text or "\n" in credential_text:
            raise JudgeProviderError()
        payload = {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "Evaluate this synthetic, review-only CivicLoop package. Apply rubric "
                        "launchloop_package_quality version 1. Return only the required schema. "
                        "This result is advisory and cannot approve or execute actions."
                    ),
                },
                {"role": "user", "content": json.dumps(package, sort_keys=True)},
            ],
            "max_output_tokens": 256,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "civicloop_evaluation",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["outcome", "score", "labels", "rationale"],
                        "properties": {
                            "outcome": {"enum": ["passed", "failed", "inconclusive"]},
                            "score": {"type": "number", "minimum": 0, "maximum": 1},
                            "labels": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 5,
                                "uniqueItems": True,
                                "items": {"enum": sorted(ALLOWED_LABELS)},
                            },
                            "rationale": {"type": "string", "maxLength": 500},
                        },
                    },
                }
            },
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {
            "Authorization": f"Bearer {credential_text}",
            "Content-Type": "application/json",
        }
        request = Request(OPENAI_RESPONSES_URL, data=body, headers=headers, method="POST")
        started = time.monotonic()
        try:
            with build_opener(_NoRedirects()).open(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if response.status != 200 or len(raw) > MAX_RESPONSE_BYTES:
                    raise JudgeProviderError()
        except (HTTPError, URLError, TimeoutError, OSError):
            raise JudgeProviderError() from None
        finally:
            headers.clear()
            request.headers.clear()
            request.unredirected_hdrs.clear()
            credential_text = ""
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            response_payload = json.loads(raw)
            output_text = next(
                content["text"]
                for item in response_payload["output"]
                for content in item.get("content", [])
                if content.get("type") == "output_text"
            )
            result = json.loads(output_text)
            usage = response_payload["usage"]
            return _validated_response(
                JudgeResponse(
                    outcome=result["outcome"],
                    score=result["score"],
                    labels=result["labels"],
                    rationale=result["rationale"],
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    latency_ms=latency_ms,
                )
            )
        except (KeyError, TypeError, ValueError, StopIteration, json.JSONDecodeError):
            raise JudgeOutputError() from None


def _validated_response(response: JudgeResponse) -> JudgeResponse:
    valid = (
        response.outcome in EvaluationResult.Outcome.values
        and isinstance(response.score, int | float)
        and not isinstance(response.score, bool)
        and 0 <= response.score <= 1
        and isinstance(response.labels, list)
        and 1 <= len(response.labels) <= 5
        and len(response.labels) == len(set(response.labels))
        and set(response.labels).issubset(ALLOWED_LABELS)
        and isinstance(response.rationale, str)
        and 1 <= len(response.rationale) <= 500
        and isinstance(response.input_tokens, int)
        and 0 <= response.input_tokens <= 4096
        and isinstance(response.output_tokens, int)
        and 0 <= response.output_tokens <= 256
        and isinstance(response.latency_ms, int)
        and 0 <= response.latency_ms <= 3_600_000
    )
    if not valid:
        raise JudgeOutputError()
    return response


def _minimized_package(workflow: Workflow) -> dict[str, Any]:
    package = workflow.package or {}
    return {
        key: package.get(key)
        for key in ("status", "assets", "audience", "sponsor", "evidence")
    }


def _secret_reference(connection: IntegrationConnection) -> SecretReference:
    if connection.secret is None:
        raise SecretUnavailable()
    return SecretReference(
        id=connection.secret.id,
        provider=connection.secret.provider,
        scope=connection.secret.scope,
        version=connection.secret.version,
    )


def _profile_and_policy() -> tuple[ModelProfile, RoutingPolicy]:
    profile = ModelProfile.objects.get(profile_id=PROFILE_ID, revision=PROFILE_REVISION)
    policy = RoutingPolicy.objects.get(model_profile=profile, purpose=profile.purpose)
    return profile, policy


def _new_run(workflow: Workflow) -> AgentRun:
    profile, policy = _profile_and_policy()
    traceparent = workflow.telemetry_traceparent
    trace_id = (
        traceparent.split("-")[1]
        if re.fullmatch(r"00-[a-f0-9]{32}-[a-f0-9]{16}-[a-f0-9]{2}", traceparent)
        else secrets.token_hex(16)
    )
    return AgentRun.objects.create(
        workflow=workflow,
        event_revision=workflow.revision,
        package_hash=workflow.package_hash,
        routing_policy=policy,
        model_profile=profile,
        fixture_manifest_id=DATASET_ID,
        fixture_manifest_revision=DATASET_VERSION,
        fixture_manifest_digest=FIXTURE_MANIFEST_DIGEST,
        privacy_mode=(
            AgentRun.PrivacyMode.PILOT_MINIMIZED
            if workflow.revision.source_snapshot_id
            else AgentRun.PrivacyMode.SYNTHETIC_FULL
        ),
        status=AgentRun.Status.QUEUED,
        attempt=1,
        trace_id=trace_id,
    )


def _fail(run: AgentRun, category: str) -> AgentRun:
    release_budget(run_id=run.id)
    now = timezone.now()
    step = run.steps.filter(sequence=1).first()
    if step is not None and step.status == AgentStep.Status.RUNNING:
        step.status = AgentStep.Status.FAILED
        step.finished_at = now
        step.output_summary = "Advisory evaluation unavailable; package remains reviewable."
        step.failure_category = (
            AgentStep.FailureCategory.INVALID_OUTPUT
            if category == "invalid_output"
            else AgentStep.FailureCategory.PROVIDER_UNAVAILABLE
        )
        step.save()
    run.status = AgentRun.Status.FAILED
    run.started_at = run.started_at or now
    run.finished_at = now
    run.failure_category = category
    run.save()
    return run


def run_fixed_judge(
    workflow: Workflow, administrator: Any, *, client: JudgeClient | None = None
) -> AgentRun:
    if not workflow.package or not workflow.package_hash:
        raise ValueError("evaluation_package_unavailable")
    if workflow.revision.snapshot.get("synthetic") is not True:
        raise ValueError("evaluation_synthetic_only")
    existing = workflow.agent_runs.order_by("-created_at").first()
    if existing is not None and existing.package_hash == workflow.package_hash:
        return existing
    run = _new_run(workflow)
    try:
        reserve_budget(
            run_id=run.id,
            profile_id=PROFILE_ID,
            profile_revision=PROFILE_REVISION,
            estimated_input_tokens=4096,
            estimated_output_tokens=256,
        )
    except BudgetExhausted:
        return _fail(run, AgentRun.FailureCategory.BUDGET_EXHAUSTED)
    except BudgetError:
        return _fail(run, AgentRun.FailureCategory.DEPENDENCY_UNAVAILABLE)

    now = timezone.now()
    run.status = AgentRun.Status.RUNNING
    run.started_at = now
    run.save()
    AgentStep.objects.create(
        run=run,
        sequence=1,
        kind=AgentStep.Kind.MODEL_INFERENCE,
        status=AgentStep.Status.RUNNING,
        started_at=now,
        input_summary="Minimized review package submitted to fixed advisory judge.",
    )
    try:
        with workflow_operation(workflow, "evaluation"):
            with workflow_stage(
                workflow,
                "launchloop.evaluation_judge",
                OpenInferenceSpanKindValues.EVALUATOR,
            ) as span:
                span.set_attribute("civicloop.model_profile_id", PROFILE_ID)
                span.set_attribute("civicloop.rubric_id", RUBRIC_ID)
                connection = IntegrationConnection.objects.select_related("secret").get(
                    provider="openai", state=ConnectionState.HEALTHY
                )
                with PostgresSecretStore().lease(
                    _secret_reference(connection),
                    caller_id=administrator.id,
                    workflow_id=workflow.id,
                    purpose="evaluation_judge",
                    ttl=timedelta(seconds=30),
                ) as credential:
                    response = _validated_response(
                        (client or OpenAIResponsesJudgeClient()).evaluate(
                            credential=credential,
                            package=_minimized_package(workflow),
                            model=run.model_profile.model,
                        )
                    )
                span.set_attribute("civicloop.outcome", response.outcome)
        reservation = settle_budget(
            run_id=run.id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        result = EvaluationResult.objects.create(
            run=run,
            evaluator=EvaluationResult.Evaluator.LLM_JUDGE,
            evaluator_profile=run.model_profile,
            outcome=response.outcome,
            score=Decimal(str(response.score)),
            reason_codes=response.labels,
            summary=response.rationale,
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            example_id=uuid.uuid5(uuid.NAMESPACE_URL, workflow.package_hash),
            prompt_reference=PROMPT_REFERENCE,
            prompt_version=PROMPT_VERSION,
            evaluation_policy_id=POLICY_ID,
            evaluation_policy_version=POLICY_VERSION,
            evaluated_schema_id="urn:civicloop:schema:launchloop:demo-state",
            evaluated_schema_version="1.0",
            candidate_id="launchloop_package",
            candidate_version=workflow.revision.version,
            judge={"config_id": PROFILE_ID},
            rubric_id=RUBRIC_ID,
            rubric_version=RUBRIC_VERSION,
            deterministic_checks=[{"check": "package_hash_bound", "passed": True}],
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_microusd=reservation.settled_cost_microusd or 0,
            latency_ms=response.latency_ms,
            trace_id=run.trace_id,
        )
    except IntegrationConnection.DoesNotExist:
        return _fail(run, AgentRun.FailureCategory.DEPENDENCY_UNAVAILABLE)
    except (JudgeOutputError, ValidationError, ValueError, TypeError):
        return _fail(run, AgentRun.FailureCategory.INVALID_OUTPUT)
    except (SecretUnavailable, JudgeProviderError, RuntimeError):
        return _fail(run, AgentRun.FailureCategory.PROVIDER_UNAVAILABLE)

    finished = timezone.now()
    step = run.steps.get(sequence=1)
    step.status = AgentStep.Status.SUCCEEDED
    step.finished_at = finished
    step.output_summary = "Schema-valid advisory evaluation recorded."
    step.duration_ms = response.latency_ms
    step.input_tokens = response.input_tokens
    step.output_tokens = response.output_tokens
    step.save()
    run.status = AgentRun.Status.SUCCEEDED
    run.finished_at = finished
    run.input_tokens = response.input_tokens
    run.output_tokens = response.output_tokens
    run.cost_microusd = result.cost_microusd
    run.span_count = 1
    run.save()
    return run
