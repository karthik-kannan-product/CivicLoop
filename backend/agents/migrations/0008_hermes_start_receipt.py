import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_receipt_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("""
            CREATE TRIGGER agents_hermes_start_receipt_immutable
            BEFORE UPDATE OR DELETE ON agents_hermesstartreceipt
            FOR EACH ROW EXECUTE FUNCTION agents_reject_hermes_evidence_mutation();
        """)


def drop_receipt_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            "DROP TRIGGER IF EXISTS agents_hermes_start_receipt_immutable "
            "ON agents_hermesstartreceipt;"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("agents", "0007_hermes_worker_lane"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="HermesStartReceipt",
            fields=[
                ("id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="launchloop.demoactor"
                    ),
                ),
                (
                    "workflow",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="launchloop.workflow"
                    ),
                ),
                (
                    "revision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="launchloop.eventrevision"
                    ),
                ),
                (
                    "run",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="start_receipt",
                        to="agents.agentrun",
                    ),
                ),
            ],
        ),
        migrations.RunPython(create_receipt_trigger, drop_receipt_trigger),
    ]
