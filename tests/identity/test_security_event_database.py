import pytest
from django.contrib.auth.models import User
from django.db import DatabaseError, connection, transaction
from identity.models import AdministratorProfile, AdministratorSecurityEvent


@pytest.mark.django_db(transaction=True)
def test_postgresql_rejects_security_event_update_and_delete() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL trigger enforcement is verified in CI and Compose.")

    user = User.objects.create_user(username="synthetic.audit.owner")
    profile = AdministratorProfile.objects.create(
        user=user,
        status=AdministratorProfile.Status.ACTIVE,
    )
    event = AdministratorSecurityEvent.objects.create(
        profile=profile,
        user=user,
        action="synthetic_event",
        outcome="success",
    )

    with pytest.raises(DatabaseError), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE identity_administratorsecurityevent SET outcome = %s WHERE id = %s",
                ["changed", event.id],
            )

    assert AdministratorSecurityEvent.objects.filter(id=event.id).exists()

    with pytest.raises(DatabaseError), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM identity_administratorsecurityevent WHERE id = %s",
                [event.id],
            )
