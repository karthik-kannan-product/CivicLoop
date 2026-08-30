import re
import uuid

from agents.redaction import validate_safe_summary
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class EvaluationResult(models.Model):
    class Evaluator(models.TextChoices):
        DETERMINISTIC = "deterministic", "Deterministic"
        LLM_JUDGE = "llm_judge", "LLM judge"
        HUMAN_REVIEW = "human_review", "Human review"

    class Outcome(models.TextChoices):
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        INCONCLUSIVE = "inconclusive", "Inconclusive"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        "agents.AgentRun", related_name="evaluation_results", on_delete=models.PROTECT
    )
    evaluator = models.CharField(max_length=20, choices=Evaluator.choices)
    evaluator_profile = models.ForeignKey(
        "agents.ModelProfile", null=True, blank=True, on_delete=models.PROTECT
    )
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    score = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    reason_codes = models.JSONField()
    summary = models.CharField(max_length=1000)
    dataset_id = models.SlugField(max_length=64)
    dataset_version = models.PositiveIntegerField()
    example_id = models.UUIDField()
    prompt_reference = models.SlugField(max_length=64)
    prompt_version = models.PositiveIntegerField()
    evaluation_policy_id = models.SlugField(max_length=64)
    evaluation_policy_version = models.PositiveIntegerField()
    evaluated_schema_id = models.CharField(max_length=160)
    evaluated_schema_version = models.CharField(max_length=32)
    candidate_id = models.CharField(max_length=64)
    candidate_version = models.PositiveIntegerField()
    judge = models.JSONField(default=dict, blank=True)
    rubric_id = models.CharField(max_length=64)
    rubric_version = models.PositiveIntegerField()
    deterministic_checks = models.JSONField()
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_microusd = models.PositiveBigIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    trace_id = models.CharField(max_length=32)
    advisory_only = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(advisory_only=True), name="evaluations_always_advisory"
            ),
            models.CheckConstraint(
                condition=Q(latency_ms__lte=3_600_000), name="evaluations_latency_range"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id}: {self.evaluator}/{self.outcome}"

    def save(self, *args, **kwargs) -> None:
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("EvaluationResult records are immutable.")
        self.advisory_only = True
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        validate_safe_summary(self.summary)
        allowed_reason_codes = {
            "expected_output",
            "policy_violation",
            "schema_invalid",
            "tool_error",
            "timeout",
        }
        if not isinstance(self.reason_codes, list) or not self.reason_codes:
            raise ValidationError({"reason_codes": "At least one reason code is required."})
        if len(self.reason_codes) > 20 or len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValidationError({"reason_codes": "Reason codes must be unique and bounded."})
        if not set(self.reason_codes).issubset(allowed_reason_codes):
            raise ValidationError({"reason_codes": "Unknown reason code."})
        if self.evaluator == self.Evaluator.LLM_JUDGE and self.evaluator_profile_id is None:
            raise ValidationError({"evaluator_profile": "LLM judge results require a profile."})
        if self.evaluator != self.Evaluator.LLM_JUDGE and self.evaluator_profile_id is not None:
            raise ValidationError({"evaluator_profile": "Only LLM judge results use a profile."})
        identifier = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
        if self.evaluator == self.Evaluator.DETERMINISTIC:
            judge_valid = self.judge == {}
        elif self.evaluator == self.Evaluator.LLM_JUDGE:
            judge_valid = (
                set(self.judge) == {"config_id"}
                and isinstance(self.judge["config_id"], str)
                and identifier.fullmatch(self.judge["config_id"]) is not None
            )
        else:
            judge_valid = set(self.judge) == {"reviewer_id", "review_policy_id"} and all(
                isinstance(self.judge[field], str)
                and identifier.fullmatch(self.judge[field]) is not None
                for field in self.judge
            )
        if not judge_valid:
            raise ValidationError(
                {"judge": "Judge metadata must match the frozen evaluator shape."}
            )
        checks_valid = (
            isinstance(self.deterministic_checks, list)
            and 1 <= len(self.deterministic_checks) <= 20
            and all(
                isinstance(check, dict)
                and set(check) == {"check", "passed"}
                and isinstance(check["check"], str)
                and 1 <= len(check["check"]) <= 64
                and isinstance(check["passed"], bool)
                for check in self.deterministic_checks
            )
        )
        if not checks_valid:
            raise ValidationError(
                {
                    "deterministic_checks": (
                        "Deterministic checks must match the frozen bounded shape."
                    )
                }
            )
