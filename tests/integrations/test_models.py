import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from integrations.models import EncryptedSecret, IntegrationConnection, IntegrationHealthCheck


def test_encrypted_secret_has_no_plaintext_or_recoverable_credential_metadata() -> None:
    field_names = {field.name for field in EncryptedSecret._meta.get_fields()}

    assert "plaintext" not in field_names
    assert "credential" not in field_names
    assert "last_four" not in field_names
    assert {"ciphertext", "nonce", "key_id", "algorithm", "scope"} <= field_names


@pytest.mark.django_db(transaction=True)
def test_database_allows_only_one_connection_for_each_approved_provider() -> None:
    IntegrationConnection.objects.create(provider="eventbrite")

    with pytest.raises(IntegrityError), transaction.atomic():
        IntegrationConnection.objects.create(provider="eventbrite")

    with pytest.raises(ValidationError):
        IntegrationConnection.objects.bulk_create([IntegrationConnection(provider="unknown")])


@pytest.mark.django_db(transaction=True)
def test_database_rejects_invalid_lifecycle_state_and_non_positive_version() -> None:
    with pytest.raises(ValidationError):
        IntegrationConnection.objects.create(provider="groq", state="unknown")

    with pytest.raises(IntegrityError), transaction.atomic():
        IntegrationConnection.objects.create(provider="iterable", version=0)


@pytest.mark.django_db
def test_configuration_is_closed_and_bounded() -> None:
    connection = IntegrationConnection(provider="iterable", configuration={"base_url": "https://x"})

    with pytest.raises(ValidationError):
        connection.full_clean()


@pytest.mark.django_db(transaction=True)
def test_save_rejects_credential_like_configuration_without_relying_on_full_clean_call() -> None:
    connection = IntegrationConnection(provider="iterable", configuration={"token": "synthetic"})

    with pytest.raises(ValidationError):
        connection.save()
    assert not IntegrationConnection.objects.filter(provider="iterable").exists()


@pytest.mark.django_db(transaction=True)
def test_save_rejects_cross_provider_secret_reference() -> None:
    secret = EncryptedSecret.objects.create(
        provider="eventbrite",
        scope="private_token",
        ciphertext=b"not-a-real-ciphertext",
        nonce=b"0123456789ab",
        key_id="integration-test",
    )
    connection = IntegrationConnection(provider="openai", secret=secret)

    with pytest.raises(ValidationError):
        connection.save()
    assert not IntegrationConnection.objects.filter(provider="openai").exists()


@pytest.mark.django_db(transaction=True)
def test_bulk_create_rejects_invalid_configuration_and_cross_provider_secret() -> None:
    secret = EncryptedSecret.objects.create(
        provider="eventbrite",
        scope="private_token",
        ciphertext=b"not-a-real-ciphertext",
        nonce=b"0123456789ab",
        key_id="integration-test",
    )

    with pytest.raises(ValidationError):
        IntegrationConnection.objects.bulk_create(
            [IntegrationConnection(provider="iterable", configuration={"token": "synthetic"})]
        )
    with pytest.raises(ValidationError):
        IntegrationConnection.objects.bulk_create(
            [IntegrationConnection(provider="openai", secret=secret)]
        )


@pytest.mark.django_db(transaction=True)
def test_queryset_update_and_bulk_update_cannot_bypass_connection_invariants() -> None:
    connection = IntegrationConnection.objects.create(provider="iterable")
    connection.configuration = {"token": "synthetic"}

    with pytest.raises(ValidationError):
        IntegrationConnection.objects.filter(id=connection.id).update(configuration=connection.configuration)
    with pytest.raises(ValidationError):
        IntegrationConnection.objects.bulk_update([connection], ["configuration"])


@pytest.mark.django_db(transaction=True)
def test_postgresql_trigger_rejects_raw_sql_connection_invariant_bypasses() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL trigger enforcement is verified in CI and Compose.")

    eventbrite_secret = EncryptedSecret.objects.create(
        provider="eventbrite",
        scope="private_token",
        ciphertext=b"not-a-real-ciphertext",
        nonce=b"0123456789ab",
        key_id="integration-test",
    )
    openai_secret = EncryptedSecret.objects.create(
        provider="openai",
        scope="project_key",
        ciphertext=b"not-a-real-ciphertext",
        nonce=b"0123456789ac",
        key_id="integration-test",
    )
    integration = IntegrationConnection.objects.create(provider="eventbrite")

    invalid_updates = [
        ("configuration = %s::jsonb", ['{"token":"synthetic"}']),
        ("capabilities = %s::jsonb", ['["inference"]']),
        ("secret_id = %s", [str(openai_secret.id)]),
        ("state = %s", ["configured"]),
    ]
    for assignment, values in invalid_updates:
        with pytest.raises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE integrations_integrationconnection SET {assignment} WHERE id = %s",
                    [*values, str(integration.id)],
                )

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE integrations_integrationconnection "
            "SET secret_id = %s, capabilities = %s::jsonb, state = %s WHERE id = %s",
            [
                str(eventbrite_secret.id),
                '["connection_test","draft_create","metadata_read"]',
                "configured",
                str(integration.id),
            ],
        )


@pytest.mark.django_db(transaction=True)
def test_health_history_requires_a_connection_and_protects_history_from_deletion() -> None:
    connection = IntegrationConnection.objects.create(provider="openai")
    health_check = IntegrationHealthCheck.objects.create(
        connection=connection,
        outcome="healthy",
        duration_ms=3,
        correlation_id="9a7af728-47a8-47e7-9dd0-354beb374bae",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        IntegrationHealthCheck.objects.create(
            connection_id=None,
            outcome="healthy",
            duration_ms=3,
            correlation_id="bc781d1e-479f-4973-9dd0-94c1690cf7b3",
        )

    with pytest.raises(ProtectedError):
        connection.delete()

    health_check.refresh_from_db()
    assert health_check.connection_id == connection.id


@pytest.mark.django_db(transaction=True)
def test_connection_protects_its_encrypted_secret_from_cascade_deletion() -> None:
    secret = EncryptedSecret.objects.create(
        provider="groq",
        scope="api_key",
        ciphertext=b"not-a-real-ciphertext",
        nonce=b"0123456789ab",
        key_id="integration-test",
    )
    connection = IntegrationConnection.objects.create(
        provider="groq",
        state="configured",
        secret=secret,
        capabilities=["connection_test", "evaluation_judge", "inference"],
    )

    with pytest.raises(ProtectedError):
        secret.delete()

    connection.delete()
    assert EncryptedSecret.objects.filter(id=secret.id).exists()
