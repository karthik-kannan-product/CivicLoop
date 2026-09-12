from django.db import migrations


def add_fixed_judge_r2(apps, _schema_editor):
    profile_model = apps.get_model("agents", "ModelProfile")
    policy_model = apps.get_model("agents", "RoutingPolicy")
    profile, _ = profile_model.objects.get_or_create(
        profile_id="launchloop_openai_judge",
        revision=2,
        defaults={
            "provider": "openai",
            "model": "gpt-5.5-2026-04-23",
            "purpose": "evaluation_judge",
            "max_input_tokens": 4096,
            "max_output_tokens": 256,
            "temperature": 0,
            "input_price_microusd_per_million": 5_000_000,
            "output_price_microusd_per_million": 30_000_000,
        },
    )
    policy_model.objects.get_or_create(
        policy_id="launchloop_openai_judge",
        revision=2,
        defaults={
            "purpose": "evaluation_judge",
            "model_profile": profile,
            "per_run_limit_microusd": 500_000,
            "monthly_limit_microusd": 25_000_000,
        },
    )


def remove_fixed_judge_r2(apps, _schema_editor):
    policy_model = apps.get_model("agents", "RoutingPolicy")
    profile_model = apps.get_model("agents", "ModelProfile")
    policy_model.objects.filter(policy_id="launchloop_openai_judge", revision=2).delete()
    profile_model.objects.filter(profile_id="launchloop_openai_judge", revision=2).delete()


class Migration(migrations.Migration):
    dependencies = [("agents", "0003_fixed_openai_judge_profile")]
    operations = [migrations.RunPython(add_fixed_judge_r2, remove_fixed_judge_r2)]
