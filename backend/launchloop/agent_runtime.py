from dataclasses import dataclass
from typing import Any

from .models import AgentRun

DETERMINISTIC_HERMES_PROVIDER = "deterministic_hermes"


@dataclass(frozen=True)
class SpecialistResult:
    specialist: str
    analyzing_message: str
    summary: str


class DeterministicHermesAdapter:
    """Tool-free adapter used until a configured Hermes provider is enabled.

    The adapter intentionally produces only safe run summaries. Deterministic
    application code remains responsible for package creation, policy checks,
    workflow state, and all external-action boundaries.
    """

    provider = DETERMINISTIC_HERMES_PROVIDER

    def run_specialists(self, package: dict[str, Any]) -> tuple[SpecialistResult, ...]:
        missing_fields = package["missing_fields"]
        audience = package["audience"]
        sponsor = package["sponsor"]
        return (
            SpecialistResult(
                specialist=AgentRun.Specialist.EVENT_READINESS,
                analyzing_message="Checking required event facts against the revision.",
                summary=(
                    "All required event facts are grounded."
                    if not missing_fields
                    else f"{len(missing_fields)} event facts need an operator response."
                ),
            ),
            SpecialistResult(
                specialist=AgentRun.Specialist.CAMPAIGN_COMPOSER,
                analyzing_message="Preparing review-only campaign drafts.",
                summary="Prepared invitation, reminder, and social drafts for review.",
            ),
            SpecialistResult(
                specialist=AgentRun.Specialist.AUDIENCE_POLICY,
                analyzing_message="Matching the approved audience and checking sponsor policy.",
                summary=(
                    "Matched the approved audience and validated sponsor pricing."
                    if audience["id"] and sponsor["passed"]
                    else "Audience or sponsor policy needs human review."
                ),
            ),
        )
