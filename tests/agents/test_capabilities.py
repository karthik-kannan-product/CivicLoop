import hashlib
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.agents.test_runs import create_workflow


@pytest.mark.django_db
def test_capability_is_opaque_hash_only_and_bound_to_current_revision():
    from agents.capabilities import issue_workflow_capability, revoke_workflow_capability
    from agents.models import WorkflowCapability

    workflow, revision, actor, _ = create_workflow()
    token = issue_workflow_capability(
        workflow_id=workflow.id,
        revision_id=revision.id,
        actor_id=actor.pk,
        tools=frozenset({"get_event_revision"}),
        lifetime_seconds=60,
    )
    stored = WorkflowCapability.objects.get()
    assert stored.token_digest == hashlib.sha256(token.encode()).hexdigest()
    assert str(workflow.id) not in token and actor.pk not in token
    assert stored.revision_id == revision.id and stored.actor_id == actor.pk
    assert timezone.now() < stored.expires_at < timezone.now() + timedelta(seconds=61)
    assert stored.audience == "civicloop-hermes"
    from deploy.hermes.adapter import CAPABILITY_TOKEN

    assert CAPABILITY_TOKEN.fullmatch(token)
    assert all(token not in str(value) for value in stored.__dict__.values())
    revoke_workflow_capability(capability=token)
    stored.refresh_from_db()
    assert stored.revoked_at is not None


@pytest.mark.django_db
@pytest.mark.parametrize("lifetime", [0, 301, True, -1])
def test_issuance_rejects_unbounded_lifetimes(lifetime):
    from agents.capabilities import AuthorizationDenied, issue_workflow_capability

    workflow, revision, actor, _ = create_workflow()
    with pytest.raises(AuthorizationDenied):
        issue_workflow_capability(
            workflow_id=workflow.id,
            revision_id=revision.id,
            actor_id=actor.pk,
            tools=frozenset({"get_event_revision"}),
            lifetime_seconds=lifetime,
        )
