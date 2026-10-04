from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("agents", "0008_hermes_start_receipt")]

    operations = [
        migrations.AddField(
            model_name="agentruncontrol",
            name="telemetry_traceparent",
            field=models.CharField(blank=True, default="", max_length=55),
        ),
    ]
