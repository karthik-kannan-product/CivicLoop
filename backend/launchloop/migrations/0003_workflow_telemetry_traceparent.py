from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("launchloop", "0002_demoactor_user")]

    operations = [
        migrations.AddField(
            model_name="workflow",
            name="telemetry_traceparent",
            field=models.CharField(blank=True, max_length=55),
        ),
    ]
