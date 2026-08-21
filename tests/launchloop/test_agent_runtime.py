import pytest
from django.db import IntegrityError
from launchloop.models import AgentRun
from launchloop.services import reset_demo


@pytest.mark.django_db
def test_agent_run_rejects_a_fourth_unknown_specialist() -> None:
    workflow = reset_demo()

    with pytest.raises(IntegrityError):
        AgentRun.objects.create(
            workflow=workflow,
            revision=workflow.revision,
            specialist="fourth_specialist",
            provider="deterministic_hermes",
            status=AgentRun.Status.QUEUED,
            summary="This record must not be persisted.",
        )
