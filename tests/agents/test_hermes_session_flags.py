import pytest

from tests.agents.test_api import validate_schema
from tests.identity.test_security_actions_api import create_authenticated_owner


@pytest.mark.django_db
@pytest.mark.parametrize(
    "hermes,pending", [(False, False), (True, False), (False, True), (True, True)]
)
def test_owner_session_reports_both_activation_flags(settings, hermes, pending):
    settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED = True
    settings.CIVICLOOP_HERMES_ENABLED = hermes
    settings.CIVICLOOP_HERMES_PENDING_OPERATIONS_ENABLED = pending
    owner, *_ = create_authenticated_owner()
    response = owner.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json()["user"]["hermes_enabled"] is (hermes and pending)
    validate_schema(response.json(), "api/session-response.schema.json")
