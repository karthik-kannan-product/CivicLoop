import django.db.models.deletion
from django.db import migrations, models


def seed_lane(apps, schema_editor):
    apps.get_model("agents", "HermesAdmissionLane").objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("agents", "0006_hermes_run_state")]
    operations = [
        migrations.AddField(
            model_name="agentrun", name="hermes_lane", field=models.BooleanField(default=False)
        ),
        migrations.AddConstraint(
            model_name="agentrun",
            constraint=models.UniqueConstraint(
                fields=("hermes_lane",),
                condition=models.Q(hermes_lane=True, status__in=("queued", "running")),
                name="agents_one_active_hermes_run",
            ),
        ),
        migrations.AddField(
            model_name="agentruncontrol",
            name="capability",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to="agents.workflowcapability",
            ),
        ),
        migrations.AddField(
            model_name="agentruncontrol",
            name="lease_expires_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.CreateModel(
            name="HermesAdmissionLane",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("admission_disabled", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "active_run",
                    models.OneToOneField(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="admission_lane",
                        to="agents.agentrun",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(id=1), name="agents_hermes_single_lane"
                    )
                ]
            },
        ),
        migrations.RunPython(seed_lane, migrations.RunPython.noop),
    ]
