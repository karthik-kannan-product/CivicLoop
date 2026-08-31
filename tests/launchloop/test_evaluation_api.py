import uuid

import pytest
from django.test import Client, override_settings
from launchloop.services import reset_demo, run_workflow

from tests.identity.test_security_actions_api import create_authenticated_owner

FEATURES = override_settings(
    CIVICLOOP_ADMIN_IDENTITY_ENABLED=True,
    CIVICLOOP_INTEGRATIONS_ENABLED=True,
)


@pytest.mark.django_db
@FEATURES
def test_fixed_evaluation_endpoint_requires_administrator(monkeypatch) -> None:
    missing = Client().post(f"/api/v1/workflows/{uuid.uuid4()}/evaluations")
    assert missing.status_code == 401

    workflow = reset_demo()
    workflow = run_workflow(workflow.id, workflow.revision.author)
    client, _profile, administrator, _password = create_authenticated_owner()
    called = {}

    def fake_judge(candidate, actor):
        called.update(workflow_id=candidate.id, administrator_id=actor.id)

    monkeypatch.setattr("launchloop.views.run_fixed_judge", fake_judge)
    response = client.post(f"/api/v1/workflows/{workflow.id}/evaluations")

    assert response.status_code == 200
    assert response.json()["evaluation"] is None
    assert called == {
        "workflow_id": workflow.id,
        "administrator_id": administrator.id,
    }
