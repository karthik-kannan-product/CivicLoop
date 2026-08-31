from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.models import CheckConstraint, UniqueConstraint
from integrations.models import IntegrationConnection, IntegrationHealthCheck


def test_initial_integration_migration_is_additive_and_declares_database_constraints() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)

    assert ("integrations", "0001_initial") in loader.disk_migrations
    constraints = IntegrationConnection._meta.constraints
    assert IntegrationConnection._meta.get_field("provider").unique is True
    assert not any(isinstance(constraint, UniqueConstraint) for constraint in constraints)
    assert sum(isinstance(constraint, CheckConstraint) for constraint in constraints) >= 2
    assert IntegrationHealthCheck._meta.get_field("connection").null is False


def test_postgresql_invariant_trigger_migration_is_present() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)

    assert ("integrations", "0002_connection_invariants") in loader.disk_migrations


@pytest.mark.django_db(transaction=True)
def test_eventbrite_capability_migration_upgrades_existing_configured_connection() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL migration upgrade behavior is verified in CI and Compose.")

    executor = MigrationExecutor(connection)
    executor.migrate([("integrations", "0002_connection_invariants")])
    old_trigger_migration = import_module(
        "integrations.migrations.0002_connection_invariants"
    )
    with connection.schema_editor() as schema_editor:
        schema_editor.execute(old_trigger_migration.CREATE_FUNCTION_SQL)
    old_apps = executor.loader.project_state(
        [("integrations", "0002_connection_invariants")]
    ).apps
    encrypted_secret = old_apps.get_model("integrations", "EncryptedSecret")
    integration_connection = old_apps.get_model("integrations", "IntegrationConnection")
    secret = encrypted_secret.objects.create(
        provider="eventbrite",
        scope="organization",
        ciphertext=b"synthetic-ciphertext",
        nonce=b"synthetic123",
        key_id="synthetic-key",
    )
    integration_connection.objects.create(
        provider="eventbrite",
        state="configured",
        capabilities=["connection_test", "draft_create", "metadata_read"],
        configuration={},
        secret=secret,
    )

    executor = MigrationExecutor(connection)
    executor.migrate([("integrations", "0003_eventbrite_read_only_capabilities")])
    migrated_apps = executor.loader.project_state(
        [("integrations", "0003_eventbrite_read_only_capabilities")]
    ).apps
    migrated_connection = migrated_apps.get_model(
        "integrations", "IntegrationConnection"
    ).objects.get(provider="eventbrite")

    assert migrated_connection.capabilities == ["connection_test", "metadata_read"]
