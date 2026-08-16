from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_image_collects_static_as_root_and_locks_down_application_files() -> None:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text()

    collectstatic = "python backend/manage.py collectstatic --noinput"
    lock_down = "chmod -R a-w /app"

    assert "chown -R civicloop:civicloop /app" not in dockerfile
    assert collectstatic in dockerfile
    assert lock_down in dockerfile
    assert dockerfile.index(collectstatic) < dockerfile.index(lock_down) < dockerfile.index(
        "USER civicloop"
    )


def test_compose_app_services_have_a_read_only_root_and_safe_temporary_storage() -> None:
    compose = (REPOSITORY_ROOT / "compose.yaml").read_text()

    assert "read_only: true" in compose
    assert "- /tmp:mode=1777" in compose
    assert compose.count("tmpfs:") == 1


def test_identity_key_mount_is_limited_to_web_and_management_contexts() -> None:
    specification = yaml.safe_load((REPOSITORY_ROOT / "compose.yaml").read_text())
    services = specification["services"]
    secret_target = "/run/secrets/civicloop-identity-keyring.json:ro"

    mounted_services = {
        name
        for name, service in services.items()
        if any(secret_target in volume for volume in service.get("volumes", []))
    }

    assert mounted_services == {"migrate", "web"}
    assert services["worker"]["environment"]["CIVICLOOP_ADMIN_IDENTITY_ENABLED"] == "false"
    assert services["scheduler"]["environment"]["CIVICLOOP_ADMIN_IDENTITY_ENABLED"] == "false"


def test_integration_key_mount_is_limited_to_web_and_worker() -> None:
    base_compose = (REPOSITORY_ROOT / "compose.yaml").read_text()
    specification = yaml.safe_load(
        (REPOSITORY_ROOT / "compose.integrations.yaml").read_text()
    )
    services = specification["services"]
    secret_target = "/run/secrets/civicloop-integration-keyring.json:ro"

    assert "CIVICLOOP_INTEGRATION_KEY_HOST_PATH" not in base_compose

    mounted_services = {
        name
        for name, service in services.items()
        if any(secret_target in volume for volume in service.get("volumes", []))
    }

    assert mounted_services == {"web", "worker"}
    base_services = yaml.safe_load(base_compose)["services"]
    assert base_services["worker"]["environment"]["CIVICLOOP_IDENTITY_KEY_FILE"] == ""
    assert all(
        not any(
            "/run/secrets/civicloop-identity-keyring.json:ro" in volume
            for volume in base_services[service_name].get("volumes", [])
        )
        for service_name in ("worker", "scheduler")
    )


def test_administrator_focus_target_does_not_collapse_the_mobile_layout() -> None:
    styles = (REPOSITORY_ROOT / "frontend" / "src" / "admin" / "admin.css").read_text()
    focus_rule = styles.split(".admin-focus-target {", 1)[1].split("}", 1)[0]

    assert "height: 0" not in focus_rule
    assert "width: 0" not in focus_rule
