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
