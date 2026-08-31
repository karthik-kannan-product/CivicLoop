from django.db import migrations


def add_fixed_judge(apps, _schema_editor):
    profile_model = apps.get_model("agents", "ModelProfile")
    policy_model = apps.get_model("agents", "RoutingPolicy")
    profile, _ = profile_model.objects.get_or_create(
        profile_id="launchloop_openai_judge",
        revision=1,
        defaults={
            "provider": "openai",
            "model": "gpt-5-mini-2025-08-07",
            "purpose": "evaluation_judge",
            "max_input_tokens": 4096,
            "max_output_tokens": 256,
            "temperature": 0,
            "input_price_microusd_per_million": 250_000,
            "output_price_microusd_per_million": 2_000_000,
        },
    )
    policy_model.objects.get_or_create(
        policy_id="launchloop_openai_judge",
        revision=1,
        defaults={
            "purpose": "evaluation_judge",
            "model_profile": profile,
            "per_run_limit_microusd": 500_000,
            "monthly_limit_microusd": 25_000_000,
        },
    )


def remove_fixed_judge(apps, _schema_editor):
    policy_model = apps.get_model("agents", "RoutingPolicy")
    profile_model = apps.get_model("agents", "ModelProfile")
    policy_model.objects.filter(policy_id="launchloop_openai_judge", revision=1).delete()
    profile_model.objects.filter(profile_id="launchloop_openai_judge", revision=1).delete()


class Migration(migrations.Migration):
    dependencies = [("agents", "0002_database_invariants")]
    operations = [migrations.RunPython(add_fixed_judge, remove_fixed_judge)]
